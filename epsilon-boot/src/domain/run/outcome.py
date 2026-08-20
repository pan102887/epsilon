"""Run 执行结果与持久化判定领域模块。

本模块承载单个 Run 执行段的结果值对象，以及从执行结果推导
RunStore 写入动作和终态事件类型的纯领域判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from domain.run.value_objects import RunEventType, RunStatus

_MISSING_APPROVAL_ID_ERROR = (
    "AWAITING_APPROVAL outcome is missing approval_id; "
    "cannot persist recoverable awaiting approval state"
)
_UNSUPPORTED_OUTCOME_STATUS_ERROR = "Unsupported run outcome status: {status}"


@dataclass(frozen=True)
class RunExecutionOutcome:
    """单次 Run 执行段的 JSON-safe 结果。"""

    status: RunStatus
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    terminal_reason: str | None = None
    can_continue: bool = False
    approval_id: str | None = None
    segment_metadata: dict[str, Any] | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: dict[str, Any] | None = None


class RunStoreMutationKind(StrEnum):
    """Run outcome 对应的 RunStore 写入动作。"""

    MARK_SUCCEEDED = "mark_succeeded"
    MARK_PAUSED = "mark_paused"
    MARK_AWAITING_APPROVAL = "mark_awaiting_approval"
    MARK_FAILED = "mark_failed"
    MARK_CANCELLED = "mark_cancelled"


@dataclass(frozen=True)
class RunStoreMutation:
    """RunStorePort 终态或暂停态写入参数。"""

    kind: RunStoreMutationKind
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    approval_id: str | None = None
    reason: str | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunOutcomePersistenceDecision:
    """Run worker 按 outcome 应执行的存储变更与事件写入。"""

    mutation: RunStoreMutation
    event_type: RunEventType
    terminal_outcome: RunExecutionOutcome


def decide_run_outcome_persistence(
    outcome: RunExecutionOutcome,
) -> RunOutcomePersistenceDecision:
    """把执行结果转换为 RunStore mutation 与 RunEventType。

    该函数只读取 outcome 字段并返回不可变决策值，不执行 I/O、
    不记录日志，也不依赖应用层、基础设施层或运行时框架。
    """

    status = outcome.status
    if status is RunStatus.SUCCEEDED:
        return RunOutcomePersistenceDecision(
            mutation=RunStoreMutation(
                kind=RunStoreMutationKind.MARK_SUCCEEDED,
                result=outcome.result or {},
                workflow_run_state=outcome.workflow_run_state,
                collaboration_summary=outcome.collaboration_summary,
            ),
            event_type=RunEventType.RUN_SUCCEEDED,
            terminal_outcome=outcome,
        )
    if status is RunStatus.PAUSED:
        return RunOutcomePersistenceDecision(
            mutation=RunStoreMutation(
                kind=RunStoreMutationKind.MARK_PAUSED,
                result=outcome.result or {},
                workflow_run_state=outcome.workflow_run_state,
                collaboration_summary=outcome.collaboration_summary,
            ),
            event_type=RunEventType.RUN_PAUSED,
            terminal_outcome=outcome,
        )
    if status is RunStatus.AWAITING_APPROVAL:
        return _decide_awaiting_approval(outcome)
    if status is RunStatus.CANCELLED:
        return RunOutcomePersistenceDecision(
            mutation=RunStoreMutation(
                kind=RunStoreMutationKind.MARK_CANCELLED,
                reason=outcome.terminal_reason or "cancelled",
                workflow_run_state=outcome.workflow_run_state,
                collaboration_summary=outcome.collaboration_summary,
            ),
            event_type=RunEventType.RUN_CANCELLED,
            terminal_outcome=outcome,
        )

    error = outcome.error or _unsupported_status_error(status)
    return RunOutcomePersistenceDecision(
        mutation=RunStoreMutation(
            kind=RunStoreMutationKind.MARK_FAILED,
            error=error,
            workflow_run_state=outcome.workflow_run_state,
            collaboration_summary=outcome.collaboration_summary,
        ),
        event_type=RunEventType.RUN_FAILED,
        terminal_outcome=outcome,
    )


def _decide_awaiting_approval(
    outcome: RunExecutionOutcome,
) -> RunOutcomePersistenceDecision:
    """判定 awaiting approval outcome 的审批等待或失败 fallback。"""

    if outcome.approval_id:
        return RunOutcomePersistenceDecision(
            mutation=RunStoreMutation(
                kind=RunStoreMutationKind.MARK_AWAITING_APPROVAL,
                approval_id=outcome.approval_id,
                result=outcome.result or {},
                workflow_run_state=outcome.workflow_run_state,
                collaboration_summary=outcome.collaboration_summary,
            ),
            event_type=RunEventType.APPROVAL_REQUIRED,
            terminal_outcome=outcome,
        )

    error = {
        "message": _MISSING_APPROVAL_ID_ERROR,
        "status": outcome.status.value,
    }
    failed_outcome = RunExecutionOutcome(
        status=RunStatus.FAILED,
        error=error,
        terminal_reason="failed",
        can_continue=False,
        segment_metadata=outcome.segment_metadata,
        workflow_run_state=outcome.workflow_run_state,
        collaboration_summary=outcome.collaboration_summary,
    )
    return RunOutcomePersistenceDecision(
        mutation=RunStoreMutation(
            kind=RunStoreMutationKind.MARK_FAILED,
            error=error,
            workflow_run_state=outcome.workflow_run_state,
            collaboration_summary=outcome.collaboration_summary,
        ),
        event_type=RunEventType.RUN_FAILED,
        terminal_outcome=failed_outcome,
    )


def _unsupported_status_error(status: RunStatus) -> dict[str, Any]:
    """构造不支持 outcome status 时写入 RunStore 的错误载荷。"""

    return {
        "message": _UNSUPPORTED_OUTCOME_STATUS_ERROR.format(status=status.value),
        "status": status.value,
    }
