"""任务 API presenter 单元测试。"""

from application.api.presenters.task_presenter import segment_budget_usage_to_response_body
from domain.agent.segmented_execution import SegmentBudgetUsage


def test_segment_budget_usage_to_response_body_preserves_all_fields() -> None:
    """验证 budget_usage 所有字段均保持既有线格式。"""
    usage = SegmentBudgetUsage(
        segment_count=3,
        continuation_count=2,
        total_tokens=1234,
        elapsed_ms=456.7,
        consecutive_paused_count=1,
        no_progress_count=2,
        repeated_tool_call_count=3,
    )

    assert segment_budget_usage_to_response_body(usage) == {
        "segment_count": 3,
        "continuation_count": 2,
        "total_tokens": 1234,
        "elapsed_ms": 456.7,
        "consecutive_paused_count": 1,
        "no_progress_count": 2,
        "repeated_tool_call_count": 3,
    }
