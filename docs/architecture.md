# 架构概览

## DDD + 六边形架构

```
application/     → 依赖 → domain/, common/（组合根可引用 infrastructure；历史迁移例外当前为空）
infrastructure/  → 依赖 → domain/, common/（禁止导入 application）
domain/          → 依赖 → common/ 仅（禁止导入 application / infrastructure）
common/          → 无上层依赖（共享内核）
```

**关键约束**：`domain/` 禁止导入 `application/`、`infrastructure/`、FastAPI、SQLAlchemy、Redis、OpenAI SDK 等任何外部库。所有外部能力通过 Port 接口（Python `Protocol`）访问。`application/container_config.py`、`application/container/*.py`、启动装配与生命周期代码是集中装配 Port→Adapter 的组合根例外；其它 application→infrastructure 导入只有在静态 guard 精确登记时才允许。当前历史迁移例外已清空，不能用目录前缀放开。

## Port → Adapter 映射

| Port（domain） | Adapter（infrastructure） | 用途 |
|---|---|---|
| `ModelAccessPort` | `OpenAICompatibleAdapter` | LLM API 调用（OpenAI 兼容） |
| `ModelRegistryPort` | `ProviderRegistry` | 多 Provider 注册 + Round-Robin |
| `SessionContextStorePort` | `LocalFileSessionContextAdapter`（默认）/ `RedisSessionContextAdapter` | 会话状态持久化 |
| `ContextCompactionPort` | `SlidingWindowCompactionAdapter` | 上下文滑动窗口压缩 |
| `AgentPort` | `ReActAgentAdapter`（委托 `AgentLoopOrchestrator` 领域服务，实现 `AgentLoopEffects` 端口，并组合 ReAct 基础设施协作者） | ReAct Agent Loop |
| `AgentRegistryPort` | `AgentRegistryAdapter` | 命名 Agent 配置管理 |
| `TaskAgentPort` | `TaskAgentAdapter`（通过组合根注入 `TaskApplicationService`） | 任务型 Agent 执行 |
| `DelegationPort` | `DelegationAdapter` | 多 Agent 委派桥接 |
| `ChatServicePort` | `ChatServiceAdapter` | 基础设施聊天适配；会话上下文与 continue/resume 用例编排由 application/chat 组件注入 |
| `RunStorePort` | `LocalFileRunStoreAdapter`（默认）/ `RedisRunStoreAdapter` | 后台 Run 快照、状态、租约和控制操作 |
| `RunEventStorePort` | `LocalFileRunStoreAdapter`（默认）/ `RedisRunStoreAdapter` | 后台 Run 事件追加、查询、等待和裁剪 |
| `RunObservationStorePort` | `LocalFileRunStoreAdapter`（默认）/ `RedisRunStoreAdapter` | 在同一原子区追加运行时事件并更新 guardrail/workflow/collaboration 摘要 |
| `RunCheckpointStorePort` | `LocalFileRunCheckpointStoreAdapter`（默认）/ `RedisRunCheckpointStoreAdapter` | Run checkpoint 与工具结果 ledger 持久化 |
| `WorkflowRegistryPort` | `StaticWorkflowRegistryAdapter` | 静态 workflow 定义注册与校验 |
| `WorkflowSelectorPort` | `StaticWorkflowSelector` | 创建 Run 时按 payload 选择 workflow 定义 |
| `HealthCheckPort` | `RedisHealthCheckAdapter` / `MysqlHealthCheckAdapter` / `LocalPersistenceHealthCheckAdapter` | 依赖健康检查（按实际装配的资源动态组装） |
| `Workspace`（`domain/workspace/ports.py`） | `LocalFilesystemWorkspace` | 文件 I/O 边界抽象，工具层统一入口 |
| `TraceStorePort` | `LocalFileTraceStoreAdapter`（经 `LocalFileTierResolver`） | 结构化 Agent 追踪存储（`.epsilon/traces/`，tier 为定位维度） |
| `ArtifactStorePort` | `LocalFileArtifactStoreAdapter`（经 `LocalFileTierResolver`） | 任务产物存储（`.epsilon/artifacts/`，tier 为定位维度；写入方由后续 spec 接入） |

