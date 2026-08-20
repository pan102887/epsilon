"""DelegationDepthPolicy 领域服务单元测试（脱离运行时）。

追溯 需求 2（AC2.2 / AC2.4）与 design Property 1：验证
``exceeds_for_next_depth`` 与 ``exceeds_for_current_depth`` 两类判据在
边界（等于上限 vs 超限）上的取值，并锁定两者的差异被保留、未被统一。
本测试仅 import ``domain.task.policy``，不触碰运行时。
"""

import pytest

from domain.task.policy import DelegationDepthPolicy


class TestExceedsForNextDepth:
    """验证「下一层是否超限」判据 ``current + 1 > max``。"""

    @pytest.mark.parametrize(
        ("current_depth", "max_delegation_depth", "expected"),
        [
            (2, 3, False),  # current+1 == max，不超限
            (3, 3, True),  # current+1 == max+1，超限
            (0, 3, False),
            (0, 0, True),  # current+1 == 1 > 0
            (5, 3, True),
        ],
    )
    def test_boundary(
        self, current_depth: int, max_delegation_depth: int, expected: bool
    ) -> None:
        """在边界与常规取值上等价于 ``current_depth + 1 > max_delegation_depth``。"""
        assert (
            DelegationDepthPolicy.exceeds_for_next_depth(
                current_depth, max_delegation_depth
            )
            is expected
        )


class TestExceedsForCurrentDepth:
    """验证「当前深度是否超限」判据 ``current > max``（delegate_parallel 专用）。"""

    @pytest.mark.parametrize(
        ("current_depth", "max_delegation_depth", "expected"),
        [
            (3, 3, False),  # depth == max，不超限
            (4, 3, True),  # depth == max+1，超限
            (0, 3, False),
            (1, 0, True),
        ],
    )
    def test_boundary(
        self, current_depth: int, max_delegation_depth: int, expected: bool
    ) -> None:
        """在边界与常规取值上等价于 ``current_depth > max_delegation_depth``。"""
        assert (
            DelegationDepthPolicy.exceeds_for_current_depth(
                current_depth, max_delegation_depth
            )
            is expected
        )


def test_two_methods_differ_at_current_equals_max() -> None:
    """差异保留（AC2.4）：同一 ``(current=max, max)`` 入参下两方法结果不同。

    ``exceeds_for_next_depth`` 判 ``current+1 > max`` 为 True，
    ``exceeds_for_current_depth`` 判 ``current > max`` 为 False，二者刻意不统一。
    """
    current = 3
    max_depth = 3
    assert DelegationDepthPolicy.exceeds_for_next_depth(current, max_depth) is True
    assert (
        DelegationDepthPolicy.exceeds_for_current_depth(current, max_depth) is False
    )
