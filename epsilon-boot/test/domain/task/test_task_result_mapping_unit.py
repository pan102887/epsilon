"""TaskResultMapper 领域服务单元测试。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain.agent.value_objects import (
    AgentResult,
    AgentTerminationReason,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.task.result_mapping import TaskResultMapper
from domain.task.value_objects import TaskStatus, TraceEntry

PROMPT_ID = "task-template@v1"


def _trace() -> list[TraceEntry]:
    return [
        TraceEntry(
            step=1,
            action="tool_result",
            detail="ok",
            timestamp_ms=1234.0,
        )
    ]


def _approval_payload() -> ApprovalRequiredPayload:
    return ApprovalRequiredPayload(
        session_id="session-1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="write_file",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        prompt_id=PROMPT_ID,
    )


@pytest.mark.parametrize(
    ("agent_result", "expected_status"),
    [
        (
            AgentResult(
                content="done",
                model="gpt-test",
                usage={"total_tokens": 7},
                latency_ms=12.5,
            ),
            TaskStatus.SUCCESS,
        ),
        (
            AgentResult(
                content="",
                model="gpt-test",
                status="approval_required",
                approval=_approval_payload(),
            ),
            TaskStatus.HUMAN_INTERVENTION_REQUIRED,
        ),
        (
            AgentResult(
                content="",
                model="gpt-test",
                terminated_reason="max_rounds",
            ),
            TaskStatus.PAUSED,
        ),
        (
            AgentResult(
                content="",
                model="gpt-test",
                terminated_reason="token_budget_exceeded",
            ),
            TaskStatus.PAUSED,
        ),
    ],
)
def test_status_for_agent_result(
    agent_result: AgentResult, expected_status: TaskStatus
) -> None:
    """覆盖 completed / approval_required / 两类暂停终止原因状态映射。"""
    assert TaskResultMapper.status_for_agent_result(agent_result) is expected_status


def test_to_task_result_completed_maps_content_and_common_fields() -> None:
    """completed 分支透传 content，并把 terminated_reason 固定为 completed。"""
    trace = _trace()
    result = TaskResultMapper.to_task_result(
        agent_result=AgentResult(
            content="done",
            model="gpt-test",
            usage={"total_tokens": 7},
            latency_ms=12.5,
        ),
        trace=trace,
        context_can_continue=True,
        prompt_id=PROMPT_ID,
    )

    assert result.status is TaskStatus.SUCCESS
    assert result.content == "done"
    assert result.model == "gpt-test"
    assert result.prompt_id == PROMPT_ID
    assert result.usage == {"total_tokens": 7}
    assert result.trace == trace
    assert result.latency_ms == 12.5
    assert result.terminated_reason == "completed"
    assert result.can_continue is False
    assert result.approval_id is None


def test_to_task_result_approval_required_maps_empty_content_and_approval_id() -> None:
    """approval_required 分支输出人工介入状态、空 content 与 approval_id。"""
    result = TaskResultMapper.to_task_result(
        agent_result=AgentResult(
            content="ignored",
            model="gpt-test",
            usage={"total_tokens": 3},
            latency_ms=4.0,
            status="approval_required",
            approval=_approval_payload(),
        ),
        trace=_trace(),
        context_can_continue=True,
        prompt_id=PROMPT_ID,
    )

    assert result.status is TaskStatus.HUMAN_INTERVENTION_REQUIRED
    assert result.content == ""
    assert result.terminated_reason == "completed"
    assert result.can_continue is False
    assert result.approval_id == "approval-1"


@pytest.mark.parametrize("terminated_reason", ["max_rounds", "token_budget_exceeded"])
def test_to_task_result_pause_maps_empty_content_and_can_continue(
    terminated_reason: AgentTerminationReason,
) -> None:
    """暂停分支保留终止原因，并透传调用方给出的可继续判定。"""
    result = TaskResultMapper.to_task_result(
        agent_result=AgentResult(
            content="ignored",
            model="gpt-test",
            usage={"total_tokens": 11},
            latency_ms=6.0,
            terminated_reason=terminated_reason,
        ),
        trace=_trace(),
        context_can_continue=True,
        prompt_id=PROMPT_ID,
    )

    assert result.status is TaskStatus.PAUSED
    assert result.content == ""
    assert result.terminated_reason == terminated_reason
    assert result.can_continue is True
    assert result.approval_id is None


def test_result_mapping_module_has_no_forbidden_layer_imports() -> None:
    """领域映射模块不得导入 application / infrastructure / domain.run。"""
    path = (
        Path(__file__).parents[3]
        / "src"
        / "domain"
        / "task"
        / "result_mapping.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    assert not any(
        module == "application" or module.startswith("application.")
        for module in imported_modules
    )
    assert not any(
        module == "infrastructure" or module.startswith("infrastructure.")
        for module in imported_modules
    )
    assert "domain.run" not in imported_modules
    assert not any(module.startswith("domain.run.") for module in imported_modules)
