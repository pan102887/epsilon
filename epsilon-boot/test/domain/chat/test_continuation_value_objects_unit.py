"""聊天继续值对象与异常单元测试模块。"""

import pytest

from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.value_objects import ChatContinueRequestVO, ChatResponseVO


def test_chat_response_continuation_defaults_are_compatible() -> None:
    """验证 ChatResponseVO 新增字段默认值兼容既有构造方式。"""
    response = ChatResponseVO(
        session_id="s1",
        reply="hello",
        model="gpt-test",
        usage={},
        prompt_id="chat-default@v1",
    )

    assert response.status == "completed"
    assert response.terminated_reason == "completed"
    assert response.can_continue is False


def test_chat_response_accepts_paused_state() -> None:
    """验证 ChatResponseVO 可以表达暂停态与可继续标记。"""
    response = ChatResponseVO(
        session_id="s1",
        reply="",
        model="gpt-test",
        usage={"total_tokens": 12},
        prompt_id="chat-default@v1",
        status="paused",
        terminated_reason="max_rounds",
        can_continue=True,
    )

    assert response.status == "paused"
    assert response.terminated_reason == "max_rounds"
    assert response.can_continue is True


def test_chat_continue_request_rejects_empty_session_id() -> None:
    """验证聊天继续请求拒绝空 session_id。"""
    with pytest.raises(ValueError, match="session_id"):
        ChatContinueRequestVO(session_id="")


def test_chat_continue_request_accepts_optional_fields() -> None:
    """验证聊天继续请求保留 stream 与 model 参数。"""
    request = ChatContinueRequestVO(
        session_id="s1",
        stream=True,
        model="gpt-test",
    )

    assert request.session_id == "s1"
    assert request.stream is True
    assert request.model == "gpt-test"


def test_continuation_unavailable_error_fields() -> None:
    """验证继续不可用异常携带稳定业务码和上下文字段。"""
    exc = ContinuationUnavailableError("s1", "最新消息不是工具结果")

    assert exc.code == 60041
    assert exc.session_id == "s1"
    assert exc.reason == "最新消息不是工具结果"
    assert exc.message == "当前会话不可继续执行：最新消息不是工具结果"
