"""Run 审批恢复分派器。

本模块负责把 Run 级审批恢复请求，按 RunKind 分派到既有聊天恢复能力或
任务恢复能力。应用层只编排领域端口和值对象，不引入 FastAPI、Redis 或
其他基础设施细节。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Any, cast

from domain.agent.value_objects import ApprovalDecision
from domain.chat.ports import ChatServicePort
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatResponseVO
from domain.run.ports import ApprovalResumeStoreResult
from domain.run.value_objects import RunKind, RunSnapshot
from domain.run.workflow import WorkflowPhase
from domain.task.enums import TaskOutcomeKind
from domain.task.policy import TaskStatusMapping
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import TaskApprovalResumeRequest, TaskResult


class RunApprovalResumer:
    """按 RunKind 分派审批恢复。"""

    def __init__(
        self,
        *,
        chat_service: ChatServicePort,
        task_agent: TaskAgentPort,
    ) -> None:
        """初始化审批恢复分派器。"""

        self._chat_service = chat_service
        self._task_agent = task_agent

    async def __call__(
        self,
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeStoreResult:
        """恢复 awaiting_approval Run，并返回同一 Run 的后续状态指令。"""

        if snapshot.kind is RunKind.CHAT:
            response = await self._chat_service.resume_approval(
                ApprovalResumeRequestVO(
                    session_id=_session_id(snapshot),
                    approval_id=_approval_id(snapshot),
                    decisions=tuple(decisions),
                    model=model,
                )
            )
            return _chat_response_to_store_result(snapshot, response)
        if snapshot.kind is RunKind.TASK:
            response = await self._task_agent.resume_approval(
                TaskApprovalResumeRequest(
                    session_id=_session_id(snapshot),
                    approval_id=_approval_id(snapshot),
                    decisions=tuple(decisions),
                    model=model,
                )
            )
            return _task_result_to_store_result(snapshot, response)
        raise ValueError(f"不支持的 RunKind: {snapshot.kind!r}")


def _chat_response_to_store_result(
    snapshot: RunSnapshot,
    response: ChatResponseVO,
) -> ApprovalResumeStoreResult:
    """把聊天审批恢复响应映射为 Run 存储状态指令。"""

    result = {
        "kind": "chat",
        "session_id": response.session_id,
        "reply": response.reply,
        "model": response.model,
        "prompt_id": response.prompt_id,
        "usage": _json_safe(response.usage),
        "status": response.status,
        "terminated_reason": response.terminated_reason,
        "action_requests": _json_safe(response.action_requests),
    }
    if response.status == "approval_required":
        return ApprovalResumeStoreResult(
            status="awaiting_approval",
            approval_id=response.approval_id,
            result=result,
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    if response.status == "paused":
        return ApprovalResumeStoreResult(
            status="queued",
            result=result,
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    if response.can_continue or _workflow_phase_can_continue(snapshot.workflow_run_state):
        return ApprovalResumeStoreResult(
            status="queued",
            result=result,
            terminal_reason=str(response.terminated_reason),
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    return ApprovalResumeStoreResult(
        status="succeeded",
        result=result,
        terminal_reason=str(response.terminated_reason),
        guardrail_summary=None,
        workflow_run_state=None,
        collaboration_summary=None,
    )


def _task_result_to_store_result(
    snapshot: RunSnapshot,
    response: TaskResult,
) -> ApprovalResumeStoreResult:
    """把任务审批恢复响应映射为 Run 存储状态指令。"""

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
    }
    kind = TaskStatusMapping.outcome_of(response.status)
    if kind is TaskOutcomeKind.PAUSED:
        return ApprovalResumeStoreResult(
            status="queued",
            result=result,
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    if kind is TaskOutcomeKind.AWAITING_APPROVAL:
        return ApprovalResumeStoreResult(
            status="awaiting_approval",
            approval_id=response.approval_id,
            result=result,
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    if kind is TaskOutcomeKind.FAILED:
        return ApprovalResumeStoreResult(
            status="failed",
            error={
                "message": response.content,
                "task_status": response.status.value,
            },
            terminal_reason="failed",
            guardrail_summary=None,
            workflow_run_state=None,
            collaboration_summary=None,
        )
    return ApprovalResumeStoreResult(
        status="succeeded",
        result=result,
        terminal_reason=str(response.terminated_reason),
        guardrail_summary=None,
        workflow_run_state=None,
        collaboration_summary=None,
    )


def _session_id(snapshot: RunSnapshot) -> str:
    """从 Run 快照中提取审批恢复所需的会话标识。"""

    payload = snapshot.payload.chat if snapshot.kind is RunKind.CHAT else snapshot.payload.task
    if isinstance(payload, dict):
        raw_session_id = payload.get("session_id")
        if isinstance(raw_session_id, str) and raw_session_id:
            return raw_session_id
    session_id = snapshot.payload.session_id
    if isinstance(session_id, str) and session_id:
        return session_id
    raise ValueError(f"Run {snapshot.run_id} 缺少 session_id，无法恢复审批")


def _approval_id(snapshot: RunSnapshot) -> str:
    """返回 awaiting_approval Run 当前审批批次标识。"""

    if isinstance(snapshot.approval_id, str) and snapshot.approval_id:
        return snapshot.approval_id
    raise ValueError(f"Run {snapshot.run_id} 缺少 approval_id，无法恢复审批")


def _workflow_phase_can_continue(workflow_run_state: dict[str, Any] | None) -> bool:
    """根据 workflow 当前阶段判断是否需要继续推进同一 Run。"""

    if not isinstance(workflow_run_state, dict):
        return False
    raw_phase = workflow_run_state.get("current_phase")
    if raw_phase is None:
        return False
    try:
        phase = WorkflowPhase(str(raw_phase))
    except ValueError:
        return False
    return phase is not WorkflowPhase.FINALIZE


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
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in cast(Iterable[object], value)]
    return str(value)
