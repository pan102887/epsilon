# epsilon TUI CLI 与云平台演进参考意见

> 当前状态备注（2026-06-14）：本文是 TUI-first 与云平台方向的历史参考意见，不是当前实现说明。当前代码已经落地 `epsilon` console script、Textual TUI、`epsilon exec`、`epsilon serve`、会话恢复/删除、Run 创建/查询/订阅/继续/审批/取消，以及 MCP tool bridge 基础适配；Web/FastAPI/TUI 也已通过共享 `RunApplicationService` 接入后台 Run runtime。本文后续章节仍保留早期建议语境，阅读时应优先以 [../project-overview.md](../project-overview.md)、[../architecture.md](../architecture.md)、[../development.md](../development.md) 和 [../tools.md](../tools.md) 为当前事实源。

## 背景

当前项目已经具备较完整的 Agent Runtime 基础：`ChatServicePort`、`TaskAgentPort`、`AgentPort`、`ToolRegistry`、`ScopedToolRegistry`、`Workspace`、模型路由、会话存储、FastAPI HTTP 入口和 Next.js 控制台。

后续如果希望对标 Codex / Copilot CLI，建议不要把产品继续定义为“Web 前端 + FastAPI 后端”，而是演进为：

```text
epsilon TUI / FastAPI API / Web 控制台
  -> 统一 Agent Runtime
  -> Agent / Skill / MCP / Tool / Workspace / Sandbox
```

FastAPI 应降级为 HTTP adapter；TUI 是新的本地主入口；Web 控制台是可选 client；核心能力由统一 Runtime 提供。

## 总体判断

系统对 TUI-first 演进的支持度较高，且第一期 CLI/TUI 主干已经落地；后续产品层重点从“建立入口”转向“完善 Skill/MCP 管理、云端沙箱和交互体验”。

已落地基础：

- `textual` 已作为 TUI 基础依赖，`application/cli/` 已提供 `epsilon` console script、Textual TUI、`epsilon exec` 和 `epsilon serve`。
- `ChatServicePort.stream_chat()` 已支持异步流式输出，适合 TUI 直接渲染增量内容。
- `TaskAgentPort.execute()` 已支持一次性任务执行，适合 `epsilon exec "..."`。
- DI 容器已有 `container.start()` / `container.stop()`，可复用 FastAPI 同一套资源生命周期。
- `Workspace` 已约束文件工具和执行工具的工作区边界。
- `ShellExecTool` / `PythonExecTool` 默认关闭，符合本地 CLI 的安全默认值。
- TUI 内部命令已经覆盖 `/help`、`/new`、`/sessions`、`/resume`、`/delete!`、`/model`、`/config doctor`、`/run chat`、`/run task`、`/runs`、`/run status`、`/run watch`、`/run continue`、`/run approve`、`/run cancel`。
- HITL 审批已有 Chat/Task/Run 恢复链路，Run 级审批恢复通过共享 `RunApplicationService` 进入同一状态机。
- MCP tool bridge 已存在，可通过 `MCP_ENABLED` / `MCP_SERVERS` 把远端 MCP 工具包装为内部 `Tool`。

主要剩余缺口：

- Skill 尚未成为运行时一等能力，缺少 `SkillRegistryPort`、本地 skill 发现、工具权限声明和 TUI 管理命令。
- MCP 已具备工具桥接基础，但还缺少完整的 TUI 配置、诊断、权限和生命周期管理体验。
- 云端多用户沙箱仍未落地；本地 `LocalFilesystemWorkspace` 不能直接作为多租户隔离方案。
- 聊天侧 trace、tool call 展示、重试、中止和审批表单体验仍需继续打磨。

## 推荐产品形态

### 外层命令

外层 shell 命令应少而稳定，只承担启动和自动化职责：

```bash
epsilon                 # 默认进入 TUI
epsilon exec "..."      # 非交互执行一次任务，适合脚本/CI
epsilon serve           # 启动 FastAPI 服务，服务 API/Web/云平台
epsilon --help
epsilon --version
```

不要把常用交互做成大量 shell 子命令，例如 `epsilon skill list`、`epsilon mcp list`。这类命令应放到 TUI 内部。

### TUI 内部命令

在 `epsilon` TUI 会话内支持自然语言输入和 slash 命令：

```text
/help
/new
/model
/tools list
/skills list
/mcp list
/config doctor
/approval
/quit
```

这样更接近 Codex / Copilot CLI 的用户心智：用户进入一个持续会话，在同一个界面内完成配置、诊断、模型切换、任务执行和工具确认。

## 推荐架构调整

### Runtime 与入口分离

新增 CLI/TUI 后，入口关系应调整为：

```text
application/cli
  -> CliRuntime
  -> ChatServicePort / TaskAgentPort / ToolRegistry

application/server_app.py
  -> FastAPI Router
  -> ChatServicePort / TaskAgentPort / ToolRegistry
```

TUI 不应依赖 `application.routers.*`，也不应通过本机 HTTP 调用 FastAPI。TUI 和 FastAPI router 应平级复用同一套 Port/Adapter。

### 建议新增模块

```text
epsilon-boot/src/application/cli/
  __init__.py
  main.py              # epsilon 命令入口
  runtime.py           # CLI Runtime 生命周期
  tui.py               # TUI 主循环
  commands.py          # slash 命令路由
  session.py           # TUI 会话状态
```

`CliRuntime` 负责：

- 初始化 `src` 路径和日志。
- 调用 `configure_container()`。
- 启动 / 停止 `container`。
- 解析 `ChatServicePort`、`TaskAgentPort`、`ToolRegistry`、`ModelRegistryPort` 等运行时依赖。
- 向 TUI 提供统一的应用服务 facade。

