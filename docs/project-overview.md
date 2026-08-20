# 项目总览

## 定位

`epsilon` 是一个通用 AI Agent 工作台组合仓库：

- `epsilon-boot/`：FastAPI 后端服务，采用 DDD + 六边形架构，提供聊天、任务执行、后台 Run runtime、模型路由、工具调用、会话存储、健康检查和可观测性能力。
- `epsilon-client/`：Next.js 前端控制台，提供聊天面板、模型选择、任务执行工作区和后台 Run 进度面板。
- `docs/`：当前项目认知入口，按主题拆分保存架构、接口、配置、开发、工具、前端、设计、研究、运维等说明；包含 `steering/`（强制规范）、`spec/`（Feature 设计文档）、`design/`（方案设计）、`research/`（研究报告）、`operations/`（运维文档）、`evaluation/`（评估报告）等子目录。
- `agents/`、`.agents/`、`.codex/skills/`：面向 Codex/Agent 工作流的本地代理与技能说明。
- `lib/`、`scripts/`、`tests/`（根级）：辅助脚本与离线依赖资源。

## 当前现状

截至当前代码，项目已经不是单纯的同步聊天 Demo，而是具备长任务后台运行能力的 Agent 工作台：

- 后端主链路稳定在 DDD + 六边形结构上，`domain/` 定义 Port 和领域规则，`application/` 提供 HTTP/CLI/Run 应用服务，`infrastructure/` 提供模型、工具、会话、Run store、checkpoint、workflow 等 Adapter。
- Chat/Task 同步入口仍可直接使用；复杂任务可通过 `/api/runs` 或 TUI/agent adapter 创建后台 Run，由 worker 领取并在事件流中暴露进度、终态和运行事实。
- 长任务阶段一至阶段六主干已落地：暂停/继续、请求内分段、后台 Run、bounded checkpoint recovery、guardrail runtime summary、轻量 workflow phase、collaboration summary、handoff 观测、role capability 与 child run 的灰度开关均有代码和测试覆盖。
- 默认兼容策略较保守：`RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED=true`，`RUN_WORKFLOW_ENABLED=true`；但 `RUN_WORKFLOW_ROLE_CAPABILITY_ENABLED=false`、`RUN_WORKFLOW_CHILD_RUN_ENABLED=false`，需要显式灰度后才强化角色权限或创建真实 child Run。
- 前端 RunView 已能查看 Run 状态、事件、checkpoint/recovery、guardrail、workflow、collaboration 和 child-run 事实；awaiting approval 当前展示为状态提示，审批提交能力由 `/api/runs/{run_id}/approve`、TUI 或既有审批入口承接。

当前边界也需要明确：

- checkpoint recovery 是 bounded recovery，通过 checkpoint 和 tool ledger 避免重复已确认工具结果；无法确认命运或超出恢复边界时进入 `lost` 或保守失败态，不承诺外部副作用 exactly-once。
- workflow 是轻量静态规则和 phase 外层编排，不是 Celery、Temporal、LangGraph、Dapr Workflow 这类外部 durable workflow engine。
- 生产化重点已从“补齐主能力”转向“硬化与灰度”：Run observation 原子写入与 cursor 一致性、workflow 集成场景、guardrail 成本配置、role capability 声明、child-run reconciliation、观测告警和发布手册。

## 运行时主链路

前端通过 Next.js rewrites 将 `/api/*` 和 `/v1/*` 代理到 FastAPI 后端。后端入口是 `epsilon-boot/main.py`，应用实例由 `src/application/api/server_app.py` 创建，并在 FastAPI lifespan 中启动 DI 容器管理的异步资源和可选 Run worker。

核心请求路径：

```text
浏览器
  -> Next.js 页面与组件
  -> /api/chat 或 /api/task/execute
  -> FastAPI Router
  -> ChatServicePort / TaskAgentPort
  -> ReAct Agent Loop
  -> ModelRegistryPort 路由模型
  -> ToolRegistry（经 Workspace 边界）执行授权工具
  -> 会话存储（本地文件 / Redis）+ 外部 LLM/网关等基础设施
```

后台长任务路径：

```text
浏览器/TUI/Agent adapter
  -> /api/runs 或 RunApplicationService
  -> RunStorePort / RunEventStorePort / RunObservationStorePort
  -> RunWorkerManager / RunWorker
  -> RunExecutionCoordinator
  -> ChatServicePort / TaskAgentPort 的首次执行、continue 或 approval resume 路径
  -> Guardrail / Workflow / Collaboration / Checkpoint runtime facts
  -> RunSnapshot + RunEvent 供轮询、SSE、TUI watch 或 Web RunView 展示
```

## 后端能力边界

后端领域层只定义核心对象、Port 和业务规则，不直接依赖 FastAPI、Redis、SQLAlchemy、OpenAI SDK 等外部框架。外部能力由 `infrastructure/` 下的 Adapter 实现，并由 `application/container_config.py` 统一装配。

主要能力：

- 聊天接口：支持同步 JSON 与 SSE 流式响应。
- 任务接口：面向目标的 Agent 执行入口，返回最终内容、状态、模型、token usage、执行轨迹和耗时。
- 后台 Run runtime：以 `run_id` 管理 chat/task 长任务，支持 queued/running/paused/awaiting_approval/cancelled/succeeded/failed/lost 等状态、事件流、取消、继续和审批恢复。Run runtime 提供本地文件与 Redis 两种 store adapter，支持 bounded checkpoint recovery、guardrail 事件/摘要收敛、workflow phase 编排、协作摘要、role capability 治理与保守 child run reconciliation；超过恢复边界或无法确认命运的 run 才进入 `lost` 或保守失败态。
- HTTP/TUI adapter：FastAPI `/api/runs*` 是薄 adapter；TUI/agent runtime 直接调用共享 `RunApplicationService`，不通过 HTTP 自调用。
- 多模型路由：通过 provider registry 维护多个 OpenAI-compatible Provider，并按模型选择适配器。
- 工具系统：文件读写、目录、HTTP/Web fetch、Web search、Shell/Python 执行、Agent 委派等工具按配置注册，所有文件 I/O 经 `Workspace` 边界受控。
- 会话上下文：默认 `SESSION_STORE_BACKEND=file`（本地文件原子写 + 文件锁，无 TTL），可切换 `redis`（含 3600s TTL 与滑动窗口压缩）。
- 健康检查与可观测：Liveness / Readiness 探针按实际装配的异步资源动态组装检查项；Prometheus、OpenTelemetry（可选 OTLP gRPC）、结构化日志（含 `trace_id`/`span_id`）。
- 注：`infrastructure/database/` 与事件总线 / 事件存储相关代码已从默认装配中移除（Domain_Event_Decommission），仅作为未来新增 MySQL 消费者时的死代码备用。

## 前端能力边界

前端是 App Router 风格的 Next.js 控制台，当前主界面是一个双栏工作区：

- `ChatPanel`：会话聊天、SSE 增量消息、清空会话、中止响应、模型选择。
- `TaskWorkspace`：提交结构化任务目标，展示状态、结果、耗时、模型、token 和 trace。
- `RunView`：展示后台 Run 状态、事件、segment metadata、checkpoint/recovery、approval、guardrail summary/runtime stats、workflow state、collaboration summary、child run/handoff 事件与终态结果/错误，并提供刷新、取消和继续动作。
- `src/lib/chat-api.ts`：集中封装后端 `/api/chat`、`/api/task/execute`、`/api/runs` 和 `/v1/models` 调用。

## 初始化后的阅读顺序

1. 先读 [architecture.md](architecture.md) 理解后端分层、Port/Adapter 和 Agent Loop。
2. 再读 [frontend.md](frontend.md) 理解前端页面和 API 代理。
3. 然后按任务读 [api.md](api.md)、[configuration.md](configuration.md)、[tools.md](tools.md)。
4. 开发前读 [development.md](development.md)，确认命令、测试和代码约束。
