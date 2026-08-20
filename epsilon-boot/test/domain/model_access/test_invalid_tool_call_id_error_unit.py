"""``InvalidToolCallIdError`` 单元测试（Task 1.3）。

覆盖 design §异常体系设计 与 requirement R1.2 / R5.1 / R6.1：

- 默认字段填 ``None`` 不省略键；
- ``extra`` 合并；
- ``code == 50007``；
- ``message`` 含 ``source`` 与 ``raw_id_value``；
- 可被 ``isinstance(exc, ModelAccessError)`` 命中。
"""

from __future__ import annotations

from domain.model_access.exceptions import InvalidToolCallIdError, ModelAccessError


def test_default_fields_filled_none_not_omitted() -> None:
    """缺省 provider / model / tool_name / tool_call_index 时键存在但值为 None。"""
    exc = InvalidToolCallIdError(source="chat_sync", raw_id_value=None)
    assert exc.code == 50007
    assert isinstance(exc, ModelAccessError)
    expected_keys = {
        "source",
        "provider",
        "model",
        "tool_name",
        "tool_call_index",
        "raw_id_value",
    }
    assert set(exc.details.keys()) >= expected_keys
    assert exc.details["provider"] is None
    assert exc.details["model"] is None
    assert exc.details["tool_name"] is None
    assert exc.details["tool_call_index"] is None
    assert exc.details["source"] == "chat_sync"
    assert exc.details["raw_id_value"] is None


def test_full_fields_propagate_into_details() -> None:
    """全字段构造时 details 应当如实承载。"""
    exc = InvalidToolCallIdError(
        source="stream_finished",
        raw_id_value="",
        provider="zhipu",
        model="glm-4-plus",
        tool_name="web_search",
        tool_call_index=2,
    )
    assert exc.details == {
        "source": "stream_finished",
        "provider": "zhipu",
        "model": "glm-4-plus",
        "tool_name": "web_search",
        "tool_call_index": 2,
        "raw_id_value": "",
    }


def test_extra_merged_into_details() -> None:
    """``extra`` 字段（如 ``skipped_count`` / ``session_id``）应合并进 details。"""
    exc = InvalidToolCallIdError(
        source="history_restore",
        raw_id_value=None,
        extra={"skipped_count": 3, "session_id": "sess-xxx"},
    )
    assert exc.details["skipped_count"] == 3
    assert exc.details["session_id"] == "sess-xxx"
    assert exc.details["source"] == "history_restore"


def test_message_contains_source_and_raw_id_value() -> None:
    """``message`` 字面应包含 source 与 raw_id_value 摘要。"""
    exc = InvalidToolCallIdError(source="chat_sync", raw_id_value="")
    assert "chat_sync" in exc.message
    assert "''" in exc.message  # repr('') == "''"


def test_isinstance_model_access_error() -> None:
    """异常应可被 ``isinstance(exc, ModelAccessError)`` 命中。"""
    exc = InvalidToolCallIdError(source="chat_sync", raw_id_value=None)
    assert isinstance(exc, ModelAccessError)
    assert isinstance(exc, InvalidToolCallIdError)
