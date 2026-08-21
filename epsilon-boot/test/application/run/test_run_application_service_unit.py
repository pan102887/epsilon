"""Run 应用服务单元测试模块。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from application.run.run_application_service import (
    ApprovalResumer,
    ApprovalResumeResult,
    RunApplicationService,
)
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.guardrails import GuardrailPolicy
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
    WorkflowSelectorPort,
)
from domain.run.runtime_context import get_run_execution_context
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from domain.run.workflow import WorkflowPhase
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRunStore:
    """测试用内存 RunStorePort fake。"""

    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}
        self.client_index: dict[str, str] = {}
        self.next_run_number = 1
        self.worker_mark_calls: list[tuple[str, str, str]] = []
        self.approval_resume_results: list[ApprovalResumeStoreResult] = []
        self.approval_resume_resolve_owners: list[str] = []
        self.approval_resume_lease_owners: list[str] = []
        self.approval_resume_release_owners: list[str] = []

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """创建 queued 快照并维护幂等索引。"""

        if request.client_request_id in self.client_index:
            return self.snapshots[self.client_index[request.client_request_id]]
        run_id = f"run-{self.next_run_number}"
        self.next_run_number += 1
        snapshot = RunSnapshot(
            run_id=run_id,
            kind=request.payload.kind,
            status=RunStatus.QUEUED,
            payload=request.payload,
            client_request_id=request.client_request_id,
            payload_hash=request.effective_payload_hash(),
            result=None,
            error=None,
            approval_id=None,
            segment_metadata={"segment_count": 0},
            latest_event_cursor=None,
            can_continue=False,
            terminal_reason=None,
            lease=None,
            created_at=_NOW,
            updated_at=_NOW,
            version=1,
            task_classification=request.task_classification,
            guardrail_summary=request.guardrail_summary,
            workflow_name=request.workflow_name,
            workflow_run_state=request.workflow_run_state,
            collaboration_summary=request.collaboration_summary,
        )
        self.snapshots[run_id] = snapshot
        if request.client_request_id is not None:
            self.client_index[request.client_request_id] = run_id
        return snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        """按 run_id 查询快照。"""

        return self.snapshots.get(run_id)

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        """按幂等键查询快照。"""

        run_id = self.client_index.get(client_request_id)
        if run_id is None:
            return None
        return self.snapshots[run_id]

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        """统计指定状态数量。"""

        return sum(1 for snapshot in self.snapshots.values() if snapshot.status in statuses)

    async def acquire_approval_resume_lease(
        self, *, run_id: str, owner_id: str, lease_seconds: int
    ) -> RunSnapshot:
        """为测试 awaiting_approval 快照写入审批恢复短租约。"""

        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(
                run_id,
                f"当前状态为 {snapshot.status.value}，不是 awaiting_approval",
            )
        self.approval_resume_lease_owners.append(owner_id)
        updated = replace(
            snapshot,
            lease=RunLease(
                owner_id=owner_id,
                lease_until=_NOW + timedelta(seconds=lease_seconds),
                heartbeat_at=_NOW,
            ),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def release_approval_resume_lease(self, *, run_id: str, owner_id: str) -> RunSnapshot:
        """释放当前审批恢复短租约。"""

        self.approval_resume_release_owners.append(owner_id)
        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            return snapshot
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            return snapshot
        updated = replace(snapshot, lease=None, version=snapshot.version + 1)
        self.snapshots[run_id] = updated
        return updated

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        """按测试状态规则请求取消。"""

        snapshot = self.snapshots[run_id]
        if snapshot.status is RunStatus.QUEUED:
            target = RunStatus.CANCELLED
            terminal_reason = "cancelled"
        else:
            target = RunStatus.CANCEL_REQUESTED
            terminal_reason = None
        updated = replace(
            snapshot,
            status=target,
            can_continue=False,
            terminal_reason=terminal_reason,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def enqueue_continue(self, *, run_id: str, model: str | None = None) -> RunSnapshot:
        """把 Run 重新入队。"""

        snapshot = self.snapshots[run_id]
        payload = replace(snapshot.payload, model=model or snapshot.payload.model)
        updated = replace(
            snapshot,
            status=RunStatus.QUEUED,
            payload=payload,
            can_continue=False,
            approval_id=None,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def mark_succeeded(
        self, *, run_id: str, owner_id: str, result: dict[str, Any]
    ) -> RunSnapshot:
        """把 Run 标记为成功终态。"""

        self.worker_mark_calls.append(("mark_succeeded", run_id, owner_id))
        if owner_id == "approval_resume":
            raise AssertionError("approval resume must not call worker mark_succeeded")
        snapshot = self.snapshots[run_id]
        updated = replace(
            snapshot,
            status=RunStatus.SUCCEEDED,
            result=result,
            can_continue=False,
            terminal_reason="completed",
            lease=None,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def mark_failed(
        self, *, run_id: str, owner_id: str, error: dict[str, Any]
    ) -> RunSnapshot:
        """把 Run 标记为失败终态。"""

        self.worker_mark_calls.append(("mark_failed", run_id, owner_id))
        if owner_id == "approval_resume":
            raise AssertionError("approval resume must not call worker mark_failed")
        snapshot = self.snapshots[run_id]
        updated = replace(
            snapshot,
            status=RunStatus.FAILED,
            error=error,
            can_continue=False,
            terminal_reason="failed",
            lease=None,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def mark_cancelled(self, *, run_id: str, owner_id: str, reason: str) -> RunSnapshot:
        """把 Run 标记为取消终态。"""

        self.worker_mark_calls.append(("mark_cancelled", run_id, owner_id))
        if owner_id == "approval_resume":
            raise AssertionError("approval resume must not call worker mark_cancelled")
        snapshot = self.snapshots[run_id]
        updated = replace(
            snapshot,
            status=RunStatus.CANCELLED,
            result={"reason": reason},
            can_continue=False,
            terminal_reason=reason,
            lease=None,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def resolve_approval_resume(
        self, *, run_id: str, owner_id: str, result: ApprovalResumeStoreResult
    ) -> RunSnapshot:
        """模拟真实 store：校验审批恢复 owner 后迁移。"""

        self.approval_resume_resolve_owners.append(owner_id)
        self.approval_resume_results.append(result)
        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(
                run_id,
                f"当前状态为 {snapshot.status.value}，不是 awaiting_approval",
            )
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            raise AssertionError("approval resume owner mismatch")

        common_fields = {
            "guardrail_summary": (
                snapshot.guardrail_summary
                if result.guardrail_summary is None
                else result.guardrail_summary
            ),
            "workflow_run_state": (
                snapshot.workflow_run_state
                if result.workflow_run_state is None
                else result.workflow_run_state
            ),
            "collaboration_summary": (
                snapshot.collaboration_summary
                if result.collaboration_summary is None
                else result.collaboration_summary
            ),
            "version": snapshot.version + 1,
        }
        if result.status == "queued":
            updated = replace(
                snapshot,
                status=RunStatus.QUEUED,
                result=result.result,
                error=None,
                can_continue=False,
                approval_id=None,
                terminal_reason=None,
                lease=None,
                **common_fields,
            )
        elif result.status == "awaiting_approval":
            updated = replace(
                snapshot,
                status=RunStatus.AWAITING_APPROVAL,
                result=result.result,
                error=None,
                can_continue=True,
                approval_id=result.approval_id,
                terminal_reason=None,
                lease=None,
                **common_fields,
            )
        elif result.status == "succeeded":
            updated = replace(
                snapshot,
                status=RunStatus.SUCCEEDED,
                result=result.result or {"terminal_reason": result.terminal_reason or "completed"},
                error=None,
                can_continue=False,
                approval_id=None,
                terminal_reason=result.terminal_reason or "completed",
                lease=None,
                **common_fields,
            )
        elif result.status == "failed":
            updated = replace(
                snapshot,
                status=RunStatus.FAILED,
                result=None,
                error=result.error or {"message": "审批恢复失败"},
                can_continue=False,
                approval_id=None,
                terminal_reason=result.terminal_reason or "failed",
                lease=None,
                **common_fields,
            )
        else:
            updated = replace(
                snapshot,
                status=RunStatus.CANCELLED,
                result=result.result or {"reason": result.terminal_reason or "cancelled"},
                error=None,
                can_continue=False,
                approval_id=None,
                terminal_reason=result.terminal_reason or "cancelled",
                lease=None,
                **common_fields,
            )
        self.snapshots[run_id] = updated
        return updated


class _MemoryEventStore:
    """测试用内存 RunEventStorePort fake。"""

    def __init__(self) -> None:
        self.events: dict[str, list[RunEvent]] = {}

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        """追加事件并分配 run 内单调 cursor。"""

        run_events = self.events.setdefault(run_id, [])
        event = RunEvent(
            run_id=run_id,
            cursor=(run_events[-1].cursor + 1) if run_events else 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        run_events.append(event)
        return event

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        """列出 cursor 之后的事件。"""

        events = self.events.get(run_id, [])
        filtered = [
            event for event in events if after_cursor is None or event.cursor > after_cursor
        ]
        return filtered[:limit]

    async def wait_events(
        self, run_id: str, after_cursor: int | None, timeout_seconds: float
    ) -> list[RunEvent]:
        """测试 fake 不阻塞等待，直接返回当前可用事件。"""

        return await self.list_events(run_id, after_cursor, 100)

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        """按 max_event_count 裁剪事件。"""

        events = self.events.get(run_id, [])
        if len(events) > policy.max_event_count:
            self.events[run_id] = events[-policy.max_event_count :]

    async def first_cursor(self, run_id: str) -> int | None:
        """返回当前保留窗口首个 cursor。"""

        events = self.events.get(run_id, [])
        if not events:
            return None
        return events[0].cursor


def _payload(message: str = "hi") -> RunPayload:
    """构造聊天 Run payload。"""

    return RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": message},
        model="model-a",
    )


def _request(message: str = "hi", client_request_id: str | None = "client-1") -> RunCreateRequest:
    """构造创建请求。"""

    return RunCreateRequest(
        payload=_payload(message),
        client_request_id=client_request_id,
    )


def _service(
    store: _MemoryRunStore | None = None,
    event_store: _MemoryEventStore | None = None,
    *,
    max_queued_runs: int = 10,
    max_running_runs: int = 10,
    max_event_count: int = 100,
    approval_resumer: ApprovalResumer | None = None,
    wakeups: list[str] | None = None,
    workflow_selector: WorkflowSelectorPort | None = None,
) -> RunApplicationService:
    """构造带内存 fake 的 RunApplicationService。"""

    return RunApplicationService(
        run_store=cast(RunStorePort, store or _MemoryRunStore()),
        event_store=cast(RunEventStorePort, event_store or _MemoryEventStore()),
        capacity_policy=RunCapacityPolicy(
            max_queued_runs=max_queued_runs,
            max_running_runs=max_running_runs,
        ),
        event_retention_policy=EventRetentionPolicy(
            max_event_count=max_event_count,
            ttl_seconds=3600,
        ),
        workflow_serializer=WorkflowSerializerAdapter(),
        worker_wakeup=(lambda: wakeups.append("wake")) if wakeups is not None else None,
        approval_resumer=approval_resumer,
        event_stream_wait_seconds=0,
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy()),
        workflow_selector=workflow_selector,
    )


async def test_create_run_success_appends_created_and_queued_events() -> None:
    """create 成功返回 queued 快照并写 run_created/run_queued 事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    wakeups: list[str] = []
    service = _service(store, events, wakeups=wakeups)

    snapshot = await service.create_run(_request())

    assert snapshot.run_id == "run-1"
    assert snapshot.status is RunStatus.QUEUED
    assert snapshot.latest_event_cursor == 3
    assert [event.event_type for event in events.events["run-1"]] == [
        RunEventType.TASK_CLASSIFIED,
        RunEventType.RUN_CREATED,
        RunEventType.RUN_QUEUED,
    ]
    assert wakeups == ["wake"]


