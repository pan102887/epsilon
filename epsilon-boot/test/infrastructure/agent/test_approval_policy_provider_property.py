"""审批策略提供器属性测试模块。"""

import json

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.exceptions import HitlConfigInvalidError
from infrastructure.agent.approval_policy_provider import StaticApprovalPolicyProvider

valid_decisions_st = st.lists(
    st.sampled_from(["approve", "edit", "reject"]),
    min_size=1,
    max_size=4,
    unique=True,
)


@settings(max_examples=100, deadline=5000)
@given(decisions=valid_decisions_st)
def test_valid_decision_order_does_not_affect_policy_set(decisions: list[str]) -> None:
    """验证合法决策数组顺序不影响策略集合。"""
    raw = json.dumps({"custom_tool": decisions})

    policy = StaticApprovalPolicyProvider(True, raw).policy_for("custom_tool")

    assert policy.interrupt is True
    assert policy.allowed_decisions == frozenset(decisions)


@settings(max_examples=100, deadline=5000)
@given(
    decision=st.text(min_size=1, max_size=20).filter(
        lambda v: v not in {"approve", "edit", "reject"}
    )
)
def test_invalid_decision_fails_fast(decision: str) -> None:
    """验证非法决策 fail-fast。"""
    raw = json.dumps({"custom_tool": [decision]})

    with pytest.raises(HitlConfigInvalidError):
        StaticApprovalPolicyProvider(True, raw)
