# TODO LIST

## 2026-06-02 项目重新评估：coding-agent + Agent 工作台/云平台基座

### 当前定位

本项目不是单一 coding-agent，也不是单一聊天工作台，而是双重定位：

1. **coding-agent runtime**：面向本地仓库和工程任务，完成“理解需求 -> 读写代码 -> 执行命令/测试 -> 产出可审计结果”的闭环。
2. **Agent 工作台/云平台基座**：面向多入口、多用户、多 Agent、多工具、多模型和未来 Skill/MCP/Sandbox 的平台化运行底座。

两条主线共享同一套领域模型、Port/Adapter、DI 容器、工具权限、审批、trace、artifact 和 sandbox 能力，不能出现“本地 coding-agent 一套、云平台工作台一套”的分叉。

```text
epsilon TUI / epsilon exec / FastAPI API / Web 控制台
  -> 统一 Agent Runtime
  -> Agent / Task / Chat / Skill / MCP / Tool
  -> Workspace + Permission + HITL Approval
  -> Trace + Artifact + Evaluation + Observability
  -> Local Runtime + Cloud Sandbox + Multi-tenant Platform
```

后续优先级按“coding-agent 最小可用闭环”和“Agent 平台基座能力”共同排序：短期优先让本地 TUI/CLI 真正可完成工程任务；中长期把同一套 runtime 服务化为云端 Agent 工作台。

### 已具备的基础能力

- [x] DDD + 六边形架构：`domain/` 定义 Port 和值对象，`infrastructure/` 实现 Adapter，`application/` 装配 HTTP / CLI / TUI。
- [x] 统一运行时：`epsilon`、`epsilon exec`、`epsilon serve` 复用 `CliRuntime`、DI 容器和后端 Port。
- [x] Textual TUI：默认 `epsilon` 进入全屏 TUI，支持流式输出、工具事件、取消、退出和基础 slash commands。
- [x] ReAct Agent Loop：支持同步、流式、事件流、工具调用、委派和上下文压缩。
- [x] 工具系统：当前 10 个可注册工具，包括文件读写、目录、Web/HTTP、Shell/Python 执行和 Agent 委派。
- [x] Workspace 边界：文件与执行类工具通过 `Workspace` 约束到工作区，不直接访问宿主绝对路径。
- [x] HITL 基础能力：已实现审批值对象、策略、状态存储、Agent 中断/恢复、HTTP resume API、SSE/TUI approval_required 提示。
- [x] 本地/Redis 会话后端、模型路由、OpenTelemetry、Prometheus、健康检查和较完整测试覆盖。

### 当前主要差距

- TUI 还不是完整 coding-agent 操作台：缺少交互式审批表单、会话/任务历史、文件变更视图、日志面板和可恢复运行记录。
- Agent 能编辑代码，但缺少一等 coding workflow：没有显式的 plan/checklist、diff 审阅、测试建议、提交/回滚辅助和 artifact 管理。
- 工具调用可观测性不足：tool call、approval、shell/python 命令、模型轮次、artifact 还没有统一持久化 trace。
- Shell/Python 仍是 workspace 级约束，不是强隔离沙箱；云端多用户场景不能直接执行宿主进程内命令。
- Skill/MCP 依赖已存在，但 registry、权限映射、配置发现和 TUI/API 操作入口尚未实现。
- 前端控制台仍偏聊天/任务面板，尚未支持 HITL 审批、工具事件时间线、代码 diff 和 artifact 浏览。
- 云平台基座还缺少用户/租户/权限/会话路由/artifact 存储/审计报表等生产级平台能力。

## P0：统一 Runtime 的最小可用闭环

### P0.1 本地 coding-agent：TUI 审批与高风险工具闭环 ✅（见 `docs/spec/tui-approval-workflow/`）

- [x] 在 TUI 中实现 `approval_required` 交互式审批面板，而不是只展示 resume API 提示。（`ApprovalScreen`，`src/application/cli/approval_screen.py`）
- [x] 支持对每个待审批 action 选择 `approve` / `edit` / `reject`，并调用 `/api/chat/sessions/{session_id}/approvals/{approval_id}/resume` 或直接走 `ChatServicePort.resume_approval`。（新增 inline 流式恢复通路 `ChatServicePort.stream_resume_approval` + `CliRuntime.resume_main_agent_events`）
- [x] `edit` 决策提供 JSON 参数编辑区，提交前做 JSON 校验并显示失败原因。（`ApprovalScreen.action_submit_edit`，校验失败原地报错不关面板）
- [x] `/approval` 命令支持查看当前模式、最近 pending approval、切换本地策略。（`SlashCommandRouter` `/approval`，本地会话级 `approval_mode`：ask/auto/manual）
- [x] TUI 恢复后继续渲染后续 assistant/tool events，支持再次中断。（`_drive_events` 续播闭环，支持反复中断-恢复）