async def test_create_run_persists_task_classification_and_event() -> None:
    """创建 Run 时应持久化确定性任务分类并写 task_classified 事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events)

    snapshot = await service.create_run(_request())
    stored = await store.get_run(snapshot.run_id)

    assert snapshot.task_classification == "tool_task"
    assert stored is not None
    assert stored.task_classification == "tool_task"
    assert [event.event_type for event in events.events["run-1"]] == [
        RunEventType.TASK_CLASSIFIED,
        RunEventType.RUN_CREATED,
        RunEventType.RUN_QUEUED,
    ]


async def test_create_run_same_idempotency_key_returns_existing_run() -> None:
    """相同 client_request_id 和 payload_hash 返回既有 run。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events)
    first = await service.create_run(_request())

    second = await service.create_run(_request())

    assert second.run_id == first.run_id
    assert len(store.snapshots) == 1
    assert len(events.events[first.run_id]) == 3


async def test_create_run_same_idempotency_key_with_different_payload_raises_conflict() -> None:
    """相同幂等键但 payload hash 不同抛 RunIdempotencyConflictError。"""

    store = _MemoryRunStore()
    service = _service(store, _MemoryEventStore())
    await service.create_run(_request("first"))

    with pytest.raises(RunIdempotencyConflictError) as caught:
        await service.create_run(_request("second"))

    assert caught.value.code == 61010


async def test_create_run_queue_full_raises_queue_full() -> None:
    """新建 Run 超过 max_queued_runs 时抛容量错误。"""

    store = _MemoryRunStore()
    service = _service(store, _MemoryEventStore(), max_queued_runs=1)
    await service.create_run(_request(client_request_id="client-1"))

    with pytest.raises(RunQueueFullError) as caught:
        await service.create_run(_request(client_request_id="client-2"))

    assert caught.value.limit_name == "max_queued_runs"


async def test_get_run_missing_raises_not_found() -> None:
    """查询不存在 run_id 抛 RunNotFoundError。"""

    service = _service(_MemoryRunStore(), _MemoryEventStore())

    with pytest.raises(RunNotFoundError):
        await service.get_run("missing")


async def test_get_run_exposes_checkpoint_recovery_fields() -> None:
    """Run 查询应只读透传阶段四 checkpoint/recovery 观察字段。"""

    store = _MemoryRunStore()
    service = _service(store, _MemoryEventStore())
    snapshot = await store.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        latest_checkpoint_id="chk_000042",
        recoverable=True,
        recovery_attempt_count=2,
        last_recovery_error={"reason": "pending_tool_replay_blocked"},
    )

    loaded = await service.get_run(snapshot.run_id)

    assert loaded.latest_checkpoint_id == "chk_000042"
    assert loaded.recoverable is True
    assert loaded.recovery_attempt_count == 2
    assert loaded.last_recovery_error == {"reason": "pending_tool_replay_blocked"}


