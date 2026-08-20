"""分段执行策略属性测试。"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from domain.agent.segmented_execution import SegmentExecutionPolicy


@given(
    max_continuations=st.integers(min_value=0, max_value=50),
    max_total_tokens=st.one_of(st.none(), st.integers(min_value=1, max_value=100000)),
    max_duration_seconds=st.one_of(
        st.none(),
        st.floats(min_value=0.001, max_value=10000, allow_nan=False, allow_infinity=False),
    ),
    max_consecutive_paused=st.integers(min_value=1, max_value=20),
    max_no_progress_segments=st.integers(min_value=1, max_value=20),
    max_repeated_tool_calls=st.integers(min_value=1, max_value=20),
)
def test_policy_accepts_all_valid_thresholds(
    max_continuations: int,
    max_total_tokens: int | None,
    max_duration_seconds: float | None,
    max_consecutive_paused: int,
    max_no_progress_segments: int,
    max_repeated_tool_calls: int,
) -> None:
    """任意合法阈值组合都应可构造策略。"""
    policy = SegmentExecutionPolicy(
        max_continuations=max_continuations,
        max_total_tokens=max_total_tokens,
        max_duration_seconds=max_duration_seconds,
        max_consecutive_paused=max_consecutive_paused,
        max_no_progress_segments=max_no_progress_segments,
        max_repeated_tool_calls=max_repeated_tool_calls,
    )

    assert policy.max_continuations == max_continuations


@given(value=st.integers(max_value=-1))
def test_policy_rejects_negative_max_continuations(value: int) -> None:
    """最大续跑次数不能为负数。"""
    with pytest.raises(ValueError):
        SegmentExecutionPolicy(max_continuations=value)


@given(value=st.integers(max_value=0))
def test_policy_rejects_non_positive_positive_thresholds(value: int) -> None:
    """正数阈值字段不能为 0 或负数。"""
    with pytest.raises(ValueError):
        SegmentExecutionPolicy(max_consecutive_paused=value)
    with pytest.raises(ValueError):
        SegmentExecutionPolicy(max_no_progress_segments=value)
    with pytest.raises(ValueError):
        SegmentExecutionPolicy(max_repeated_tool_calls=value)
