# epsilon CLI Runtime 设计

## 模块布局

新增 `epsilon-boot/src/application/cli/`：

- `main.py`：console script 入口，解析 `epsilon` / `epsilon exec` / `epsilon serve`。
- `runtime.py`：CLI Runtime 生命周期与应用服务 facade。
- `tui.py`：基于 `prompt-toolkit` 的交互循环。
- `commands.py`：slash 命令路由。
- `session.py`：TUI 会话状态。

该包位于 application 层，职责是入口编排。它只依赖 domain Port、common container 和组合根，不导入 FastAPI router，也不直接 new infrastructure adapter。

## 顶层 application 包导出

`application/__init__.py` 保留 `app` 与 `service_config` 的兼容导出，但采用与
`application/api/__init__.py` 一致的 lazy export 模式：

1. 顶层模块只声明 `__all__ = ["app", "service_config"]` 和 `__getattr__`。
2. 访问 `application.app` 时才导入 `application.api.server_app`，从而创建 FastAPI app 并执行现有容器装配逻辑。
3. 访问 `application.service_config` 时只导入 `application.api.server_config`，不得加载 `application.api.server_app`。
4. CLI/TUI 导入 `application.cli.*` 只触发普通 Python 包加载，不得因顶层兼容导出提前初始化 HTTP adapter。

## Runtime

`CliRuntime` 是异步上下文管理器：

1. `configure_container()`
2. `container.start()`
3. 解析所需 Port
4. 对外暴露 `stream_main_agent()`、`execute_once()`、`list_models()`、`doctor()`
5. 退出时 `container.stop()`

`stream_main_agent()` 构造 `ChatRequestVO(stream=True)`，逐个透传 `StreamingChunk.delta_content`。它不接收或暴露工具列表；工具 schema 由 `ChatServiceAdapter` 在容器装配阶段注入，并在启用 tool calling 时交给 `AgentPort`。`execute_once()` 构造 `Task` 并返回 `TaskResult`。

## TUI

`TuiApp` 接收 `CliRuntime`，用 `PromptSession.prompt_async()` 读取输入：

- 空输入跳过。
- `/` 开头交给 `SlashCommandRouter`。
- 普通输入调用 `runtime.stream_main_agent()` 并即时输出增量文本。

`Ctrl+C` 首期只中断当前输入或当前流，完整的可取消任务控制留到阶段 2。

## 主 Agent 与工具边界

TUI 默认启动的是一个主 Agent 会话，而不是工具选择器。CLI 不解析 `ToolRegistry`，也不向用户展示工具列表。工具是否可用、是否调用、调用哪个工具，均由 Chat 编排层和 Agent Loop 根据 Prompt、模型输出与权限边界决定。

## Slash 命令

`SlashCommandRouter` 返回结构化 `CommandResult`，包含 `message` 与 `should_exit`。路由层只修改 `TuiSessionState`，具体运行时信息通过 `CliRuntime` facade 读取。

支持命令仅保留会话、模型、诊断和退出类命令：`/help`、`/new`、`/model`、`/model <name>`、`/config doctor`、`/quit`。`/tools list` 不属于用户级 TUI 命令。

## Serve 命令

`epsilon serve` 调用 `uvicorn.run("application.api.server_app:app", ...)`，继续复用现有 FastAPI 应用创建逻辑。

## 错误处理

CLI 顶层捕获异常并打印可读错误后返回非零退出码。模型/容器启动错误不吞掉，避免误以为 TUI 已正常就绪。

## 后续边界

- ApprovalPort 与 shell/python 工具确认流不在本期实现。
- SkillRegistryPort、McpServerRegistryPort 不在本期实现。
- 云端 SandboxPort 不在本期实现。
