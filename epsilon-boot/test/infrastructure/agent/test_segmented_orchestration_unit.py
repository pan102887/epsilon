"""分段执行停止决策单元测试。"""

from __future__ import annotations

import pytest

from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentProgressSnapshot,
)
from infrastructure.agent.segmented_orchestration import decide_next_segment

_PROGRESS = SegmentProgressSnapshot(0, 1, token_delta=1)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": "completed"}, "completed"),
        ({"status": "approval_required"}, "approval_required"),
        ({"can_continue": False}, "continue_precondition_failed"),
        ({"tool_boundary_available": False}, "tool_boundary_unavailable"),
    ],
)
def test_decide_next_segment_terminal_preconditions(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    """终止态和前置条件失败应先于自动续跑配置。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(auto_continue_enabled=True),
        usage=SegmentBudgetUsage(),
        status=str(kwargs.get("status", "paused")),
        can_continue=bool(kwargs.get("can_continue", True)),
        progress=_PROGRESS,
        tool_boundary_available=bool(kwargs.get("tool_boundary_available", True)),
    )

    assert decision.should_continue is False
    assert decision.stop_reason == reason


def test_decide_next_segment_stops_when_auto_disabled() -> None:
    """自动续跑关闭时返回 auto_disabled。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(auto_continue_enabled=False),
        usage=SegmentBudgetUsage(),
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
    )

    assert decision.should_continue is False
    assert decision.stop_reason == "auto_disabled"


@pytest.mark.parametrize(
    ("usage", "policy", "reason"),
    [
        (
            SegmentBudgetUsage(continuation_count=3),
            SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=3),
            "max_continuations_reached",
        ),
        (
            SegmentBudgetUsage(total_tokens=100),
            SegmentExecutionPolicy(auto_continue_enabled=True, max_total_tokens=100),
            "total_token_budget_reached",
        ),
        (
            SegmentBudgetUsage(elapsed_ms=1000),
            SegmentExecutionPolicy(auto_continue_enabled=True, max_duration_seconds=1),
            "total_duration_budget_reached",
        ),
        (
            SegmentBudgetUsage(consecutive_paused_count=2),
            SegmentExecutionPolicy(auto_continue_enabled=True, max_consecutive_paused=2),
            "consecutive_paused_limit",
        ),
        (
            SegmentBudgetUsage(no_progress_count=2),
            SegmentExecutionPolicy(auto_continue_enabled=True, max_no_progress_segments=2),
            "no_progress",
        ),
        (
            SegmentBudgetUsage(repeated_tool_call_count=2),
            SegmentExecutionPolicy(auto_continue_enabled=True, max_repeated_tool_calls=2),
            "repeated_tool_call",
        ),
    ],
)
def test_decide_next_segment_stops_at_budget_and_loop_limits(
    usage: SegmentBudgetUsage,
    policy: SegmentExecutionPolicy,
    reason: str,
) -> None:
    """预算和反循环阈值命中时不进入下一段。"""
    decision = decide_next_segment(
        policy=policy,
        usage=usage,
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
    )

    assert decision.should_continue is False
    assert decision.stop_reason == reason


def test_decide_next_segment_stops_when_risk_gate_required() -> None:
    """风险门禁命中时停止自动续跑并交给人工继续。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(auto_continue_enabled=True),
        usage=SegmentBudgetUsage(),
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
        risk_gate_required=True,
    )

    assert decision.should_continue is False
    assert decision.stop_reason == "risk_gate_required"


def test_decide_next_segment_allows_continuation_when_no_limits_hit() -> None:
    """启用自动续跑且未命中限制时允许继续。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(auto_continue_enabled=True),
        usage=SegmentBudgetUsage(segment_count=1, consecutive_paused_count=1),
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
    )

    assert decision.should_continue is True
    assert decision.stop_reason == "completed"
