# epsilon CLI Runtime 需求

## 背景评估

`docs/suggestions/epsilon-tui-cli-cloud-evolution.md` 判断成立：当前后端已有可复用的 `ChatServicePort`、`TaskAgentPort`、`ToolRegistry`、`Workspace` 与 DI 容器生命周期，TUI 不应通过本机 HTTP 绕行 FastAPI，而应作为与 FastAPI router 平级的 adapter 直接复用统一 Agent Runtime。

2026-05-27 修订：TUI 不应把工具列表作为用户级能力直接暴露。用户进入 TUI 后默认面对一个主 Agent；工具只作为 Agent 运行时可选能力，由 Agent Loop 根据任务自主决定是否调用。

本期仅实现阶段 1「epsilon TUI 骨架」。Approval、Skill/MCP、云端强隔离沙箱属于后续 spec，不在本期混入。

## 需求

### 1. 外层 CLI 命令

- 新增 `epsilon` console script。
- `epsilon` 默认进入 TUI 会话。
- `epsilon exec "..."` 执行一次性任务，直接调用 `TaskAgentPort.execute()`。
- `epsilon serve` 启动现有 FastAPI 应用。
- `epsilon --help` / `epsilon --version` 可用。

### 2. CLI Runtime 生命周期

- CLI/TUI 必须调用 `configure_container()` 并复用 `container.start()` / `container.stop()`。
- CLI Runtime 解析 `ChatServicePort`、`TaskAgentPort`、`ModelRegistryPort`、`Workspace` 等运行时依赖。
- CLI Runtime 不直接向 TUI 暴露 `ToolRegistry` 或工具列表；工具 schema 仅由 Agent/Chat 编排层内部使用。
- TUI 和 `exec` 不得依赖 `application.routers.*`，不得调用本机 HTTP API。

### 3. TUI 会话状态

- TUI 必须集中维护 `session_id`、当前模型、approval 模式、是否退出等状态。
- 新会话命令应生成新的 `session_id` 并清理旧会话上下文。
- 首期 approval 仅保留状态展示，不接入工具确认流。

### 4. TUI 交互与内部命令

- TUI 支持自然语言输入，并默认进入主 Agent 会话。
- 主 Agent 会话通过 `ChatServicePort.stream_chat()` 进入既有 Chat 编排层；当 `CHAT_TOOL_CALLING_ENABLED=true` 且存在工具 schema 时，由 `ChatServiceAdapter` 委托 `AgentPort.run_streaming()`，工具选择由 Agent 决定。
- TUI 支持内部 slash 命令：
  - `/help`
  - `/new`
  - `/model`
  - `/model <name>`
  - `/config doctor`
  - `/quit`
- TUI 不提供 `/tools list` 这类工具清单命令。
- 未知 slash 命令应给出可读错误，不进入模型调用。

### 5. 验证

- 增加单元测试覆盖 slash 命令路由与 CLI Runtime facade 行为。
- 运行与本期新增代码相关的 pytest 子集。

### 6. 顶层 application 包导入边界

- 顶层 `application` 包不得在导入时创建 FastAPI app，不得隐式调用 `configure_container()`。
- `import application`、`import application.cli.runtime`、`from application.cli.runtime import CliRuntime` 不得加载 `application.api.server_app`。
- `from application import app` 与 `from application import service_config` 继续作为兼容公开接口保留，但必须按属性访问惰性加载。
- 访问 `application.service_config` 不得创建 FastAPI app；只有访问 `application.app` 时才允许加载现有 FastAPI adapter。
