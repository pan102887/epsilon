"""任务 API presenter。"""

from __future__ import annotations

from domain.agent.segmented_execution import SegmentBudgetUsage


def segment_budget_usage_to_response_body(
    value: SegmentBudgetUsage,
) -> dict[str, int | float]:
    """把分段预算用量映射为任务 HTTP 响应体字段。

    Args:
        value: 分段运行累计预算用量。

    Returns:
        与任务 API ``budget_usage`` 字段线格式等价的字典。
    """
    return {
        "segment_count": value.segment_count,
        "continuation_count": value.continuation_count,
        "total_tokens": value.total_tokens,
        "elapsed_ms": value.elapsed_ms,
        "consecutive_paused_count": value.consecutive_paused_count,
        "no_progress_count": value.no_progress_count,
        "repeated_tool_call_count": value.repeated_tool_call_count,
    }
