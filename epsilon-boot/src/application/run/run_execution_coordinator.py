"""Run 执行协调器。

本模块把后台 Run 快照转换为既有 Chat/Task 领域端口调用，并把返回值
归一为 worker 可持久化的 JSON-safe 执行结果。协调器属于应用层，不依赖
FastAPI、TUI 或基础设施实现。
"""

from __future__ import annotations

import inspect
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Any

from application.run.run_checkpoint_sink import RunCheckpointSink
from application.run.serialization_ports import SegmentSerializerPort
from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from domain.chat.ports import ChatServicePort
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO, ChatResponseVO
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import (
    RunCheckpointStorePort,
    RunEventStorePort,
    RunProgressSink,
    WorkflowRegistryPort,
)
from domain.run.runtime_context import (
    RunExecutionContext,
    get_run_execution_context,
    reset_run_execution_context,
    set_run_execution_context,
)
from domain.run.value_objects import (
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RunKind,
    RunSnapshot,
    RunStatus,
)
from domain.run.workflow import WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from domain.task.enums import TaskOutcomeKind
from domain.task.policy import TaskStatusMapping
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import Task, TaskContinueRequest, TaskResult


class RunExecutionCoordinator:
    """协调后台 Run 执行到既有 Chat/Task 端口。"""

    def __init__(
        self,
        *,
        chat_service: ChatServicePort,
        task_agent: TaskAgentPort,
        segment_serializer: SegmentSerializerPort,
        checkpoint_store: RunCheckpointStorePort | None = None,
        event_store: RunEventStorePort | None = None,
        retention_policy: CheckpointRetentionPolicy | None = None,
        checkpoint_enabled: bool = False,
        workflow_orchestrator: WorkflowRunOrchestrator | None = None,
        workflow_registry: WorkflowRegistryPort | None = None,
    ) -> None:
        """注入 Chat 与 Task 执行端口及分段元数据序列化端口。"""

        self._chat_service = chat_service
        self._task_agent = task_agent
        self._segment_serializer = segment_serializer
        self._checkpoint_store = checkpoint_store
        self._event_store = event_store
        self._retention_policy = retention_policy
        self._checkpoint_enabled = checkpoint_enabled
        self._workflow_orchestrator = workflow_orchestrator
        self._workflow_registry = workflow_registry

    async def execute(
        self,
        snapshot: RunSnapshot,
        progress: RunProgressSink,
    ) -> RunExecutionOutcome:
        """执行一个 Run 快照并返回可持久化 outcome。

        首次执行使用 create payload；暂停或审批恢复后的同一 Run 只调用
        continue 端口，避免重复追加原始用户消息或任务目标。
        """

        recovery_checkpoint = await self._load_recovery_checkpoint(snapshot)
        segment_index = _next_segment_index(snapshot, recovery_checkpoint)
        await progress.segment_started(snapshot.run_id, segment_index)

        recovery_mode = recovery_checkpoint is not None
        run_execution_token = set_run_execution_context(
            RunExecutionContext(
                run_id=snapshot.run_id,
                owner_id=snapshot.lease.owner_id if snapshot.lease is not None else "",
                segment_index=segment_index,
                recovery_mode=recovery_mode,
                guardrail_summary=snapshot.guardrail_summary,
                workflow_run_state=snapshot.workflow_run_state,
                collaboration_summary=snapshot.collaboration_summary,
            )
        )

        checkpoint_token = None
        if (
            self._checkpoint_enabled
            and self._checkpoint_store is not None
            and self._event_store is not None
            and self._retention_policy is not None
        ):
            sink = RunCheckpointSink(
                checkpoint_store=self._checkpoint_store,
                event_store=self._event_store,
                retention_policy=self._retention_policy,
            )
            checkpoint_token = set_run_checkpoint_context(
                RunCheckpointExecutionContext(
                    run_id=snapshot.run_id,
                    owner_id=snapshot.lease.owner_id if snapshot.lease is not None else "",
                    segment_index=segment_index,
                    recovery_mode=recovery_mode,
                    sink=sink,
                    checkpoint_id=(
                        recovery_checkpoint.checkpoint_id
                        if recovery_checkpoint is not None
                        else None
                    ),
                    context_snapshot=(
                        recovery_checkpoint.context_snapshot
                        if recovery_checkpoint is not None
                        else None
                    ),
                    round_num=(
                        recovery_checkpoint.round_num if recovery_checkpoint is not None else None
                    ),
                    usage=(recovery_checkpoint.usage if recovery_checkpoint is not None else None),
                    segment_metadata=(
                        recovery_checkpoint.segment_metadata
                        if recovery_checkpoint is not None
                        else None
                    ),
                )
            )

        workflow_context_token = _set_workflow_context(
            snapshot,
            self._workflow_registry,
        )

        async def execute_existing(current_snapshot: RunSnapshot) -> RunExecutionOutcome:
            if current_snapshot.kind is RunKind.CHAT:
                return await self._execute_chat(current_snapshot, recovery_checkpoint)
            if current_snapshot.kind is RunKind.TASK:
                return await self._execute_task(current_snapshot, recovery_checkpoint)
            return _failed_outcome(ValueError(f"不支持的 RunKind: {current_snapshot.kind!r}"))

        try:
            if self._workflow_orchestrator is not None:
                outcome = await self._workflow_orchestrator.execute_phase(
                    snapshot=snapshot,
                    execute_existing=execute_existing,
                )
            else:
                outcome = await execute_existing(snapshot)
        except Exception as exc:
            outcome = _failed_outcome(exc)
        else:
            outcome = _with_runtime_workflow_state(outcome)
        finally:
            if workflow_context_token is not None:
                reset_workflow_collaboration_context(workflow_context_token)
            if checkpoint_token is not None:
                reset_run_checkpoint_context(checkpoint_token)
            reset_run_execution_context(run_execution_token)

        await progress.segment_done(snapshot.run_id, outcome.segment_metadata or {})
        return outcome

    async def _load_recovery_checkpoint(
        self,
        snapshot: RunSnapshot,
    ) -> DurableCheckpoint | None:
        """恢复模式下读取最新 checkpoint；普通执行不触碰 checkpoint store。"""

        if (
            not self._checkpoint_enabled
            or self._checkpoint_store is None
            or snapshot.latest_checkpoint_id is None
        ):
            return None
        checkpoint = await self._checkpoint_store.latest_checkpoint(snapshot.run_id)
        if checkpoint is None:
            raise ValueError(
                f"Run {snapshot.run_id} 缺少恢复检查点 {snapshot.latest_checkpoint_id}"
            )
        return checkpoint

    async def _execute_chat(
        self,
        snapshot: RunSnapshot,
        recovery_checkpoint: DurableCheckpoint | None = None,
    ) -> RunExecutionOutcome:
        """执行聊天 Run。"""

        payload = snapshot.payload
        if _should_continue(snapshot):
            if recovery_checkpoint is not None:
                await _restore_checkpoint_context(
                    self._chat_service,
                    _session_id(snapshot),
                    recovery_checkpoint,
                )
            response = await self._chat_service.continue_chat(
                ChatContinueRequestVO(
                    session_id=_session_id(snapshot),
                    stream=False,
                    model=payload.model,
                )
            )
        else:
            chat_payload = payload.chat or {}
            response = await self._chat_service.chat(
                ChatRequestVO(
                    session_id=_session_id(snapshot, chat_payload),
                    message=_required_str(chat_payload, "message"),
                    stream=bool(chat_payload.get("stream", False)),
                    model=chat_payload.get("model") or payload.model,
                )
            )
        return self._chat_outcome(response)

    async def _execute_task(
        self,
        snapshot: RunSnapshot,
        recovery_checkpoint: DurableCheckpoint | None = None,
    ) -> RunExecutionOutcome:
        """执行任务 Run。"""

        payload = snapshot.payload
        if _should_continue(snapshot):
            if recovery_checkpoint is not None:
                await _restore_checkpoint_context(
                    self._task_agent,
                    _session_id(snapshot),
                    recovery_checkpoint,
                )
            response = await self._task_agent.continue_task(
                TaskContinueRequest(
                    session_id=_session_id(snapshot),
                    model=payload.model,
                )
            )
        else:
            task_payload = payload.task or {}
            response = await self._task_agent.execute(
                Task(
                    goal=_required_str(task_payload, "goal"),
                    input_data=_dict_or_empty(task_payload.get("input_data")),
                    constraints=_list_or_empty(task_payload.get("constraints")),
                    output_format=task_payload.get("output_format"),
                    model=task_payload.get("model") or payload.model,
                    session_id=task_payload.get("session_id") or payload.session_id,
                    tool_names=_tool_names(task_payload.get("tool_names")),
                    delegation_depth=int(task_payload.get("delegation_depth", 0)),
                )
            )
        return self._task_outcome(response)

    def _chat_outcome(self, response: ChatResponseVO) -> RunExecutionOutcome:
        """把聊天响应转换为 Run outcome。"""

        if response.status == "approval_required":
            status = RunStatus.AWAITING_APPROVAL
        elif response.status == "paused":
            status = RunStatus.PAUSED
        else:
            status = RunStatus.SUCCEEDED

        return RunExecutionOutcome(
            status=status,
            result={
                "kind": "chat",
                "session_id": response.session_id,
                "reply": response.reply,
                "model": response.model,
                "prompt_id": response.prompt_id,
                "usage": _json_safe(response.usage),
                "status": response.status,
                "terminated_reason": response.terminated_reason,
                "action_requests": _json_safe(response.action_requests),
                "trace_id": response.session_id,
                "trace_ref": {
                    "available": True,
                    "trace_id": response.session_id,
                    "url": f"/api/traces/{response.session_id}",
                },
                "artifact_ref": {
                    "available": True,
                    "session_id": response.session_id,
                    "url": f"/api/artifacts/{response.session_id}",
                },
            },
            terminal_reason=str(response.terminated_reason),
            can_continue=bool(response.can_continue),
            approval_id=response.approval_id,
            segment_metadata=self._segment_metadata(response.segment_metadata),
        )

    def _task_outcome(self, response: TaskResult) -> RunExecutionOutcome:
        """把任务响应转换为 Run outcome。"""

        kind = TaskStatusMapping.outcome_of(response.status)
        if kind is TaskOutcomeKind.SUCCEEDED:
            status = RunStatus.SUCCEEDED
        elif kind is TaskOutcomeKind.PAUSED:
            status = RunStatus.PAUSED
        elif kind is TaskOutcomeKind.AWAITING_APPROVAL:
            status = RunStatus.AWAITING_APPROVAL
        else:
            status = RunStatus.FAILED

        result = {
            "kind": "task",
            "content": response.content,
            "task_status": response.status.value,
            "model": response.model,
            "prompt_id": response.prompt_id,
            "usage": _json_safe(response.usage),
            "trace": _json_safe(response.trace),
            "latency_ms": response.latency_ms,
            "terminated_reason": response.terminated_reason,
            "trace_id": _session_id(snapshot),
            "trace_ref": {
                "available": True,
                "trace_id": _session_id(snapshot),
                "url": f"/api/traces/{_session_id(snapshot)}",
            },
            "artifact_ref": {
                "available": True,
                "session_id": _session_id(snapshot),
                "url": f"/api/artifacts/{_session_id(snapshot)}",
            },
        }
        error = None
        if status is RunStatus.FAILED:
            error = {"message": response.content, "task_status": response.status.value}

        approval_id = response.approval_id or _extract_approval_id(response.content)

        return RunExecutionOutcome(
            status=status,
            result=result,
            error=error,
            terminal_reason=(
                "failed" if status is RunStatus.FAILED else str(response.terminated_reason)
            ),
            can_continue=bool(response.can_continue),
            approval_id=approval_id,
            segment_metadata=self._segment_metadata(response.segment_metadata),
        )

    def _segment_metadata(self, metadata: Any) -> dict[str, Any]:
        """把 SegmentRunMetadata 或 dict 转换为 JSON-safe dict。"""

        from domain.agent.segmented_execution import SegmentRunMetadata

        if isinstance(metadata, SegmentRunMetadata):
            return _json_safe(
                self._segment_serializer.segment_run_metadata_to_http_dict(metadata)
            )
        safe = _json_safe(metadata)
        return safe if isinstance(safe, dict) else {}


