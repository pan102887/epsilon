# ruff: noqa: RUF012
# pyright: reportPrivateUsage=false, reportUnusedFunction=false

"""MCPToolBridge / MCPTool 单元测试。

使用 fastmcp 的 in-memory 传输（直接传入 ``FastMCP`` 实例）构建测试用 MCP Server，
无需真实网络。覆盖工具发现、schema 映射、正常调用、错误调用与名称前缀/清洗。
"""

from collections.abc import AsyncGenerator

import pytest
from fastmcp import FastMCP

from domain.agent.exceptions import ToolExecutionError
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.tools.mcp import MCPTool, MCPToolBridge
from infrastructure.tools.mcp.mcp_config import MCPConfig
from infrastructure.tools.mcp.mcp_tool_bridge import _sanitize


def _build_server() -> FastMCP:
    """构造含两个工具的内存 MCP Server：add（正常）与 boom（抛错）。"""
    server = FastMCP("test-server")

    @server.tool
    async def add(a: int, b: int) -> int:
        """求两数之和。"""
        return a + b

    @server.tool
    async def boom() -> str:
        """总是失败的工具，用于错误路径测试。"""
        raise ValueError("intentional failure")

    return server


class _FakeMCPMetadata:
    """用于直接构造 MCPTool 的远端工具元数据。"""

    name = "complex"
    description = "complex schema"
    inputSchema = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "limits": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                    },
                },
                "required": ["enabled", "limits"],
            }
        },
        "required": ["payload"],
    }


class _FakeMCPResult:
    """模拟 fastmcp CallToolResult 的最小返回对象。"""

    is_error = False
    content: list[object] = []
    structured_content = {"ok": True}


class _FakeClient:
    """记录 MCPTool 传给远端的参数。"""

    def __init__(self) -> None:
        self.last_kwargs: dict[str, object] | None = None

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def call_tool(
        self, name: str, arguments: dict[str, object], raise_on_error: bool
    ) -> _FakeMCPResult:
        self.last_kwargs = arguments
        return _FakeMCPResult()


@pytest.fixture
async def bridge() -> AsyncGenerator[MCPToolBridge, None]:
    bridge = MCPToolBridge(transport=_build_server())
    try:
        yield bridge
    finally:
        await bridge.aclose()


async def test_discover_maps_tool_metadata(bridge: MCPToolBridge) -> None:
    tools = {t.name: t for t in await bridge.discover()}
    assert set(tools) == {"add", "boom"}
    add_tool = tools["add"]
    assert isinstance(add_tool, MCPTool)
    assert "求两数之和" in add_tool.description
    # inputSchema 应映射为 JSON Schema object，含 a/b 参数
    assert add_tool.parameters["type"] == "object"
    assert set(add_tool.parameters["properties"]) >= {"a", "b"}


async def test_execute_returns_remote_result(bridge: MCPToolBridge) -> None:
    add_tool = next(t for t in await bridge.discover() if t.name == "add")
    result = await add_tool.execute(a=2, b=3)
    assert "5" in result.content
    # metadata 字段名/类型对齐 design §3.14。
    assert result.metadata["mcp_tool_name"] == "add"
    assert isinstance(result.metadata["mcp_tool_name"], str)
    assert isinstance(result.metadata["mcp_server"], str)
    assert set(result.metadata.keys()) == {"mcp_server", "mcp_tool_name"}


async def test_execute_metadata_mcp_server_reflects_server_name() -> None:
    """metadata.mcp_server 反映构造时传入的 server_name。"""
    bridge = MCPToolBridge(transport=_build_server(), server_name="calc-server")
    add_tool = next(t for t in await bridge.discover() if t.name == "add")
    result = await add_tool.execute(a=1, b=1)
    assert result.metadata["mcp_server"] == "calc-server"


async def test_execute_via_run_pipeline(bridge: MCPToolBridge) -> None:
    """经基类 run()（JSON 解析 + 校验 + execute）端到端调用。"""
    add_tool = next(t for t in await bridge.discover() if t.name == "add")
    out = await add_tool.run(ToolCallRequest(id="1", name="add", arguments='{"a": 7, "b": 8}'))
    assert "15" in out.content


async def test_mcp_tool_run_casts_nested_input_schema() -> None:
    """验证 MCP 透传复杂 inputSchema 时，run() 会递归 cast 后再调用远端。"""
    client = _FakeClient()
    tool = MCPTool(client=client, mcp_tool=_FakeMCPMetadata())  # type: ignore[arg-type]

    await tool.run(
        ToolCallRequest(
            id="complex-1",
            name="complex",
            arguments='{"payload": {"enabled": "false", "limits": ["1", "2"]}}',
        )
    )

    assert client.last_kwargs == {"payload": {"enabled": False, "limits": [1, 2]}}


async def test_error_tool_raises_tool_execution_error(bridge: MCPToolBridge) -> None:
    boom_tool = next(t for t in await bridge.discover() if t.name == "boom")
    with pytest.raises(ToolExecutionError):
        await boom_tool.execute()


async def test_tool_prefix_applied() -> None:
    bridge = MCPToolBridge(transport=_build_server(), tool_prefix="mcp_")
    names = {t.name for t in await bridge.discover()}
    assert names == {"mcp_add", "mcp_boom"}


def test_sanitize_replaces_invalid_chars() -> None:
    assert _sanitize("weather.get tool") == "weather_get_tool"
    assert _sanitize("ok-name_1") == "ok-name_1"


def test_config_get_servers_parsing() -> None:
    # 扁平写法
    flat = MCPConfig(servers='{"weather": {"url": "http://x/mcp"}}')
    assert flat.get_servers() == {"weather": {"url": "http://x/mcp"}}
    # mcpServers 包裹写法
    wrapped = MCPConfig(servers='{"mcpServers": {"w": {"url": "http://y"}}}')
    assert wrapped.get_servers() == {"w": {"url": "http://y"}}
    # 空配置
    assert MCPConfig().get_servers() == {}
