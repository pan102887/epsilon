"""OpenAICompatibleAdapter 重试集成单元测试。

通过 mock AsyncOpenAI.chat.completions.create 验证：
- chat 重试 + 成功
- chat 重试达上限抛 ModelTimeoutError
- stream 首次握手抛异常 → 重试
- stream yield 后中途异常不重试
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APITimeoutError

from domain.chat.context import UserMessage
from domain.model_access.exceptions import ModelTimeoutError
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from infrastructure.model_access.provider_config import ProviderConfig


def _make_config() -> ProviderConfig:
    """构造最小化 ProviderConfig。"""
    return ProviderConfig(
        provider_name="test",
        api_key="sk-test",
        api_base="http://localhost:9999/v1",
        enabled=True,
        default_model="gpt-test",
        models="gpt-test",
        temperature=0.7,
        max_tokens=100,
        timeout=5.0,
        max_retries=0,
        max_connections=10,
        max_keepalive_connections=5,
    )


def _make_chat_completion(content: str = "hello"):
    """构造 mock ChatCompletion 对象。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock()
    usage.prompt_tokens = 5
    usage.completion_tokens = 3
    usage.total_tokens = 8
    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = usage
    completion.model = "gpt-test"
    return completion


class TestChatRetry:
    """chat() 方法重试验证。"""

    @pytest.mark.asyncio
    async def test_chat_retry_succeeds(self):
        """第一次超时，第二次成功。"""
        config = _make_config()
        adapter = OpenAICompatibleAdapter(config, retry_attempts=3)

        mock_create = AsyncMock(
            side_effect=[APITimeoutError(request=MagicMock()), _make_chat_completion("ok")]
        )
        adapter._client.chat.completions.create = mock_create

        request = ChatRequest(messages=[UserMessage(content="hi")])
        result = await adapter.chat(request)
        assert result.content == "ok"
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_chat_retry_exhausted(self):
        """连续超时达上限。"""
        config = _make_config()
        adapter = OpenAICompatibleAdapter(config, retry_attempts=2)

        mock_create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
        adapter._client.chat.completions.create = mock_create

        request = ChatRequest(messages=[UserMessage(content="hi")])
        with pytest.raises(ModelTimeoutError):
            await adapter.chat(request)
        assert mock_create.call_count == 2


class TestStreamRetry:
    """stream() 方法重试验证。"""

    @pytest.mark.asyncio
    async def test_stream_handshake_retry_succeeds(self):
        """首次握手失败，重试后成功。"""
        config = _make_config()
        adapter = OpenAICompatibleAdapter(config, retry_attempts=3)

        # 构造 async iterator mock
        chunk = MagicMock()
        chunk.choices = []
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = 1
        chunk.usage.completion_tokens = 2
        chunk.usage.total_tokens = 3

        async def fake_stream():
            yield chunk

        mock_create = AsyncMock(side_effect=[APITimeoutError(request=MagicMock()), fake_stream()])
        adapter._client.chat.completions.create = mock_create

        request = ChatRequest(messages=[UserMessage(content="hi")])
        chunks = []
        async for c in adapter.stream(request):
            chunks.append(c)

        assert mock_create.call_count == 2
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_stream_mid_iteration_no_retry(self):
        """stream yield 后中途异常不触发重试（仅握手重试）。"""
        config = _make_config()
        adapter = OpenAICompatibleAdapter(config, retry_attempts=3)

        # 构造一个会在迭代中抛异常的 async iterator
        async def failing_stream():
            raise RuntimeError("mid-stream failure")
            yield  # pragma: no cover - 使其为 async generator

        mock_create = AsyncMock(return_value=failing_stream())
        adapter._client.chat.completions.create = mock_create

        request = ChatRequest(messages=[UserMessage(content="hi")])
        with pytest.raises(RuntimeError, match="mid-stream failure"):
            async for _ in adapter.stream(request):
                pass

        # 只调用一次 create（握手成功了，中途异常不重试）
        assert mock_create.call_count == 1
