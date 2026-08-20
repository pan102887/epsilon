# 配置说明

## 主配置源

**`epsilon-boot/config.properties`**（Java 风格 `KEY=VALUE`）是所有配置的主源。`.env` 文件和环境变量仅用于本地开发覆盖，不作为主配置。

新增或修改配置项时，**必须先写入 `config.properties`**。

## 配置系统

- 使用 `PropertiesBaseSettings`（基于 pydantic-settings）+ `create_config()` 工厂
- 支持热重载：在配置类上声明 `hot_reload: ClassVar[bool] = True`；`create_config` 返回的 `ConfigProxy` 会感知文件变更自动重新加载
- 配置类位于 `infrastructure` 层的各子包内（除 `common/configuration/` 中的基类外）

## 多 Provider 配置

每个候选 Provider 通过 `MODEL_<PREFIX>_*` 键组配置，实际是否注册由 `ENABLED` + `API_KEY` + `PROVIDER_NAME` + 模型列表非空共同决定：

```properties
MODEL_CLIPROXY_ENABLED=true
MODEL_CLIPROXY_PROVIDER_NAME=cliproxy
MODEL_CLIPROXY_API_BASE=http://localhost:8317/v1
MODEL_CLIPROXY_API_KEY=...
MODEL_CLIPROXY_DEFAULT_MODEL=glm-4.7
MODEL_CLIPROXY_MODELS=glm-4.7
```

`container_config.PROVIDERS` 候选 env_prefix：`cliproxy` / `zhipu` / `deepseek` / `qwen` / `openai`；当前 `config.properties` 默认启用 `cliproxy`、`zhipu`、`qwen`、`openai` 配置组，但 API key 为空的 Provider 会在启动时跳过注册（`deepseek` 作为扩展位，按需补齐配置键即可启用）。

路由相关：`MODEL_ROUTER_DEFAULT_PROVIDER` / `MODEL_ROUTER_ROUTING_STRATEGY` / `MODEL_ROUTER_DEFAULT_MODEL`。

## 工具功能开关

| 工具 | 配置键 | 默认值 |
|---|---|---|
| `ReadFileTool` / `WriteFileTool` / `EditFileTool` / `ListDirTool` | 始终启用 | — |
| `HttpRequestTool` | `HTTP_REQUEST_ENABLED` | true |
| `WebFetchTool` | `WEB_FETCH_ENABLED` | true（无显式 false 时） |
| `WebSearchTool` | `TAVILY_API_KEY` 非空 | 关闭 |
| `ShellExecTool` | `SHELL_EXEC_ENABLED` | true（`config.properties` 默认开启；字段安全默认仍为 false，配置全缺失时回退关闭） |
| `PythonExecTool` | `PYTHON_EXEC_ENABLED` | true（`config.properties` 默认开启；字段安全默认仍为 false，配置全缺失时回退关闭） |
| `DelegateToAgentTool` / `HandoffToAgentTool` / `DelegateParallelTool` | `AGENT_DELEGATE_TOOL_ENABLED` | true |

## Workspace / 本地持久化 / 会话后端

- `WORKSPACE_BACKEND`：本期仅支持 `local_filesystem`。
- `WORKSPACE_ROOT`：工作区根目录宿主绝对路径；为空时默认使用进程当前工作目录作为工作区。
- `WORKSPACE_FOLLOW_SYMLINKS`（默认 `false`）、`WORKSPACE_CREATE_IF_MISSING`（默认 `false`）。
- `SESSION_STORE_BACKEND`：`file`（默认） / `redis`。
- `LOCAL_PERSISTENCE_*`：`ROOT`（**默认留空**，见下方「`LOCAL_PERSISTENCE_ROOT` 默认迁移」）、`CREATE_IF_MISSING`（默认 `true`）、`FSYNC_ON_WRITE`（默认 `true`）、`LOCK_ACQUIRE_TIMEOUT_MS`（默认 `5000`）、`TMP_SWEEP_MAX_AGE_SECONDS`（默认 `3600`，仅作用于 `*.tmp-*` 半写残留）。
- 本期 `file` 会话后端**无 TTL**，不存在后台过期回收；已废弃的 `LOCAL_PERSISTENCE_SESSION_TTL_SECONDS` / `LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS` 若在环境变量中出现会 fail-fast。

### `LOCAL_PERSISTENCE_ROOT` 默认迁移（USER tier）

`config.properties` 中的 `LOCAL_PERSISTENCE_ROOT` 默认**留空/注释**（决策 1a，ADR-0006）：

