"""模型 usage 合并工具测试。"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from infrastructure.chat.usage import merge_usage


def test_merge_usage_empty_input_returns_empty_dict() -> None:
    """空输入返回空字典。"""
    assert merge_usage() == {}


def test_merge_usage_skips_none() -> None:
    """None usage 会被忽略。"""
    assert merge_usage(None, {"prompt_tokens": 2}) == {"prompt_tokens": 2}


def test_merge_usage_accumulates_missing_keys_as_zero() -> None:
    """缺失 key 按 0 处理并保留所有出现过的 key。"""
    assert merge_usage(
        {"prompt_tokens": 2, "total_tokens": 3},
        {"completion_tokens": 4, "total_tokens": 4},
    ) == {
        "prompt_tokens": 2,
        "completion_tokens": 4,
        "total_tokens": 7,
    }


def test_merge_usage_rejects_non_int_value() -> None:
    """非 int usage 值会被拒绝。"""
    with pytest.raises(ValueError, match="int"):
        merge_usage({"prompt_tokens": "1"})  # type: ignore[dict-item]


def test_merge_usage_rejects_negative_value() -> None:
    """负数 usage 值会被拒绝。"""
    with pytest.raises(ValueError, match="非负"):
        merge_usage({"prompt_tokens": -1})


@given(
    st.lists(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=12),
            values=st.integers(min_value=0, max_value=10_000),
            max_size=8,
        ),
        max_size=10,
    )
)
def test_merge_usage_sums_arbitrary_usage_dicts(
    usages: list[dict[str, int]],
) -> None:
    """任意 usage 字典列表按 key 求和。"""
    expected: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            expected[key] = expected.get(key, 0) + value

    assert merge_usage(*usages) == expected