### P0.2 统一运行产物与日志 ✅（见 `docs/spec/local-trace-artifacts/`）

- [x] 建立 `.epsilon/` 本地目录规范。（`StorageTier` 抽象 + `LocalFileTierResolver` 的 tier→目录映射与 `sessions/traces/artifacts/logs` 子目录布局，物理路径下沉 infrastructure。）
- [~] `.epsilon/sessions/` 保存 TUI 会话摘要和恢复索引。（`Sessions_Dir` 职责已定义、resolver 已提供 `sessions_dir()`；会话摘要/恢复索引的写入方属后续 spec，本 spec 只交付子目录抽象。）
- [x] `.epsilon/traces/` 保存每轮模型调用、tool call、approval decision、usage、latency。（既有 `LocalFileTraceStoreAdapter` 复用并纳入 tier 抽象，PROJECT tier 与既有 `.epsilon/traces` 等价。）
- [~] `.epsilon/artifacts/` 保存任务产物、命令输出摘要、生成文件清单。（`ArtifactTrace` 值对象、`ArtifactStorePort`、`LocalFileArtifactStoreAdapter` 与 DI 装配已交付；工具/入口的写入侧接入属后续 spec。）
- [x] `.epsilon/logs/` 保存 TUI/CLI 本地日志；当前 TUI 默认没有文件日志。（`Local_File_Log_Sink` + 脱敏 Filter，默认开启，落 USER tier `~/.epsilon/<project-hash>/logs/`。）
- [x] `.epsilon/config.local.properties` 支持本地覆盖配置，并明确优先级低于环境变量。（优先级链 env > local > properties > .env，缺失不报错，不入库。）
- [x] 为云端预留同构 trace/artifact schema，后续可从本地 file backend 切换到 Redis/DB/OSS。（同构 JSONL schema + `schema_version` 元数据 + tier 抽象与后端解耦。）

### P0.3 结构化 tool trace

- [x] 定义 `AgentStepTrace` / `ToolCallTrace` / `ApprovalTrace` / `ArtifactTrace` 值对象。（`AgentStepTrace`/`ModelCallTrace`/`ToolCallTrace`/`ApprovalTrace`/`ErrorTrace`/`SessionTrace` 已定义；`ToolCallTrace` 新增 `metadata` 结构化元数据字段；`ArtifactTrace` 值对象与 `ArtifactStorePort`/本地文件存储由 `docs/spec/local-trace-artifacts/` 补齐。）
- [x] 在 `ReActAgentAdapter` 每轮记录模型请求摘要、响应类型、tool_calls、审批中断、工具结果和错误。（model/tool_calls/审批中断/工具结果已接入；`ErrorTrace` 现已在 `run`/`resume`/`run_streaming`/`run_events` 四入口异常路径记录，`max_rounds==1` 快速路径补录 `ModelCallTrace`。见 `docs/spec/structured-tool-result/`。）
- [~] Shell/Python trace 记录命令、cwd、退出码、耗时、stdout/stderr 截断摘要和脱敏环境摘要。（工具 `execute()` 返回 `ToolExecutionResult`，`ToolCallTrace.metadata` 已记录 command_summary/code_summary、working_dir、exit_code、stdout_bytes/stderr_bytes、truncated、memory_limited，耗时经 `latency_ms`；脱敏环境摘要字段待补。见 `docs/spec/structured-tool-result/`。）
- [~] 文件工具 trace 记录逻辑路径、操作类型、变更前后摘要，不记录完整敏感内容。（`ToolCallTrace.metadata` 已记录 logical_path、operation、bytes_written / lines_returned 等；变更前后内容摘要待补。见 `docs/spec/structured-tool-result/`。）
- [x] Chat、Task、TUI、HTTP 共用同一 trace port，避免每个入口各记一套（均经 `AgentPort → ReActAgentAdapter`，共享 DI 中单例 `TraceStorePort`）。
- [x] trace schema 同时支持 coding-agent 工程任务和通用 Agent 工作台任务（`ModelCall/ToolCall/Approval/Error` 均为通用摘要结构，不绑定 coding 场景）。

### P0.4 Coding workflow 基础命令 ✅（见 `docs/spec/coding-workflow-commands/`）

- [x] TUI 增加 `/status`：展示当前 session、model、workspace、pending approval、最近 trace。
- [x] TUI 增加 `/diff`：展示当前工作区 git diff 摘要，走受控 `git_diff` 工具，不回退任意 shell。
- [x] TUI 增加 `/tests`：展示最近测试命令、结果和失败摘要（只读 trace，不主动执行测试）。
- [x] TUI 增加 `/files`：展示本次会话读写过的文件清单（来自 trace metadata）。
- [x] `epsilon exec` 输出结构化 JSON 可选模式，便于 CI/脚本读取状态、usage、trace 和 artifacts。

