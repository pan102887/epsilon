"""审批值对象前置校验单元测试（Task 2.3）。

对应 design 测试矩阵 T12 / T13 / T14 / T15：

- T12：``PendingActionRequest(tool_call_id=None, ...)`` 抛 ``InvalidApprovalActionError``
- T13：``PendingActionRequest(tool_call_id="", ...)`` 同上
- T14：``ApprovalDecision(type="approve", tool_call_id="")`` 抛 ``InvalidApprovalActionError``
- T15：合法 ``tool_call_id="call_xxx"`` 正常构造（回归保护）
"""

from __future__ import annotations

import pytest

from domain.agent.exceptions import InvalidApprovalActionError
from domain.agent.value_objects import (
    ApprovalDecision,
    EditedAction,
    PendingActionRequest,
)


def test_t12_pending_action_request_none_id_raises() -> None:
    """T12: tool_call_id=None 抛 InvalidApprovalActionError。"""
    with pytest.raises(InvalidApprovalActionError) as exc_info:
        PendingActionRequest(
            tool_call_id=None,  # type: ignore[arg-type]
            tool_name="web_search",
            arguments="{}",
            allowed_decisions=frozenset({"approve"}),
        )
    exc = exc_info.value
    assert exc.value_object == "PendingActionRequest"
    assert exc.field == "tool_call_id"
    assert exc.raw_value is None
    assert exc.details["tool_name"] == "web_search"


def test_t13_pending_action_request_empty_id_raises() -> None:
    """T13: tool_call_id="" 抛 InvalidApprovalActionError。"""
    with pytest.raises(InvalidApprovalActionError) as exc_info:
        PendingActionRequest(
            tool_call_id="",
            tool_name="web_search",
            arguments="{}",
            allowed_decisions=frozenset({"approve"}),
        )
    exc = exc_info.value
    assert exc.value_object == "PendingActionRequest"
    assert exc.raw_value == ""


def test_t14_approval_decision_empty_id_raises() -> None:
    """T14: ApprovalDecision tool_call_id="" 抛 InvalidApprovalActionError。"""
    with pytest.raises(InvalidApprovalActionError) as exc_info:
        ApprovalDecision(type="approve", tool_call_id="")
    exc = exc_info.value
    assert exc.value_object == "ApprovalDecision"
    assert exc.field == "tool_call_id"
    assert exc.raw_value == ""


def test_t14_approval_decision_none_id_raises() -> None:
    """T14（补充）：ApprovalDecision tool_call_id=None 同样抛错。"""
    with pytest.raises(InvalidApprovalActionError) as exc_info:
        ApprovalDecision(
            type="edit",
            tool_call_id=None,  # type: ignore[arg-type]
            edited_action=EditedAction(name="x", arguments="{}"),
        )
    assert exc_info.value.value_object == "ApprovalDecision"


def test_t15_valid_construction_passes() -> None:
    """T15: 合法 tool_call_id 正常构造（回归保护）。"""
    par = PendingActionRequest(
        tool_call_id="call_xxx",
        tool_name="web_search",
        arguments='{"q": "hi"}',
        allowed_decisions=frozenset({"approve", "reject"}),
    )
    assert par.tool_call_id == "call_xxx"
    assert par.tool_name == "web_search"

    decision = ApprovalDecision(type="approve", tool_call_id="call_xxx")
    assert decision.tool_call_id == "call_xxx"
    assert decision.type == "approve"
