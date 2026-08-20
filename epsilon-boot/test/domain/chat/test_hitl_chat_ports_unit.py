"""HITL 聊天端口契约测试模块。"""

import inspect

from domain.chat.ports import ChatServicePort


def test_chat_service_port_resume_approval_signature() -> None:
    """验证 ChatServicePort.resume_approval(...) 协议签名。"""
    signature = inspect.signature(ChatServicePort.resume_approval)

    assert list(signature.parameters) == ["self", "request"]
    assert (
        str(signature.parameters["request"].annotation).replace('"', "").replace("'", "")
        == "ApprovalResumeRequestVO"
    )
    assert str(signature.return_annotation).replace('"', "").replace("'", "") == "ChatResponseVO"
