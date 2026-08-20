"""分段执行编排决策领域模块。

判定逻辑自 infrastructure/agent 平移至领域层同子域（与 ``segmented_execution.py``
同层，ADR-0015），为零基础设施依赖的纯领域判定：不执行 Agent，也不修改上下文，
仅提供可测试的纯决策。不改动 Segmented_Execution_Value_Objects。
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentProgressSnapshot,
    SegmentStopReason,
)


@dataclass(frozen=True)
class SegmentContinuationDecision:
    """单段结束后的续跑决策。"""

    should_continue: bool
    stop_reason: SegmentStopReason


def decide_next_segment(
    *,
    policy: SegmentExecutionPolicy,
    usage: SegmentBudgetUsage,
    status: str,
    can_continue: bool,
    progress: SegmentProgressSnapshot,
    approval_required: bool = False,
    tool_boundary_available: bool = True,
    risk_gate_required: bool = False,
) -> SegmentContinuationDecision:
    """判断是否应自动进入下一段。"""
    if status == "completed":
        return SegmentContinuationDecision(False, "completed")
    if approval_required or status == "approval_required":
        return SegmentContinuationDecision(False, "approval_required")
    if not can_continue:
        return SegmentContinuationDecision(False, "continue_precondition_failed")
    if not tool_boundary_available:
        return SegmentContinuationDecision(False, "tool_boundary_unavailable")
    if risk_gate_required:
        return SegmentContinuationDecision(False, "risk_gate_required")
    if not policy.auto_continue_enabled:
        return SegmentContinuationDecision(False, "auto_disabled")
    if usage.continuation_count >= policy.max_continuations:
        return SegmentContinuationDecision(False, "max_continuations_reached")
    if policy.max_total_tokens is not None and usage.total_tokens >= policy.max_total_tokens:
        return SegmentContinuationDecision(False, "total_token_budget_reached")
    if (
        policy.max_duration_seconds is not None
        and usage.elapsed_ms >= policy.max_duration_seconds * 1000
    ):
        return SegmentContinuationDecision(False, "total_duration_budget_reached")
    if usage.consecutive_paused_count >= policy.max_consecutive_paused:
        return SegmentContinuationDecision(False, "consecutive_paused_limit")
    if usage.no_progress_count >= policy.max_no_progress_segments:
        return SegmentContinuationDecision(False, "no_progress")
    if usage.repeated_tool_call_count >= policy.max_repeated_tool_calls:
        return SegmentContinuationDecision(False, "repeated_tool_call")
    return SegmentContinuationDecision(True, "completed")