async def test_cancel_queued_run_directly_cancelled() -> None:
    """queued cancel 直接进入 cancelled 并写终态取消事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events)
    snapshot = await service.create_run(_request())

    cancelled = await service.request_cancel(snapshot.run_id)

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.terminal_reason == "cancelled"
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_CANCELLED


async def test_cancel_running_paused_and_awaiting_approval_enter_cancel_requested() -> None:
    """running/paused/awaiting_approval cancel 进入 cancel_requested。"""

    for status in (
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.AWAITING_APPROVAL,
    ):
        store = _MemoryRunStore()
        events = _MemoryEventStore()
        service = _service(store, events)
        snapshot = await service.create_run(_request(client_request_id=status.value))
        store.snapshots[snapshot.run_id] = replace(snapshot, status=status)

        cancelled = await service.request_cancel(snapshot.run_id)

        assert cancelled.status is RunStatus.CANCEL_REQUESTED
        assert events.events[snapshot.run_id][-1].event_type is RunEventType.CANCEL_REQUESTED


async def test_cancel_requested_repeat_is_idempotent_but_terminal_cancel_conflicts() -> None:
    """cancel_requested 可重复取消，终态取消返回冲突。"""

    store = _MemoryRunStore()
    service = _service(store, _MemoryEventStore())
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.CANCEL_REQUESTED,
    )

    repeated = await service.request_cancel(snapshot.run_id)

    assert repeated.status is RunStatus.CANCEL_REQUESTED

    store.snapshots[snapshot.run_id] = replace(snapshot, status=RunStatus.SUCCEEDED)
    with pytest.raises(RunCancelUnavailableError):
        await service.request_cancel(snapshot.run_id)


async def test_continue_paused_can_continue_enqueues_same_run() -> None:
    """paused 且 can_continue=true 可继续并重新入队。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    wakeups: list[str] = []
    service = _service(store, events, wakeups=wakeups)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.PAUSED,
        can_continue=True,
    )

    queued = await service.continue_run(snapshot.run_id, model="model-b")

    assert queued.run_id == snapshot.run_id
    assert queued.status is RunStatus.QUEUED
    assert queued.payload.model == "model-b"
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_QUEUED
    assert wakeups == ["wake", "wake"]


