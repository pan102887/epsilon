"""ApprovalResumePrecondition 领域服务单元测试（脱离运行时）。

追溯 需求 5（AC5.2 / AC5.3）与 design Property 4：验证审批决策集合的
数量匹配、顺序（tool_call_id 对齐）、决策类型属于 allowed_decisions 三类
前置校验，以及全部合法时无异常。断言各异常携带的参数与既有内联校验逐字段
等价。本测试仅 import ``domain.*`` 并构造领域值对象作输入，不触碰运行时。
"""

import pytest

from domain.agent.exceptions import (
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
)
from domain.agent.value_objects import ApprovalDecision, PendingActionRequest
from domain.task.policy import ApprovalResumePrecondition


def _action(
    tool_call_id: str,
    *,
    tool_name: str = "shell",
    allowed: frozenset[str] = frozenset({"approve", "reject"}),
) -> PendingActionRequest:
    """构造待审批动作值对象辅助函数。"""
    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments="{}",
        allowed_decisions=allowed,  # type: ignore[arg-type]
    )


def _decision(tool_call_id: str, *, decision_type: str = "approve") -> ApprovalDecision:
    """构造审批决策值对象辅助函数。"""
    return ApprovalDecision(
        type=decision_type,  # type: ignore[arg-type]
        tool_call_id=tool_call_id,
    )


def test_all_valid_returns_none() -> None:
    """全部合法（数量/顺序/类型均满足）时无异常，返回 None。"""
    actions = [_action("call-1"), _action("call-2")]
    decisions = [_decision("call-1"), _decision("call-2")]
    assert ApprovalResumePrecondition.check(actions, decisions) is None


def test_count_mismatch_raises() -> None:
    """决策数量与动作数量不一致时抛出 CountMismatchError，参数为 (期望, 实际)。"""
    actions = [_action("call-1"), _action("call-2")]
    decisions = [_decision("call-1")]
    with pytest.raises(ApprovalDecisionCountMismatchError) as exc_info:
        ApprovalResumePrecondition.check(actions, decisions)
    assert exc_info.value.expected_count == 2
    assert exc_info.value.actual_count == 1


def test_order_mismatch_raises() -> None:
    """决策 tool_call_id 与动作不对齐时抛出 OrderMismatchError，参数为 (期望, 实际)。"""
    actions = [_action("call-1"), _action("call-2")]
    decisions = [_decision("call-1"), _decision("call-X")]
    with pytest.raises(ApprovalDecisionOrderMismatchError) as exc_info:
        ApprovalResumePrecondition.check(actions, decisions)
    assert exc_info.value.expected_tool_call_id == "call-2"
    assert exc_info.value.actual_tool_call_id == "call-X"


def test_not_allowed_raises() -> None:
    """决策类型不在 allowed_decisions 内时抛出 NotAllowedError，参数为 tool_name/type/allowed。"""
    actions = [
        _action("call-1", tool_name="shell", allowed=frozenset({"approve"})),
    ]
    decisions = [_decision("call-1", decision_type="reject")]
    with pytest.raises(ApprovalDecisionNotAllowedError) as exc_info:
        ApprovalResumePrecondition.check(actions, decisions)
    assert exc_info.value.tool_name == "shell"
    assert exc_info.value.decision_type == "reject"
    assert exc_info.value.allowed_decisions == frozenset({"approve"})
