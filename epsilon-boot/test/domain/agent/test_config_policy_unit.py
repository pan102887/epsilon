"""``DelegationDepthNormalizationPolicy`` 领域服务单元测试模块。

覆盖 feature ``ddd-anemic-domain-agent-followups`` 候选 A（需求 6 AC6.2，
Property 1）：委派深度归一三分支逐一等价锁定。本测试为脱离运行时单测，
**仅 import ``domain.agent.config_policy``**，不依赖 ``application``/
``infrastructure`` 或框架运行时。

覆盖矩阵（与上提前 ``_clamp_max_delegation_depth`` 逐一等价）：

* ``None`` 原样返回（交回 pydantic 用字段默认值）；
* ``0`` / ``-5`` / ``"0"``（可转 int 的 ``<= 0``）归一为 3；
* ``5`` / ``"7"``（可转 int 且 ``> 0``）原样返回；
* ``"abc"`` / 非数字对象（触发 ``TypeError``/``ValueError``）保留原值；
* ``3.9``（float 转 int：``int(3.9) == 3 > 0``）原样返回 ``3.9``（等价性锚点）；
* ``default_max_delegation_depth() == 3``。
"""

import pytest

from domain.agent.config_policy import (
    DEFAULT_MAX_DELEGATION_DEPTH,
    DelegationDepthNormalizationPolicy,
)


def test_default_max_delegation_depth_is_three() -> None:
    assert DEFAULT_MAX_DELEGATION_DEPTH == 3
    assert DelegationDepthNormalizationPolicy.default_max_delegation_depth() == 3


def test_normalize_none_returns_original() -> None:
    """``raw is None`` 分支：原样返回 ``None``，不改动。"""
    assert DelegationDepthNormalizationPolicy.normalize(None) is None


@pytest.mark.parametrize("raw", [0, -5, "0", "-3"])
def test_normalize_non_positive_falls_back_to_default(raw: object) -> None:
    """可转 int 且 ``int(raw) <= 0`` 分支：归一为默认值 3。"""
    assert DelegationDepthNormalizationPolicy.normalize(raw) == 3


@pytest.mark.parametrize("raw", [5, 1, "7", "10"])
def test_normalize_positive_int_preserved(raw: object) -> None:
    """可转 int 且 ``int(raw) > 0`` 分支：原样返回。"""
    assert DelegationDepthNormalizationPolicy.normalize(raw) == raw


def test_normalize_float_above_zero_preserved() -> None:
    """``3.9`` 等价性锚点：``int(3.9) == 3 > 0``，原样返回浮点原值。"""
    assert DelegationDepthNormalizationPolicy.normalize(3.9) == 3.9


@pytest.mark.parametrize("raw", ["abc", object(), [1, 2], {"k": "v"}])
def test_normalize_non_convertible_preserves_original(raw: object) -> None:
    """``int(raw)`` 抛 ``TypeError``/``ValueError`` 分支：吞异常、保留原值。"""
    assert DelegationDepthNormalizationPolicy.normalize(raw) is raw
