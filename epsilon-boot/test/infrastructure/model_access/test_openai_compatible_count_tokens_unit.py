"""OpenAICompatibleAdapter.count_tokens 单元测试。"""

import pytest

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from infrastructure.model_access.provider_config import ProviderConfig


def _make_adapter(encoding: str | None = None) -> OpenAICompatibleAdapter:
    """构造仅用于 count_tokens 的最小 adapter。"""
    config = ProviderConfig(
        provider_name="test",
        api_base="http://localhost:1234",
        api_key="fake",
    )
    return OpenAICompatibleAdapter(config, tokenizer_encoding=encoding)


class TestCountTokens:
    """count_tokens 方法行为验证。"""

    def test_empty_list_returns_zero(self) -> None:
        """空消息列表返回 0。"""
        adapter = _make_adapter()
        assert adapter.count_tokens([]) == 0

    def test_pure_text_messages_is_positive_int(self) -> None:
        """纯文本消息应返回正整数。"""
        adapter = _make_adapter()
        messages: list[BaseMessage] = [
            SystemMessage(content="你是一个助手"),
            UserMessage(content="你好"),
        ]
        result = adapter.count_tokens(messages)
        assert isinstance(result, int)
        assert result > 0

    def test_with_tool_calls_is_positive_int(self) -> None:
        """携带 tool_calls 的消息列表应返回正整数。"""
        adapter = _make_adapter()
        messages: list[BaseMessage] = [
            UserMessage(content="查天气"),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCallRequest(id="call_1", name="get_weather", arguments='{"city": "北京"}'),
                ],
            ),
            ToolMessage(content="晴，25°C", tool_call_id="call_1", tool_name="get_weather"),
        ]
        result = adapter.count_tokens(messages)
        assert isinstance(result, int)
        assert result > 0

    def test_invalid_encoding_raises_configuration_error(self) -> None:
        """非法 encoding 名称应在构造期抛出 ConfigurationError。"""
        from common.configuration import ConfigurationError

        with pytest.raises(ConfigurationError, match="非法或不可用"):
            _make_adapter(encoding="nonexistent_encoding_xyz")

    def test_message_list_equals_sum_of_individual_messages(self) -> None:
        """列表 token 数等于逐条消息 token 数之和。"""
        adapter = _make_adapter()
        messages: list[BaseMessage] = [
            SystemMessage(content="system"),
            UserMessage(content="hello"),
            AssistantMessage(content="world"),
        ]
        total = adapter.count_tokens(messages)
        individual_sum = sum(adapter.count_tokens([m]) for m in messages)
        assert total == individual_sum