def _with_runtime_workflow_state(outcome: RunExecutionOutcome) -> RunExecutionOutcome:
    """把工具执行期写入 RunExecutionContext 的 workflow 状态合并到 outcome。"""

    run_context = get_run_execution_context()
    if run_context is None:
        return outcome
    runtime_workflow_state = run_context.workflow_run_state
    workflow_run_state = outcome.workflow_run_state or runtime_workflow_state
    if (
        isinstance(outcome.workflow_run_state, dict)
        and isinstance(runtime_workflow_state, dict)
        and isinstance(runtime_workflow_state.get("handoff_state"), dict)
    ):
        workflow_run_state = {
            **outcome.workflow_run_state,
            "active_role": runtime_workflow_state.get(
                "active_role",
                outcome.workflow_run_state.get("active_role"),
            ),
            "handoff_state": runtime_workflow_state["handoff_state"],
        }
    collaboration_summary = run_context.collaboration_summary or outcome.collaboration_summary
    if (
        workflow_run_state is outcome.workflow_run_state
        and collaboration_summary is outcome.collaboration_summary
    ):
        return outcome
    return replace(
        outcome,
        workflow_run_state=workflow_run_state,
        collaboration_summary=collaboration_summary,
    )


def _set_workflow_context(
    snapshot: RunSnapshot,
    workflow_registry: WorkflowRegistryPort | None,
) -> Any | None:
    """根据当前 workflow state 设置协作上下文。"""

    if workflow_registry is None or snapshot.workflow_run_state is None:
        return None
    try:
        context = _workflow_context(snapshot, workflow_registry)
    except Exception:
        return None
    if context is None:
        return None
    return set_workflow_collaboration_context(context)


