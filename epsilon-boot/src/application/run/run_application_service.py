"""Run 应用服务实现。

本模块实现阶段三后台 Run runtime 的 adapter-neutral 编排能力，包括创建、
查询、取消、继续、审批恢复和事件 replay/stream。应用服务只面向领域端口
编程，不导入 FastAPI、TUI 或基础设施 adapter。
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, Protocol

from application.run.serialization_ports import WorkflowSerializerPort
from domain.agent.value_objects import ApprovalDecision
from domain.run.exceptions import (
    RunCancelUnavailableError,
    RunContinuationUnavailableError,
    RunEventReplayExpiredError,
    RunIdempotencyConflictError,
    RunNotFoundError,
    RunQueueFullError,
)
from domain.run.ports import (
    ApprovalResumeStoreResult,
    RunEventStorePort,
    RunStorePort,
    WorkflowSelection,
    WorkflowSelectorPort,
)
from domain.run.runtime_context import (
    RunExecutionContext,
    reset_run_execution_context,
    set_run_execution_context,
)
from domain.run.state_machine import RunStateMachine
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunSnapshot,
    RunStatus,
)
from domain.run.workflow import WorkflowRunState

RunWorkerWakeup = Callable[[], None]
"""唤醒后台 worker 的轻量回调类型。"""


logger = logging.getLogger(__name__)

_APPROVAL_RESUME_LEASE_SECONDS = 60
"""审批恢复期间允许写入 Run 观察事件的短租约秒数。"""


@dataclass(frozen=True)
class RunRuntimeMetricsSnapshot:
    """阶段三 Run runtime 的轻量观测快照。"""

    queued_count: int = 0
    running_count: int = 0
    claim_success_count: int = 0
    lease_expired_count: int = 0
    lost_count: int = 0
    cancel_request_count: int = 0
    execution_duration_seconds_total: float = 0.0
    execution_duration_count: int = 0
    replay_expired_count: int = 0
    queue_full_count: int = 0
    execution_failed_count: int = 0


class RunRuntimeMetrics:
    """进程内 Run runtime 指标收集器。

    该实现仅维护测试和 readiness 可读取的内存计数，不引入外部 metrics
    依赖。调用方可通过 ``snapshot`` 获取不可变快照。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._queued_count = 0
        self._running_count = 0
        self._claim_success_count = 0
        self._lease_expired_count = 0
        self._lost_count = 0
        self._cancel_request_count = 0
        self._execution_duration_seconds_total = 0.0
        self._execution_duration_count = 0
        self._replay_expired_count = 0
        self._queue_full_count = 0
        self._execution_failed_count = 0

    def set_run_counts(self, *, queued_count: int, running_count: int) -> None:
        """设置当前 queued/running 数量。"""

        with self._lock:
            self._queued_count = queued_count
            self._running_count = running_count

    def increment_claim_success(self) -> None:
        """记录一次成功 claim。"""

        with self._lock:
            self._claim_success_count += 1

    def increment_lost(self, count: int = 1) -> None:
        """记录 lease 过期导致的 lost 数量。"""

        if count <= 0:
            return
        with self._lock:
            self._lease_expired_count += count
            self._lost_count += count

    def increment_cancel_request(self) -> None:
        """记录一次取消请求。"""

        with self._lock:
            self._cancel_request_count += 1

    def observe_execution_duration(self, duration_seconds: float) -> None:
        """记录一次 Run 执行耗时。"""

        with self._lock:
            self._execution_duration_seconds_total += max(duration_seconds, 0.0)
            self._execution_duration_count += 1

    def increment_replay_expired(self) -> None:
        """记录一次 replay 过期。"""

        with self._lock:
            self._replay_expired_count += 1

    def increment_queue_full(self) -> None:
        """记录一次容量拒绝。"""

        with self._lock:
            self._queue_full_count += 1

    def increment_execution_failed(self) -> None:
        """记录一次执行失败。"""

        with self._lock:
            self._execution_failed_count += 1

    def snapshot(self) -> RunRuntimeMetricsSnapshot:
        """返回当前指标快照。"""

        with self._lock:
            return RunRuntimeMetricsSnapshot(
                queued_count=self._queued_count,
                running_count=self._running_count,
                claim_success_count=self._claim_success_count,
                lease_expired_count=self._lease_expired_count,
                lost_count=self._lost_count,
                cancel_request_count=self._cancel_request_count,
                execution_duration_seconds_total=(self._execution_duration_seconds_total),
                execution_duration_count=self._execution_duration_count,
                replay_expired_count=self._replay_expired_count,
                queue_full_count=self._queue_full_count,
                execution_failed_count=self._execution_failed_count,
            )


