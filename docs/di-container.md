# DI 容器

实现位于 `common/container.py`，为自建轻量容器，非第三方框架。

## 核心概念

```python
Scope(enum):  SINGLETON | TRANSIENT

Registration(dataclass):
    provider    # 工厂函数（可同步或 async）
    scope       # Scope
    is_async    # bool

AsyncResourceEntry(dataclass):
    name
    initializer  # async def () -> None
    cleanup      # async def () -> None
```

## 主要 API

```python
from common.container import container, inject, inject_all

# 注册
container.register(AbstractType, provider_fn, Scope.SINGLETON)
container.register_async_resource("name", init_fn, cleanup_fn)

# 未显式指定 name 时，默认 key 为 (AbstractType, "abstractType")

# 解析（异步，支持 TypeVar 类型安全）
resource = await container.resolve(AbstractType)

# 解析同一类型的所有无名称和命名实例（无注册时返回空列表）
resources = await container.resolve_all(AbstractType)

# FastAPI 集成
your_svc: YourPort = Depends(inject(YourPort))

# FastAPI 集合注入
services: list[YourPort] = Depends(inject_all(YourPort))

# 生命周期
await container.start()   # 按注册顺序初始化，fail-fast + 回滚清理
await container.stop()    # 逆序清理，best-effort（单个失败继续处理后续）

# 查询
container.has_async_resource("redis")   # True/False
```

`Container.lifespan` 为 FastAPI `asynccontextmanager`，直接作为 `FastAPI(lifespan=container.lifespan)` 传入即可串联应用启停与资源生命周期。

## 生命周期

1. **注册**（同步，启动前）：`configure_container()` 完成所有 Port→Adapter 绑定
2. **启动**（异步）：`container.start()` 依次调用 AsyncResource initializers；任一失败则触发已完成资源的回滚清理
3. **关闭**（异步）：`container.stop()` 逆序调用 cleanups，单个失败不中断后续清理
4. FastAPI lifespan 上下文管理器中集成（见 `application/server_app.py`）

## Scope 行为

- `SINGLETON`：首次 resolve 后缓存，后续返回同一实例
- `TRANSIENT`：每次 resolve 创建新实例
- 重复注册同一类型：后注册覆盖前注册
- `resolve_all(Type)`：按注册顺序返回该类型的无名称及全部命名实例；每个实例独立遵循自己的 Scope
- `inject_all(Type)`：将 `resolve_all(Type)` 暴露为 FastAPI 集合依赖；没有匹配注册时注入空列表

## 错误类型

| 异常 | 场景 |
|---|---|
| `DependencyNotRegisteredError` | 解析未注册类型 |
| `CircularDependencyError` | 检测到循环依赖（含详细路径信息） |
| `ProviderError` | provider 调用异常（解析期失败） |

## 绑定位置

所有 Port→Adapter 绑定仍从 `application/container_config.py::configure_container()` 统一进入；具体注册分组委托给 `application/container/*.py`。FastAPI router 通过 `Depends(inject(Port))` 注入，不直接引用 Adapter 实现。

`application/container_config.py` 与 `application/container/{agent,chat,task,run,tools,storage}.py` 是组合根例外：它们可以同时引用 application collaborator、domain Port 与 infrastructure Adapter，用于集中完成装配。普通 application 模块默认不得导入 infrastructure；静态导入守卫只允许组合根和精确登记的受控迁移例外，当前 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS == {}`。

当前关键装配边界：

- `_create_chat_service()` 解析 `SessionContextStorePort`、`SessionIndexPort`、`ModelRegistryPort`、`PromptRegistryPort`、`ContextBuilderPort`、`ToolRegistry`、`AgentPort` 与可选 `ApprovalStateStorePort` 后，在组合根内构造 `ChatSessionContextWorkflow` 与 `ChatApplicationService`，再注入 `infrastructure/chat/ChatServiceAdapter`。ChatServiceAdapter 通过本地结构协议消费这两个应用组件，基础设施层不直接导入 application。`chat-default` 系统 prompt 的加载经单一来源 `infrastructure/chat/chat_default_prompt.py::resolve_chat_default_system_prompt` 完成，组合根与 adapter 共用该 helper。
- `_create_task_agent()` 在组合根中构造 `TaskTraceWorkflow` 与 `TaskApplicationService`，并把已解析的 `task-template` prompt id 注入 `TaskAgentAdapter`。`TaskAgentAdapter` 通过结构协议消费应用服务，不直接 import application；prompt、tool schema、model registry、`AgentConfig`、`AgentPort` 调用和 TraceStore 仍在基础设施边界。
- Run 序列化 adapter 装配：`_create_run_execution_coordinator()` / `_create_run_guardrail_recorder()` / `_create_run_recovery_service()` / `_create_run_application_service()` / `_create_workflow_run_orchestrator()` 分别构造并注入 `infrastructure/run/run_serialization_adapters.py` 的 `SegmentSerializerAdapter` / `GuardrailSerializerAdapter` / `WorkflowSerializerAdapter`（实现 `application/run/serialization_ports.py` 的序列化 Protocol），使 `application/run/*` 不再直接导入 infrastructure serializer（`ddd-followup-refinements` 切片 A，静态 allowlist 收敛为空）。
- `_create_run_worker_manager()` 解析 `RunExecutionCoordinator` 作为 `RunSegmentExecutor`，在 checkpoint recovery 开启时解析 `RunRecoveryService` 作为 `RunRecoverySweep`，再注入 `RunWorkerManager(executor=..., recovery_sweep=...)`。`RunWorker` / `RunWorkerManager` 文件只依赖 `infrastructure/run/worker_contracts.py` 的结构协议和 domain 类型，不导入 application 具体类。

## 当前注册顺序（节选）

```python
# 异步资源（按序初始化）
telemetry → model_client → (redis 或 local_persistence) → gateway
         → workspace → delegate_tool_registration（延迟注册委派/handoff 工具）

# 基础设施实例
GatewayClient, Workspace

# Port → Adapter 绑定
register_storage_components → register_tool_components →
register_chat_components → register_task_components →
PromptRegistryPort → register_agent_components → register_run_components
```

其中 `SESSION_STORE_BACKEND=redis` 时注册 `redis` 异步资源，默认 `file` 时注册 `local_persistence` 异步资源；`ReadinessAggregator` 的 checks 列表按实际装配的异步资源动态组装（`has_async_resource` 判断）。
