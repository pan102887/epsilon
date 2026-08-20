"""``InvalidApprovalActionError`` 单元测试（Task 1.4）。

覆盖 design §异常体系设计 与 requirement R4.4 / R5.3 / R6.1：

- ``code == 60040``；
- ``details["source"] == "approval_resume"``；
- ``value_object`` / ``field`` / ``raw_value`` 属性可读；
- ``isinstance(exc, BizException)`` 真，``isinstance(exc, ModelAccessError)`` 假。
"""

from __future__ import annotations

from common.exceptions import BizException
from domain.agent.exceptions import InvalidApprovalActionError
from domain.model_access.exceptions import ModelAccessError


def test_code_and_details_source() -> None:
    """错误码 60040 且 details.source 固定为 approval_resume。"""
    exc = InvalidApprovalActionError(
        value_object="ApprovalDecision",
        field="tool_call_id",
        raw_value="",
    )
    assert exc.code == 60040
    assert exc.details["source"] == "approval_resume"


def test_attributes_readable() -> None:
    """``value_object`` / ``field`` / ``raw_value`` 应作为实例属性暴露。"""
    exc = InvalidApprovalActionError(
        value_object="PendingActionRequest",
        field="tool_call_id",
        raw_value=None,
        tool_name="web_search",
    )
    assert exc.value_object == "PendingActionRequest"
    assert exc.field == "tool_call_id"
    assert exc.raw_value is None
    assert exc.details["tool_name"] == "web_search"
    assert exc.details["raw_id_value"] is None


def test_isinstance_disjoint_from_model_access_error() -> None:
    """归属 BizException 但与 ModelAccessError 互不继承。"""
    exc = InvalidApprovalActionError(
        value_object="ApprovalDecision",
        field="tool_call_id",
        raw_value="",
    )
    assert isinstance(exc, BizException)
    assert isinstance(exc, InvalidApprovalActionError)
    assert not isinstance(exc, ModelAccessError)


def test_message_contains_value_object_and_field() -> None:
    """message 字面包含 value_object.field 与 raw_value 摘要。"""
    exc = InvalidApprovalActionError(
        value_object="PendingActionRequest",
        field="tool_call_id",
        raw_value="",
    )
    assert "PendingActionRequest" in exc.message
    assert "tool_call_id" in exc.message
    assert "''" in exc.message


def test_default_tool_name_none() -> None:
    """未传 tool_name 时 details.tool_name 为 None 且键存在。"""
    exc = InvalidApprovalActionError(
        value_object="ApprovalDecision",
        field="tool_call_id",
        raw_value=None,
    )
    assert "tool_name" in exc.details
    assert exc.details["tool_name"] is None
    assert exc.details["provider"] is None
    assert exc.details["model"] is None
    assert exc.details["tool_call_index"] is None