ApprovalResumeResult = ApprovalResumeStoreResult
"""审批恢复回调返回类型，复用 Run 存储端口的审批恢复指令值对象。"""


class ApprovalResumer(Protocol):
    """审批恢复回调端口。

    具体 adapter 可在组合根注入调用既有审批恢复能力的 callable。应用服务
    不直接依赖 ChatServicePort、TaskAgentPort 或任何基础设施实现。
    """

    async def __call__(
        self,
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        """恢复 awaiting_approval Run 的审批并返回后续状态指令。"""
        ...


class RunApplicationService:
    """后台 Run 应用服务。

    该服务负责协调 RunStorePort、RunEventStorePort、容量策略、事件保留策略
    和 worker 唤醒回调，为各类 adapter 提供一致的 Run 操作语义。
    """

    def __init__(
        self,
        *,
        run_store: RunStorePort,
        event_store: RunEventStorePort,
        capacity_policy: RunCapacityPolicy,
        event_retention_policy: EventRetentionPolicy,
        workflow_serializer: WorkflowSerializerPort,
        worker_wakeup: RunWorkerWakeup | None = None,
        approval_resumer: ApprovalResumer | None = None,
        event_stream_wait_seconds: float = 1.0,
        metrics: RunRuntimeMetrics | None = None,
        guardrail_policy: Any | None = None,
        workflow_selector: WorkflowSelectorPort | None = None,
    ) -> None:
        """初始化 Run 应用服务。

        Args:
            run_store: Run 快照和控制状态存储端口。
            event_store: Run 事件存储端口。
            capacity_policy: 创建和运行容量策略。
            event_retention_policy: 事件历史保留策略。
            worker_wakeup: 可选 worker 唤醒回调，创建/继续后调用。
            approval_resumer: 可选审批恢复回调端口。
            event_stream_wait_seconds: stream_events 每轮等待新事件的秒数。
            metrics: 可选进程内 Run runtime 指标收集器。
            workflow_serializer: 工作流值对象序列化端口，由组合根注入。
            workflow_selector: 可选工作流选择端口。
        """

        self._run_store = run_store
        self._event_store = event_store
        self._capacity_policy = capacity_policy
        self._event_retention_policy = event_retention_policy
        self._worker_wakeup = worker_wakeup
        self._approval_resumer = approval_resumer
        self._event_stream_wait_seconds = event_stream_wait_seconds
        self._metrics = metrics
        self._guardrail_policy = guardrail_policy
        self._workflow_serializer = workflow_serializer
        self._workflow_selector = workflow_selector
        self._state_machine = RunStateMachine()

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """创建后台 Run，或按幂等键返回既有 Run。

        Args:
            request: 创建 Run 的领域请求，包含 payload、幂等键和 payload hash。

        Returns:
            新建或幂等命中的 Run 快照。

        Raises:
            RunIdempotencyConflictError: 同一幂等键对应不同 payload hash。
            RunQueueFullError: 队列或运行容量达到上限。
        """

        request = self._with_effective_payload_hash(request)
        existing = await self._find_existing_idempotent_run(request)
        if existing is not None:
            logger.info(
                "Run create idempotency hit",
                extra=_log_extra(existing, worker_id=None),
            )
            return existing

        await self._assert_create_capacity()
        request = self._with_task_classification(request)
        request, workflow_selection = self._with_workflow_selection(request)
        snapshot = await self._run_store.create_run(request)
        latest_cursor = snapshot.latest_event_cursor
        if snapshot.task_classification is not None:
            event = await self._append_event(
                snapshot.run_id,
                RunEventType.TASK_CLASSIFIED,
                {"task_classification": snapshot.task_classification},
            )
            latest_cursor = event.cursor
        if workflow_selection is not None:
            event_type = (
                RunEventType.WORKFLOW_SELECTED
                if workflow_selection.workflow is not None
                else RunEventType.WORKFLOW_SELECTION_SKIPPED
            )
            event = await self._append_event(
                snapshot.run_id,
                event_type,
                _workflow_selection_event_payload(workflow_selection),
            )
            latest_cursor = event.cursor
        for event_type in (RunEventType.RUN_CREATED, RunEventType.RUN_QUEUED):
            event = await self._append_event(snapshot.run_id, event_type, {})
            latest_cursor = event.cursor
        snapshot = replace(snapshot, latest_event_cursor=latest_cursor)
        logger.info(
            "Run created and queued",
            extra=_log_extra(snapshot, worker_id=None),
        )
        self._wake_worker()
        return snapshot

    def _with_task_classification(self, request: RunCreateRequest) -> RunCreateRequest:
        """使用 guardrail policy 为新 Run 填充确定性任务分类。"""

        if self._guardrail_policy is None or request.task_classification is not None:
            return request
        classify = getattr(self._guardrail_policy, "classify_payload", None)
        if not callable(classify):
            return request
        try:
            task_classification = classify(request.payload, has_tools=True)
        except Exception:
            logger.warning("Run task classification failed", exc_info=True)
            return request
        value = getattr(task_classification, "value", str(task_classification))
        return replace(request, task_classification=value)

    def _with_workflow_selection(
        self, request: RunCreateRequest
    ) -> tuple[RunCreateRequest, WorkflowSelection | None]:
        """使用注入的选择器为新 Run 填充 workflow 初始状态。"""

        if self._workflow_selector is None:
            return request, None
        selection = self._workflow_selector.select(request)
        if selection.workflow is None:
            return request, selection

        first_phase = selection.workflow.phases[0].phase
        workflow_state = self._workflow_serializer.workflow_run_state_to_dict(
            WorkflowRunState(
                workflow_name=selection.workflow.name,
                current_phase=first_phase,
                phase_started_at=None,
            )
        )
        return (
            replace(
                request,
                workflow_name=selection.workflow.name,
                workflow_run_state=workflow_state,
            ),
            selection,
        )

    async def get_run(self, run_id: str) -> RunSnapshot:
        """按 run_id 查询最新 Run 快照。

        Args:
            run_id: Run 唯一标识。

        Returns:
            最新 Run 快照。

        Raises:
            RunNotFoundError: 当 run_id 不存在时抛出。
        """

        snapshot = await self._run_store.get_run(run_id)
        if snapshot is None:
            raise RunNotFoundError(run_id)
        return snapshot

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        """请求取消 queued、running、paused 或 awaiting_approval Run。

        queued Run 会直接进入 cancelled；running、paused、awaiting_approval 会
        进入 cancel_requested；cancel_requested 的重复请求按幂等处理。
        终态 Run 返回客户端可见冲突。
        """

        current = await self.get_run(run_id)
        if self._state_machine.is_terminal(current.status):
            raise RunCancelUnavailableError(run_id, f"当前状态为 {current.status.value}")
        if current.status is RunStatus.CANCEL_REQUESTED:
            return current
        if not self._state_machine.can_cancel(current.status):
            raise RunCancelUnavailableError(run_id, f"当前状态为 {current.status.value}")

        if self._metrics is not None:
            self._metrics.increment_cancel_request()
        snapshot = await self._run_store.request_cancel(run_id)
        if snapshot.status is RunStatus.CANCELLED:
            event_type = RunEventType.RUN_CANCELLED
        else:
            event_type = RunEventType.CANCEL_REQUESTED
        event = await self._append_event(
            run_id,
            event_type,
            {"previous_status": current.status.value, "status": snapshot.status.value},
        )
        logger.info(
            "Run cancel requested",
            extra=_log_extra(
                snapshot,
                worker_id=None,
                previous_status=current.status.value,
                event_type=event_type.value,
            ),
        )
        return replace(snapshot, latest_event_cursor=event.cursor)

    async def continue_run(self, run_id: str, model: str | None = None) -> RunSnapshot:
        """继续 paused 且 can_continue=true 的 Run。

        Args:
            run_id: Run 唯一标识。
            model: 可选模型覆盖，交由后续 worker/执行协调器解释。

        Returns:
            重新入队后的 Run 快照。

        Raises:
            RunContinuationUnavailableError: 非 paused、终态或 can_continue=false。
        """

        current = await self.get_run(run_id)
        self._assert_continue_available(current)
        snapshot = await self._run_store.enqueue_continue(run_id=run_id, model=model)
        event = await self._append_event(
            run_id,
            RunEventType.RUN_QUEUED,
            {"previous_status": current.status.value, "model": model},
        )
        logger.info(
            "Run continuation queued",
            extra=_log_extra(snapshot, worker_id=None, previous_status=current.status.value),
        )
        self._wake_worker()
        return replace(snapshot, latest_event_cursor=event.cursor)

    async def resume_approval_run(
        self,
        run_id: str,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> RunSnapshot:
        """提交审批决策并让 awaiting_approval Run 重新入队或进入终态。

        Args:
            run_id: Run 唯一标识。
            decisions: 审批决策列表。
            model: 可选模型覆盖。

        Returns:
            审批恢复后同一 Run 的最新快照。

        Raises:
            RunContinuationUnavailableError: Run 不在 awaiting_approval 状态，
                或服务未注入审批恢复回调。
        """

        current = await self.get_run(run_id)
        if current.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(
                run_id,
                f"当前状态为 {current.status.value}，不是 awaiting_approval",
            )
        if self._approval_resumer is None:
            raise RunContinuationUnavailableError(run_id, "未配置审批恢复回调")

        lease_owner = f"approval-resume-{uuid.uuid4().hex}"
        leased = await self._run_store.acquire_approval_resume_lease(
            run_id=run_id,
            owner_id=lease_owner,
            lease_seconds=_APPROVAL_RESUME_LEASE_SECONDS,
        )
        try:
            resume_result = await self._call_approval_resumer_with_run_context(
                leased,
                decisions,
                model,
                owner_id=lease_owner,
            )
        except Exception:
            try:
                await self._run_store.release_approval_resume_lease(
                    run_id=run_id,
                    owner_id=lease_owner,
                )
            except Exception:
                logger.warning(
                    "Run approval resume lease release failed",
                    exc_info=True,
                    extra=_log_extra(leased, worker_id=None),
                )
            raise
        snapshot = await self._run_store.resolve_approval_resume(
            run_id=run_id,
            owner_id=lease_owner,
            result=resume_result,
        )
        if resume_result.status == "queued":
            event_type = RunEventType.RUN_QUEUED
            payload = {
                "previous_status": current.status.value,
                "model": model,
                "terminal_reason": resume_result.terminal_reason or snapshot.terminal_reason,
            }
            self._wake_worker()
        elif resume_result.status == "awaiting_approval":
            event_type = RunEventType.APPROVAL_REQUIRED
            payload = {
                "previous_status": current.status.value,
                "approval_id": resume_result.approval_id or snapshot.approval_id,
                "result": resume_result.result,
                "terminal_reason": resume_result.terminal_reason or snapshot.terminal_reason,
            }
        elif resume_result.status == "succeeded":
            event_type = RunEventType.RUN_SUCCEEDED
            payload = {"terminal_reason": resume_result.terminal_reason or snapshot.terminal_reason}
        elif resume_result.status == "failed":
            event_type = RunEventType.RUN_FAILED
            payload = {"terminal_reason": resume_result.terminal_reason or snapshot.terminal_reason}
        else:
            event_type = RunEventType.RUN_CANCELLED
            payload = {"terminal_reason": resume_result.terminal_reason or snapshot.terminal_reason}

        event = await self._append_event(run_id, event_type, payload)
        logger.info(
            "Run approval resume resolved",
            extra=_log_extra(
                snapshot,
                worker_id=None,
                previous_status=current.status.value,
                event_type=event_type.value,
            ),
        )
        return replace(snapshot, latest_event_cursor=event.cursor)

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        """按 cursor 查询 Run 事件，并显式处理 replay 过期。

        Args:
            run_id: Run 唯一标识。
            after_cursor: 只返回该 cursor 之后的事件；None 表示从保留窗口起点开始。
            limit: 最大返回事件数。

        Returns:
            Run 事件列表。

        Raises:
            RunNotFoundError: Run 不存在。
            RunEventReplayExpiredError: after_cursor 早于当前可完整 replay 的窗口。
        """

        await self.get_run(run_id)
        await self._assert_replay_available(run_id, after_cursor)
        return await self._event_store.list_events(run_id, after_cursor, limit)

    async def stream_events(self, run_id: str, after_cursor: int | None) -> AsyncIterator[RunEvent]:
        """订阅 Run 事件流，终态事件送达后自然结束。

        Args:
            run_id: Run 唯一标识。
            after_cursor: 只发送该 cursor 之后的事件；None 表示从保留窗口起点开始。

        Yields:
            Run 事件对象，包含单调递增 cursor。

        Raises:
            RunNotFoundError: Run 不存在。
            RunEventReplayExpiredError: after_cursor 早于当前可完整 replay 的窗口。
        """

        await self.get_run(run_id)
        await self._assert_replay_available(run_id, after_cursor)
        cursor = after_cursor

        while True:
            events = await self._event_store.list_events(
                run_id,
                cursor,
                self._event_retention_policy.max_event_count,
            )
            if not events:
                events = await self._event_store.wait_events(
                    run_id,
                    cursor,
                    self._event_stream_wait_seconds,
                )
            for event in events:
                cursor = event.cursor
                yield event
                if self._is_terminal_event(event.event_type):
                    return

            snapshot = await self.get_run(run_id)
            if self._state_machine.is_terminal(snapshot.status) and not events:
                return

    def _with_effective_payload_hash(self, request: RunCreateRequest) -> RunCreateRequest:
        """补齐创建请求中的有效 payload hash。"""

        return replace(request, payload_hash=request.effective_payload_hash())

    async def _find_existing_idempotent_run(self, request: RunCreateRequest) -> RunSnapshot | None:
        """按 client_request_id 处理幂等命中和 payload 冲突。"""

        if request.client_request_id is None:
            return None
        existing = await self._run_store.get_by_client_request_id(request.client_request_id)
        if existing is None:
            return None
        if existing.payload_hash != request.payload_hash:
            raise RunIdempotencyConflictError(request.client_request_id)
        requested_workflow = _normalized_workflow_name(request.workflow_name)
        if requested_workflow is not None and requested_workflow != _normalized_workflow_name(
            existing.workflow_name
        ):
            raise RunIdempotencyConflictError(request.client_request_id)
        return existing

    async def _assert_create_capacity(self) -> None:
        """校验创建新 Run 时的队列与运行容量。"""

        queued_count = await self._run_store.count_by_status({RunStatus.QUEUED})
        running_count = await self._run_store.count_by_status(
            {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}
        )
        if self._metrics is not None:
            self._metrics.set_run_counts(
                queued_count=queued_count,
                running_count=running_count,
            )
        if queued_count >= self._capacity_policy.max_queued_runs:
            if self._metrics is not None:
                self._metrics.increment_queue_full()
            logger.warning(
                "Run queue capacity full",
                extra={
                    "run_id": None,
                    "run_kind": None,
                    "run_status": RunStatus.QUEUED.value,
                    "worker_id": None,
                    "client_request_id": None,
                    "limit_name": "max_queued_runs",
                    "limit": self._capacity_policy.max_queued_runs,
                    "queued_count": queued_count,
                    "running_count": running_count,
                },
            )
            raise RunQueueFullError(
                "max_queued_runs",
                self._capacity_policy.max_queued_runs,
            )
        if running_count >= self._capacity_policy.max_running_runs:
            if self._metrics is not None:
                self._metrics.increment_queue_full()
            logger.warning(
                "Run running capacity full",
                extra={
                    "run_id": None,
                    "run_kind": None,
                    "run_status": RunStatus.RUNNING.value,
                    "worker_id": None,
                    "client_request_id": None,
                    "limit_name": "max_running_runs",
                    "limit": self._capacity_policy.max_running_runs,
                    "queued_count": queued_count,
                    "running_count": running_count,
                },
            )
            raise RunQueueFullError(
                "max_running_runs",
                self._capacity_policy.max_running_runs,
            )

    def _assert_continue_available(self, snapshot: RunSnapshot) -> None:
        """校验 paused 且 can_continue=true 的继续前置条件。"""

        if self._state_machine.is_terminal(snapshot.status):
            raise RunContinuationUnavailableError(
                snapshot.run_id,
                f"当前状态为终态 {snapshot.status.value}",
            )
        if snapshot.status is not RunStatus.PAUSED:
            raise RunContinuationUnavailableError(
                snapshot.run_id,
                f"当前状态为 {snapshot.status.value}，不是 paused",
            )
        if not snapshot.can_continue:
            raise RunContinuationUnavailableError(snapshot.run_id, "can_continue=false")

    async def _append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        """追加事件并执行保留策略裁剪。"""

        event = await self._event_store.append_event(run_id, event_type, payload)
        await self._event_store.trim_events(run_id, self._event_retention_policy)
        return event

    async def _assert_replay_available(self, run_id: str, after_cursor: int | None) -> None:
        """校验 after_cursor 没有落在事件保留窗口之前。"""

        if after_cursor is None:
            return
        first_cursor = await self._event_store.first_cursor(run_id)
        if first_cursor is None:
            return
        if after_cursor < first_cursor - 1:
            if self._metrics is not None:
                self._metrics.increment_replay_expired()
            logger.warning(
                "Run event replay expired",
                extra={
                    "run_id": run_id,
                    "run_kind": None,
                    "run_status": None,
                    "worker_id": None,
                    "client_request_id": None,
                    "after_cursor": after_cursor,
                    "first_cursor": first_cursor,
                },
            )
            raise RunEventReplayExpiredError(run_id, after_cursor)

    async def _call_approval_resumer(
        self,
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None,
    ) -> ApprovalResumeResult:
        """调用注入的审批恢复回调，并兼容同步/异步 callable。"""

        assert self._approval_resumer is not None
        result = self._approval_resumer(snapshot, decisions, model)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _call_approval_resumer_with_run_context(
        self,
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None,
        *,
        owner_id: str,
    ) -> ApprovalResumeResult:
        """在审批恢复短租约上下文中调用恢复回调。"""

        token = set_run_execution_context(
            RunExecutionContext(
                run_id=snapshot.run_id,
                owner_id=owner_id,
                segment_index=_next_resume_segment_index(snapshot),
                recovery_mode=False,
                guardrail_summary=snapshot.guardrail_summary,
            )
        )
        try:
            return await self._call_approval_resumer(snapshot, decisions, model)
        finally:
            reset_run_execution_context(token)

    def _wake_worker(self) -> None:
        """在创建或继续入队后唤醒后台 worker。"""

        if self._worker_wakeup is not None:
            self._worker_wakeup()

    def _is_terminal_event(self, event_type: RunEventType) -> bool:
        """判断事件是否代表 Run 终态。"""

        return event_type in {
            RunEventType.RUN_CANCELLED,
            RunEventType.RUN_SUCCEEDED,
            RunEventType.RUN_FAILED,
            RunEventType.RUN_LOST,
        }


def _next_resume_segment_index(snapshot: RunSnapshot) -> int:
    """根据快照分段元数据推断审批恢复观察事件的段序号。"""

    metadata = snapshot.segment_metadata or {}
    raw_count = metadata.get("segment_count", 0)
    return raw_count + 1 if isinstance(raw_count, int) and raw_count >= 0 else 1


def _log_extra(
    snapshot: RunSnapshot,
    *,
    worker_id: str | None,
    **extra: Any,
) -> dict[str, Any]:
    """构造 Run 结构化日志字段，避免记录原始 payload/result。"""

    data = {
        "run_id": snapshot.run_id,
        "run_kind": snapshot.kind.value,
        "run_status": snapshot.status.value,
        "worker_id": worker_id,
        "client_request_id": snapshot.client_request_id,
    }
    data.update(extra)
    return data


def _workflow_selection_event_payload(
    selection: WorkflowSelection,
) -> dict[str, Any]:
    """构造不包含原始 payload 的 workflow 选择事件载荷。"""

    workflow = selection.workflow
    payload: dict[str, Any] = {
        "reason": _safe_event_text(selection.reason),
        "explicit": selection.explicit,
        "workflow_name": workflow.name if workflow is not None else None,
        "first_phase": None,
    }
    if workflow is not None and workflow.phases:
        first_phase = workflow.phases[0].phase
        payload["first_phase"] = first_phase.value
    return payload


def _normalized_workflow_name(value: str | None) -> str | None:
    """归一化显式 workflow 名称，用于幂等语义比较。"""

    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _safe_event_text(value: str, *, max_length: int = 200) -> str:
    """限制事件 reason 长度，避免把上游异常文本完整写入事件。"""

    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length]
