"""Bug Condition 探索性测试：验证 APIConnectionError 未被正确捕获的缺陷。

本模块通过属性测试（Hypothesis）验证 OpenAICompatibleAdapter 的 chat() 和 stream()
方法在 OpenAI SDK 抛出 APIConnectionError 时的异常处理行为。

Bug Condition:
    当模型服务不可达（连接被拒绝、DNS 解析失败等）时，OpenAI SDK 抛出
    APIConnectionError。该异常继承自 APIError 但不具有 status_code 属性，
    因此落入 ``except APIError as exc`` 分支后，访问 ``exc.status_code``
    触发 AttributeError 二次异常。

    适配器应将 APIConnectionError 捕获并转换为领域层 ModelConnectionError
    （code=50006），而非让其冒泡或产生 AttributeError。

预期行为:
    在未修复代码上运行时，测试 MUST FAIL（因为 ModelConnectionError 尚未定义，
    或 APIConnectionError 未被正确转换），确认 bug 存在。
    修复后测试应 PASS，验证 APIConnectionError 被正确捕获并转换。

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from openai import APIConnectionError

from domain.chat.context import UserMessage
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter

# ---------------------------------------------------------------------------
# 尝试导入 ModelConnectionError；若尚未定义则创建占位类以便测试编写。
# 在未修复代码上，即使导入成功（占位），适配器也不会抛出此异常，测试仍会失败。
# ---------------------------------------------------------------------------
try:
    from domain.model_access.exceptions import ModelConnectionError
except ImportError:
    # ModelConnectionError 尚未实现，定义占位类使测试代码可编译运行。
    # 测试将因适配器未抛出此异常而失败，正确证明 bug 存在。
    class ModelConnectionError(Exception):  # type: ignore[no-redef]
        """占位类：ModelConnectionError 尚未在领域层定义。"""

        code: int = -1
        message: str = ""


# ---------------------------------------------------------------------------
# Hypothesis 策略：生成随机连接错误消息
# ---------------------------------------------------------------------------

_connection_error_messages = st.sampled_from(
    [
        "Connection refused",
        "DNS resolution failed",
        "Network is unreachable",
        "Connection timed out while connecting",
        "Could not connect to host",
        "Name or service not known",
        "No route to host",
        "Connection reset by peer",
        "SSL handshake failed",
        "Temporary failure in name resolution",
    ]
) | st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=5,
    max_size=80,
).filter(lambda s: s.strip())


# ---------------------------------------------------------------------------
# 辅助函数：构造测试用的适配器和请求
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


def _make_api_connection_error(message: str) -> APIConnectionError:
    """构造一个 APIConnectionError 实例。

    Args:
        message: 连接错误描述信息。

    Returns:
        带有 Mock request 的 APIConnectionError 实例。
    """
    mock_request = httpx.Request("POST", "https://fake-api.example.com/v1/chat/completions")
    return APIConnectionError(message=message, request=mock_request)


# ---------------------------------------------------------------------------
# Property 1: Bug Condition — APIConnectionError 应被转换为 ModelConnectionError
# ---------------------------------------------------------------------------


class TestBugConditionChatConnectionError:
    """属性测试：chat() 方法应捕获 APIConnectionError 并转换为 ModelConnectionError。

    Property 1 (chat): Bug Condition - APIConnectionError 未被捕获导致冒泡

    对于任意连接错误消息，当 SDK 抛出 APIConnectionError 时，
    chat() 应抛出 ModelConnectionError(code=50006)，而非让
    APIConnectionError 冒泡或触发 AttributeError。

    在未修复代码上，APIConnectionError 落入 ``except APIError`` 分支，
    访问 ``exc.status_code`` 触发 AttributeError，测试将失败。

    **Validates: Requirements 1.1, 2.1**
    """

    @settings(max_examples=50, deadline=None)
    @given(error_message=_connection_error_messages)
    @pytest.mark.asyncio
    async def test_chat_converts_api_connection_error_to_model_connection_error(
        self, error_message: str
    ) -> None:
        """chat() 应将 APIConnectionError 转换为 ModelConnectionError。

        在未修复代码上预期失败：APIConnectionError 落入 except APIError 分支，
        访问 exc.status_code 触发 AttributeError。
        """
        adapter = _make_adapter()
        request = _make_chat_request()
        error = _make_api_connection_error(error_message)

        # Mock SDK 的 chat.completions.create 使其抛出 APIConnectionError
        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        # 期望：适配器应捕获 APIConnectionError 并抛出 ModelConnectionError
        # 实际（未修复）：抛出 AttributeError（访问 exc.status_code）
        with pytest.raises(ModelConnectionError) as exc_info:
            await adapter.chat(request)

        # 验证 ModelConnectionError 的属性
        assert exc_info.value.code == 50006, (
            f"ModelConnectionError.code 应为 50006，实际为 {exc_info.value.code}"
        )
        assert error_message in exc_info.value.message or "连接失败" in exc_info.value.message, (
            f"ModelConnectionError.message 应包含连接失败描述，实际为 {exc_info.value.message!r}"
        )


class TestBugConditionStreamConnectionError:
    """属性测试：stream() 方法应捕获 APIConnectionError 并转换为 ModelConnectionError。

    Property 1 (stream): Bug Condition - APIConnectionError 未被捕获导致冒泡

    对于任意连接错误消息，当 SDK 抛出 APIConnectionError 时，
    stream() 应抛出 ModelConnectionError(code=50006)。

    在未修复代码上，行为与 chat() 相同——APIConnectionError 落入
    ``except APIError`` 分支触发 AttributeError。

    **Validates: Requirements 1.2, 2.2**
    """

    @settings(max_examples=50)
    @given(error_message=_connection_error_messages)
    @pytest.mark.asyncio
    async def test_stream_converts_api_connection_error_to_model_connection_error(
        self, error_message: str
    ) -> None:
        """stream() 应将 APIConnectionError 转换为 ModelConnectionError。

        在未修复代码上预期失败：与 chat() 相同的 AttributeError 问题。
        """
        adapter = _make_adapter()
        request = _make_chat_request()
        error = _make_api_connection_error(error_message)

        # Mock SDK 的 chat.completions.create 使其抛出 APIConnectionError
        adapter._client.chat.completions.create = AsyncMock(side_effect=error)

        # 期望：适配器应捕获 APIConnectionError 并抛出 ModelConnectionError
        with pytest.raises(ModelConnectionError) as exc_info:
            async for _ in adapter.stream(request):
                pass

        assert exc_info.value.code == 50006, (
            f"ModelConnectionError.code 应为 50006，实际为 {exc_info.value.code}"
        )
        assert error_message in exc_info.value.message or "连接失败" in exc_info.value.message, (
            f"ModelConnectionError.message 应包含连接失败描述，实际为 {exc_info.value.message!r}"
        )
