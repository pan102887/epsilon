"""MCPToolBridge 持久 session 单元测试。

验证：
- discover() 后 session 持久保持（bridge._session_owner=True）
- 多次 execute 正常工作
- aclose() 后 _session_owner=False
- 重复 aclose() 不抛异常（幂等）
"""

from collections.abc import AsyncGenerator

import pytest
from fastmcp import FastMCP

from infrastructure.tools.mcp import MCPToolBridge


def _build_server() -> FastMCP:
    """构造含一个工具的内存 MCP Server。"""
    server = FastMCP("session-test")

    @server.tool
    async def echo(text: str) -> str:
        """回显输入。"""
        return f"echo: {text}"

    del echo

    return server


@pytest.fixture
async def bridge() -> AsyncGenerator[MCPToolBridge, None]:
    bridge = MCPToolBridge(transport=_build_server())
    try:
        yield bridge
    finally:
        await bridge.aclose()


class TestPersistentSession:
    """持久 session 行为验证。"""

    @pytest.mark.asyncio
    async def test_discover_opens_session(self, bridge: MCPToolBridge):
        """discover() 后 session 持久保持。"""
        assert bridge.session_owner is False
        await bridge.discover()
        assert bridge.session_owner is True

    @pytest.mark.asyncio
    async def test_multiple_execute_after_discover(self, bridge: MCPToolBridge):
        """discover 后多次 execute 均正常。"""
        tools = await bridge.discover()
        echo_tool = tools[0]
        r1 = await echo_tool.execute(text="hello")
        r2 = await echo_tool.execute(text="world")
        assert "hello" in r1.content
        assert "world" in r2.content

    @pytest.mark.asyncio
    async def test_aclose_releases_session(self, bridge: MCPToolBridge):
        """aclose() 后 _session_owner 变为 False。"""
        await bridge.discover()
        assert bridge.session_owner is True
        await bridge.aclose()
        assert bridge.session_owner is False

    @pytest.mark.asyncio
    async def test_aclose_idempotent(self, bridge: MCPToolBridge):
        """重复 aclose() 不抛异常。"""
        await bridge.discover()
        await bridge.aclose()
        await bridge.aclose()  # 第二次不应抛
        assert bridge.session_owner is False

    @pytest.mark.asyncio
    async def test_aclose_without_discover(self, bridge: MCPToolBridge):
        """未 discover 直接 aclose 不抛。"""
        await bridge.aclose()
        assert bridge.session_owner is False