async def test_continue_non_paused_or_without_can_continue_conflicts() -> None:
    """非 paused 或 can_continue=false 的继续请求抛冲突。"""

    store = _MemoryRunStore()
    service = _service(store, _MemoryEventStore())
    snapshot = await service.create_run(_request())

    with pytest.raises(RunContinuationUnavailableError):
        await service.continue_run(snapshot.run_id)

    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.PAUSED,
        can_continue=False,
    )
    with pytest.raises(RunContinuationUnavailableError):
        await service.continue_run(snapshot.run_id)


async def test_resume_approval_run_uses_injected_resumer_and_enqueues() -> None:
    """awaiting_approval 审批恢复通过注入 callable 后重新入队同一 Run。"""

    calls: list[tuple[str, str | None, int]] = []
    observed_contexts: list[tuple[str, str, int]] = []

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        calls.append((snapshot.run_id, model, len(decisions)))
        run_context = get_run_execution_context()
        assert run_context is not None
        observed_contexts.append(
            (run_context.run_id, run_context.owner_id, run_context.segment_index)
        )
        return ApprovalResumeResult(status="queued", result={"accepted": True})

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
    )
    decision = ApprovalDecision(type="approve", tool_call_id="call-1")

    queued = await service.resume_approval_run(
        snapshot.run_id,
        [decision],
        model="model-c",
    )

    assert queued.status is RunStatus.QUEUED
    assert calls == [(snapshot.run_id, "model-c", 1)]
    assert len(store.approval_resume_lease_owners) == 1
    assert store.approval_resume_lease_owners[0].startswith("approval-resume-")
    assert observed_contexts == [(snapshot.run_id, store.approval_resume_lease_owners[0], 1)]
    assert get_run_execution_context() is None
    assert store.approval_resume_resolve_owners == [store.approval_resume_lease_owners[0]]
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(status="queued", result={"accepted": True})
    ]
    assert store.worker_mark_calls == []
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_QUEUED


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ApprovalDecisionCountMismatchError(1, 0), id="count-mismatch"),
        pytest.param(ApprovalDecisionOrderMismatchError("call-1", "call-2"), id="order-mismatch"),
        pytest.param(
            ApprovalDecisionNotAllowedError("shell_exec", "respond", frozenset({"approve"})),
            id="not-allowed",
        ),
        pytest.param(ApprovalExpiredError("session-1", "approval-1"), id="expired"),
        pytest.param(ApprovalConsumedError("session-1", "approval-1"), id="consumed"),
        pytest.param(ApprovalNotFoundError("session-1", "approval-1"), id="not-found"),
    ],
)
async def test_resume_approval_run_releases_short_lease_after_approval_error_and_allows_retry(
    error: Exception,
) -> None:
    """审批恢复异常应释放短租约并允许同一 Run 立即重试。"""

    calls = 0

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return ApprovalResumeResult(status="queued", result={"accepted": True})

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    guardrail_summary = {"action": "require_approval", "evaluation_count": 1}
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
        guardrail_summary=guardrail_summary,
    )

    with pytest.raises(type(error)):
        await service.resume_approval_run(
            snapshot.run_id,
            [ApprovalDecision(type="approve", tool_call_id="call-1")],
        )

    after_error = await service.get_run(snapshot.run_id)
    assert after_error.status is RunStatus.AWAITING_APPROVAL
    assert after_error.approval_id == "approval-1"
    assert after_error.lease is None
    assert after_error.guardrail_summary == guardrail_summary
    assert store.approval_resume_release_owners == [store.approval_resume_lease_owners[0]]

    retried = await service.resume_approval_run(
        snapshot.run_id,
        [ApprovalDecision(type="approve", tool_call_id="call-1")],
    )

    assert retried.status is RunStatus.QUEUED
    assert retried.guardrail_summary == guardrail_summary
    assert calls == 2
    assert len(store.approval_resume_lease_owners) == 2
    assert store.approval_resume_resolve_owners == [store.approval_resume_lease_owners[1]]
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_QUEUED