- **留空即启用 USER tier 默认**：会话主状态（session context/index、run/checkpoint）默认落到 `~/.epsilon/persistence/<project-hash>/`；`<project-hash>` 由 `LocalFileTierResolver.project_hash()` 对 PROJECT 基点（`WORKSPACE_ROOT`，空则进程 CWD）规范化路径取 sha256 前 16 位生成，随用户走、跨项目按 hash 分区，不污染项目工作区 git status。
- **显式配置优先**：显式设置 `LOCAL_PERSISTENCE_ROOT=<绝对路径>` 时尊重原值、不迁移；启动期 `_validate_local_persistence_root` 校验（含与 `WORKSPACE_ROOT` 相互包含 fail-fast）不弱化。
- **redis 不受影响**：`SESSION_STORE_BACKEND=redis` 时本项不生效，会话主状态走 Redis。
- **旧数据搬迁（不自动搬运）**：旧默认目录 `../.local_persistence/epsilon-boot` 的数据不会自动迁移，可二选一——(a) 手动把旧目录内容（`sessions/`、`runs/` 等）拷贝到新默认目录 `~/.epsilon/persistence/<project-hash>/`；或 (b) 显式设置 `LOCAL_PERSISTENCE_ROOT=<旧绝对路径>` 保留原位置。首次启动若检测到旧默认目录非空且新默认目录为空，会 `logger.info` 输出一次性中文迁移提示（含旧路径、新路径与两种选项），检测失败静默跳过。
- **安全禁令保留**：默认路径迁移不弱化既有安全禁令——禁止指向 NFS / SMB / OSS FUSE 网络盘，禁止多容器共享（overlayfs / bind mount 下锁语义未验证）；本地 file 后端仅保证单主机单实例，多实例/多租户生产走 redis。

## 本地覆盖配置 `config.local.properties`

`config.local.properties`（local-trace-artifacts / ADR-0004）用于**本地调试覆盖**，不污染主配置源、也不越过部署期环境变量：

- **格式**：与 `config.properties` 完全相同的 Java `KEY=VALUE` Properties 格式与键名到字段的映射规则（复用 `PropertiesFileSettingsSource` 的转换约定）。
- **定位**：优先 `<WORKSPACE_ROOT 或进程 CWD>/.epsilon/config.local.properties`，缺失时向上兜底 `_find_file("config.local.properties")`。
- **优先级链（高 → 低）**：构造参数 > 环境变量 > `config.local.properties` > `config.properties` > `.env` > secrets > 默认值。即同键同时存在时，环境变量覆盖 `config.local.properties`，`config.local.properties` 覆盖 `config.properties`。
- **缺失不报错**：`config.local.properties` 不存在时，配置解析结果与引入本特性前完全一致（缺失文件由解析层返回空 dict）；存在时纳入 `ConfigProxy` 的 mtime 热更新监听列表。
- **不入库**：位于 `<workspace>/.epsilon/` 下，天然被既有 `.epsilon/` 忽略规则忽略，本地覆盖值不进入版本库。
- **不改主源约定**（config-source.md）：新增/修改配置项仍**优先写入 `config.properties`**，`config.local.properties` 仅用于本地覆盖。

## 任务产物与本地文件日志

local-trace-artifacts 引入两组配置键（详见 [architecture.md](architecture.md) 的 StorageTier 抽象）：

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `ARTIFACT_ENABLED` | `true` | 任务产物存储总开关；启用后写入方经 `ArtifactStorePort` 记录任务产物到 PROJECT tier `.epsilon/artifacts/` 的本地 JSONL 文件；关闭时工厂返回 `None`，写入方静默跳过（零行为变化）。写入方由后续 spec 接入。 |
| `EPSILON_LOG_TO_FILE` | `true` | TUI/CLI 本地文件日志总开关（ADR-0005）。默认开启，落 USER tier `~/.epsilon/<project-hash>/logs/epsilon.log`（随用户走、不污染项目工作区）；写盘前对敏感字段脱敏。`serve` 路径不装配，既有 FastAPI 日志链路不受影响。 |
| `EPSILON_LOG_LEVEL` | `INFO` | 文件日志级别。 |
| `EPSILON_LOG_ROTATION_MAX_BYTES` | `10485760` | 单个日志文件轮转阈值（字节），默认 10MB（`RotatingFileHandler`）。 |
| `EPSILON_LOG_ROTATION_BACKUP_COUNT` | `5` | 轮转保留的历史文件数量。 |

## 后台 Run Runtime

Run runtime 跟随 `SESSION_STORE_BACKEND` 选择本地文件或 Redis store，并通过独立 `RUN_*` 配置控制 worker、容量、事件保留、checkpoint recovery、guardrail 收敛与 workflow 治理：

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `RUN_WORKER_ENABLED` | `true` | 是否在容器 lifespan 中启动 RunWorkerManager。 |
| `RUN_WORKER_COUNT` | `1` | 后台 worker 数量。 |
| `RUN_AUTO_CONTINUE_PAUSED_RUNS` | `true` | 是否将可继续的 paused run 自动重新入队；风险门、审批、无进展、重复工具、总预算等停止原因不会自动续跑。 |
| `RUN_AUTO_CONTINUE_MAX_SEGMENTS` | `20` | 单个 run 自动重新入队的最大段数，超过后保留 paused 等待人工处理。 |
| `RUN_LEASE_SECONDS` | `60` | worker claim 后的租约有效期。 |
| `RUN_HEARTBEAT_INTERVAL_SECONDS` | `10` | worker 心跳刷新间隔，必须小于 lease。 |
| `RUN_MAX_QUEUED_RUNS` | `100` | queued run 容量上限。 |
| `RUN_MAX_RUNNING_RUNS` | `2` | running/cancel_requested run 并发上限。 |
| `RUN_EVENT_MAX_COUNT` | `1000` | 每个 run 保留的事件数量上限。 |
| `RUN_EVENT_TTL_SECONDS` | `86400` | Redis 事件历史 TTL；本地文件后端主要使用 count trim。 |
| `RUN_EVENT_STREAM_WAIT_SECONDS` | `15.0` | SSE/stream 等待新事件的单轮等待时间。 |
| `RUN_LOST_SWEEP_INTERVAL_SECONDS` | `30` | worker manager 扫描过期租约并触发 lost/recovery 处理的间隔。 |
| `RUN_CHECKPOINT_ENABLED` | `true` | 是否记录 Run checkpoint 与工具结果 ledger。 |
| `RUN_CHECKPOINT_AUTO_RECOVERY_ENABLED` | `true` | 是否对可恢复的 lost/过期运行自动入队恢复。 |
| `RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS` | `3` | 单个 run 自动恢复最大尝试次数。 |
| `RUN_CHECKPOINT_MAX_COUNT` | `200` | 单个 run 保留 checkpoint 数量上限。 |
| `RUN_CHECKPOINT_TTL_SECONDS` | `604800` | checkpoint 保留时间。 |
| `RUN_CHECKPOINT_MAX_PAYLOAD_BYTES` | `262144` | 单个 checkpoint payload 字节上限。 |
| `RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT` | `1000` | 单个 run 工具结果 ledger 数量上限。 |
| `RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED` | `true` | 是否把 ReAct guardrail 观测写入 Run 事件和 `guardrail_summary`。 |

checkpoint recovery 是 bounded recovery：file/Redis 后端都通过 checkpoint store 和工具结果 ledger 复用已确认状态，避免重复执行已提交工具；当 checkpoint、租约或 child-run reconciliation 状态不足以确认命运时，run 会进入 `lost` 或保守失败态，不承诺外部副作用 exactly-once。

## Workflow / Collaboration Runtime

Workflow runtime 默认保持兼容，只在显式配置和 workflow 定义要求时强化治理：

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `RUN_WORKFLOW_ENABLED` | `true` | 是否启用 workflow 选择与 phase 编排。 |
| `RUN_WORKFLOW_DEFAULT_WORKFLOW` | 空 | 未显式指定时的默认 workflow；为空表示按 selector 规则选择。 |
| `RUN_WORKFLOW_ENABLED_WORKFLOWS` | `research,code_change,report,batch_processing` | 启用的静态 workflow 名称列表。 |
| `RUN_WORKFLOW_MAX_RECURSION_DEPTH` | `3` | 多 Agent 协作递归深度上限。 |
| `RUN_WORKFLOW_MAX_PARALLEL_DELEGATIONS` | `3` | 单次并行委派上限。 |
| `RUN_WORKFLOW_MAX_REVISE_PER_PHASE` | `1` | 单个 phase 允许 revise 次数。 |
| `RUN_WORKFLOW_MAX_HANDOFF_COUNT` | `1` | 单个 run handoff 次数上限。 |
| `RUN_WORKFLOW_RECENT_COLLABORATION_SUMMARY_LIMIT` | `5` | `collaboration_summary.latest_steps` 保留条数；键名保留 recent 以兼容既有配置。 |
| `RUN_WORKFLOW_MAX_CHILD_RUNS` | `0` | 单个 parent run 可创建的 child run 上限；默认不允许。 |
| `RUN_WORKFLOW_ROLE_CAPABILITY_ENABLED` | `false` | 是否强制 workflow role capability 最小权限。关闭时保持旧兼容行为。 |
| `RUN_WORKFLOW_CHILD_RUN_ENABLED` | `false` | 是否允许显式策略创建真实 child run。关闭时保持既有 in-run delegation/handoff 路径。 |

