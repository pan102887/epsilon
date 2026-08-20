# MCP 协议适配层 — 任务清单

- [x] T1. `mcp_config.py`：`MCPConfig`（`MCP_` 前缀）+ `get_servers()` + 模块级实例。对应 R1。
- [x] T2. `mcp_tool_bridge.py`：`_sanitize` / `_extract_text` 辅助；`MCPTool`（Tool 子类）。对应 R2.2/R2.3/R3。
- [x] T3. `mcp_tool_bridge.py`：`MCPToolBridge`（构造 Client + `discover()`）。对应 R2.1。
- [x] T4. `__init__.py`：导出 `MCPToolBridge`、`MCPTool`。
- [x] T5. `container_config.py`：在 `_create_tool_registry` 末尾条件注册 MCP 工具，fail-soft。对应 R1.1/R4.1。
- [x] T6. `config.properties`：追加 MCP 配置段（默认禁用 + 示例注释）。
- [x] T7. 单测 `test/infrastructure/tools/mcp/test_mcp_tool_bridge.py`：基于 in-memory FastMCP server
      覆盖发现 / 调用 / 错误 / 前缀。对应 NFR-3。
- [x] T8. 运行 pytest 验证：MCP 7/7 通过；工具目录 176 通过（1 处 web_search hypothesis 边界用例失败，与本次改动无关）。