> 备注：事件总线与事件存储（`EventBusPort` / `EventStorePort` / `DomainEvent`）已在 Domain_Event_Decommission 中移除，不再是当前架构的一部分。`infrastructure/database/` 连同 `SessionProviderPort`（基础设施层内部端口）默认不装配，保留为死代码备用。

## API presenter 与导入边界

HTTP 路由位于 `application/api/routers/`。API/HTTP response body 的轻量 presenter 归属 `application/api/presenters/`，当前已承载：

- `health_presenter.py::readiness_result_to_response_body(...)`：`health.py` 不再导入 `infrastructure.health.health_serialization`。
- `task_presenter.py::segment_budget_usage_to_response_body(...)`：`task.py` 不再导入 `infrastructure.agent.segment_serialization`。

静态导入守卫 `test/static/test_architecture_import_boundaries.py` 继续禁止 `infrastructure -> application`，并要求 application→infrastructure 命中必须等于“组合根路径 + 精确迁移例外”。`application/run/*` 对 `workflow_serialization` / `guardrail_serialization` / `segment_serialization` 的历史受控例外已由 `ddd-followup-refinements` 切片 A **全部消除，`APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 收敛为空 `{}`**：Run 应用层改为依赖 `application/run/serialization_ports.py` 的序列化 Protocol（`WorkflowSerializerPort` / `GuardrailSerializerPort` / `SegmentSerializerPort`），由组合根注入 `infrastructure/run/run_serialization_adapters.py` 的 delegating adapter，序列化实现仍留基础设施层（[ADR-0008](adr/0008-extract-domain-serialization-to-infrastructure-mappers.md)）。

## ReAct Agent Loop 流程

```
用户请求 → ChatServiceAdapter.chat()
  → ChatSessionContextWorkflow.load_for_chat() 加载 ConversationContext、写 session_id、幂等注入 system prompt、追加用户消息
  → AgentPort.run(context, config, model_access)
    ┌── 每轮（max_rounds=CHAT_MAX_TOOL_ROUNDS，默认 0=不限制）─┐
    │  1. ContextCompactionPort.compact(messages)          │
    │  2. ContextBuilderPort.build() → 领域消息列表          │
    │  3. ModelAccessPort.chat(ChatRequest)                │
    │  4. 有 tool_calls → 权限检查 → 执行                  │
    │     → 追加 ToolMessage → 下一轮                      │
    │  5. 无 tool_calls → 返回 AgentResult                 │
    └──────────────────────────────────────────────────────┘
  → ChatSessionContextWorkflow.save_context_and_index() 保存 ConversationContext 并刷新 SessionIndex
  → ChatResponseVO
