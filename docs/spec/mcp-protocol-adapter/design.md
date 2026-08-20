# MCP 协议适配层 — 技术设计

## 架构定位

遵循六边形架构：MCP 是**外部协议**，适配代码归属基础设施层 `infrastructure/tools/mcp/`，
通过继承领域层 `domain/agent/tools.Tool` 把远端能力翻译为领域内的统一工具接口。
领域层与 Agent Loop **零改动**。

```
ReAct Agent ──> ToolRegistry ──> MCPTool(Tool 子类) ──> fastmcp.Client ──> 远端 MCP Server
                     ▲
            MCPToolBridge.discover() 在启动期把远端工具注册进来
```

## 组件设计

### 1. `MCPConfig`（`mcp_config.py`）
基于 `PropertiesBaseSettings`，`env_prefix="MCP_"`：

| 字段 | 配置键 | 默认 | 说明 |
|------|--------|------|------|
| `enabled` | `MCP_ENABLED` | `False` | 总开关 |
| `servers` | `MCP_SERVERS` | `""` | JSON 字符串，描述 server 集合 |
| `tool_prefix` | `MCP_TOOL_PREFIX` | `""` | 工具名前缀（可选） |
| `timeout` | `MCP_TIMEOUT` | `30.0` | 调用超时（秒） |

`get_servers() -> dict`：解析 `servers` JSON；兼容 `{"mcpServers": {...}}` 与扁平 `{...}`
两种写法，统一返回 `{server_name: server_spec}` 字典；空配置返回 `{}`。

模块级实例 `mcp_config = create_config(MCPConfig)`。

### 2. `MCPTool`（`mcp_tool_bridge.py`，继承 `Tool`）
包装单个远端工具，持有共享的 `fastmcp.Client` 引用与远端工具元数据。

- `name`：`_sanitize(prefix + 远端 name)`（仅保留 `[a-zA-Z0-9_-]`，其余替换为 `_`）。
- `description` / `parameters`：取远端 `description` / `inputSchema`；`inputSchema` 为空时
  回退为 `{"type": "object", "properties": {}}`。
- `execute(**kwargs)`：在 `async with self._client:` 上下文内以**原始远端工具名**调用
  `client.call_tool(原名, kwargs, raise_on_error=False)`；
  - `isError=True` → 抛 `ToolExecutionError`（附远端文本）。
  - 正常 → 用 `_extract_text` 把 `content` 内容块拼为字符串返回；无内容时回退到
    `structuredContent` 的 JSON 或占位串。

### 3. `MCPToolBridge`（`mcp_tool_bridge.py`）
- `__init__(transport, tool_prefix="", timeout=30.0)`：`transport` 透传给 `fastmcp.Client`
  （生产为 `{"mcpServers": {...}}` dict；测试可直接传 FastMCP 实例实现 in-memory）。
  构造单个 `Client` 实例并复用（fastmcp Client 对并发/嵌套 `async with` 采用引用计数，安全）。
- `async discover() -> list[MCPTool]`：`async with self._client: tools = await list_tools()`，
  为每个工具构造 `MCPTool`。

### 4. 装配（`application/container_config.py::_create_tool_registry`）
在现有条件注册序列尾部追加：

```python
try:
    from infrastructure.tools.mcp.mcp_config import mcp_config
    servers = mcp_config.get_servers()
    if mcp_config.enabled and servers:
        from infrastructure.tools.mcp import MCPToolBridge
        bridge = MCPToolBridge(
            transport={"mcpServers": servers},
            tool_prefix=mcp_config.tool_prefix,
            timeout=mcp_config.timeout,
        )
        for tool in await bridge.discover():
            registry.register(tool)
    else:
        logger.info("MCP_ENABLED=false 或未配置 servers，跳过 MCP 工具注册")
except Exception as e:  # fail-soft：MCP 为可选增强，失败不阻断启动（R4.1）
    logger.warning("MCP 工具发现失败，跳过注册: %s", e)
```

## 关键决策
- **每次调用短连接 vs 常驻连接**：复用单个 `Client` 实例，但每次 `discover`/`execute` 都用
  `async with` 进入会话。fastmcp 的引用计数使并发调用共享底层会话，避免常驻连接的生命周期
  管理（无显式 shutdown hook），兼顾健壮性与简洁性。
- **错误模型对齐**：所有远端错误统一翻译为 `ToolExecutionError`，复用既有 Agent Loop 容错。
- **fail-soft 启动**：MCP 发现失败仅告警，保证核心链路可用。

## 测试策略
用 `fastmcp.FastMCP` 定义内存 server（注册若干 `@server.tool`），把实例作为 `transport`
传入 `MCPToolBridge`，覆盖：发现工具、schema 映射、正常调用、错误调用 → `ToolExecutionError`、
前缀与名称清洗。无网络依赖。
