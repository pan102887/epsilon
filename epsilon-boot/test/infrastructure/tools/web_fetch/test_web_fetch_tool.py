"""WebFetchTool 测试模块。

验证网页抓取工具的接口约定、抓取结果格式化和条件注册逻辑。
"""

import socket
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.agent.tools import Tool, ToolRegistry
from infrastructure.tools.web_fetch.web_fetch_tool import WebFetchTool


class TestWebFetchToolInterface:
    """WebFetchTool 接口合规性测试。"""

    def _create_tool(self) -> WebFetchTool:
        """创建 WebFetchTool 实例。"""
        return WebFetchTool()

    def test_is_tool_instance(self) -> None:
        """验证 WebFetchTool 是 Tool 的实例。"""
        tool = self._create_tool()
        assert isinstance(tool, Tool)

    def test_name_returns_web_fetch(self) -> None:
        """验证 name 属性返回 `web_fetch`。"""
        tool = self._create_tool()
        assert tool.name == "web_fetch"

    def test_parameters_require_only_url(self) -> None:
        """验证参数 schema 至少要求 url 字段。"""
        tool = self._create_tool()
        assert tool.parameters["required"] == ["url"]
        assert "url" in tool.parameters["properties"]
        assert "timeout" in tool.parameters["properties"]
        assert tool._client.follow_redirects is False


@pytest.mark.asyncio
async def test_execute_returns_formatted_fetch_result() -> None:
    """验证 execute 返回包含请求信息和正文内容的格式化结果。"""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.headers = {"content-type": "text/html; charset=utf-8"}
    response.url = "https://example.com/final"
    response.text = "<html><body><article><p>Hello web fetch</p></article></body></html>"
    response.content = response.text.encode("utf-8")

    tool = WebFetchTool(timeout=9, max_response_size=4096)

    with (
        patch("infrastructure.tools.web_fetch.web_fetch_tool.validate_url_safety"),
        patch.object(tool._client, "get", AsyncMock(return_value=response)) as mock_get,
    ):
        result = await tool.execute(url="https://example.com/start")

    mock_get.assert_awaited_once()
    assert "FETCH 200" in result.content
    assert "Requested URL: https://example.com/start" in result.content
    assert "Final URL: https://example.com/final" in result.content
    assert "Content-Type: text/html; charset=utf-8" in result.content
    assert "Hello web fetch" in result.content
    assert result.metadata["url"] == "https://example.com/start"
    assert result.metadata["content_type"] == "text/html; charset=utf-8"
    assert result.metadata["response_bytes"] == len(response.content)


@pytest.mark.asyncio
async def test_execute_follows_checked_redirects() -> None:
    """验证 web_fetch 会逐跳校验并跟随安全重定向。"""
    redirect_response = MagicMock(spec=httpx.Response)
    redirect_response.status_code = 302
    redirect_response.headers = {"location": "/final"}
    redirect_response.url = "https://example.com/start"
    redirect_response.text = ""

    final_response = MagicMock(spec=httpx.Response)
    final_response.status_code = 200
    final_response.headers = {"content-type": "text/plain"}
    final_response.url = "https://example.com/final"
    final_response.text = "ok after redirect"
    final_response.content = b"ok after redirect"

    tool = WebFetchTool()

    with (
        patch("infrastructure.tools.web_fetch.web_fetch_tool.validate_url_safety") as mock_validate,
        patch.object(
            tool._client,
            "get",
            AsyncMock(side_effect=[redirect_response, final_response]),
        ) as mock_get,
    ):
        result = await tool.execute(url="https://example.com/start")

    assert mock_validate.call_args_list == [
        call("https://example.com/start", tool_name="web_fetch"),
        call("https://example.com/final", tool_name="web_fetch"),
    ]
    assert mock_get.await_count == 2
    assert "FETCH 200" in result.content
    assert "Final URL: https://example.com/final" in result.content
    assert "ok after redirect" in result.content


@pytest.mark.asyncio
async def test_execute_rejects_redirect_to_unsafe_ip() -> None:
    """验证重定向目标会在发起下一跳请求前执行 SSRF 校验。"""
    redirect_response = MagicMock(spec=httpx.Response)
    redirect_response.status_code = 302
    redirect_response.headers = {"location": "http://127.0.0.1/admin"}
    redirect_response.url = "https://example.com/start"
    redirect_response.text = ""

    tool = WebFetchTool()
    public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    with (
        patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo",
            return_value=public_dns,
        ),
        patch.object(tool._client, "get", AsyncMock(return_value=redirect_response)) as mock_get,
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await tool.execute(url="https://example.com/start")

    assert mock_get.await_count == 1
    assert exc_info.value.tool_name == "web_fetch"
    assert "127.0.0.1" in exc_info.value.message


@pytest.mark.asyncio
async def test_execute_rejects_too_many_redirects() -> None:
    """验证超过最大重定向跳数时中止抓取。"""
    responses = []
    for index in range(6):
        response = MagicMock(spec=httpx.Response)
        response.status_code = 302
        response.headers = {"location": f"/next-{index}"}
        response.url = f"https://example.com/step-{index}"
        response.text = ""
        responses.append(response)

    tool = WebFetchTool()

    with (
        patch("infrastructure.tools.web_fetch.web_fetch_tool.validate_url_safety"),
        patch.object(tool._client, "get", AsyncMock(side_effect=responses)) as mock_get,
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await tool.execute(url="https://example.com/start")

    assert mock_get.await_count == 6
    assert exc_info.value.tool_name == "web_fetch"
    assert "重定向超过最大跳数" in exc_info.value.message


@pytest.mark.asyncio
async def test_execute_wraps_unexpected_exception() -> None:
    """验证 execute 会将底层异常包装为 ToolExecutionError。"""
    tool = WebFetchTool()

    with (
        patch("infrastructure.tools.web_fetch.web_fetch_tool.validate_url_safety"),
        patch.object(tool._client, "get", AsyncMock(side_effect=RuntimeError("network boom"))),
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await tool.execute(url="https://example.com")

    assert exc_info.value.tool_name == "web_fetch"
    assert "网页抓取失败" in exc_info.value.message
    assert "network boom" in exc_info.value.message


def test_conditional_registration_logic() -> None:
    """验证 enabled 配置会控制 `web_fetch` 工具是否注册。"""
    from infrastructure.tools.web_fetch.web_fetch_config import WebFetchConfig

    for enabled in (True, False):
        registry = ToolRegistry()
        config = WebFetchConfig(enabled=enabled)

        if config.enabled:
            registry.register(
                WebFetchTool(
                    timeout=config.timeout,
                    max_response_size=config.max_response_size,
                )
            )

        assert registry.has("web_fetch") is enabled