def _workflow_context(
    snapshot: RunSnapshot,
    workflow_registry: WorkflowRegistryPort,
) -> WorkflowCollaborationContext | None:
    """构造当前执行窗口的 workflow 协作上下文。"""

    state = snapshot.workflow_run_state or {}
    workflow_name = state.get("workflow_name") or snapshot.workflow_name
    raw_phase = state.get("current_phase")
    if not isinstance(workflow_name, str) or raw_phase is None:
        return None
    phase = WorkflowPhase(str(raw_phase))
    workflow = workflow_registry.require_definition(workflow_name)
    source_role = None
    for phase_definition in workflow.phases:
        if phase_definition.phase is phase:
            source_role = phase_definition.role
            break

    summary = snapshot.collaboration_summary or {}
    task_payload = snapshot.payload.task or {}
    active_role = state.get("active_role")
    if isinstance(active_role, str) and active_role.strip():
        source_role = active_role.strip()

    return WorkflowCollaborationContext(
        run_id=snapshot.run_id,
        workflow_name=workflow.name,
        phase=phase,
        source_role=source_role,
        limit=workflow.collaboration_limit,
        depth=_safe_int(task_payload.get("delegation_depth"), default=0),
        handoff_count=_safe_int(summary.get("handoff_count"), default=0),
        delegation_count=_safe_int(summary.get("delegation_count"), default=0),
        role_capability_enabled=workflow.execution_policy.role_capability_enabled,
        roles=workflow.roles,
    )