## Agent / 聊天行为

- `CHAT_MAX_MESSAGES`（滑动窗口保留的非 system 消息条数，默认 50）、`CHAT_MAX_TOOL_ROUNDS`（`config.properties` 默认 0=不限制轮次，≤0 归一化为不可达大数哨兵，由 token 预算/工具超时兜底；字段默认 10）、`CHAT_TOOL_CALLING_ENABLED`（默认 true）。
- `CHAT_SEGMENT_AUTO_CONTINUE_ENABLED`（`config.properties` 默认 true）、`CHAT_SEGMENT_MAX_CONTINUATIONS`（默认 8）；跨段 token/duration 为 0 表示不限制。
- `CHAT_SYSTEM_PROMPT` 已废弃；若在环境变量或 `config.properties` 中出现，启动期会 fail-fast。系统提示词通过 `PROMPT_CHAT_DEFAULT_VERSION` 选择 `prompts/chat-default/v<N>.md`。
- `TASK_AGENT_MAX_ROUNDS`（`config.properties` 默认 0=不限制轮次，语义同 `CHAT_MAX_TOOL_ROUNDS`；字段默认 10）。
- `TASK_AGENT_SEGMENT_AUTO_CONTINUE_ENABLED`（`config.properties` 默认 true）、`TASK_AGENT_SEGMENT_MAX_CONTINUATIONS`（默认 8）；跨段 token/duration 为 0 表示不限制。
- `AGENT_MAX_DELEGATION_DEPTH`（默认 3）、`AGENT_HANDOFF_MAX_ROUNDS`（`config.properties` 默认 0=不限制轮次，避免 handoff 子 Agent 第 10 轮暂停）、`AGENT_DELEGATE_TOOL_ENABLED`（默认 true）。

## 其他配置组

- 服务：`SERVER_HOST` / `SERVER_PORT`（默认 7777） / `SERVER_DEBUG` / `SERVER_WORKERS`。
- Redis：`REDIS_HOST` / `PORT` / `PASSWORD` / `DB`。
- MySQL：`DB_*`（本期默认未装配，保留供未来恢复）。
  - `DB_PASSWORD` 在仓库默认配置中必须为空；生产、预发和个人环境通过环境变量或 Secret 管理系统覆盖。
  - 禁止把真实 API Key、数据库密码、Redis 密码写入 `config.properties`、`.env` 示例或文档代码块。
- 网关：`GATEWAY_BASE_URL` / `GATEWAY_TIMEOUT` / `GATEWAY_MAX_RETRIES`。
- 请求日志中间件：`LOGGING_REQUEST_ENABLED` / `LOGGING_REQUEST_MAX_BODY_LOG_SIZE` / `LOGGING_REQUEST_BODY_ENABLED` / `LOGGING_RESPONSE_BODY_ENABLED` / `LOGGING_REQUEST_SENSITIVE_HEADERS` / `LOGGING_REQUEST_SENSITIVE_BODY_FIELDS`。
  - 请求体和响应体日志默认关闭；临时开启时仍会对 JSON body 中的敏感字段递归脱敏。
- OpenTelemetry：`OTEL_*`（默认关闭；启用后 `OTEL_EXPORTER_ENDPOINT` 为空时退回 `ConsoleSpanExporter`）。

## 生产部署（K8S）

- **进程模型**：每个 Pod 单个 uvicorn worker，通过 K8S 副本水平扩展
- **会话状态**：生产集群建议显式 `SESSION_STORE_BACKEND=redis`（本地文件后端仅单主机单实例可靠）；Redis 键格式 `session:context:{session_id}`，TTL 3600s
- **Run 状态**：生产多副本场景建议显式 `SESSION_STORE_BACKEND=redis`，否则本地文件 Run store 仅适合单主机单实例。Run worker 数量和并发容量通过 `RUN_WORKER_COUNT`、`RUN_MAX_RUNNING_RUNS` 控制。
- **可观测性**：Prometheus 指标、OpenTelemetry（OTLP gRPC，可选）、结构化日志（含 `trace_id`/`span_id`）
- **Docker**：非 root 用户（`appuser`），包含 Rust（供 uv 构建依赖使用）
