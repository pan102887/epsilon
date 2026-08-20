"""聊天继续端口契约测试模块。"""

import inspect

from domain.chat.ports import ChatServicePort


def test_chat_service_port_continue_chat_signature() -> None:
    """验证 ChatServicePort.continue_chat(...) 协议签名。"""
    signature = inspect.signature(ChatServicePort.continue_chat)

    assert list(signature.parameters) == ["self", "request"]
    assert (
        str(signature.parameters["request"].annotation).replace('"', "").replace("'", "")
        == "ChatContinueRequestVO"
    )
    assert str(signature.return_annotation).replace('"', "").replace("'", "") == "ChatResponseVO"


def test_chat_service_port_stream_continue_chat_events_signature() -> None:
    """验证 ChatServicePort.stream_continue_chat_events(...) 协议签名。"""
    signature = inspect.signature(ChatServicePort.stream_continue_chat_events)

    assert list(signature.parameters) == ["self", "request"]
    assert (
        str(signature.parameters["request"].annotation).replace('"', "").replace("'", "")
        == "ChatContinueRequestVO"
    )
    assert (
        str(signature.return_annotation).replace('"', "").replace("'", "")
        == "AsyncIterator[AgentStreamEvent]"
    )