async def test_resume_approval_run_accept_keeps_payload_without_duplicate_chat_input(
) -> None:
    """审批通过恢复时应复用同一 Run，
    且不得把原始用户输入复制进恢复结果。
    """

    received_snapshots: list[RunSnapshot] = []
    accepted_result = {"status": "completed", "reply": "done", "accepted": True}

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        assert decisions == [ApprovalDecision(type="approve", tool_call_id="call-1")]
        assert model == "model-c"
        received_snapshots.append(snapshot)
        return ApprovalResumeResult(status="queued", result=accepted_result)

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request(message="approve me"))
    original_payload = snapshot.payload
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
        result={"status": "approval_required", "message": "pending"},
    )

    queued = await service.resume_approval_run(
        snapshot.run_id,
        [ApprovalDecision(type="approve", tool_call_id="call-1")],
        model="model-c",
    )

    assert len(received_snapshots) == 1
    assert received_snapshots[0].run_id == snapshot.run_id
    assert received_snapshots[0].payload == original_payload
    assert received_snapshots[0].payload.chat == {"message": "approve me"}
    assert queued.run_id == snapshot.run_id
    assert queued.status is RunStatus.QUEUED
    assert queued.payload == original_payload
    assert queued.payload.chat == {"message": "approve me"}
    assert queued.result == accepted_result
    assert queued.result == {"status": "completed", "reply": "done", "accepted": True}
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(status="queued", result=accepted_result)
    ]
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_QUEUED


async def test_resume_approval_run_can_return_terminal_succeeded_snapshot() -> None:
    """审批恢复回调可让同一 Run 通过专用 store 方法进入成功终态。"""

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        return ApprovalResumeResult(
            status="succeeded",
            result={"message": "done"},
            terminal_reason="completed",
        )

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
    )

    succeeded = await service.resume_approval_run(snapshot.run_id, [])

    assert succeeded.status is RunStatus.SUCCEEDED
    assert succeeded.result == {"message": "done"}
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(
            status="succeeded",
            result={"message": "done"},
            terminal_reason="completed",
        )
    ]
    assert store.worker_mark_calls == []
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_SUCCEEDED