### TUI 状态模型

第一期需要显式维护：

- 当前 `session_id`
- 当前模型
- 当前 workspace
- 当前 approval 模式
- 当前消息历史展示状态
- 当前运行任务 / 是否可中止
- 可用工具列表
- 可用 Skill / MCP 状态

这些状态不要散落在 prompt 回调里，应集中为 `TuiSessionState` 或等价对象。

## 本地沙箱建议

第一期采用“受控 workspace 沙箱”，不先实现强隔离容器。

安全基线：

- 文件工具继续强制通过 `Workspace`。
- 禁止工具直接访问宿主绝对路径。
- `ShellExecTool` / `PythonExecTool` 默认关闭。
- 启用 shell/python 后必须接 approval。
- 子进程 cwd 锁定到 workspace。
- 保留超时、输出截断、环境变量脱敏、内存限制。

建议 approval 模式：

```text
suggest  # 只建议，不自动执行高风险工具
ask      # 高风险工具执行前询问
auto     # 仅允许低风险工具自动执行
```

需要新增 `ApprovalPort`，由 TUI adapter 实现用户确认。云平台后续可用 Web/API adapter 实现同一 Port。

## Skill 与 MCP 建议

Skill / MCP 不应直接绕过工具系统，而应接入现有权限边界。

建议抽象：

```text
SkillRegistryPort
McpServerRegistryPort
```

第一期 Skill 可先支持本地文件发现：

```text
.epsilon/skills/
  my-skill/
    SKILL.md
```

Skill 元数据建议包含：

- 名称
- 描述
- 触发条件
- prompt 指令
- 允许工具
- 可选模型
- 可选 MCP server / tool 依赖

MCP 第一期可先做配置式连接和列举，不急于实现复杂权限 UI。MCP tool 最终应映射到 `ScopedToolRegistry` 或等价权限层。

## 云平台演进建议

云平台方向与本地 CLI 可以共享 Runtime，但不能共享本地沙箱实现。

云端建议形态：

```text
FastAPI API
  -> Auth / Tenant / Session
  -> SandboxPort
  -> isolated container / pod
  -> Agent Runtime
  -> Workspace volume
  -> artifacts / trace / audit
```

云端必须满足：

- 多用户不能共享同一个 `LocalFilesystemWorkspace`。
- shell/python 不能在 API 服务进程内执行。
- 每个用户 / 会话 / 任务应分配独立 workspace。
- 会话状态使用 Redis 或数据库，不使用本地 file backend 承载多实例生产会话。
- artifact 使用独立存储，后续可接 OSS / S3。
- 记录用户、会话、任务、tool call、approval、trace、artifact 元数据。
- 支持租户级工具权限、Skill 权限、MCP 权限和模型权限。

建议新增 `SandboxPort`，本地实现为 workspace sandbox，云端实现为容器 / Pod sandbox。

## 分阶段路线

### 阶段 1：epsilon TUI 骨架

目标：建立 TUI-first 产品形态。

- 新增 `epsilon` 命令入口。
- `epsilon` 默认进入 TUI。
- 支持流式 chat。
- 支持 `epsilon exec "..."`。
- 支持 `epsilon serve`。
- 支持 `/help`、`/model`、`/tools list`、`/config doctor`、`/quit`。
- CLI/TUI 直接调用 `ChatServicePort` / `TaskAgentPort`，不走 HTTP。

### 阶段 2：TUI 可用性与安全闭环

目标：补齐接近 Codex 的关键体验。

- 支持 Ctrl+C 中止当前生成。
- 支持会话新建和清理。
- 展示 tool call / trace。
- 新增 `ApprovalPort`。
- shell/python 执行接入 approval。
- 补齐 `/approval` 命令。

### 阶段 3：Skill / MCP 运行时

目标：让 Skill / MCP 成为 TUI 内的一等能力。

- 实现 `SkillRegistryPort`。
- 实现 `/skills list`。
- 支持从本地 `.epsilon/skills` 加载 Skill。
- 实现 `McpServerRegistryPort`。
- 实现 `/mcp list`。
- MCP tool 接入工具权限边界。

### 阶段 4：云端多用户沙箱

目标：支持容器化部署和多用户隔离。

- 新增 `SandboxPort`。
- 云端 shell/python 进入隔离容器 / Pod。
- 会话状态迁移到 Redis / DB。
- artifact 存储外置。
- 增加租户级权限和审计。

## 推荐 Spec 拆分

建议后续不要用一个大 spec 承载所有内容，而是拆为：

- `docs/spec/epsilon-cli-runtime/`
- `docs/spec/tui-approval-runtime/`
- `docs/spec/skill-mcp-registry/`
- `docs/spec/cloud-multi-tenant-sandbox/`

优先实现 `epsilon-cli-runtime`。完成 TUI 骨架后，再进入 approval、Skill/MCP 和云端强沙箱。

## 风险与边界

最高风险不是技术实现，而是范围失控。第一期不应同时做完整 TUI、Skill、MCP、approval 和云端沙箱。

第一期建议坚持以下边界：

- 做 TUI 骨架，不做复杂布局。
- 做 chat / exec / serve，不做完整管理后台。
- 做 `/tools list` 和 `/config doctor`，`/skills list`、`/mcp list` 可先返回未启用状态。
- shell/python 保持默认关闭。
- 不实现云端强隔离，只预留 `SandboxPort` 设计点。

这样可以尽快把项目形态从“Web 后端服务”转为“Codex 类本地 Agent CLI + 可选 API/Web 服务”，同时不破坏当前 DDD 和 Port/Adapter 架构。
