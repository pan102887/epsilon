"""Preservation 属性测试：验证非 APIConnectionError 异常路径行为不变。

本模块通过属性测试（Hypothesis）验证 OpenAICompatibleAdapter 的 chat() 和 stream()
方法在以下场景中的行为在修复 APIConnectionError 处理后保持不变：

- 正常响应路径：chat() 返回 LLMResponse，stream() 产出 StreamingChunk
- APITimeoutError 路径：被捕获并转换为 ModelTimeoutError
- RateLimitError 路径：被捕获并转换为 ModelRateLimitError
- APIError（HTTP 4xx/5xx）路径：被捕获并转换为 ModelAccessError 且包含 status_code

这些测试在未修复代码上应全部通过，确认基线行为。修复后重新运行仍应通过，
确认修复未引入回归。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from openai import APIStatusError, APITimeoutError, RateLimitError

from domain.chat.context import UserMessage
from domain.model_access.exceptions import (
    ModelAccessError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from domain.model_access.value_objects import ChatRequest, LLMResponse
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter

# ---------------------------------------------------------------------------
# Hypothesis 策略
# ---------------------------------------------------------------------------

_response_content = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda s: s.strip())
"""生成非空的响应文本内容。"""

_model_names = st.sampled_from(
    [
        "gpt-4",
        "gpt-3.5-turbo",
        "glm-4",
        "qwen-turbo",
        "deepseek-chat",
    ]
)
"""生成常见的模型名称。"""

_http_status_codes = st.sampled_from([400, 401, 403, 404, 500, 502, 503])
"""生成常见的 HTTP 错误状态码（排除 429，因为 429 由 RateLimitError 处理）。"""

_error_messages = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=3,
    max_size=100,
).filter(lambda s: s.strip())
"""生成非空的错误消息文本。"""

_retry_after_values = st.one_of(
    st.just(None),
    st.integers(min_value=1, max_value=300).map(str),
)
"""生成 retry-after 头部值：None 或字符串形式的秒数。"""


# ---------------------------------------------------------------------------
# 辅助函数：构造测试用的适配器、请求和 Mock 对象
# ---------------------------------------------------------------------------


def _make_adapter() -> OpenAICompatibleAdapter:
    """构造一个用于测试的 OpenAICompatibleAdapter 实例。

    使用 Mock 的 ProviderConfig 避免真实的网络连接和配置依赖。

    Returns:
        配置了 Mock 参数的 OpenAICompatibleAdapter 实例。
    """
    mock_config = MagicMock()
    mock_config.api_key = "test-key"
    mock_config.api_base = "https://fake-api.example.com/v1"
    mock_config.timeout = 30
    mock_config.max_retries = 0
    mock_config.max_connections = 10
    mock_config.max_keepalive_connections = 5
    mock_config.provider_name = "test-provider"
    mock_config.default_model = "test-model"
    mock_config.temperature = 0.7
    mock_config.max_tokens = 4096
    return OpenAICompatibleAdapter(mock_config)


def _make_chat_request() -> ChatRequest:
    """构造一个最小化的 ChatRequest 用于测试。

    Returns:
        包含单条用户消息的 ChatRequest 实例。
    """
    return ChatRequest(messages=[UserMessage(content="hello")])


def _make_mock_completion(content: str, model: str = "test-model") -> MagicMock:
    """构造一个模拟的 OpenAI ChatCompletion 响应对象。

    模拟 SDK 返回的完整 completion 对象结构，包含 choices、message、usage 等字段。

    Args:
        content: 模型回复的文本内容。
        model: 模型名称，默认为 "test-model"。

    Returns:
        模拟的 ChatCompletion 对象。
    """
    mock_message = MagicMock()
    mock_message.content = content
    mock_message.tool_calls = None

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 20
    mock_usage.total_tokens = 30

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_completion.model = model
    mock_completion.usage = mock_usage

    return mock_completion


def _make_mock_request() -> httpx.Request:
    """构造一个模拟的 httpx.Request 对象。

    Returns:
        指向测试 API 端点的 POST 请求对象。
    """
    return httpx.Request("POST", "https://fake-api.example.com/v1/chat/completions")


def _make_mock_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """构造一个模拟的 httpx.Response 对象。

    Args:
        status_code: HTTP 状态码。
        headers: 可选的响应头字典。

    Returns:
        带有指定状态码和请求的 httpx.Response 对象。
    """
    resp = httpx.Response(
        status_code=status_code,
        request=_make_mock_request(),
        headers=headers or {},
    )
    return resp


# ---------------------------------------------------------------------------
# Property A: 正常响应 — chat() 返回 LLMResponse.content 与 Mock 响应一致
# ---------------------------------------------------------------------------


class TestPreservationNormalResponse:
    """属性测试：正常响应路径在修复后行为不变。

    Property A: 对于任意正常响应内容，chat() 返回的 LLMResponse.content
    与 Mock 的 completion 响应内容一致。

    在未修复代码和修复后代码上均应通过，确认正常路径不受影响。

    **Validates: Requirements 3.1**
    """

    @settings(max_examples=50, deadline=None)
    @given(content=_response_content)
    @pytest.mark.asyncio
    async def test_chat_returns_llm_response_with_matching_content(self, content: str) -> None:
        """chat() 正常调用应返回 LLMResponse，其 content 与 Mock 响应一致。

        对于任意非空响应文本，验证适配器正确将 SDK 的 completion 对象
        转换为领域层 LLMResponse，且内容不丢失、不篡改。
        """
        adapter = _make_adapter()
        request = _make_chat_request()
        mock_completion = _make_mock_completion(content)

        adapter._client.chat.completions.create = AsyncMock(return_value=mock_completion)

        response = await adapter.chat(request)

        assert isinstance(response, LLMResponse), (
            f"chat() 应返回 LLMResponse，实际返回 {type(response).__name__}"
        )
        assert response.content == content, (
            f"LLMResponse.content 应为 {content!r}，实际为 {response.content!r}"
        )


# ---------------------------------------------------------------------------
# Property B: APITimeoutError → ModelTimeoutError
# ---------------------------------------------------------------------------


class TestPreservationTimeoutError:
    """属性测试：APITimeoutError 异常路径在修复后行为不变。

    Property B: 对于任意超时场景，APITimeoutError 被捕获并转换为 ModelTimeoutError。

    在未修复代码和修复后代码上均应通过，确认超时异常处理不受影响。

    **Validates: Requirements 3.2**
    """

    @settings(max_examples=30, deadline=None)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_chat_converts_api_timeout_to_model_timeout(self, data: st.DataObject) -> None:
        """chat() 应将 APITimeoutError 转换为 ModelTimeoutError。

        APITimeoutError 由 SDK 在请求超时时抛出，适配器应捕获并转换为
        领域层 ModelTimeoutError，保持现有行为不变。
        """
        adapter = _make_adapter()
        request = _make_chat_request()
        mock_request = _make_mock_request()
        error = APITimeoutError(request=mock_request)

        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(ModelTimeoutError):
            await adapter.chat(request)

    @settings(max_examples=30, deadline=None)
    @given(data=st.data())
    @pytest.mark.asyncio
    async def test_stream_converts_api_timeout_to_model_timeout(self, data: st.DataObject) -> None:
        """stream() 应将 APITimeoutError 转换为 ModelTimeoutError。

        与 chat() 相同的超时异常转换逻辑，验证流式调用路径行为一致。
        """
        adapter = _make_adapter()
        request = _make_chat_request()
        mock_request = _make_mock_request()
        error = APITimeoutError(request=mock_request)

        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(ModelTimeoutError):
            async for _ in adapter.stream(request):
                pass


# ---------------------------------------------------------------------------
# Property C: RateLimitError → ModelRateLimitError
# ---------------------------------------------------------------------------


class TestPreservationRateLimitError:
    """属性测试：RateLimitError 异常路径在修复后行为不变。

    Property C: 对于任意 retry-after 值，RateLimitError 被捕获并转换为
    ModelRateLimitError。

    在未修复代码和修复后代码上均应通过，确认速率限制异常处理不受影响。

    **Validates: Requirements 3.3**
    """

    @settings(max_examples=50, deadline=None)
    @given(retry_after=_retry_after_values)
    @pytest.mark.asyncio
    async def test_chat_converts_rate_limit_to_model_rate_limit(
        self, retry_after: str | None
    ) -> None:
        """chat() 应将 RateLimitError 转换为 ModelRateLimitError。

        RateLimitError（HTTP 429）由 SDK 在触发速率限制时抛出。
        适配器应捕获并转换为领域层 ModelRateLimitError，
        可选地携带 retry-after 信息。
        """
        adapter = _make_adapter()
        request = _make_chat_request()

        headers = {}
        if retry_after is not None:
            headers["retry-after"] = retry_after

        mock_response = _make_mock_response(429, headers=headers)
        error = RateLimitError(
            message="Rate limit exceeded",
            response=mock_response,
            body=None,
        )

        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(ModelRateLimitError):
            await adapter.chat(request)

    @settings(max_examples=50, deadline=None)
    @given(retry_after=_retry_after_values)
    @pytest.mark.asyncio
    async def test_stream_converts_rate_limit_to_model_rate_limit(
        self, retry_after: str | None
    ) -> None:
        """stream() 应将 RateLimitError 转换为 ModelRateLimitError。

        与 chat() 相同的速率限制异常转换逻辑，验证流式调用路径行为一致。
        """
        adapter = _make_adapter()
        request = _make_chat_request()

        headers = {}
        if retry_after is not None:
            headers["retry-after"] = retry_after

        mock_response = _make_mock_response(429, headers=headers)
        error = RateLimitError(
            message="Rate limit exceeded",
            response=mock_response,
            body=None,
        )

        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(ModelRateLimitError):
            async for _ in adapter.stream(request):
                pass


# ---------------------------------------------------------------------------
# Property D: APIError (HTTP 4xx/5xx) → ModelAccessError with status_code
# ---------------------------------------------------------------------------


class TestPreservationAPIError:
    """属性测试：APIError（HTTP 4xx/5xx）异常路径在修复后行为不变。

    Property D: 对于任意 HTTP 状态码和错误消息，APIError（即 APIStatusError）
    被捕获并转换为 ModelAccessError，且 details 包含 status_code。

    注意：这里使用 APIStatusError 构造异常，因为它是具有 status_code 属性的
    APIError 子类，代表真实的 HTTP 错误响应场景。

    在未修复代码和修复后代码上均应通过，确认 API 错误异常处理不受影响。

    **Validates: Requirements 3.4**
    """

    @settings(max_examples=50, deadline=None)
    @given(
        status_code=_http_status_codes,
        error_message=_error_messages,
    )
    @pytest.mark.asyncio
    async def test_chat_converts_api_error_to_model_access_error_with_status_code(
        self, status_code: int, error_message: str
    ) -> None:
        """chat() 应将 APIStatusError 转换为 ModelAccessError 且包含 status_code。

        当模型服务返回 HTTP 4xx/5xx 错误时，SDK 抛出 APIStatusError（APIError 子类）。
        适配器应捕获并转换为领域层 ModelAccessError，details 中包含 status_code
        以便上层代码识别具体的 HTTP 错误类型。
        """
        adapter = _make_adapter()
        request = _make_chat_request()

        mock_response = _make_mock_response(status_code)
        error = APIStatusError(
            message=error_message,
            response=mock_response,
            body=None,
        )

        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(ModelAccessError) as exc_info:
            await adapter.chat(request)

        # 确保不是更具体的子类异常（ModelTimeoutError 或 ModelRateLimitError）
        assert not isinstance(exc_info.value, ModelTimeoutError), "不应转换为 ModelTimeoutError"
        assert not isinstance(exc_info.value, ModelRateLimitError), "不应转换为 ModelRateLimitError"
        assert exc_info.value.details.get("status_code") == status_code, (
            f"ModelAccessError.details['status_code'] 应为 {status_code}，"
            f"实际为 {exc_info.value.details.get('status_code')}"
        )

    @settings(max_examples=50, deadline=None)
    @given(
        status_code=_http_status_codes,
        error_message=_error_messages,
    )
    @pytest.mark.asyncio
    async def test_stream_converts_api_error_to_model_access_error_with_status_code(
        self, status_code: int, error_message: str
    ) -> None:
        """stream() 应将 APIStatusError 转换为 ModelAccessError 且包含 status_code。

        与 chat() 相同的 API 错误异常转换逻辑，验证流式调用路径行为一致。
        """
        adapter = _make_adapter()
        request = _make_chat_request()

        mock_response = _make_mock_response(status_code)
        error = APIStatusError(
            message=error_message,
            response=mock_response,
            body=None,
        )

        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(ModelAccessError) as exc_info:
            async for _ in adapter.stream(request):
                pass

        assert not isinstance(exc_info.value, ModelTimeoutError), "不应转换为 ModelTimeoutError"
        assert not isinstance(exc_info.value, ModelRateLimitError), "不应转换为 ModelRateLimitError"
        assert exc_info.value.details.get("status_code") == status_code, (
            f"ModelAccessError.details['status_code'] 应为 {status_code}，"
            f"实际为 {exc_info.value.details.get('status_code')}"
        )