### P0.5 Agent 工作台 API 基础闭环

- [x] HTTP/SSE API 输出与 TUI 事件模型对齐，统一 `assistant_delta`、`tool_start`、`tool_result`、`tool_error`、`approval_required`。（兼容旧 data 分片，新增显式 `event_type` 与命名 SSE 事件。）
- [x] `/api/chat`、`/api/task/execute` 返回 trace/artifact 引用 ID，而不是只返回最终文本。（新增 `trace_id` / `trace_ref` / `artifact_ids` / `artifact_ref`；任务未传 session 时引用为空。）
- [x] 增加 trace 查询 API，供 Web 控台和云平台复用（`GET /api/traces`、`GET /api/traces/{session_id}`；`TraceStorePort` 已注册进 DI，写入侧与读取侧共享同一实例）。新增 artifact 查询 API（`GET /api/artifacts/{session_id}`）复用 `ArtifactStorePort`。
- [x] 前端控制台支持 HITL 审批和工具事件时间线，避免云端工作台落后于本地 TUI。（Run 面板支持 approval_required 动作展示与 approve/edit/reject 提交，事件列表继续展示工具/审批时间线摘要。）

## P1：可靠 coding-agent 与平台基座

### P1.1 代码编辑与审阅能力

- [ ] 引入一等 Patch/Edit 抽象，区分普通文件写入、精确替换、结构化 patch 和批量重写。
- [ ] 为 `edit_file` 增加更强的失败诊断：多匹配、零匹配、上下文漂移、行尾差异。
- [ ] 在工具层记录编辑前后 hash，恢复时检测文件是否已被用户或其他进程修改。
- [ ] 增加“只读计划模式”：允许读文件、列目录、搜索，但禁止写文件和执行命令。
- [ ] 增加“review 模式”：默认不改代码，只输出发现、风险、测试缺口和建议 patch。

### P1.2 测试、评估与质量门禁

- [ ] 建立测试命令推荐器：根据改动文件推断应运行的 pytest / frontend lint / build 命令。
- [ ] 将 `uv run --frozen pytest test`、前端 lint/build、evaluation 脚本纳入可配置 quality gates。
- [ ] 将历史 `docs/evaluation/` 指标接入 CI 门禁，回归超过阈值时失败。
- [ ] 为 coding-agent 场景增加评测集：代码修改正确率、工具选择正确率、测试修复率、无关改动率。
- [ ] 为 Agent 工作台场景增加评测集：多轮任务完成率、工具调用成功率、委派正确率、审批恢复成功率。
- [ ] 对 prompt / tool description 变更建立 A/B 评估或基线对比。

### P1.3 Skill / MCP Registry

- [ ] 设计 `SkillRegistryPort`：发现、校验、列举、加载本地 Skill。
- [ ] 设计 Skill 文件格式：Markdown + YAML metadata，包含名称、说明、提示词、允许工具、模型偏好、风险等级。
- [ ] 设计 `McpServerRegistryPort`：读取 MCP server 配置，列举 MCP tools。
- [ ] 将 MCP tool 映射进 `ToolRegistry` / `ScopedToolRegistry`，统一走权限与 HITL。
- [ ] TUI 增加 `/skills list`、`/skills use`、`/mcp list`。
- [ ] API 增加 Skill/MCP 查询接口，前端可展示可用能力。

### P1.4 前端 Agent 工作台 / coding-agent 控制台

- [ ] 前端 SSE 支持 `tool_start`、`tool_result`、`tool_error`、`approval_required` 事件，而不只拼接 assistant 文本。
- [ ] 增加 HITL 审批 UI：展示工具名、参数、风险、允许决策，支持 approve/edit/reject。
- [ ] 增加 trace timeline：模型轮次、工具调用、审批、测试命令和 artifacts。
- [ ] 增加 diff viewer：展示本次任务产生的文件变更。
- [ ] 增加 task artifact viewer：展示生成文件、命令输出、测试报告。
- [ ] 增加 Agent/Skill/Tool 管理视图，服务通用 Agent 工作台而不仅是代码任务。

## P2：云端 Agent 工作台平台化

### P2.1 强隔离 Sandbox

- [ ] 规划 `SandboxPort`：创建、回收、限制和观测每个任务/会话的隔离环境。
- [ ] 云端 shell/python 必须进入隔离容器或 Pod，不能在 API 服务进程内执行。
- [ ] 每个用户/会话/任务独立 workspace，隔离文件系统、进程、网络、CPU、内存和超时。
- [ ] 支持 sandbox artifact 上传到 OSS/S3/R2 等独立对象存储。
- [ ] 支持 sandbox 日志和 trace 回传到统一 trace store。

### P2.2 多用户、多租户与权限

