"""异常 ``isinstance`` 互斥用例（Task 7.2）。

对应 design 测试矩阵 T18 / requirement R5.3 / R6.1：

- ``InvalidToolCallIdError(...)`` 实例：``isinstance(exc, ModelAccessError)``
  与 ``isinstance(exc, InvalidToolCallIdError)`` 均为 ``True``，
  ``isinstance(exc, InvalidApprovalActionError)`` 为 ``False``
- ``InvalidApprovalActionError(...)`` 实例：``isinstance(exc, BizException)``
  与 ``isinstance(exc, InvalidApprovalActionError)`` 均为 ``True``，
  ``isinstance(exc, ModelAccessError)`` 与
  ``isinstance(exc, InvalidToolCallIdError)`` 均为 ``False``
"""

from __future__ import annotations

from common.exceptions import BizException
from domain.agent.exceptions import InvalidApprovalActionError
from domain.model_access.exceptions import (
    InvalidToolCallIdError,
    ModelAccessError,
)


def test_invalid_tool_call_id_error_isinstance_chain() -> None:
    """InvalidToolCallIdError 命中 ModelAccessError；不命中 InvalidApprovalActionError。"""
    exc = InvalidToolCallIdError(source="chat_sync", raw_id_value=None)
    assert isinstance(exc, ModelAccessError)
    assert isinstance(exc, InvalidToolCallIdError)
    assert isinstance(exc, BizException)
    assert not isinstance(exc, InvalidApprovalActionError)


def test_invalid_approval_action_error_isinstance_chain() -> None:
    """InvalidApprovalActionError 命中 BizException；
    不命中 ModelAccessError / InvalidToolCallIdError。
    """
    exc = InvalidApprovalActionError(
        value_object="ApprovalDecision",
        field="tool_call_id",
        raw_value="",
    )
    assert isinstance(exc, BizException)
    assert isinstance(exc, InvalidApprovalActionError)
    assert not isinstance(exc, ModelAccessError)
    assert not isinstance(exc, InvalidToolCallIdError)
