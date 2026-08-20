# MCP 协议适配层 — 需求文档

## 背景

本项目（`epsilon-boot`）的 Agent 工具体系基于私有的 `Tool` 抽象基类
（`domain/agent/tools.py`），所有工具通过 `ToolRegistry` 注册后供 ReAct Agent
调用。当前缺少对 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 的支持：
无法接入业界已大量提供的 MCP Server（文件、检索、数据库、SaaS 工具等）。

业界主流方案对比：Anthropic / OpenAI / Cursor / Cline 均已原生支持 MCP 作为外部工具
接入标准。本项目已在 `pyproject.toml` 声明 `fastmcp>=2.14.2` 依赖（实际安装 3.2.0），
但尚未实现适配层。

## 目标

通过 `fastmcp` 客户端连接一个或多个远端 MCP Server，将其暴露的工具**桥接**为本项目的
`Tool` 子类实例，注册到 `ToolRegistry`，使 Agent 能像调用内置工具一样**透明调用**远端
MCP 工具，无需感知 MCP 协议细节。

## 需求（EARS 格式）

### R1 — 配置驱动的启用
- R1.1 当配置项 `MCP_ENABLED=false`（默认）时，系统不得创建任何 MCP 连接或注册任何 MCP 工具。
- R1.2 当 `MCP_ENABLED=true` 且 `MCP_SERVERS` 配置了至少一个有效 server 时，系统应在启动期
  连接这些 server 并注册其工具。
- R1.3 `MCP_SERVERS` 以 JSON 字符串描述 server 集合，兼容 fastmcp 的 `mcpServers` 格式
  （支持 `url` 远端 HTTP/SSE 传输与本地 `command` 传输）。

### R2 — 工具发现与桥接
- R2.1 系统应在启动期调用 MCP Server 的 `list_tools`，为每个远端工具创建一个 `Tool` 子类实例。
- R2.2 每个桥接工具的 `name` / `description` / `parameters`（JSON Schema）应源自远端工具的
  `name` / `description` / `inputSchema`。
- R2.3 当配置 `MCP_TOOL_PREFIX` 非空时，桥接工具的注册名应加上该前缀，以避免与内置工具或多
  server 间命名冲突；工具名须满足 OpenAI function calling 命名约束（`[a-zA-Z0-9_-]`）。

### R3 — 透明调用
- R3.1 当 Agent 调用某个 MCP 桥接工具时，系统应通过 fastmcp 客户端向远端发起 `call_tool`，
  并将远端返回内容转换为字符串回灌给 Agent。
- R3.2 当远端工具返回错误（`isError=True`）或调用过程抛出异常时，系统应抛出 `ToolExecutionError`，
  与现有工具错误模型保持一致。

### R4 — 启动健壮性（fail-soft）
- R4.1 当 MCP 连接或工具发现失败时，系统应记录告警并跳过 MCP 工具注册，**不得**阻断应用启动
  （MCP 为可选增强能力，不是核心链路）。

## 非功能需求
- NFR-1 不破坏现有 `Tool` / `ToolRegistry` 抽象与既有工具子类（不变量保持）。
- NFR-2 遵循项目 DDD 分层：适配代码置于 `infrastructure/tools/mcp/`，配置走 `PropertiesBaseSettings`。
- NFR-3 单测不得依赖真实网络：使用 fastmcp 的 in-memory（FastMCP 实例）传输。

## 范围
- 本期仅聚焦 **MCP 协议适配（问题 #1）**。多模态消息、Prompt 缓存为独立 Feature，不在本期范围。