def _safe_int(value: Any, *, default: int) -> int:
    """安全转换非负整数。"""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _should_continue(snapshot: RunSnapshot) -> bool:
    """判断当前快照是否代表同一 Run 的继续执行段。"""

    if snapshot.latest_checkpoint_id is not None:
        return True
    if snapshot.status in {RunStatus.PAUSED, RunStatus.AWAITING_APPROVAL}:
        return True
    return snapshot.status in {RunStatus.QUEUED, RunStatus.RUNNING} and snapshot.result is not None


def _session_id(
    snapshot: RunSnapshot,
    payload: dict[str, Any] | None = None,
) -> str:
    """从 payload 或快照中提取非空 session_id。"""

    value = (payload or {}).get("session_id") or snapshot.payload.session_id
    if not isinstance(value, str) or not value:
        raise ValueError("Run payload 缺少 session_id")
    return value


async def _restore_checkpoint_context(
    service: Any,
    session_id: str,
    checkpoint: DurableCheckpoint,
) -> None:
    restore = getattr(service, "restore_checkpoint_context", None)
    if callable(restore):
        result = restore(session_id, checkpoint.context_snapshot)
        if inspect.isawaitable(result):
            await result


def _required_str(payload: dict[str, Any], field_name: str) -> str:
    """从 payload 中读取必填非空字符串。"""

    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Run payload 缺少 {field_name}")
    return value


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """返回 dict 值或空字典。"""

    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[str]:
    """返回字符串列表或空列表。"""

    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _tool_names(value: Any) -> frozenset[str] | None:
    """把 payload 中的工具名集合转换为 Task 需要的 frozenset。"""

    if value is None:
        return None
    if isinstance(value, (list, tuple, set)) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    raise ValueError("Run payload tool_names 必须为字符串集合")


def _next_segment_index(
    snapshot: RunSnapshot,
    checkpoint: DurableCheckpoint | None = None,
) -> int:
    """根据已有快照元数据推断即将执行的段序号。"""

    metadata = checkpoint.segment_metadata if checkpoint is not None else snapshot.segment_metadata
    metadata = metadata or {}
    raw_count = metadata.get("segment_count", 0)
    if not isinstance(raw_count, int) or raw_count < 0:
        raw_count = 0
    return raw_count + 1


def _failed_outcome(exc: Exception) -> RunExecutionOutcome:
    """把不可恢复异常转换为 failed outcome。"""

    return RunExecutionOutcome(
        status=RunStatus.FAILED,
        error={
            "type": type(exc).__name__,
            "message": str(exc),
        },
        terminal_reason="failed",
        can_continue=False,
        segment_metadata={},
    )


def _extract_approval_id(content: str) -> str | None:
    """从任务审批 content 中尽量提取 approval_id。"""

    if not content:
        return None
    for marker in ("approval_id=", "approval_id:"):
        if marker in content:
            tail = content.split(marker, 1)[1].strip()
            return tail.split()[0].strip(",;")
    return None


def _json_safe(value: Any) -> Any:
    """递归转换为 JSON-safe 结构。"""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (StrEnum, Enum)):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if not isinstance(value, type) and is_dataclass(value):
        return _json_safe({field.name: getattr(value, field.name) for field in fields(value)})
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)