- [x] 会话状态生产环境使用 Redis 或数据库，不使用本地 file backend 承载多实例会话。
- [ ] 实现会话路由：分布式环境下将同一 session 路由到对应实例或共享状态后端。
- [ ] 持久化用户、会话、任务、tool call、approval、trace、artifact 元数据。
- [ ] 增加租户级工具权限、Skill 权限、MCP server 权限、模型权限。
- [ ] 增加组织级审批流和审计报表。
- [ ] 支持云端工作台项目/空间概念，把用户、仓库、Agent、Skill、MCP、artifact 归入统一资源边界。

### P2.3 长期记忆与知识检索

- [ ] 设计 coding-agent 记忆边界：用户偏好、仓库约定、常用命令、失败教训。
- [ ] 引入 RAG 管道：文档切分、向量检索、仓库知识索引。
- [ ] 支持 per-repo knowledge base，避免不同仓库知识串扰。
- [ ] 将长期记忆写入/读取纳入 HITL 或可配置权限。

## 技术债与生产风险

### P0 技术债

- [ ] 调整请求日志中间件：`LOGGING_REQUEST_ENABLED` 需要被 `server_app.py` 尊重；收集阶段即按字节上限截断；对 `/api/chat`、`/api/task/execute` 默认脱敏或不记录 body。
- [ ] 补齐异步 HTTP 客户端生命周期关闭：`OpenAICompatibleAdapter`、`HttpRequestTool`、`WebFetchTool` 的内部 `httpx.AsyncClient` 需要由容器统一 `aclose()` 或改为注入共享 client。
- [ ] 移除或条件挂载生产 app 中的 `test_router`、`/api/test/get` 和 `/resource`。

### P1 技术债

- [ ] 清理 `common/` 反向依赖 `infrastructure/` 的架构违规：`common/tools/common_tools.py` 仍导入 `infrastructure.workspace.local_filesystem._common_impl`。
- [ ] 增加机器可读架构守卫，防止 `domain -> infrastructure/application`、`common -> infrastructure` 等反向依赖复发。
- [ ] Provider 健康探测 + 退避重试：连续失败后 TTL 期内跳过不健康 provider。
- [ ] 定义核心 SLO：chat 成功率、首 token p95、tool call 成功率、approval 等待时长、token 成本。
- [ ] Prompt Injection 防御分层：对 shell/http/python 等高风险工具做策略化参数检测和告警。

## 推荐 Spec 拆分

- [x] `docs/spec/tui-approval-workflow/`：TUI 审批面板、resume 调用、再次中断渲染。
- [x] `docs/spec/local-trace-artifacts/`：`.epsilon/`、trace store、artifact store、本地日志。
- [x] `docs/spec/structured-tool-result/`：`ToolExecutionResult` 值对象、工具 metadata、`ToolCallTrace.metadata`、`ErrorTrace`/`ModelCallTrace` 补录、JSONL 兼容。
- [ ] `docs/spec/coding-workflow-commands/`：`/status`、`/diff`、`/tests`、`/files`、`epsilon exec --json`。
- [ ] `docs/spec/skill-mcp-registry/`：Skill 注册、MCP 注册、工具权限映射。
- [ ] `docs/spec/frontend-hitl-trace/`：前端审批、工具时间线、diff/artifact 展示。
- [ ] `docs/spec/cloud-sandbox-runtime/`：强隔离 sandbox、多用户权限、artifact 对象存储。

## 已完成能力索引

- [x] traceId 集成（OpenTelemetry）
- [x] Message 类型层次结构（BaseMessage -> SystemMessage/UserMessage/AssistantMessage/ToolMessage）
- [x] AssistantMessage.tool_calls + ToolMessage.tool_call_id
- [x] SSE 流式解析（sse-starlette + OpenAICompatibleAdapter.stream）
- [x] 工具调用框架（Tool ABC + ToolRegistry + ScopedToolRegistry + 10 个可注册工具）
- [x] 模型路由负载均衡（ProviderRegistry + Round-Robin + 配置驱动多提供商）
- [x] Agent 间通信（AgentRegistryPort + AgentRegistryAdapter + DelegateToAgentTool + 递归深度限制 + 上下文隔离）
- [x] WebSearchTool、HttpRequestTool、WebFetchTool
- [x] ShellExecTool、PythonExecTool（默认关闭，workspace cwd，超时，输出截断）
- [x] Workspace 边界与本地文件持久化
- [x] epsilon CLI Runtime 第一阶段（TUI、exec、serve、基础 slash commands）
- [x] Human-in-the-loop 工具审批 v1（策略、状态存储、Agent 中断/恢复、HTTP/SSE/TUI 提示）
- [x] 领域事件基础设施已按后续评估移除（`EventBusPort` / `EventStorePort` / `DomainEvent` 不再是当前运行时能力）
