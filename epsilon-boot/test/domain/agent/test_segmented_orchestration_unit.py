"""分段续跑判定领域单元测试（脱离运行时）。

逐条命中 12 道判定门 + None 阈值短路 + 全部门未触发续跑，锁定
decide_next_segment 平移前后行为等价（ADR-0015，Property 5）；并断言
infrastructure 垫片 re-export 的类/函数与领域实现为同一对象（Property 8）。
"""

from __future__ import annotations

import pytest

from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentProgressSnapshot,
)
from domain.agent.segmented_orchestration import (
    SegmentContinuationDecision,
    decide_next_segment,
)

_PROGRESS = SegmentProgressSnapshot(0, 1, token_delta=1)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": "completed"}, "completed"),
        ({"status": "approval_required"}, "approval_required"),
        ({"approval_required": True}, "approval_required"),
        ({"can_continue": False}, "continue_precondition_failed"),
        ({"tool_boundary_available": False}, "tool_boundary_unavailable"),
        ({"risk_gate_required": True}, "risk_gate_required"),
    ],
)
def test_decide_next_segment_precondition_gates(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    """终止态、审批、前置条件、工具边界与风险门按序命中。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(auto_continue_enabled=True),
        usage=SegmentBudgetUsage(),
        status=str(kwargs.get("status", "paused")),
        can_continue=bool(kwargs.get("can_continue", True)),
        progress=_PROGRESS,
        approval_required=bool(kwargs.get("approval_required", False)),
        tool_boundary_available=bool(kwargs.get("tool_boundary_available", True)),
        risk_gate_required=bool(kwargs.get("risk_gate_required", False)),
    )

    assert decision.should_continue is False
    assert decision.stop_reason == reason


def test_decide_next_segment_stops_when_auto_disabled() -> None:
    """自动续跑关闭时命中 auto_disabled 门。"""
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
def test_decide_next_segment_budget_and_loop_gates(
    usage: SegmentBudgetUsage,
    policy: SegmentExecutionPolicy,
    reason: str,
) -> None:
    """预算与反循环阈值以 >= 命中时不进入下一段（含 duration ×1000）。"""
    decision = decide_next_segment(
        policy=policy,
        usage=usage,
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
    )

    assert decision.should_continue is False
    assert decision.stop_reason == reason


@pytest.mark.parametrize(
    "usage",
    [
        SegmentBudgetUsage(total_tokens=10_000_000),
        SegmentBudgetUsage(elapsed_ms=10_000_000.0),
    ],
)
def test_decide_next_segment_none_threshold_short_circuits(
    usage: SegmentBudgetUsage,
) -> None:
    """max_total_tokens / max_duration_seconds 为 None 时对应门不命中。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_total_tokens=None,
            max_duration_seconds=None,
        ),
        usage=usage,
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
    )

    assert decision == SegmentContinuationDecision(True, "completed")


def test_decide_next_segment_allows_continuation_when_no_gate_hit() -> None:
    """全部门未触发时允许继续并返回 (True, completed)。"""
    decision = decide_next_segment(
        policy=SegmentExecutionPolicy(auto_continue_enabled=True),
        usage=SegmentBudgetUsage(segment_count=1, consecutive_paused_count=1),
        status="paused",
        can_continue=True,
        progress=_PROGRESS,
    )

    assert decision == SegmentContinuationDecision(True, "completed")


def test_shim_reexports_same_class_and_function_objects() -> None:
    """基础设施垫片 re-export 的类/函数与领域实现为同一对象（Property 8）。"""
    import domain.agent.segmented_orchestration as dom_so
    import infrastructure.agent.segmented_orchestration as infra_so

    assert infra_so.SegmentContinuationDecision is dom_so.SegmentContinuationDecision
    assert infra_so.decide_next_segment is dom_so.decide_next_segment
