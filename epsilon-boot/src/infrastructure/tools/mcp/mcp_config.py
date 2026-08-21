"""MCP 协议适配配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``MCP_`` 为前缀的配置项。
"""

import json
from typing import Any, cast

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class MCPConfig(PropertiesBaseSettings):
    """MCP 协议适配配置，对应环境变量前缀 ``MCP_``。

    Attributes:
        enabled: 总开关，对应 ``MCP_ENABLED``，默认 ``False``。
        servers: 远端 server 集合的 JSON 字符串，对应 ``MCP_SERVERS``，默认空。
        tool_prefix: 桥接工具注册名前缀，对应 ``MCP_TOOL_PREFIX``，默认空。
        timeout: 远端调用超时秒数，对应 ``MCP_TIMEOUT``，默认 ``30.0``。
        max_retries: call_tool 最大重试次数，对应 ``MCP_MAX_RETRIES``，默认 ``2``。
        retry_base_delay: 重试基础延迟秒数，对应 ``MCP_RETRY_BASE_DELAY``，默认 ``0.5``。
    """

    model_config = SettingsConfigDict(env_prefix="MCP_")

    enabled: bool = False
    servers: str = ""
    tool_prefix: str = ""
    timeout: float = 30.0
    max_retries: int = 2
    retry_base_delay: float = 0.5

    def get_servers(self) -> dict[str, Any]:
        """解析 ``servers`` JSON，返回 ``{server_name: server_spec}`` 字典。

        兼容 ``{"mcpServers": {...}}`` 包裹写法与扁平 ``{...}`` 写法；
        空配置或解析失败时返回空字典。
        """
        if not self.servers.strip():
            return {}
        data = json.loads(self.servers)
        if isinstance(data, dict) and "mcpServers" in data:
            wrapped = cast(dict[str, object], data)["mcpServers"]
            return cast(dict[str, Any], wrapped) if isinstance(wrapped, dict) else {}
        return cast(dict[str, Any], data) if isinstance(data, dict) else {}


mcp_config = create_config(MCPConfig)