async def test_resume_approval_run_can_return_terminal_failed_snapshot() -> None:
    """审批恢复回调可让同一 Run 通过专用 store 方法进入失败终态。"""

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        return ApprovalResumeResult(
            status="failed",
            error={"message": "审批恢复失败"},
            terminal_reason="failed",
        )

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
    )

    failed = await service.resume_approval_run(snapshot.run_id, [])

    assert failed.status is RunStatus.FAILED
    assert failed.error == {"message": "审批恢复失败"}
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(
            status="failed",
            error={"message": "审批恢复失败"},
            terminal_reason="failed",
        )
    ]
    assert store.worker_mark_calls == []
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_FAILED


async def test_resume_approval_run_can_return_terminal_cancelled_snapshot() -> None:
    """审批恢复回调可让同一 Run 通过专用 store 方法进入取消终态。"""

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        return ApprovalResumeResult(
            status="cancelled",
            result={"reason": "rejected"},
            terminal_reason="rejected",
        )

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
    )

    cancelled = await service.resume_approval_run(snapshot.run_id, [])

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.result == {"reason": "rejected"}
    assert cancelled.terminal_reason == "rejected"
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(
            status="cancelled",
            result={"reason": "rejected"},
            terminal_reason="rejected",
        )
    ]
    assert store.worker_mark_calls == []
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_CANCELLED


async def test_resume_approval_run_reenters_awaiting_approval_with_new_approval_id() -> None:
    """审批恢复后再次命中审批时应保留同一 Run 并写入新 approval_id。"""

    workflow_state = {"current_phase": WorkflowPhase.EXECUTE.value}
    guardrail_summary = {"action": "require_approval", "evaluation_count": 2}
    collaboration_summary = {"latest_steps": [{"id": "step-1"}]}

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        return ApprovalResumeResult(
            status="awaiting_approval",
            approval_id="approval-2",
            result={"status": "approval_required", "message": "again"},
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_state,
            collaboration_summary=collaboration_summary,
        )

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
    )

    updated = await service.resume_approval_run(snapshot.run_id, [])

    assert updated.status is RunStatus.AWAITING_APPROVAL
    assert updated.approval_id == "approval-2"
    assert updated.result == {"status": "approval_required", "message": "again"}
    assert updated.guardrail_summary == guardrail_summary
    assert updated.workflow_run_state == workflow_state
    assert updated.collaboration_summary == collaboration_summary
    assert updated.payload.chat == {"message": "hi"}
    assert updated.payload.task is None
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(
            status="awaiting_approval",
            approval_id="approval-2",
            result={"status": "approval_required", "message": "again"},
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_state,
            collaboration_summary=collaboration_summary,
        )
    ]
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.APPROVAL_REQUIRED
    assert events.events[snapshot.run_id][-1].payload["approval_id"] == "approval-2"


