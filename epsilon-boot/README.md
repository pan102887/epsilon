# epsilon-boot

基于 FastAPI + DDD 架构构建的通用 AI Agent 工作台。支持多轮对话、ReAct Agent Loop、多模型智能路由与负载均衡、Agent 间任务委派，以及覆盖文件操作、Web 搜索/抓取、HTTP 请求、Shell/Python 执行的工具生态。

## 项目结构（DDD 分层）

```
src/
├── application/              # 应用层：HTTP 入口、路由、异常处理、生命周期管理
│   ├── server_app.py         # FastAPI app 实例（注册中间件、异常处理、路由）
│   ├── server_config.py      # 服务器运行参数配置（host/port/workers）
│   ├── container_config.py   # DI 容器配置（Port → Adapter 绑定、异步资源注册）
│   ├── exception_handlers.py # 统一异常处理（BizException / 校验 / HTTP / 兜底）
│   ├── middlewares/          # ASGI 中间件
│   │   ├── logging_config.py # 日志格式配置
│   │   └── request_logging.py# 请求日志中间件
│   └── routers/              # API 路由
│       ├── health.py         # 健康检查（liveness / readiness / prometheus）
│       ├── chat.py           # 聊天对话（同步 / 流式 SSE / 会话清除）
│       ├── task.py           # 任务执行（面向任务的 Agent 入口）
│       ├── models.py         # 模型列表查询（OpenAI 兼容 /v1/models）
│       └── test_router.py    # 测试路由
│
├── domain/                   # 领域层：核心业务逻辑，不依赖任何外部框架
│   ├── agent/                # Agent 域
│   │   ├── ports.py          # AgentPort / AgentRegistryPort
│   │   ├── tools.py          # Tool 抽象基类 / ToolRegistry / ScopedToolRegistry
│   │   ├── value_objects.py  # AgentConfig / AgentResult / NamedAgentConfig
│   │   └── exceptions.py     # ToolExecutionError / AgentNotFoundError / DelegationDepthExceededError
│   ├── chat/                 # 聊天对话域
│   │   ├── ports.py          # ChatServicePort / SessionContextStorePort / ContextCompactionPort
│   │   ├── context.py        # Message 类型层次 / ConversationContext
│   │   └── value_objects.py  # ChatRequestVO / ChatResponseVO
│   ├── task/                 # 任务域
│   │   ├── ports.py          # TaskAgentPort
│   │   └── value_objects.py  # Task / TaskResult / TaskStatus / TraceEntry
│   ├── model_access/         # 模型接入域
│   │   ├── ports.py          # ModelAccessPort / ModelRegistryPort
│   │   ├── value_objects.py  # ChatRequest / ChatResponse / StreamingChunk / ModelInfo
│   │   └── exceptions.py     # ModelAccessError / ModelTimeoutError / ModelRateLimitError
│   ├── health/               # 健康检查域
│   │   ├── ports.py          # HealthCheckPort
│   │   ├── aggregator.py     # ReadinessAggregator
│   │   └── value_objects.py  # HealthStatus / HealthCheckResult / ReadinessResult
│   ├── prompt/               # Prompt 资产注册域
│   └── workspace/            # Workspace 边界域
│
├── infrastructure/           # 基础设施层：端口接口的具体实现
│   ├── agent/                # Agent 适配器
│   │   ├── react_agent_adapter.py      # ReAct Agent Loop（同步/流式）
│   │   ├── agent_registry_adapter.py   # AgentRegistryPort 内存实现
│   │   └── delegate_to_agent_tool.py   # Agent 间委派工具
│   ├── task/                 # 任务适配器
│   │   └── task_agent_adapter.py       # TaskAgentPort 实现
│   ├── model_access/         # 多模型接入适配器
│   │   ├── provider_registry.py        # 供应商注册中心（Round-Robin 路由）
│   │   ├── openai_compatible_adapter.py# OpenAI 兼容协议适配器
│   │   ├── provider_config.py          # 供应商配置类
│   │   └── router_config.py            # 路由策略配置类
│   ├── chat/                 # 聊天服务适配器
│   │   ├── chat_service_adapter.py     # ChatServicePort 实现
│   │   ├── chat_config.py              # 聊天配置
│   │   └── sliding_window_compaction_adapter.py  # 滑动窗口上下文压缩
│   ├── tools/                # 工具实现
│   │   ├── filesystem/       # 文件系统工具
│   │   │   ├── read_file_tool.py       # ReadFileTool
│   │   │   ├── write_file_tool.py      # WriteFileTool
│   │   │   ├── edit_file_tool.py       # EditFileTool
│   │   │   └── list_dir_tool.py        # ListDirTool
│   │   ├── web_search/       # Web 搜索工具
│   │   │   ├── web_search_tool.py      # WebSearchTool（Tavily API）
│   │   │   └── tavily_config.py        # Tavily 配置
│   │   ├── http_request/     # HTTP 请求工具
│   │   │   ├── http_request_tool.py    # HttpRequestTool + SSRF 防护
│   │   │   └── http_request_config.py  # HTTP 请求配置
│   │   ├── web_fetch/        # Web 抓取工具
│   │   ├── python_exec/      # Python 脚本执行工具
│   │   ├── shell_exec/       # Shell 命令执行工具
│   │   │   ├── shell_exec_tool.py      # ShellExecTool（asyncio 子进程）
│   │   │   └── shell_exec_config.py    # Shell 执行配置
│   ├── redis/                # Redis 连接（仅在显式 SESSION_STORE_BACKEND=redis 时装配）
│   ├── database/             # 数据库连接（本期保留为死代码备用；默认不装配 MySQL 消费者）
│   ├── session/              # 会话存储（默认 LocalFileSessionContextAdapter；Redis 可显式切换）
│   ├── persistence/
│   │   └── local_file/       # 本地文件持久化共享工具（锁 / 原子写 / 路径策略 / 启动期清理）
│   ├── gateway/              # 网关客户端
│   ├── health/               # 健康检查（按装配后端动态组装：local_persistence / redis）
│   └── telemetry/            # OpenTelemetry 可观测性
│
└── common/                   # 共享内核
    ├── container.py          # 轻量级 DI 容器（Singleton/Transient、异步资源生命周期）
    ├── container_models.py   # 容器内部数据模型
    ├── container_errors.py   # 容器异常类
    ├── exceptions.py         # 业务异常基类（BizException）
    ├── configuration/        # 配置中心（pydantic-settings + 热更新）
    └── tools/                # 通用工具函数
```

