"""测试 model_access 值对象的扩展功能。"""

import pytest

from domain.chat.context import UserMessage
from domain.model_access.value_objects import ChatRequest, ThinkingConfig


def test_thinking_config_default_values():
    """测试 ThinkingConfig 的默认值。"""
    thinking = ThinkingConfig()
    assert thinking.type == "enabled"
    assert thinking.budget_tokens is None


def test_thinking_config_with_budget():
    """测试 ThinkingConfig 带预算限制。"""
    thinking = ThinkingConfig(type="enabled", budget_tokens=3000)
    assert thinking.type == "enabled"
    assert thinking.budget_tokens == 3000


def test_chat_request_with_system_field():
    """测试 ChatRequest 的 system 字段。"""
    request = ChatRequest(
        messages=[UserMessage(content="Hello")],
        system="You are a helpful assistant",
    )
    assert request.system == "You are a helpful assistant"
    assert request.provider is None
    assert request.thinking is None


def test_chat_request_with_provider_field():
    """测试 ChatRequest 的 provider 字段。"""
    request = ChatRequest(
        messages=[UserMessage(content="Hello")],
        provider="claude",
    )
    assert request.provider == "claude"
    assert request.system is None


def test_chat_request_with_thinking_config():
    """测试 ChatRequest 带 Extended Thinking 配置。"""
    thinking = ThinkingConfig(type="enabled", budget_tokens=2000)
    request = ChatRequest(
        messages=[UserMessage(content="Complex question")],
        thinking=thinking,
    )
    assert request.thinking is not None
    assert request.thinking.type == "enabled"
    assert request.thinking.budget_tokens == 2000


def test_chat_request_backward_compatible():
    """测试向后兼容性：不提供新字段应正常工作。"""
    request = ChatRequest(
        messages=[UserMessage(content="Hello")],
        temperature=0.7,
        max_tokens=1000,
    )
    assert request.system is None
    assert request.provider is None
    assert request.thinking is None
    assert request.temperature == 0.7
    assert request.max_tokens == 1000


def test_chat_request_all_new_fields():
    """测试同时使用所有新字段。"""
    thinking = ThinkingConfig(type="enabled", budget_tokens=5000)
    request = ChatRequest(
        messages=[UserMessage(content="Test")],
        model="claude-3-5-sonnet-20241022",
        temperature=0.8,
        max_tokens=2000,
        system="Custom system prompt",
        provider="claude",
        thinking=thinking,
        extra_params={"top_p": 0.9},
    )

    assert request.model == "claude-3-5-sonnet-20241022"
    assert request.temperature == 0.8
    assert request.max_tokens == 2000
    assert request.system == "Custom system prompt"
    assert request.provider == "claude"
    assert request.thinking.type == "enabled"
    assert request.thinking.budget_tokens == 5000
    assert request.extra_params == {"top_p": 0.9}


def test_chat_request_validation_still_works():
    """测试 ChatRequest 的验证逻辑仍然有效。"""
    # 空 messages
    with pytest.raises(ValueError, match="messages 不能为空"):
        ChatRequest(messages=[])

    # 无效的 temperature
    with pytest.raises(ValueError, match=r"temperature 必须在 0.0-2.0 之间"):
        ChatRequest(
            messages=[UserMessage(content="test")],
            temperature=3.0,
        )

    # 无效的 max_tokens
    with pytest.raises(ValueError, match="max_tokens 必须大于 0"):
        ChatRequest(
            messages=[UserMessage(content="test")],
            max_tokens=-1,
        )

    # messages 元素必须为 BaseMessage 子类（不再接受 OpenAI 协议字典）
    with pytest.raises(
        ValueError,
        match=r"messages\[0\] 必须为 BaseMessage 子类实例",
    ):
        ChatRequest(messages=[{"role": "user", "content": "hi"}])  # type: ignore[list-item]
