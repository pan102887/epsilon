"""DelegationResult 值对象属性测试模块。

使用 Hypothesis 对 DelegationResult 值对象进行属性测试，验证：
- 构造正确性：任意合法参数构造后字段值保留（round-trip）
- 不可变性：frozen dataclass 赋值属性时抛出 FrozenInstanceError
"""

from dataclasses import FrozenInstanceError

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.value_objects import DelegationResult

# ── Hypothesis 策略 ──

content_st = st.text()
success_st = st.booleans()


# ── Property 1: DelegationResult 构造 round-trip 与不可变性 ──
# Feature: agent-delegation-decoupling, Property 1: DelegationResult round-trip and immutability


@settings(max_examples=100, deadline=5000)
@given(content=content_st, success=success_st)
def test_delegation_result_construction_preserves_fields(
    content: str,
    success: bool,
) -> None:
    """验证 DelegationResult 构造成功且所有字段值保留。

    **Validates: Requirements 2.1, 2.2**

    对于任意合法的 content 字符串和 success 布尔值，
    构造 DelegationResult 后各字段应与传入值完全一致。
    """
    result = DelegationResult(content=content, success=success)

    assert result.content == content
    assert result.success == success


@settings(max_examples=100, deadline=5000)
@given(content=content_st, success=success_st)
def test_delegation_result_is_frozen(
    content: str,
    success: bool,
) -> None:
    """验证 DelegationResult 为 frozen dataclass，赋值属性时抛出 FrozenInstanceError。

    **Validates: Requirements 2.1, 2.2**

    对于任意合法参数构造的 DelegationResult，尝试修改任意属性应抛出 FrozenInstanceError。
    """
    result = DelegationResult(content=content, success=success)

    with pytest.raises(FrozenInstanceError):
        result.content = "changed"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        result.success = not success  # type: ignore[misc]
