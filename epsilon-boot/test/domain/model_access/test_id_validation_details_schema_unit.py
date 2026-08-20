"""异常 details schema 对齐用例（Task 7.1）。

对应 design 测试矩阵 T17 / requirement R5.1 / R5.4：分别构造 4 类
抛出实例，断言每个异常 ``set(exc.details.keys()) >= 统一字段集``，且
不适用字段值为 ``None``（**键存在**）。
"""

from __future__ import annotations

from domain.agent.exceptions import InvalidApprovalActionError
from domain.model_access.exceptions import InvalidToolCallIdError

_UNIFIED_KEYS = {
    "source",
    "provider",
    "model",
    "tool_name",
    "tool_call_index",
    "raw_id_value",
}


def test_chat_sync_details_keys_complete() -> None:
    exc = InvalidToolCallIdError(
        source="chat_sync",
        raw_id_value=None,
        provider="deepseek",
        model="deepseek-chat",
        tool_name="x",
        tool_call_index=0,
    )
    assert set(exc.details.keys()) >= _UNIFIED_KEYS


def test_stream_finished_details_keys_complete_with_none_for_inapplicable() -> None:
    """stream_finished 链路 provider 不适用，键存在但值为 None。"""
    exc = InvalidToolCallIdError(
        source="stream_finished",
        raw_id_value="",
        model="m",
        tool_name="x",
        tool_call_index=1,
    )
    assert set(exc.details.keys()) >= _UNIFIED_KEYS
    assert exc.details["provider"] is None  # 不适用字段必须键存在 + None


def test_history_restore_details_keys_complete() -> None:
    exc = InvalidToolCallIdError(
        source="history_restore",
        raw_id_value=None,
        extra={"skipped_count": 1, "session_id": "s"},
    )
    assert set(exc.details.keys()) >= _UNIFIED_KEYS
    assert exc.details["provider"] is None
    assert exc.details["model"] is None
    assert exc.details["tool_name"] is None
    assert exc.details["tool_call_index"] is None


def test_approval_resume_details_keys_complete_with_audit_fields() -> None:
    """approval_resume 链路 provider/model/tool_call_index 不适用，键存在 + None；
    并补充审批侧专用字段 value_object / field。"""
    exc = InvalidApprovalActionError(
        value_object="ApprovalDecision",
        field="tool_call_id",
        raw_value="",
    )
    assert set(exc.details.keys()) >= _UNIFIED_KEYS
    assert exc.details["provider"] is None
    assert exc.details["model"] is None
    assert exc.details["tool_call_index"] is None
    # 审批侧专用字段
    assert exc.details["value_object"] == "ApprovalDecision"
    assert exc.details["field"] == "tool_call_id"
