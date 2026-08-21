"""GatewayClient 单元测试。

验证 GatewayClient 的生命周期管理（start/stop）、请求分发（get/post/put/delete）、
请求头合并、以及异常场景（未启动调用、重复启动）等行为。
使用 httpx 内置的 MockTransport 替代真实网络调用。
"""

from typing import Any

import httpx
import pytest

from infrastructure.gateway.gateway_client import GatewayClient
from infrastructure.gateway.gateway_config import GatewayConfig

# ── 测试用 Fixtures ──


def _make_config(**overrides: object) -> GatewayConfig:
    """创建测试用 GatewayConfig，通过构造参数覆盖默认值。

    Args:
        **overrides: 需要覆盖的配置字段，如 base_url、timeout、max_retries。

    Returns:
        填充了测试值的 GatewayConfig 实例。
    """
    defaults: dict[str, object] = {
        "base_url": "http://test-gateway:8080",
        "timeout": 5,
        "max_retries": 0,
    }
    defaults.update(overrides)
    return GatewayConfig.model_validate(defaults)


def _mock_transport(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    text_body: str = "ok",
) -> httpx.MockTransport:
    """创建返回固定响应的 MockTransport。

    Args:
        status_code: 响应状态码。
        json_body: JSON 响应体，为 None 时使用 text_body。
        text_body: 纯文本响应体。

    Returns:
        httpx.MockTransport 实例。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(
                status_code,
                json=json_body,
                request=request,
            )
        return httpx.Response(
            status_code,
            text=text_body,
            request=request,
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def config() -> GatewayConfig:
    """提供测试用网关配置。"""
    return _make_config()


@pytest.fixture
def client(config: GatewayConfig) -> GatewayClient:
    """提供未启动的 GatewayClient 实例。"""
    return GatewayClient(config=config)


# ── 生命周期测试 ──


async def test_start_initializes_client(client: GatewayClient) -> None:
    """验证 start() 后 is_started 为 True。"""
    assert not client.is_started
    await client.start()
    assert client.is_started
    await client.stop()


async def test_stop_clears_client(client: GatewayClient) -> None:
    """验证 stop() 后 is_started 恢复为 False。"""
    await client.start()
    await client.stop()
    assert not client.is_started


async def test_stop_is_idempotent(client: GatewayClient) -> None:
    """验证多次调用 stop() 不会抛出异常（幂等）。"""
    await client.start()
    await client.stop()
    await client.stop()  # 第二次调用不应报错
    assert not client.is_started


async def test_start_twice_raises(client: GatewayClient) -> None:
    """验证重复调用 start() 抛出 RuntimeError。"""
    await client.start()
    with pytest.raises(RuntimeError, match="已启动"):
        await client.start()
    await client.stop()


# ── 未启动时调用请求方法 ──


async def test_request_before_start_raises(client: GatewayClient) -> None:
    """验证未调用 start() 时发起请求抛出 RuntimeError。"""
    with pytest.raises(RuntimeError, match="尚未启动"):
        await client.get("/test")


async def test_get_before_start_raises(client: GatewayClient) -> None:
    """验证未启动时 get() 抛出 RuntimeError。"""
    with pytest.raises(RuntimeError, match="尚未启动"):
        await client.get("/test")


async def test_post_before_start_raises(client: GatewayClient) -> None:
    """验证未启动时 post() 抛出 RuntimeError。"""
    with pytest.raises(RuntimeError, match="尚未启动"):
        await client.post("/test")


# ── HTTP 方法测试（使用 MockTransport） ──


async def _start_with_mock(
    client: GatewayClient,
    config: GatewayConfig,
    transport: httpx.MockTransport,
) -> None:
    """用 MockTransport 替换底层 httpx 客户端，模拟网络调用。

    先正常 start() 初始化，然后替换内部 _client 为使用 MockTransport 的实例。

    Args:
        client: GatewayClient 实例。
        config: 网关配置。
        transport: 用于模拟响应的 MockTransport。
    """
    await client.start()
    await client.replace_client(
        httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout),
            transport=transport,
        )
    )


async def test_get_request(config: GatewayConfig) -> None:
    """验证 get() 发起 GET 请求并返回正确响应。"""
    transport = _mock_transport(200, json_body={"status": "ok"})
    client = GatewayClient(config=config)
    await _start_with_mock(client, config, transport)

    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    await client.stop()


async def test_post_request_with_json(config: GatewayConfig) -> None:
    """验证 post() 可以发送 JSON 请求体。"""
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(201, json={"id": "123"}, request=request)

    transport = httpx.MockTransport(handler)
    client = GatewayClient(config=config)
    await _start_with_mock(client, config, transport)

    resp = await client.post("/api/items", json={"name": "test"})
    assert resp.status_code == 201
    assert resp.json() == {"id": "123"}
    assert len(captured_requests) == 1
    assert captured_requests[0].method == "POST"
    await client.stop()


async def test_put_request(config: GatewayConfig) -> None:
    """验证 put() 发起 PUT 请求。"""
    captured_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_methods.append(request.method)
        return httpx.Response(200, text="updated", request=request)

    transport = httpx.MockTransport(handler)
    client = GatewayClient(config=config)
    await _start_with_mock(client, config, transport)

    resp = await client.put("/api/items/1", json={"name": "updated"})
    assert resp.status_code == 200
    assert captured_methods == ["PUT"]
    await client.stop()


async def test_delete_request(config: GatewayConfig) -> None:
    """验证 delete() 发起 DELETE 请求。"""
    captured_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_methods.append(request.method)
        return httpx.Response(204, text="", request=request)

    transport = httpx.MockTransport(handler)
    client = GatewayClient(config=config)
    await _start_with_mock(client, config, transport)

    resp = await client.delete("/api/items/1")
    assert resp.status_code == 204
    assert captured_methods == ["DELETE"]
    await client.stop()


# ── 请求头合并测试 ──


async def test_default_headers_are_sent(config: GatewayConfig) -> None:
    """验证构造时传入的 default_headers 会附加到每次请求中。"""
    captured_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(dict(request.headers))
        return httpx.Response(200, text="ok", request=request)

    transport = httpx.MockTransport(handler)
    client = GatewayClient(
        config=config,
        default_headers={"x-trace-id": "abc-123", "x-app": "test"},
    )
    await _start_with_mock(client, config, transport)

    await client.get("/api/test")
    assert captured_headers[0]["x-trace-id"] == "abc-123"
    assert captured_headers[0]["x-app"] == "test"
    await client.stop()


async def test_per_request_headers_override_defaults(config: GatewayConfig) -> None:
    """验证单次请求的 headers 参数可以覆盖 default_headers 中的同名键。"""
    captured_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(dict(request.headers))
        return httpx.Response(200, text="ok", request=request)

    transport = httpx.MockTransport(handler)
    client = GatewayClient(
        config=config,
        default_headers={"x-trace-id": "default-trace"},
    )
    await _start_with_mock(client, config, transport)

    await client.get("/api/test", headers={"x-trace-id": "override-trace"})
    assert captured_headers[0]["x-trace-id"] == "override-trace"
    await client.stop()


# ── 配置注入测试 ──


async def test_custom_config_applied() -> None:
    """验证自定义 GatewayConfig 的 base_url 被正确应用到请求中。"""
    custom_config = _make_config(base_url="http://custom-gw:9090")
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, text="ok", request=request)

    transport = httpx.MockTransport(handler)
    client = GatewayClient(config=custom_config)
    await _start_with_mock(client, custom_config, transport)

    await client.get("/api/check")
    assert "custom-gw:9090" in captured_urls[0]
    assert "/api/check" in captured_urls[0]
    await client.stop()


# ── 错误响应透传测试 ──


async def test_error_status_code_returned(config: GatewayConfig) -> None:
    """验证服务端返回 4xx/5xx 时，响应原样透传给调用方（不自动抛异常）。"""
    transport = _mock_transport(502, text_body="Bad Gateway")
    client = GatewayClient(config=config)
    await _start_with_mock(client, config, transport)

    resp = await client.get("/api/failing")
    assert resp.status_code == 502
    assert resp.text == "Bad Gateway"
    await client.stop()
