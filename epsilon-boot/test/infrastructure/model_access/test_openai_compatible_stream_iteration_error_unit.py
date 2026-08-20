"""``OpenAICompatibleAdapter.stream`` 迭代阶段异常映射单元测试（Task 1.3）。

对应 design 风险 1 的修复：``async for chunk in response:`` 循环外
包裹 try-except，将 SDK / httpx 层异常统一映射为领域异常，并通过
``request_info.phase = "stream_iteration"`` 字段与握手阶段区分。

覆盖 5 类异常：

- ``APITimeoutError`` → ``ModelTimeoutError``
- ``RateLimitError``  → ``ModelRateLimitError``
- ``APIConnectionError`` → ``ModelConnectionError``
- ``APIError`` (其他) → ``ModelAccessError``
- ``httpx.ReadTimeout`` → ``ModelTimeoutError``
- ``httpx.RemoteProtocolError`` → ``ModelConnectionError``
- ``httpx.ReadError`` → ``ModelConnectionError``

每个用例验证：异常类型映射正确、``code`` 正确、``details`` /
``request_info`` 含 ``phase="stream_iteration"`` 字段。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError

from domain.chat.context import UserMessage
from domain.model_access.exceptions import (
    ModelAccessError,
    ModelConnectionError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter() -> OpenAICompatibleAdapter:
    """构造测试用 adapter，注入 Mock 配置避免真实网络。"""
    cfg = MagicMock()
    cfg.api_key = "k"
    cfg.api_base = "https://fake/v1"
    cfg.timeout = 30
    cfg.max_retries = 0
    cfg.max_connections = 10
    cfg.max_keepalive_connections = 5
    cfg.provider_name = "test"
    cfg.default_model = "test-model"
    cfg.temperature = 0.7
    cfg.max_tokens = 4096
    return OpenAICompatibleAdapter(cfg)


class _FailingAsyncStream:
    """异步迭代器，先 yield 一个文本分片再抛出指定异常，模拟迭代中断。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        # 先吐一个正常文本分片，证明异常发生在迭代中（非首次握手）
        delta = SimpleNamespace(content="hi", tool_calls=None)
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        yield SimpleNamespace(choices=[choice], usage=None)
        raise self._exc


def _mock_request() -> httpx.Request:
    """构造一个虚拟 httpx.Request，供需要 request 参数的 SDK 异常使用。"""
    return httpx.Request("POST", "https://fake/v1/chat/completions")


def _make_api_timeout() -> APITimeoutError:
    """构造 APITimeoutError 实例。"""
    return APITimeoutError(request=_mock_request())


def _make_api_connection_error(message: str = "connection refused") -> APIConnectionError:
    """构造 APIConnectionError 实例。"""
    return APIConnectionError(message=message, request=_mock_request())


def _make_rate_limit_error(retry_after: str | None = None) -> RateLimitError:
    """构造 RateLimitError 实例，可选携带 ``retry-after`` 响应头。"""
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    response = httpx.Response(
        status_code=429,
        headers=headers,
        request=_mock_request(),
    )
    return RateLimitError(message="rate limited", response=response, body=None)


def _make_api_error(message: str = "internal error", status_code: int = 500) -> APIError:
    """构造通用 APIError 实例（非 timeout / 非 rate limit / 非 connection）。"""
    err = APIError(message=message, request=_mock_request(), body=None)
    # APIError 的 status_code 通过子类 / 属性赋值；此处显式注入便于测试断言。
    err.status_code = status_code  # type: ignore[attr-defined]
    return err


# ---------------------------------------------------------------------------
# 测试：APITimeoutError → ModelTimeoutError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_api_timeout_maps_to_model_timeout() -> None:
    """迭代阶段 ``APITimeoutError`` → ``ModelTimeoutError`` 且 ``phase`` 字段正确。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(_make_api_timeout())
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelTimeoutError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.code == 50002
    assert exc.details["timeout_seconds"] == 30
    assert exc.details["model"] == "test-model"
    assert exc.details["phase"] == "stream_iteration"


# ---------------------------------------------------------------------------
# 测试：RateLimitError → ModelRateLimitError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_rate_limit_maps_to_model_rate_limit() -> None:
    """迭代阶段 ``RateLimitError`` → ``ModelRateLimitError`` 含 retry-after。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(_make_rate_limit_error(retry_after="3"))
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelRateLimitError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.code == 50003
    assert exc.details["retry_after_seconds"] == 3.0
    assert exc.details["model"] == "test-model"
    assert exc.details["phase"] == "stream_iteration"


