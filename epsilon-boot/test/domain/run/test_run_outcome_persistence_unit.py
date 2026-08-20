"""Run outcome 持久化判定单元测试模块。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import domain.run.outcome as outcome_module
from domain.run.outcome import (
    RunExecutionOutcome,
    RunOutcomePersistenceDecision,
    RunStoreMutationKind,
    decide_run_outcome_persistence,
)
from domain.run.value_objects import RunEventType, RunStatus

_WORKFLOW_RUN_STATE = {"workflow": "review", "phase": "execute"}
_COLLABORATION_SUMMARY = {"handoff_count": 2, "last_agent": "reviewer"}
_SEGMENT_METADATA = {"segment_count": 3, "last_segment": "approval"}


def test_succeeded_outcome_uses_success_mutation_and_result_fallback() -> None:
    """succeeded outcome 应写入成功状态并对空 result 使用空 dict。"""
    outcome = _outcome(RunStatus.SUCCEEDED)

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_SUCCEEDED
    assert decision.mutation.result == {}
    assert decision.event_type is RunEventType.RUN_SUCCEEDED
    assert decision.terminal_outcome == outcome
    _assert_workflow_and_collaboration_propagated(decision, RunStatus.SUCCEEDED)


def test_paused_outcome_uses_paused_mutation_and_keeps_result() -> None:
    """paused outcome 应写入暂停状态并保留可继续执行语义。"""
    result = {"content": "partial"}
    outcome = _outcome(RunStatus.PAUSED, result=result, can_continue=True)

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_PAUSED
    assert decision.mutation.result == result
    assert decision.event_type is RunEventType.RUN_PAUSED
    assert decision.terminal_outcome.status is RunStatus.PAUSED
    assert decision.terminal_outcome.can_continue is True
    _assert_workflow_and_collaboration_propagated(decision, RunStatus.PAUSED)


def test_awaiting_approval_with_id_uses_approval_mutation() -> None:
    """awaiting approval 带 approval_id 时应写入审批等待状态。"""
    outcome = _outcome(
        RunStatus.AWAITING_APPROVAL,
        approval_id="approval-1",
    )

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_AWAITING_APPROVAL
    assert decision.mutation.approval_id == "approval-1"
    assert decision.mutation.result == {}
    assert decision.event_type is RunEventType.APPROVAL_REQUIRED
    assert decision.terminal_outcome == outcome
    _assert_workflow_and_collaboration_propagated(decision, RunStatus.AWAITING_APPROVAL)


def test_awaiting_approval_without_id_falls_back_to_failed_decision() -> None:
    """awaiting approval 缺少 approval_id 时不得写入审批等待状态。"""
    outcome = _outcome(
        RunStatus.AWAITING_APPROVAL,
        result={"ignored": True},
    )

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_FAILED
    assert decision.mutation.kind is not RunStoreMutationKind.MARK_AWAITING_APPROVAL
    assert decision.event_type is RunEventType.RUN_FAILED
    assert decision.event_type is not RunEventType.APPROVAL_REQUIRED
    assert decision.mutation.error is not None
    assert "approval_id" in str(decision.mutation.error["message"])
    assert decision.mutation.error["status"] == RunStatus.AWAITING_APPROVAL.value
    assert decision.terminal_outcome.status is RunStatus.FAILED
    assert decision.terminal_outcome.error == decision.mutation.error
    assert decision.terminal_outcome.terminal_reason == "failed"
    assert decision.terminal_outcome.result is None
    _assert_workflow_and_collaboration_propagated(decision, RunStatus.FAILED)


def test_cancelled_outcome_uses_cancelled_mutation_and_reason_fallback() -> None:
    """cancelled outcome 应写入取消状态并对空 reason 使用 cancelled。"""
    outcome = _outcome(RunStatus.CANCELLED)

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_CANCELLED
    assert decision.mutation.reason == "cancelled"
    assert decision.event_type is RunEventType.RUN_CANCELLED
    assert decision.terminal_outcome == outcome
    _assert_workflow_and_collaboration_propagated(decision, RunStatus.CANCELLED)


def test_failed_outcome_without_error_uses_unsupported_status_fallback() -> None:
    """failed outcome 缺少 error 时沿用当前 worker 的错误 fallback。"""
    outcome = _outcome(RunStatus.FAILED)

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_FAILED
    assert decision.event_type is RunEventType.RUN_FAILED
    assert decision.mutation.error == {
        "message": "Unsupported run outcome status: failed",
        "status": "failed",
    }
    assert decision.terminal_outcome == outcome
    _assert_workflow_and_collaboration_propagated(decision, RunStatus.FAILED)


@pytest.mark.parametrize(
    "status",
    (
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.CANCEL_REQUESTED,
        RunStatus.LOST,
    ),
)
def test_unsupported_outcome_statuses_fall_back_to_failed_decision(
    status: RunStatus,
) -> None:
    """queued/running/lost 等非 outcome 终态应转为失败写入和失败事件。"""
    outcome = _outcome(status)

    decision = decide_run_outcome_persistence(outcome)

    assert decision.mutation.kind is RunStoreMutationKind.MARK_FAILED
    assert decision.event_type is RunEventType.RUN_FAILED
    assert decision.mutation.error == {
        "message": f"Unsupported run outcome status: {status.value}",
        "status": status.value,
    }
    assert decision.terminal_outcome.status is status
    _assert_workflow_and_collaboration_propagated(decision, status)


def test_outcome_module_keeps_domain_import_boundary() -> None:
    """outcome 领域模块不得导入应用层、基础设施层或运行时框架。"""
    module_file = outcome_module.__file__
    assert module_file is not None
    imports = _collect_imports(Path(module_file))
    forbidden_prefixes = {
        "application",
        "infrastructure",
        "pydantic",
        "fastapi",
        "contextvars",
        "asyncio",
        "opentelemetry",
    }

    forbidden_hits = {
        imported
        for imported in imports
        for prefix in forbidden_prefixes
        if imported == prefix or imported.startswith(f"{prefix}.")
    }

    assert forbidden_hits == set()


def _outcome(
    status: RunStatus,
    *,
    result: dict[str, object] | None = None,
    error: dict[str, object] | None = None,
    terminal_reason: str | None = None,
    can_continue: bool = False,
    approval_id: str | None = None,
) -> RunExecutionOutcome:
    """构造带 workflow/collaboration 字段的 RunExecutionOutcome。"""

    return RunExecutionOutcome(
        status=status,
        result=result,
        error=error,
        terminal_reason=terminal_reason,
        can_continue=can_continue,
        approval_id=approval_id,
        segment_metadata=_SEGMENT_METADATA,
        workflow_run_state=_WORKFLOW_RUN_STATE,
        collaboration_summary=_COLLABORATION_SUMMARY,
    )


def _assert_workflow_and_collaboration_propagated(
    decision: RunOutcomePersistenceDecision,
    expected_terminal_status: RunStatus,
) -> None:
    """断言 store mutation 与 terminal outcome 都传播 workflow/collaboration 字段。"""

    assert decision.mutation.workflow_run_state == _WORKFLOW_RUN_STATE
    assert decision.mutation.collaboration_summary == _COLLABORATION_SUMMARY
    assert decision.terminal_outcome.status is expected_terminal_status
    assert decision.terminal_outcome.segment_metadata == _SEGMENT_METADATA
    assert decision.terminal_outcome.workflow_run_state == _WORKFLOW_RUN_STATE
    assert decision.terminal_outcome.collaboration_summary == _COLLABORATION_SUMMARY


def _collect_imports(path: Path) -> set[str]:
    """用 AST 收集模块 import，不执行被测生产代码之外的其它模块。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports
