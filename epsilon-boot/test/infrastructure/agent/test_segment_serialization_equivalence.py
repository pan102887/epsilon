"""分段执行序列化映射器的字面快照等价性测试。

对 ``segment_budget_usage_to_dict`` / ``segment_run_metadata_to_http_dict``
分别写字面快照断言，锁定行为等价重构不改变对外线格式。
"""

from __future__ import annotations

from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentRunMetadata,
)
from infrastructure.agent.segment_serialization import (
    segment_budget_usage_to_dict,
    segment_run_metadata_to_http_dict,
)


def test_segment_budget_usage_to_dict_literal_snapshot() -> None:
    """``segment_budget_usage_to_dict`` 应产出逐字段字面快照。"""
    usage = SegmentBudgetUsage(
        segment_count=3,
        continuation_count=2,
        total_tokens=1500,
        elapsed_ms=1234.5,
        consecutive_paused_count=1,
        no_progress_count=0,
        repeated_tool_call_count=2,
    )

    result = segment_budget_usage_to_dict(usage)

    assert result == {
        "segment_count": 3,
        "continuation_count": 2,
        "total_tokens": 1500,
        "elapsed_ms": 1234.5,
        "consecutive_paused_count": 1,
        "no_progress_count": 0,
        "repeated_tool_call_count": 2,
    }


def test_segment_budget_usage_to_dict_defaults_snapshot() -> None:
    """默认构造的 ``SegmentBudgetUsage`` 应产出全零快照。"""
    usage = SegmentBudgetUsage()

    result = segment_budget_usage_to_dict(usage)

    assert result == {
        "segment_count": 0,
        "continuation_count": 0,
        "total_tokens": 0,
        "elapsed_ms": 0.0,
        "consecutive_paused_count": 0,
        "no_progress_count": 0,
        "repeated_tool_call_count": 0,
    }


def test_segment_run_metadata_to_http_dict_literal_snapshot() -> None:
    """``segment_run_metadata_to_http_dict`` 应产出逐字段字面快照。"""
    metadata = SegmentRunMetadata(
        segment_index=2,
        segment_count=4,
        auto_continue_attempted=True,
        segment_stop_reason="max_continuations_reached",
        budget_usage=SegmentBudgetUsage(
            segment_count=4,
            continuation_count=3,
            total_tokens=2048,
            elapsed_ms=987.0,
            consecutive_paused_count=0,
            no_progress_count=1,
            repeated_tool_call_count=0,
        ),
        risk_gate_required=True,
        guardrail_reason="risk_gate",
    )

    result = segment_run_metadata_to_http_dict(metadata)

    assert result == {
        "segment_index": 2,
        "segment_count": 4,
        "auto_continue_attempted": True,
        "segment_stop_reason": "max_continuations_reached",
        "budget_usage": {
            "segment_count": 4,
            "continuation_count": 3,
            "total_tokens": 2048,
            "elapsed_ms": 987.0,
            "consecutive_paused_count": 0,
            "no_progress_count": 1,
            "repeated_tool_call_count": 0,
        },
        "risk_gate_required": True,
        "guardrail_reason": "risk_gate",
    }


def test_segment_run_metadata_to_http_dict_defaults_snapshot() -> None:
    """默认构造的 ``SegmentRunMetadata`` 应产出默认字段快照。"""
    metadata = SegmentRunMetadata()

    result = segment_run_metadata_to_http_dict(metadata)

    assert result == {
        "segment_index": 1,
        "segment_count": 1,
        "auto_continue_attempted": False,
        "segment_stop_reason": "completed",
        "budget_usage": {
            "segment_count": 0,
            "continuation_count": 0,
            "total_tokens": 0,
            "elapsed_ms": 0.0,
            "consecutive_paused_count": 0,
            "no_progress_count": 0,
            "repeated_tool_call_count": 0,
        },
        "risk_gate_required": False,
        "guardrail_reason": None,
    }
