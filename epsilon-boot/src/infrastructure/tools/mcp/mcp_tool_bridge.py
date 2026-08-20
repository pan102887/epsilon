"""MCP 工具桥接实现。

通过 ``fastmcp.Client`` 连接远端 MCP Server，将其暴露的工具发现并包装为本项目的
``Tool`` 子类（``MCPTool``），注册到 ``ToolRegistry`` 后供 Agent 透明调用。

- ``MCPToolBridge``：持有共享 ``Client``，``discover()`` 列举远端工具并构造 ``MCPTool``。
- ``MCPTool``：单个远端工具的领域适配，``execute`` 通过 ``Client`` 发起 ``call_tool``。

强化特性：
- 每 server 一个 ``MCPToolBridge``，逐 server fail-soft（单 server 故障不影响其余）。
- ``call_tool`` 带指数退避重试，应对远端瞬时抖动。
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from fastmcp import Client

from domain.agent.exceptions import ToolExecutionError
from domain.agent.tools import Tool, ToolExecutionResult

logger = logging.getLogger(__name__)

# OpenAI function calling 名称约束：仅允许字母、数字、下划线、连字符
_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_-]")

# 重试默认配置
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_RETRY_BASE_DELAY = 0.5  # 秒


def _sanitize(name: str) -> str:
    """将工具名清洗为满足 function calling 命名约束的形式。"""
    return _NAME_INVALID.sub("_", name)


def _extract_text(result: Any) -> str:
    """从 ``CallToolResult`` 提取文本表示。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured:
        return json.dumps(structured, ensure_ascii=False)
    return ""


class MCPTool(Tool):
    """远端 MCP 工具的领域适配，继承 ``Tool`` 抽象基类。

    持有共享的 ``fastmcp.Client`` 引用与远端工具元数据，对外暴露统一的工具接口。
    注册名可经前缀与字符清洗，但调用时仍使用远端原始工具名。
    ``execute`` 内置指数退避重试，应对远端瞬时故障。
    """

    def __init__(
        self,
        client: Client,
        mcp_tool: Any,
        prefix: str = "",
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY,
        server_name: str = "",
        session_is_open: Callable[[], bool] | None = None,
    ) -> None:
        self._client = client
        self._mcp_name = mcp_tool.name
        self._name = _sanitize(f"{prefix}{mcp_tool.name}")
        self._description = mcp_tool.description or mcp_tool.name
        self._parameters = mcp_tool.inputSchema or {"type": "object", "properties": {}}
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        # 远端 MCP server 标识，供 trace metadata 使用；未知时退化为 prefix。
        self._server_name = server_name or prefix
        self._session_is_open = session_is_open or (lambda: False)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """向远端 MCP Server 发起工具调用，带指数退避重试。

        依赖 ``MCPToolBridge`` 持久 session：bridge.discover() 已打开 session，
        此处嵌套 ``async with self._client`` 在 fastmcp 引用计数下走 fast path，
        不会重复建立连接。若 bridge 未持有 session（退化场景），嵌套 with 仍可
        正常自行打开/关闭——兼容旧行为。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为远端工具返回的文本表示
            （空时为 ``"(无返回内容)"``）；``metadata`` 含以下键：

            - ``mcp_server`` (str): MCP server 标识。
            - ``mcp_tool_name`` (str): MCP 侧原始工具名。
        """
        metadata: dict[str, Any] = {
            "mcp_server": self._server_name,
            "mcp_tool_name": self._mcp_name,
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                if self._session_is_open():
                    result = await self._client.call_tool(
                        self._mcp_name, kwargs, raise_on_error=False
                    )
                else:
                    async with self._client:
                        result = await self._client.call_tool(
                            self._mcp_name, kwargs, raise_on_error=False
                        )
                # 成功通信，检查业务错误（不重试）
                is_error = getattr(result, "is_error", None)
                if is_error is None:
                    is_error = getattr(result, "isError", False)
                if is_error:
                    raise ToolExecutionError(
                        message=f"MCP 工具返回错误: {_extract_text(result)}",
                        tool_name=self.name,
                    )
                return ToolExecutionResult(
                    content=_extract_text(result) or "(无返回内容)",
                    metadata=metadata,
                )
            except ToolExecutionError:
                raise  # 业务错误不重试
            except Exception as e:
                last_exc = e
                if attempt < self._max_retries:
                    delay = self._retry_base_delay * (2**attempt)
                    logger.warning(
                        "MCP 工具 %s 调用失败(第%d次)，%.1fs 后重试: %s",
                        self.name,
                        attempt + 1,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)

        raise ToolExecutionError(
            message=f"MCP 工具调用失败(重试{self._max_retries}次后): {last_exc}",
            tool_name=self.name,
        ) from last_exc


class MCPToolBridge:
    """MCP 工具桥接器（单 server 粒度）。

    每个 ``MCPToolBridge`` 对应一个远端 MCP Server，实现逐 server 失败隔离。
    ``discover()`` 首次调用时开启持久 session（``__aenter__``），后续工具调用
    复用该 session（fastmcp 引用计数保证嵌套 ``async with`` 为 fast path）。
    进程退出前可调用 ``aclose()`` 显式关闭 session。
    """

    def __init__(
        self,
        transport: Any,
        tool_prefix: str = "",
        timeout: float | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY,
        server_name: str = "",
    ) -> None:
        self._client = Client(transport, timeout=timeout)
        self._prefix = tool_prefix
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._server_name = server_name
        self._session_owner: bool = False

    async def discover(self) -> list[MCPTool]:
        """连接远端 Server 并发现其工具。

        首次调用时打开持久 session，失败则保持 fail-soft（不持有 session）。
        """
        if not self._session_owner:
            await self._client.__aenter__()
            self._session_owner = True
        try:
            tools = await self._client.list_tools()
        except Exception:
            # discover 失败 → 释放刚打开的 session，保留 fail-soft 语义
            await self.aclose()
            raise
        return [
            MCPTool(
                self._client,
                tool,
                self._prefix,
                max_retries=self._max_retries,
                retry_base_delay=self._retry_base_delay,
                server_name=self._server_name,
                session_is_open=lambda: self._session_owner,
            )
            for tool in tools
        ]

    async def aclose(self) -> None:
        """显式关闭持久 session，幂等（重复调用不抛异常）。"""
        if self._session_owner:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("MCPToolBridge.aclose() 异常: %s", exc)
            finally:
                self._session_owner = False