@pytest.mark.asyncio
async def test_stream_iteration_rate_limit_without_header() -> None:
    """无 retry-after 头时 ``retry_after_seconds`` 为 None。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(_make_rate_limit_error(retry_after=None))
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelRateLimitError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    assert exc_info.value.details["retry_after_seconds"] is None
    assert exc_info.value.details["phase"] == "stream_iteration"


# ---------------------------------------------------------------------------
# 测试：APIConnectionError → ModelConnectionError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_api_connection_error_maps_to_model_connection() -> None:
    """迭代阶段 ``APIConnectionError`` → ``ModelConnectionError``。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(_make_api_connection_error("connection lost"))
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelConnectionError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.code == 50006
    assert exc.details["model"] == "test-model"
    assert exc.details["phase"] == "stream_iteration"


# ---------------------------------------------------------------------------
# 测试：APIError (其他) → ModelAccessError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_api_error_maps_to_model_access_error() -> None:
    """迭代阶段通用 ``APIError`` → ``ModelAccessError`` 且携带 status_code。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(_make_api_error("server boom", status_code=503))
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelAccessError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    # ModelAccessError 不应被更具体子类捕获覆盖；并且不属于 ConnectionError 等
    assert not isinstance(exc, (ModelTimeoutError, ModelRateLimitError, ModelConnectionError))
    assert exc.code == 50001
    assert exc.details["status_code"] == 503
    assert exc.details["model"] == "test-model"
    assert exc.details["phase"] == "stream_iteration"
    assert "流式迭代中模型服务错误" in exc.message
    assert "server boom" in exc.message


# ---------------------------------------------------------------------------
# 测试：httpx.ReadTimeout → ModelTimeoutError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_httpx_read_timeout_maps_to_model_timeout() -> None:
    """迭代阶段 ``httpx.ReadTimeout`` → ``ModelTimeoutError``。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(httpx.ReadTimeout("read timeout"))
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelTimeoutError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.code == 50002
    assert exc.details["timeout_seconds"] == 30
    assert exc.details["phase"] == "stream_iteration"


# ---------------------------------------------------------------------------
# 测试：httpx.RemoteProtocolError → ModelConnectionError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_httpx_protocol_error_maps_to_connection() -> None:
    """迭代阶段 ``httpx.RemoteProtocolError`` → ``ModelConnectionError``。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(
            httpx.RemoteProtocolError("server disconnected mid-stream")
        )
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelConnectionError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.code == 50006
    assert "server disconnected mid-stream" in exc.message
    assert exc.details["phase"] == "stream_iteration"


# ---------------------------------------------------------------------------
# 测试：httpx.ReadError → ModelConnectionError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_iteration_httpx_read_error_maps_to_connection() -> None:
    """迭代阶段 ``httpx.ReadError`` → ``ModelConnectionError``。"""
    adapter = _make_adapter()
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_FailingAsyncStream(httpx.ReadError("socket read error"))
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelConnectionError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.code == 50006
    assert "socket read error" in exc.message
    assert exc.details["phase"] == "stream_iteration"


# ---------------------------------------------------------------------------
# 测试：迭代阶段异常的 ``phase`` 与握手阶段区分（回归保护）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_phase_does_not_carry_iteration_marker() -> None:
    """握手阶段抛 ``APITimeoutError`` 时，``request_info`` **不**应含
    ``phase="stream_iteration"``，确保两个阶段的诊断字段相互独立。"""
    adapter = _make_adapter()
    # 在 stream_open 阶段直接抛 APITimeoutError
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=_make_api_timeout()
    )

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(ModelTimeoutError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    # 握手阶段不写 phase 字段，迭代阶段写 phase=stream_iteration
    assert exc.details.get("phase") != "stream_iteration"
