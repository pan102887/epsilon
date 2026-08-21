"""分段执行领域值对象单元测试。"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentProgressSnapshot,
    SegmentRunMetadata,
)
from infrastructure.agent.segment_serialization import (
    segment_budget_usage_to_dict,
    segment_run_metadata_to_http_dict,
)


def test_segment_execution_policy_defaults() -> None:
    """默认策略关闭自动续跑并使用保守阈值。"""
    policy = SegmentExecutionPolicy()

    assert policy.auto_continue_enabled is False
    assert policy.max_continuations == 3
    assert policy.max_total_tokens is None
    assert policy.max_duration_seconds is None
    assert policy.max_consecutive_paused == 2
    assert policy.max_no_progress_segments == 2
    assert policy.max_repeated_tool_calls == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_continuations", -1),
        ("max_total_tokens", 0),
        ("max_duration_seconds", 0),
        ("max_consecutive_paused", 0),
        ("max_no_progress_segments", 0),
        ("max_repeated_tool_calls", 0),
    ],
)
def test_segment_execution_policy_rejects_invalid_values(
    field: str,
    value: int,
) -> None:
    """策略字段必须拒绝无法执行的非法阈值。"""
    constructor = cast(Callable[..., SegmentExecutionPolicy], SegmentExecutionPolicy)
    with pytest.raises(ValueError):
        constructor(**{field: value})


def test_segment_budget_usage_plus_segment_accumulates_counts() -> None:
    """预算用量应按段累加并维护连续计数。"""
    usage = SegmentBudgetUsage()

    first = usage.plus_segment(
        total_tokens_delta=12,
        elapsed_ms_delta=25.5,
        paused=True,
        has_progress=False,
        repeated_tool_call=False,
    )
    second = first.plus_segment(
        total_tokens_delta=8,
        elapsed_ms_delta=5.0,
        paused=False,
        has_progress=True,
        repeated_tool_call=True,
    )

    assert first.segment_count == 1
    assert first.continuation_count == 0
    assert first.consecutive_paused_count == 1
    assert first.no_progress_count == 1
    assert second.segment_count == 2
    assert second.continuation_count == 1
    assert second.total_tokens == 20
    assert second.elapsed_ms == 30.5
    assert second.consecutive_paused_count == 0
    assert second.no_progress_count == 0
    assert second.repeated_tool_call_count == 1


def test_segment_budget_usage_to_dict_is_http_ready() -> None:
    """预算用量可转换为 HTTP 响应友好的字典。"""
    data = segment_budget_usage_to_dict(
        SegmentBudgetUsage(segment_count=2, total_tokens=10)
    )

    assert data == {
        "segment_count": 2,
        "continuation_count": 0,
        "total_tokens": 10,
        "elapsed_ms": 0.0,
        "consecutive_paused_count": 0,
        "no_progress_count": 0,
        "repeated_tool_call_count": 0,
    }


@pytest.mark.parametrize(
    "snapshot",
    [
        SegmentProgressSnapshot(0, 1, new_tool_message_count=1),
        SegmentProgressSnapshot(0, 1, new_trace_count=1),
        SegmentProgressSnapshot(0, 1, token_delta=1),
        SegmentProgressSnapshot(0, 1, final_content_present=True),
    ],
)
def test_segment_progress_snapshot_has_progress(snapshot: SegmentProgressSnapshot) -> None:
    """任一进展信号存在时 has_progress 为 True。"""
    assert snapshot.has_progress is True


def test_segment_progress_snapshot_without_signal_has_no_progress() -> None:
    """没有任何进展信号时 has_progress 为 False。"""
    snapshot = SegmentProgressSnapshot(1, 1)

    assert snapshot.has_progress is False


def test_segment_run_metadata_to_http_dict() -> None:
    """分段元数据可转换为 HTTP 响应字段。"""
    metadata = SegmentRunMetadata(
        segment_index=2,
        segment_count=3,
        auto_continue_attempted=True,
        segment_stop_reason="max_continuations_reached",
        budget_usage=SegmentBudgetUsage(segment_count=3, total_tokens=99),
        risk_gate_required=True,
        guardrail_reason="tool_risk_gate_required",
    )

    assert segment_run_metadata_to_http_dict(metadata) == {
        "segment_index": 2,
        "segment_count": 3,
        "auto_continue_attempted": True,
        "segment_stop_reason": "max_continuations_reached",
        "budget_usage": {
            "segment_count": 3,
            "continuation_count": 0,
            "total_tokens": 99,
            "elapsed_ms": 0.0,
            "consecutive_paused_count": 0,
            "no_progress_count": 0,
            "repeated_tool_call_count": 0,
        },
        "risk_gate_required": True,
        "guardrail_reason": "tool_risk_gate_required",
    }
