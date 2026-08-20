# MCP 协议适配层 — 交付总结

## Feature
`mcp-protocol-adapter`：通过 fastmcp 将远端 MCP Server 工具桥接为内部 `Tool`，
注册进 `ToolRegistry`，供 ReAct Agent 透明调用。仅聚焦问题 #1（MCP 协议适配）。

## 产出物
- 规范：`docs/spec/mcp-protocol-adapter/{requirement,design,tasks}.md`
- 代码：
  - `src/infrastructure/tools/mcp/mcp_config.py` — `MCPConfig`（`MCP_` 前缀）+ `get_servers()`
  - `src/infrastructure/tools/mcp/mcp_tool_bridge.py` — `MCPTool`(Tool 子类) + `MCPToolBridge`
  - `src/infrastructure/tools/mcp/__init__.py` — 导出 `MCPTool` / `MCPToolBridge`
  - `src/application/container_config.py` — `_create_tool_registry` 末尾 fail-soft 条件注册
  - `config.properties` — 新增 MCP 配置段（默认禁用）
- 测试：`test/infrastructure/tools/mcp/test_mcp_tool_bridge.py`

## 关键设计决策
- **领域零侵入**：远端能力经 `Tool` 子类翻译，Agent Loop / `ToolRegistry` 抽象不变。
- **共享 Client + 引用计数会话**：复用单 `Client`，每次 `discover`/`execute` 用 `async with`
  进入会话，规避常驻连接生命周期管理，兼容并发工具调用。
- **错误模型对齐**：远端 `is_error` 或调用异常统一翻译为 `ToolExecutionError`。
- **fail-soft 启动**：MCP 连接/发现失败仅告警，不阻断核心链路。
- **属性兼容**：`call_tool` 结果同时兼容 `is_error`/`isError`、`structured_content`/`structuredContent`。

## 测试覆盖
in-memory `FastMCP` server（无网络）覆盖：工具发现与 schema 映射、正常调用、`run()` 端到端
管道、错误调用 → `ToolExecutionError`、前缀注入、名称清洗、配置 JSON 解析。**7/7 通过**；
工具目录回归 176 通过（1 处 `web_search` hypothesis 边界用例失败，与本次改动无关，为既有问题）。

## 后续可选项（不在本期范围）
- 常驻连接池 + 健康检查 + 重连退避，降低逐调用连接延迟。
- MCP 工具调用接入审批策略（HITL）与 OpenTelemetry 追踪。
- 问题 #2 多模态消息、问题 #3 Prompt 缓存作为独立 Feature 迭代。
