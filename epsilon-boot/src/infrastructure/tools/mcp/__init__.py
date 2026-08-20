"""MCP 协议适配模块。

通过 fastmcp 客户端连接 MCP Server，将远端工具桥接为内部 Tool 子类，
注册到 ToolRegistry 使 Agent 可透明调用。
"""

from infrastructure.tools.mcp.mcp_tool_bridge import MCPTool, MCPToolBridge

__all__ = ["MCPTool", "MCPToolBridge"]