```

工具权限拒绝错误作为 ToolMessage 内容返回（非异常），允许 LLM 自我纠正。

聊天用例编排边界当前拆为两层：`application/chat/session_context_workflow.py::ChatSessionContextWorkflow` 负责 session load、`session_id` 写入、system prompt 幂等注入、save + session index upsert 和 preview；`application/chat/chat_application_service.py::ChatApplicationService` 负责 continue 的可继续性校验、approval resume 的 load / expired / decision 校验 / consume / `AgentPort.resume(...)` 顺序，以及分段执行的风险门、保存时机、自动续跑决策与 `SegmentRunMetadata` 聚合。分段流式路径由 `SegmentStreamFrame` 表达 application 业务帧，adapter 再翻译为既有 `AgentStreamEvent`/SSE 线格式。`infrastructure/chat/ChatServiceAdapter` 仍是 ChatServicePort 的 infrastructure adapter，保留模型解析、direct LLM path、stream/chunk/event 包装、approval metadata、segmented stream 线格式、prompt load 与 workspace guidance，并通过组合根注入上述应用服务而不直接导入 application。

Agent Loop 的**循环编排主体**（`Round_Loop_Control` 轮次推进骨架 + `Termination_Decision` 终止判定状态机）已上提至领域服务 `AgentLoopOrchestrator`（`domain/agent/agent_loop_orchestration.py`），以异步生成器 `iter_rounds()` 产出 `RoundOutcome` 五态（[ADR-0012](adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md) P2 第二片，行为等价）。全部运行时副作用（OTel trace、checkpoint、guardrail 累加、流式累加、审批持久化、日志）经领域端口 `AgentLoopEffects`（`Protocol`，`domain/agent/ports.py`）回调，由 `ReActAgentAdapter` 实现——领域编排零基础设施依赖。`perform_model_round` effect 方法封装 span 开闭后返回 `ModelRoundResult`，orchestrator 在 span 外 yield，解决 OTel contextvars/yield 冲突。

**纯编排叶子判定**（token 预算计算 / 超限判定、handoff 检测、`RoundOutcome → AgentResult` 结果翻译、guardrail 分支解释 `interpret_tool_guardrail_decision`、工具执行异常分类 `classify_tool_execution`、审批动作筛选 `collect_pending_actions`）与 `RoundOutcome` / `RoundOutcomeKind` / `ToolExecutionClassification` / `ToolGuardrailBranch` / `ModelRoundResult` 值对象均位于领域层 `domain/agent/agent_loop_policy.py` + `domain/agent/ports.py`（[ADR-0011](adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md) 首片 + [ADR-0012](adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md) 第二片）。首片 `infrastructure/agent/round_outcome.py` re-export 垫片已清理。

`ReActAgentAdapter` 仍是 `AgentPort` / `AgentLoopEffects` 门面，但 P0 adapter 瘦身已把三类基础设施内部职责拆为协作者：`ReactToolExecutionCoordinator` 承载同轮工具 dispatch/progress/events 编排，`ReactApprovalResumeCoordinator` 承载 approve/edit/reject 决策应用与 latest tool call 查找，`ReactFinalRoundStreamer` 承载最终轮 streaming/events 累积、usage 合并与 finished 输出。`_execute_tool_call` 本体、OTel / ContextVar、checkpoint I/O、ToolRegistry 调用、guardrail 运行时累加器与 concrete tool execution 仍留基础设施，未上提 domain。

Task 入口也已按同一原则瘦身：`domain/task/result_mapping.py::TaskResultMapper` 承载 `AgentResult` → `TaskStatus` / `TaskResult` 的纯映射；`application/task/TaskTraceWorkflow` 承载无 I/O trace shaping；`application/task/TaskApplicationService` 承载 execute / continue / approval resume 的 session 编排、分段续跑聚合、风险门 metadata 与审批 load/check/consume 顺序。`TaskAgentAdapter` 保留 prompt、tool schema、模型解析、`AgentConfig` 构造、`AgentPort` 调用和 TraceStore 持久化边界，并通过结构协议接收 application service，避免 infrastructure 反向导入 application。

此外，Agent 护栏的**任务分类与预算 / 风险决策**（`StaticAgentGuardrailPolicy`）经充血化试点上提为领域服务 `domain/agent/guardrail_policy.py`（结构化实现 `AgentGuardrailPolicyPort`，零基础设施依赖），基础设施同名文件 `infrastructure/agent/static_guardrail_policy.py` 降为向后兼容 re-export 垫片（[ADR-0014](adr/0014-introduce-guardrail-domain-service-in-agent-subdomain.md)）。充血化后续片再把 `domain/agent` 另三处纯判定收敛 / 平移进领域层（[ADR-0015](adr/0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md)，行为等价）：委派深度规范化 `config_policy.py::DelegationDepthNormalizationPolicy`、审批默认查表 `approval_lookup.py::ApprovalDefaultLookup`，以及平移至领域层的分段续跑判定 `segmented_orchestration.py::decide_next_segment`（基础设施同名文件降为向后兼容 re-export 垫片）；委托方 `AgentRuntimeConfig`（pydantic-settings）与审批 JSON 配置解析因框架 / 配置边界依赖按 [ADR-0008](adr/0008-extract-domain-serialization-to-infrastructure-mappers.md) 留 infrastructure。

## 结构化 Agent 追踪（TraceStorePort）

`ReActAgentAdapter` 在 Agent Loop 各步骤经 `TraceStorePort`（→ `LocalFileTraceStoreAdapter`，写 `.epsilon/traces/` JSONL）落结构化追踪值对象（`domain/agent/trace_value_objects.py`）。追踪写入统一走 fire-and-forget 语义（`try/except` + `logger.warning`），失败不阻塞主流程；`trace_store` 为 `None` 时静默 no-op。

trace 构建 / 写入职责已由 `ddd-followup-refinements` 切片 C 按 SRP 收敛到基础设施协作者 `infrastructure/agent/react_trace_recorder.py::ReActTraceRecorder`，门面 `ReActAgentAdapter` 通过组合持有并在原调用点委托（行为等价）。同批拆分还把 guardrail 运行时统计累加器（`guardrail_runtime_accumulator.py`）、工具并发骨架（`react_concurrent_tool_executor.py`，依 [ADR-0013](adr/0013-defer-concurrent-tool-skeleton-relocation.md) 仍留基础设施）与审批中断状态缝合（`react_approval_checkpoint.py`）拆为独立协作模块；`ReActAgentAdapter` 保留为门面，`AgentPort` 与 `AgentLoopEffects` 契约不变，均属基础设施层内部重排，不上提领域层。

- **`ToolCallTrace.metadata`**：工具执行结果 `ToolExecutionResult.metadata` 经 `_record_tool_call_trace` 透传到 `ToolCallTrace.metadata`（`dict[str, Any]`，默认空 dict），记录 exit_code、逻辑路径、操作类型、字节数等结构化字段（各工具字段见 [tools.md](tools.md)）。写入前由 `_truncate_metadata` 将单条 metadata 序列化体积限制在 ≈2KB，超限丢弃剩余键并写 `_truncated` 标记（`json.dumps(default=str)` 兜底异构值）。工具失败（`is_error=True`）时 `error_class` 取 `metadata["error_class"]`、`error_message` 取截断后的 `result.content`；成功时二者为 `None`。`result_summary` 仍取 `content` 截断值，LLM 回灌内容（`ToolMessage.content`）与 checkpoint 均只用 `content`，不受 metadata 影响。JSONL 前向兼容：`_dict_to_step` 用 `pop("metadata", {})` 读回，旧行缺该字段回退空 dict。
- **`ErrorTrace` 补录**：Agent Loop 级别的**非工具**异常（模型调用失败、上下文构建错误、HITL 状态加载失败等）由 `_record_error_trace` 记为 `ErrorTrace`（`round_num` / `error_class` / `error_message` 截断 / `timestamp_epoch`）后继续向上抛出原始异常，覆盖 `run` / `resume` / `run_streaming` / `run_events` 四个入口。**工具执行失败不走 `ErrorTrace`**——已由 `ToolCallTrace.success=False` + `error_class` / `error_message` 记录，二者不重复。
- **`max_rounds==1` 快速路径 `ModelCallTrace` 补录**：`run_streaming` / `run_events` 在 `max_rounds==1` 时走绕过 `_iter_rounds` 的快速路径，通过 `_stream_final_round` / `_stream_events_final_round` 的 `response_capture` 收集单轮 `LLMResponse`，在流结束后补录 `ModelCallTrace`（`round_num=1`，model/prompt_id/token/latency/timestamp 与多轮路径一致），使所有入口的 trace 时间线完整一致。

## 多 Agent 委派与 handoff

Agent A 调用 `DelegateToAgentTool` / `DelegateParallelTool` / `HandoffToAgentTool` → `DelegationAdapter` 检查深度（max=`AGENT_MAX_DELEGATION_DEPTH`，默认 3）→ 若处于 workflow Run 且 role capability 开启，则按当前 active role 校验 delegation/handoff 能力 → `AgentRegistryPort.get(name)` → delegate 通过 `TaskAgentPort` 启动子 ReAct Loop，handoff 由目标 Agent 接管当前上下文 → 结果作为 ToolMessage 返回。成功 workflow handoff 会额外写入 `WORKFLOW_HANDOFF_RECORDED` 和 `workflow_run_state.handoff_state`。

handoff 前置限制中的纯判定已收敛到 `domain/agent/handoff_policy.py::decide_handoff(...)`，只根据当前 depth、配置侧 max depth 与可选 workflow collaboration context 的 recursion / handoff count limit 返回 allow/reject decision。`HandoffToAgentTool` 仍在 infrastructure 读取 ContextVar、调用 `DelegationPort.handoff(...)`、构造 `ToolExecutionResult`、记录 collaboration event，并在成功时抛 `HandoffPerformed`；本次边界收敛不修复既有 handoff model discrepancy。

## 后台 Run Runtime

后台 Run runtime 用于把长任务从单次 HTTP 请求生命周期中剥离出来，并在段边界收敛 checkpoint、guardrail、workflow 与协作事实：

```text
RunApplicationService.create_run()
  → WorkflowSelectorPort.select()（可选）
  → RunStorePort.create_run()
  → RunEventStorePort.append_event(run_created/run_queued)
  → RunWorkerManager.wake_up()
  → RunWorker.claim_next(owner_id, lease_seconds)
  → RunExecutionCoordinator.execute(snapshot, progress)
     → 设置 RunExecutionContext / RunCheckpointContext / workflow context
     → WorkflowRunOrchestrator.execute_phase()（可选）
     → ChatServicePort.chat/continue_chat/resume_approval 或 TaskAgentPort.execute/continue_task/resume_approval
     → RunGuardrailRecorder 通过 RunObservationStorePort 原子写 guardrail 事件与摘要
     → Workflow/Collaboration/ChildRun 状态在 RunExecutionOutcome 中收敛
  → domain/run/outcome.py::decide_run_outcome_persistence(outcome)
  → RunWorker 执行 mark_succeeded / mark_paused / mark_awaiting_approval / mark_failed / mark_cancelled
  → RunEventStorePort 追加 segment/run 事件并裁剪保留窗口
