"""Task delegation_depth 属性测试与单元测试模块。

验证 Task 值对象的 delegation_depth 字段：
- 负数 delegation_depth 校验（Property 4）
- 默认值和非负整数构造
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.task.value_objects import Task

# ---------------------------------------------------------------------------
# Property 4: Task 负数 delegation_depth 校验
# Feature: agent-inter-communication, Property 4: Task 负数 delegation_depth 校验
# **验证: 需求 4.3**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(d=st.integers(max_value=-1))
def test_negative_delegation_depth_raises_value_error(d: int) -> None:
    """验证 delegation_depth 为负数时构造 Task 抛出 ValueError。

    对于任意负整数 d，Task(goal="test", delegation_depth=d) 应在
    __post_init__ 中抛出 ValueError。
    """
    with pytest.raises(ValueError, match="delegation_depth"):
        Task(goal="test", delegation_depth=d)


# ---------------------------------------------------------------------------
# 单元测试：Task delegation_depth 默认值
# 需求: 4.2
# ---------------------------------------------------------------------------


class TestTaskDelegationDepthDefault:
    """验证 Task delegation_depth 的默认值和非负整数构造。"""

    def test_default_delegation_depth_is_zero(self) -> None:
        """不传 delegation_depth 时默认为 0。"""
        task = Task(goal="test")
        assert task.delegation_depth == 0

    def test_non_negative_delegation_depth_constructs_successfully(self) -> None:
        """传入非负整数时正常构造。"""
        task = Task(goal="test", delegation_depth=5)
        assert task.delegation_depth == 5

    def test_zero_delegation_depth_constructs_successfully(self) -> None:
        """传入 0 时正常构造。"""
        task = Task(goal="test", delegation_depth=0)
        assert task.delegation_depth == 0