> 当前版本未内置领域事件基础设施（`EventBusPort` / `EventStorePort` / `DomainEvent` 已在本期随"零消费者"评估结论一并移除）；如有需求请提交新 feature。相应的 MySQL `event_records` / `event_handler_results` 表不再被本服务使用，运维升级指南见 [`../docs/operations/runtime-backends.md`](../docs/operations/runtime-backends.md#83-领域事件表的清理运维手动执行)。

## 快速开始（零依赖 `uv run` 启动）

本期默认会话后端为**本地文件**（`SESSION_STORE_BACKEND=file`），零外部中间件依赖即可启动，不再要求本地拉起 Redis / MySQL：

```bash
cd epsilon-boot
uv sync --frozen
uv run python main.py
```

服务启动后监听 `0.0.0.0:7777`；`WORKSPACE_ROOT` 留空时默认使用进程当前目录作为 workspace。首次启动会在 `../.local_persistence/epsilon-boot/` 下按需创建会话目录，避免会话文件落入默认 workspace。启动日志会打印 `_local_persistence_root` 的最终绝对路径与 `TmpFileSweeper 扫描完成 scanned=… deleted=… errored=…` 摘要。

需要接入集群级 Redis 会话后端（保持 TTL 自动过期语义）时，显式在 `config.properties` 中声明：

```properties
SESSION_STORE_BACKEND=redis
```

详细的后端组合、单主机约束、健康检查差异与升级指南见 [`../docs/operations/runtime-backends.md`](../docs/operations/runtime-backends.md)。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health.json` | 存活探针（Liveness Probe） |
| GET | `/readiness` | 就绪探针（按装配后端动态组装：`file` → `local_persistence`；`redis` → `redis`） |
| GET | `/prometheus` | Prometheus 指标暴露 |
| POST | `/api/chat` | 聊天对话（同步 JSON / 流式 SSE） |
| DELETE | `/api/chat/sessions/{session_id}` | 清除指定会话的对话历史 |
| POST | `/api/task/execute` | 任务执行（面向任务的 Agent 入口） |
| GET | `/v1/models` | 模型列表查询（OpenAI 兼容格式） |

## 配置管理

配置基于 `pydantic-settings`，通过 `PropertiesBaseSettings` 基类统一管理。

配置源优先级（从高到低）：
1. 构造参数
2. 环境变量
3. `config.properties` 文件（Java Properties 格式，主配置源）
4. `.env` 文件（本地兜底配置）
5. secrets 文件源
6. 字段默认值

因此，容器 / K8S 部署应优先通过环境变量或 Secret 注入敏感配置；`config.properties` 会覆盖 `.env`，`.env` 不适合作为覆盖 `config.properties` 的来源。

支持配置热更新：配置类声明 `hot_reload: ClassVar[bool] = True` 后，通过 `create_config()` 工厂函数创建的实例会自动检测配置文件变更并重新加载。

## 多模型路由与负载均衡

支持多个 LLM 提供商，通过 `container_config.py` 中的 `PROVIDERS` 列表初始化并注册到 `ProviderRegistry`：

| 标识名 | 环境变量前缀 | 说明 |
|--------|-------------|------|
| cliproxy | `MODEL_CLIPROXY_` | CLIProxyAPI 代理网关 |
| zhipu | `MODEL_ZHIPU_` | 智谱 AI (OpenAI 兼容) |
| deepseek | `MODEL_DEEPSEEK_` | DeepSeek（OpenAI 兼容，候选扩展位） |
| qwen | `MODEL_QWEN_` | 阿里云百炼 / 通义千问 (DashScope OpenAI 兼容) |

路由与负载均衡策略：
1. 配置驱动：每个提供商在配置中声明支持的模型列表（`models` 属性）
2. 自动路由：根据 `ChatRequest.model` 自动查找支持该模型的提供商
3. 负载均衡：多个提供商支持同一模型时，使用 Round-Robin 轮询分发
4. 默认模型：请求未指定模型时使用 `router_config.default_model`

## 已注册工具清单

| 工具 | 注册方式 | 说明 |
|------|----------|------|
| ReadFileTool | 始终注册 | 读取文件内容 |
| WriteFileTool | 始终注册 | 写入/创建文件 |
| EditFileTool | 始终注册 | 编辑文件指定行 |
| ListDirTool | 始终注册 | 列出目录内容 |
| WebSearchTool | 条件注册（`TAVILY_API_KEY`） | Tavily API 联网搜索 |
| HttpRequestTool | 条件注册（`HTTP_REQUEST_ENABLED`） | 通用 HTTP 请求 + SSRF 防护 |
| WebFetchTool | 条件注册（`WEB_FETCH_ENABLED`） | 网页抓取 + 响应体截断 |
| ShellExecTool | 条件注册（`SHELL_EXEC_ENABLED`） | 异步 Shell 命令执行 + 环境变量清理 + 超时控制 |
| PythonExecTool | 条件注册（`PYTHON_EXEC_ENABLED`） | Python 脚本执行 + AST 白名单 + 超时控制 |
| DelegateToAgentTool | 条件注册（`AGENT_DELEGATE_TOOL_ENABLED`） | Agent 间任务委派 |

## Agent 能力

- ReAct Agent Loop：推理→行动→观察循环，同步/流式两种模式
- 多 Agent 协作：AgentRegistryPort + DelegateToAgentTool，支持递归委派（深度限制）+ 上下文隔离
- 命名 Agent 配置：NamedAgentConfig（tool_names 子集 + model 选择）
- 工具子集隔离：ScopedToolRegistry，每个 Agent 只能访问授权的工具
- 双入口：对话入口 `/api/chat` + 任务入口 `/api/task/execute`

## 生产部署（K8S 多 Pod）

### 部署架构

```
K8S Cluster
├── Ingress / Service（负载均衡，轮询分发）
├── Pod 1~N ─── Container ─── asyncio + Uvicorn（单进程）
├── Redis（集群部署时显式 SESSION_STORE_BACKEND=redis；承担会话状态存储 + 健康检查）
└── OSS / S3（文件共享存储，待实现）
```

> 本期已移除领域事件基础设施与 MySQL 默认装配；集群部署不再要求部署 MySQL。单 Pod / 单主机场景可直接使用默认的本地文件会话后端，无需 Redis（见"快速开始"章节与 [`../docs/operations/runtime-backends.md`](../docs/operations/runtime-backends.md)）。

### 进程模型

每个 Pod 单 worker 进程，用 Pod 副本数水平扩展，不需要 Gunicorn。

理由：K8S HPA 基于 Pod 级别指标，单 worker 让 CPU/内存指标更准确；Agent 应用是 I/O 密集型，单进程 asyncio 并发能力足够。

### K8S 探针配置

```yaml
containers:
  - name: epsilon-boot
    livenessProbe:
      httpGet: { path: /health.json, port: 7777 }
      initialDelaySeconds: 10
      periodSeconds: 15
      failureThreshold: 3
    readinessProbe:
      httpGet: { path: /readiness, port: 7777 }
      initialDelaySeconds: 5
      periodSeconds: 10
    startupProbe:
      httpGet: { path: /health.json, port: 7777 }
      initialDelaySeconds: 5
      periodSeconds: 5
      failureThreshold: 30
```

### 容器启动命令

```dockerfile
ENTRYPOINT ["sh", "-c", ".venv/bin/python3 main.py"]
```