```

边界约束：

- `domain/run` 定义 `RunStatus`、`RunKind`、`RunEventType`、值对象、状态机、异常、Port，以及 `RunExecutionOutcome` / `RunOutcomePersistenceDecision` / `decide_run_outcome_persistence(...)` 这类可脱离 runtime 的纯判定；不导入 FastAPI、Redis、application 或 infrastructure。
- `RunApplicationService` 是 adapter-neutral 应用服务，提供 create/query/events/stream/cancel/continue/approval resume，并通过 `RunApprovalResumer` 按 `RunKind` 分派 Chat/Task 审批恢复。
- `infrastructure/run/worker_contracts.py` 定义 `RunSegmentExecutor`、`RunRecoverySweep`、`RunRuntimeMetricsSink` 结构化协议。`RunWorker` / `RunWorkerManager` 不导入 `application.run.*` 具体类；当前组合根把 `RunExecutionCoordinator` 按 `RunSegmentExecutor` 注入，并在 checkpoint recovery 开启时把 `RunRecoveryService` 按 `RunRecoverySweep` 注入，metrics sink 作为协议依赖保留在 worker/manager 构造边界。
- `RunWorker` 保留 claim、lease refresh、heartbeat task、cancel pre/post segment check、progress event、store/event 调用、日志和 metrics 等 worker runtime 技术职责；只在段边界处理取消，不尝试中断正在 await 的模型或工具调用。
- TUI/agent adapter 直接调用 `RunApplicationService`；FastAPI `/api/runs*` 只做 DTO、异常映射和 SSE 包装。
- 当前不引入 Celery、Temporal、LangGraph、Dapr Workflow 等外部 durable workflow runtime；checkpoint recovery 是当前 Run runtime 内的 bounded recovery，无法确认的运行仍会进入 `lost` 或保守失败态，不承诺超出 checkpoint ledger 的 exactly-once 外部副作用语义。
- Guardrail、workflow、collaboration 与 child-run 状态只从 `RunSnapshot` / `RunEvent` 读取并展示，HTTP、CLI/TUI 与 Web adapter 不复制策略判断。

## Workspace 边界

所有文件系统类工具（`ReadFileTool` / `WriteFileTool` / `EditFileTool` / `ListDirTool` / `ShellExecTool` / `PythonExecTool`）统一通过注入的 `Workspace`（`domain.workspace.ports.Workspace`）完成 I/O，使用工作区相对 POSIX 路径。启动期由 `_create_local_filesystem_workspace` 做 7 步防御校验：空路径 / 相对路径 / 不存在处理 / 非目录 / 读写权限 / 构造 Policy + Adapter。详见 `docs/spec/workspace/design.md`。

## DI 容器

使用**自建 DI 容器**（`common/container.py`），非第三方框架。

生命周期：
1. **注册**（同步）：`configure_container()` 注册异步资源，并委托 `application/container/*.py` 分组注册 Port→Adapter 绑定
2. **启动**（异步）：`container.start()` 初始化资源（Telemetry、Model Providers、Redis 或本地持久化、Gateway、Workspace、委派/handoff 工具延迟注册），fail-fast 语义
3. **关闭**（异步）：`container.stop()` 逆序清理，best-effort 语义

**循环依赖解法**：`ToolRegistry → DelegateToAgentTool/HandoffToAgentTool/DelegateParallelTool → DelegationPort → TaskAgentPort → AgentPort → ToolRegistry` 通过先创建不含委派系工具的 ToolRegistry，再将三类工具作为后置异步资源（`delegate_tool_registration`）追加注册来解决，保持依赖图为 DAG。

组合根拆分后的注册入口仍是 `application/container_config.py::configure_container()`；`application/container/agent.py`、`chat.py`、`task.py`、`run.py`、`tools.py`、`storage.py` 只承载分组注册函数。它们属于组合根子模块，可以引用 infrastructure concrete adapter；普通 application 模块仍不得导入 infrastructure，静态守卫保持 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS == {}`。

## 多 Provider 模型路由

`PROVIDERS` 注册表中列出 `cliproxy` / `zhipu` / `deepseek` / `qwen` / `openai` 五个候选 env_prefix，实际是否注册取决于对应 `MODEL_<PREFIX>_ENABLED`、`MODEL_<PREFIX>_API_KEY` 与模型列表是否配置；当前 `config.properties` 默认启用 `cliproxy`、`zhipu`、`qwen`、`openai` 配置组，其中 API key 为空的 Provider 会在启动时跳过注册。路由策略：`model_prefix`（默认）或按模型轮询。`ProviderRegistry` 为每个模型维护 `itertools.cycle` 实现 Round-Robin 分发。

## 会话存储后端

由 `SESSION_STORE_BACKEND` 选择：
- `file`（默认）：`LocalFileSessionContextAdapter`，基于本地文件 + 文件锁 + 原子写，无 TTL、无后台回收任务；启动时同步执行一次 `TmpFileSweeper` 清理 `*.tmp-*` 半写残留。
- `redis`：`RedisSessionContextAdapter`，键格式 `session:context:{session_id}`，默认 TTL 3600s。

Run store 与会话后端共用 `SESSION_STORE_BACKEND` 分支：

- `file`（默认）：`LocalFileRunStoreAdapter`，布局为 `runs/snapshots/<bucket>/<run_id>.json`、`runs/events/<bucket>/<run_id>.jsonl`、`runs/indexes/client_request/<hash>.json`，通过文件锁和原子写维护 claim、状态和事件 cursor。
- `redis`：`RedisRunStoreAdapter`，维护 snapshot、queue、running set、client request index 和事件列表，使用 Redis 原子操作保护 claim 和幂等冲突。

## 存储等级抽象（StorageTier）

local-trace-artifacts 引入 `StorageTier` 作为运行产物存储的**唯一逻辑定位维度**（ADR-0002/0003），令 domain 只认「等级」、不认物理路径与后端，物理路径映射下沉到 infrastructure，使未来可在同一 tier 语义下切换本地文件 / 对象存储 / 分布式 FS 后端而不改动写入方与 domain。

- **`StorageTier`（domain，`src/domain/storage/storage_tier.py`）**：`StrEnum`，取值 `USER`（用户级，跨项目、单用户、强一致）、`PROJECT`（项目级，随工作区/仓库），预留 `TENANT`（租户级，云端多租户，本期不实现对应后端与可见性策略）。枚举与依赖它的 Port 不含任何物理路径 / 后端字符串，不导入 infrastructure。
- **`TraceStorePort` / `ArtifactStorePort`（domain，`src/domain/agent/ports.py`）**：以 `StorageTier` 为定位维度——各方法追加 keyword-only、默认 `StorageTier.PROJECT` 的 `tier` 参数，既有不传 tier 的调用点行为不变（可选注入零行为变化）。`ArtifactStorePort` 定义 `append_artifact` / `list_artifacts`，`ArtifactTrace` 值对象与既有 `AgentStepTrace` 系列同构（见 [domain-model.md](domain-model.md)）。
- **`LocalFileTierResolver`（infrastructure，`src/infrastructure/storage/local_file_tier_resolver.py`）**：本地文件后端的 tier→目录映射，是**唯一知晓 `.epsilon`/`~`/`WORKSPACE_ROOT` 的地方**，也是**全仓库唯一的 project-hash 生成点**（`project_hash()` = PROJECT 基点规范化路径 sha256 前 16 位，不含路径明文）。云端后端在同一 tier 语义下同构可替换。

本地文件 tier→目录映射：

```
PROJECT 基点 = WORKSPACE_ROOT（空 → 进程 CWD）
USER    基点 = Path.home()
project-hash = LocalFileTierResolver.project_hash()（全仓库唯一生成点）

<PROJECT 基点>/.epsilon/ ── sessions/  traces/  artifacts/  meta.json   （随项目/工作区，默认入 .gitignore）
<USER>/.epsilon/<project-hash>/ ── logs/                                （TUI/CLI 本地文件日志）
<USER>/.epsilon/persistence/<project-hash>/                             （会话主状态 USER tier 默认）
```

| 子目录 | tier | 物理位置 | 写入方 | 读取方 |
|---|---|---|---|---|
| `sessions/` | PROJECT | `<workspace>/.epsilon/sessions/` | 会话摘要/恢复索引（后续 spec） | TUI 恢复 |
| `traces/` | PROJECT | `<workspace>/.epsilon/traces/` | `ReActAgentAdapter`（经 `TraceStorePort`） | trace 查询 API |
| `artifacts/` | PROJECT | `<workspace>/.epsilon/artifacts/` | 后续 spec 工具/入口（经 `ArtifactStorePort`） | 后续 spec / 未来控制台 |
| `logs/` | **USER** | `~/.epsilon/<project-hash>/logs/` | TUI/CLI `Local_File_Log_Sink` | 运维排障 |
| `persistence/<project-hash>/` | USER | `~/.epsilon/persistence/<project-hash>/` | 会话主状态（run/checkpoint/context/index） | 会话恢复 |

> `PROJECT` tier 的 `traces/` 在本地默认（CWD == WORKSPACE_ROOT）与既有 `TRACE_STORE_DIR=.epsilon/traces` 等价，既有 trace 写入位置语义不变。日志与会话主状态均落 USER tier，共享同一 `project_hash()` 分区键，不污染项目工作区。

**Schema 元数据**：`write_schema_meta`（`src/infrastructure/storage/schema_meta.py`）在 tier `home` 下幂等写 `meta.json`（`{"schema_version": 1}`），支持未来产物 schema 迁移；DI 装配 resolver、本地文件后端就绪后对 PROJECT tier `home` 调用一次，失败仅 `logger.warning` 不中断。