async def test_resume_approval_run_preserves_snapshot_summaries_when_resumer_returns_none() -> None:
    """审批恢复结果缺少新摘要时，store 应保留当前已持久化摘要字段。"""

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        return ApprovalResumeResult(
            status="queued",
            result={"accepted": True},
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request())
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
        guardrail_summary={"action": "require_approval", "evaluation_count": 9},
        workflow_run_state={"current_phase": WorkflowPhase.EXECUTE.value},
        collaboration_summary={"latest_steps": [{"id": "persisted-step"}]},
    )

    updated = await service.resume_approval_run(snapshot.run_id, [])

    assert updated.status is RunStatus.QUEUED
    assert updated.guardrail_summary == {"action": "require_approval", "evaluation_count": 9}
    assert updated.workflow_run_state == {"current_phase": WorkflowPhase.EXECUTE.value}
    assert updated.collaboration_summary == {"latest_steps": [{"id": "persisted-step"}]}
    assert store.approval_resume_results == [
        ApprovalResumeStoreResult(
            status="queued",
            result={"accepted": True},
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    ]


async def test_resume_approval_run_rejected_keeps_same_payload_and_writes_cancelled_event() -> None:
    """审批拒绝进入取消终态时不应重复原始输入，只保留同一 Run payload。"""

    async def resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        return ApprovalResumeResult(
            status="cancelled",
            result={"reason": "rejected"},
            terminal_reason="rejected",
        )

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, approval_resumer=resumer)
    snapshot = await service.create_run(_request(message="approve me"))
    original_payload = snapshot.payload
    store.snapshots[snapshot.run_id] = replace(
        snapshot,
        status=RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
        result={"status": "approval_required", "message": "pending"},
    )

    updated = await service.resume_approval_run(
        snapshot.run_id, [ApprovalDecision(type="reject", tool_call_id="call-1")]
    )

    assert updated.status is RunStatus.CANCELLED
    assert updated.result == {"reason": "rejected"}
    assert updated.payload == original_payload
    assert updated.payload.chat == {"message": "approve me"}
    assert events.events[snapshot.run_id][-1].event_type is RunEventType.RUN_CANCELLED


async def test_resume_approval_requires_awaiting_approval_state() -> None:
    """非 awaiting_approval 的审批恢复请求抛继续不可用。"""

    store = _MemoryRunStore()
    service = _service(store, _MemoryEventStore())
    snapshot = await service.create_run(_request())

    with pytest.raises(RunContinuationUnavailableError):
        await service.resume_approval_run(snapshot.run_id, [])


async def test_list_events_detects_replay_expired() -> None:
    """after_cursor 早于保留窗口时抛 RunEventReplayExpiredError。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, max_event_count=2)
    snapshot = await service.create_run(_request())
    await events.append_event(snapshot.run_id, RunEventType.SEGMENT_STARTED, {})
    await events.trim_events(
        snapshot.run_id,
        EventRetentionPolicy(max_event_count=2, ttl_seconds=3600),
    )

    with pytest.raises(RunEventReplayExpiredError):
        await service.list_events(snapshot.run_id, after_cursor=0, limit=10)


async def test_list_events_returns_events_after_cursor_when_replay_available() -> None:
    """after_cursor 未过期时返回后续事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events)
    snapshot = await service.create_run(_request())

    listed = await service.list_events(snapshot.run_id, after_cursor=1, limit=10)

    assert [event.cursor for event in listed] == [2, 3]


async def test_list_events_exposes_checkpoint_recovery_event_types_read_only() -> None:
    """事件查询应透传阶段四事件，且不触发恢复编排。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events)
    snapshot = await store.create_run(_request())
    for event_type in (
        RunEventType.CHECKPOINT_SAVED,
        RunEventType.RUN_RECOVERY_QUEUED,
        RunEventType.RUN_RECOVERY_FAILED,
        RunEventType.TOOL_RESULT_REPLAYED,
    ):
        await events.append_event(snapshot.run_id, event_type, {"source": "test"})

    listed = await service.list_events(snapshot.run_id, after_cursor=None, limit=10)

    assert [event.event_type for event in listed] == [
        RunEventType.CHECKPOINT_SAVED,
        RunEventType.RUN_RECOVERY_QUEUED,
        RunEventType.RUN_RECOVERY_FAILED,
        RunEventType.TOOL_RESULT_REPLAYED,
    ]


async def test_stream_events_yields_until_terminal_event() -> None:
    """stream_events 发送终态事件后关闭。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events)
    snapshot = await service.create_run(_request())
    await events.append_event(snapshot.run_id, RunEventType.RUN_SUCCEEDED, {})

    streamed = [event async for event in service.stream_events(snapshot.run_id, after_cursor=3)]

    assert [event.event_type for event in streamed] == [RunEventType.RUN_SUCCEEDED]
