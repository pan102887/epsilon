# 领域模型

## 消息类型层次（`domain/chat/context.py`）

```
BaseMessage (abstract, @dataclass(kw_only=True))
├── role: str          (由子类实现的抽象只读属性)
├── content: str
├── metadata: dict
├── to_dict() / from_dict()   (from_dict 按 role 值分发)
│
├── SystemMessage      role = "system"
├── UserMessage        role = "user"
├── AssistantMessage   role = "assistant"
│   └── tool_calls: list[ToolCallRequest] = []
└── ToolMessage        role = "tool"
    ├── tool_name: str
    └── tool_call_id: str = ""
```

`Message = BaseMessage` 作为向后兼容别名仍然导出。

## 会话上下文（`domain/chat/context.py`）

`ConversationContext`：持有完整 `list[BaseMessage]`，不内置截断逻辑（截断由 `ContextCompactionPort` 负责）。支持 `to_dict()` / `from_dict()` JSON 序列化用于持久化。

持久化实现由 `SessionContextStorePort` 决定：
- `file` 后端（默认）：`<LOCAL_PERSISTENCE_ROOT>/sessions/<bucket>/<stem>.json`，配套 `.lock` 和 `.tmp-*`，无 TTL。
- `redis` 后端：键格式 `session:context:{session_id}`，默认 TTL 3600s。

## 聊天请求 / 响应（`domain/chat/value_objects.py`）

```python
ChatRequestVO(frozen dataclass):
    session_id: str            # 非空
    message: str               # 非空、非纯空白
    stream: bool = False
    model: str | None = None

ChatContinueRequestVO(frozen dataclass):
    session_id: str            # 非空
    stream: bool = False
    model: str | None = None

ChatResponseVO(frozen dataclass, kw_only=True):
    session_id: str
    reply: str
    model: str
    usage: dict[str, int]
    prompt_id: str
    status: "completed" | "approval_required" | "paused" = "completed"
    approval_id: str | None = None
    action_requests: tuple[PendingActionRequest, ...] = ()
    terminated_reason: AgentTerminationReason = "completed"
    can_continue: bool = False
    segment_metadata: SegmentRunMetadata = SegmentRunMetadata()
```

## 任务模型（`domain/task/value_objects.py`）

```python
TaskStatus(Enum):  SUCCESS | FAILED | PAUSED | HUMAN_INTERVENTION_REQUIRED

Task(frozen dataclass):
    goal: str                       # 非空、非纯空白
    input_data: dict[str, Any] = {}
    constraints: list[str] = []
    output_format: str | None = None
    model: str | None = None
    session_id: str | None = None
    tool_names: frozenset[str] | None = None   # None = 使用全部工具
    delegation_depth: int = 0       # 不可为负

TraceEntry(frozen dataclass):       # step, action, detail, timestamp_ms

TaskResult(frozen dataclass):
    content: str
    status: TaskStatus
    model: str
    prompt_id: str
    usage: dict[str, int] = {}
    trace: list[TraceEntry] = []
    latency_ms: float = 0.0
    terminated_reason: AgentTerminationReason = "completed"
    can_continue: bool = False
    segment_metadata: SegmentRunMetadata = SegmentRunMetadata()
    approval_id: str | None = None

TaskContinueRequest(frozen dataclass):
    session_id: str
    model: str | None = None

TaskApprovalResumeRequest(frozen dataclass):
    session_id: str
    approval_id: str
    decisions: tuple[ApprovalDecision, ...]
    model: str | None = None
```

### 任务结果映射与 trace workflow

`domain/task/result_mapping.py::TaskResultMapper` 是 task 子域的纯领域映射服务，只依赖 `domain.agent` 与 `domain.task`，不导入 `application`、`infrastructure` 或 `domain.run`。

- `status_for_agent_result(...)`：`approval_required` 映射为 `HUMAN_INTERVENTION_REQUIRED`；`TaskContinuationPolicy.should_pause(terminated_reason)` 为真时映射为 `PAUSED`；其它完成结果映射为 `SUCCESS`。
- `to_task_result(...)`：approval 结果 `content=""`、`approval_id` 来自 `AgentResult.approval`、`can_continue=False`；success 结果保留 `agent_result.content`；paused 结果 `content=""`、保留暂停 `terminated_reason`，`can_continue` 由调用方传入。

`application/task/task_trace_workflow.py::TaskTraceWorkflow` 不属于 domain，也不执行 I/O；它从 `ConversationContext` 的指定 `start_index` 后提取 `AssistantMessage.tool_calls` 与 `ToolMessage` 为 `TraceEntry`。时间戳优先使用 `ConversationContext.event_timestamps[index]`，缺失时回退当前时间。`TaskApplicationService` 组合该 workflow，Run 子域仍只消费 Task/Chat 结果并映射到 `RunExecutionOutcome`。

## 后台 Run 模型（`domain/run/`）

```python
RunStatus(StrEnum):
    queued | running | paused | awaiting_approval |
    cancel_requested | cancelled | succeeded | failed | lost

RunKind(StrEnum):
    chat | task

RunPayload(frozen dataclass):
    kind: RunKind
    session_id: str | None
    chat: dict[str, Any] | None = None
    task: dict[str, Any] | None = None
    model: str | None = None
    stable_hash() -> str

RunSnapshot(frozen dataclass):
    run_id, kind, status, payload, client_request_id, payload_hash,
    result, error, approval_id, segment_metadata, latest_event_cursor,
    can_continue, terminal_reason, lease, created_at, updated_at, version,
    latest_checkpoint_id, recoverable, recovery_attempt_count, last_recovery_error,
    task_classification, workflow_name, workflow_run_state,
    collaboration_summary, guardrail_summary

RunEvent(frozen dataclass):
    run_id: str
    cursor: int
    event_type: RunEventType
    payload: dict[str, Any]
    created_at: datetime

RunExecutionOutcome(frozen dataclass):
    status: RunStatus
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    terminal_reason: str | None
    can_continue: bool
    approval_id: str | None
    segment_metadata: dict[str, Any] | None
    workflow_run_state: dict[str, Any] | None
    collaboration_summary: dict[str, Any] | None

RunStoreMutationKind(StrEnum):
    mark_succeeded | mark_paused | mark_awaiting_approval |
    mark_failed | mark_cancelled

RunStoreMutation(frozen dataclass):
    kind: RunStoreMutationKind
    result / error / approval_id / reason
    workflow_run_state / collaboration_summary

RunOutcomePersistenceDecision(frozen dataclass):
    mutation: RunStoreMutation
    event_type: RunEventType
    terminal_outcome: RunExecutionOutcome
```

状态机核心规则：

- 只有 `queued` 可被 worker claim。
- `queued -> running/cancelled`，`running -> paused/awaiting_approval/cancel_requested/succeeded/failed/lost`。
- `paused` 和 `awaiting_approval` 可重新入队或请求取消。
- `cancelled/succeeded/failed/lost` 是终态，不允许 cancel、continue 或 claim。

RunEventType 主要事件族：

- 生命周期：`run_created`、`run_queued`、`run_claimed`、`run_heartbeat`、`segment_started`、`segment_done`、`run_paused`、`approval_required`、`cancel_requested`、`run_cancelled`、`run_succeeded`、`run_failed`、`run_lost`、`replay_expired`。
- Checkpoint / recovery：`checkpoint_saved`、`run_recovery_queued`、`run_recovery_failed`、`tool_result_replayed`。
- Guardrail：`guardrail_evaluated`、`guardrail_blocked`。
- Workflow / collaboration：`workflow_selected`、`workflow_selection_skipped`、`workflow_phase_started`、`workflow_phase_completed`、`workflow_phase_failed`、`workflow_handoff_recorded`、`role_capability_rejected`、`collaboration_step_recorded`、`collaboration_limit_hit`。
- Child run：`child_run_linked`、`child_run_waiting`、`child_run_reconciled`。

Run Port：

- `RunStorePort`：创建、幂等查询、容量统计、claim、lease refresh、cancel、mark terminal/paused/approval、approval resume、continue 入队、lost sweep、recovery 入队。
- `RunEventStorePort`：append/list/wait/trim/first_cursor。
- `RunObservationStorePort`：在同一原子区追加运行时事件并更新 `guardrail_summary` / `workflow_run_state` / `collaboration_summary`。
- `RunCheckpointStorePort`：保存 checkpoint 与工具结果 ledger，支撑 bounded recovery 和防重复工具执行。
- `RunProgressSink`：worker 执行段内向事件流写进度。

`domain/run/outcome.py::decide_run_outcome_persistence(...)` 是 Run 子域的纯判定：把 `RunExecutionOutcome.status` 映射为 `RunStoreMutationKind` 与终态 `RunEventType`，覆盖 succeeded、paused、awaiting approval、missing approval id fallback、cancelled、failed 和 unsupported status。它不执行 I/O，不记录日志，不导入 application / infrastructure / Pydantic / FastAPI / asyncio；`RunWorker` 只消费该决策并执行 `RunStorePort` / `RunEventStorePort` 调用。

Workflow / Collaboration 值对象：

- `WorkflowDefinition`、`WorkflowPhase`、`WorkflowRunState`：定义 workflow phase、当前 phase、历史记录、结果/错误摘要、active role 与 handoff state。
- `WorkflowExecutionPolicy`：控制 role capability、phase handoff/review/revise、child run 等执行策略，默认兼容关闭严格治理。
- `AgentRoleCapability`：声明角色允许的工具、delegation、handoff 与 child run 能力；未声明能力默认拒绝。
- `CollaborationSummary`：规范字段为 `latest_steps`，旧 `recent_steps` 仅在读取时兼容映射。
- `ChildRunOrchestrationState`：记录 parent-child run 链接、等待、reconciliation 与保守恢复状态。

## Agent 配置（`domain/agent/value_objects.py`）

```python
AgentConfig(frozen dataclass):
    system_prompt: str
    tool_schemas: list[dict]            # OpenAI function-calling 格式
    model: str | None
    max_rounds: int                     # > 0
    allowed_tool_names: frozenset[str]  # 默认从 tool_schemas 自动提取

AgentResult(frozen dataclass):
    content, model, usage, latency_ms, status, approval, terminated_reason

NamedAgentConfig(frozen dataclass):
    name, description, system_prompt,
    tool_names: frozenset[str] | None = None,
    model: str | None = None

DelegationResult(frozen dataclass):
    content: str
    success: bool
```

## Agent Loop 编排构件（`domain/agent/agent_loop_policy.py` + `agent_loop_orchestration.py` + `ports.py`）

承载 ReAct Agent Loop 的**循环编排主体、纯编排叶子判定与轮次终止形态值对象**，均为可脱离运行时的领域构件，零基础设施 / 框架 / Pydantic 依赖（[ADR-0011](../adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md) P2 首片 + [ADR-0012](../adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md) P2 第二片）。

### AgentLoopOrchestrator 领域服务（`domain/agent/agent_loop_orchestration.py`）

承载 `Round_Loop_Control`（轮次循环推进骨架、terminal 边界、budget 跨轮状态机、`Terminal_Round_Boundary_Assert`、`RoundOutcome` 五态产出协议）与 `Termination_Decision`（text/handoff/token_budget_exceeded/max_rounds 终止原因决策）。以异步生成器 `iter_rounds(...) -> AsyncIterator[RoundOutcome]` 形态产出轮次结果，全部运行时副作用经 `AgentLoopEffects` 端口回调，可脱离运行时以 fake effects 单测（[ADR-0012](../adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md)）。

### AgentLoopEffects 领域端口（`domain/agent/ports.py`，`Protocol`）

承载 `AgentLoopOrchestrator` 编排所需的全部运行时 I/O / 副作用回调（`prepare_runtime` / `perform_model_round` / `record_assistant_with_tool_calls` / `resolve_approval_policies` / `save_interrupt` / `prepare_tool_calls_for_execution` / `checkpoint_model_completed` / `checkpoint_approval_interrupt` / `record_terminated`）；方法签名只引用领域类型。`perform_model_round` 封装 span 开闭后返回 `ModelRoundResult`，解决 OTel contextvars/yield 冲突。由 `ReActAgentAdapter` 实现（[ADR-0012](../adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md)）。

### 值对象与纯判定

```python
RoundOutcomeKind = Literal["text", "tool_calls", "approval", "final", "handoff"]

RoundOutcome(frozen dataclass):          # Agent Loop 单轮终止形态（领域通用语言）
    kind, round_num, response, total_usage,
    tool_calls=(), approval=None, assistant_message_index=None,
    terminated_reason="completed", handoff_target=None, handoff_content=""

ModelRoundResult(frozen dataclass):      # 单轮模型调用的领域结果（封装 span/stream/guardrail 输出）
    response: LLMResponse
    total_usage: dict[str, int]

ToolGuardrailBranch = Literal["proceed", "require_approval", "stop"]

ToolExecutionClassification(frozen dataclass):  # 工具执行异常分类结果
    is_error: bool
    handoff_target: str | None
    content: str
    error_class: str | None
```

- `RoundOutcome` / `RoundOutcomeKind`：刻画单轮推进结果的五态（`text` / `tool_calls` / `approval` / `final` / `handoff`）通用语言值对象。
- `ModelRoundResult`：`perform_model_round` effect 返回的领域载体，封装模型响应与累计 usage。
- `ToolGuardrailBranch`：guardrail 决策到控制流分支的映射结果（`proceed` / `require_approval` / `stop`）。
- `ToolExecutionClassification`：工具执行异常分类结果（handoff/permission/timeout/其它 Exception）。
- `compute_total_tokens(total_usage) -> int` / `is_token_budget_exceeded(config, total_usage) -> bool`：token 预算累计计算与超限判定。
- `detect_handoff(context) -> tuple[str, str] | None`：会话上下文尾部 handoff 标记检测。
- `outcome_to_agent_result(outcome) -> AgentResult`：轮次结果到对外 `AgentResult` 的纯翻译。
- `interpret_tool_guardrail_decision(decision) -> ToolGuardrailBranch`：guardrail 决策映射为控制流分支。
- `classify_tool_execution(exc, *, handoff_signal, timeout) -> ToolExecutionClassification`：工具执行异常分类。
- `collect_pending_actions(tool_calls, allowed_tool_names, policies) -> tuple[PendingActionRequest, ...]`：审批动作筛选（纯函数，策略经入参注入）。

所有纯函数无状态、无 I/O，由 `ReActAgentAdapter` 调用点委托；`_execute_tool_call` 本体（副作用顺序）、工具并发骨架、guardrail 运行时累加器、流式累加器实现仍留基础设施。

## 护栏策略领域服务（`domain/agent/guardrail_policy.py`）

Agent 护栏的**任务类型分类**（`classify_run` / `classify_payload` 及启发式 `_looks_batch` / `_segment_count`）与**预算 / 风险护栏决策**（四个 `evaluate_*` 与内部 `_budget_decision` / `_risk_decision`）经充血化试点上提为领域服务 `StaticAgentGuardrailPolicy`（`domain/agent/guardrail_policy.py`，结构化实现 `AgentGuardrailPolicyPort`），零基础设施 / 框架 / Pydantic 依赖、可脱离运行时单测（[ADR-0014](../adr/0014-introduce-guardrail-domain-service-in-agent-subdomain.md)）。判定内嵌的 `_json_safe` 归一复用同包 `domain/agent/guardrails.py` 既有实现。基础设施同名文件 `infrastructure/agent/static_guardrail_policy.py` 降为向后兼容 re-export 垫片；护栏观测持久化、OTel span 与 guardrail 运行时统计累加仍留 `react_agent_adapter.py`。

承接首片方向，`domain/agent` 子域另三处散落 / 放错层的纯判定亦经充血化后续片收敛 / 平移进领域层（[ADR-0015](../adr/0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md)，均行为等价、零基础设施依赖）：**委派深度规范化**领域服务 `config_policy.py::DelegationDepthNormalizationPolicy`（承载「`<= 0` 回退默认值 3」归一与 `DEFAULT_MAX_DELEGATION_DEPTH = 3` 常量，与做「运行期深度 `current vs max` 比较」的 `domain/task/policy.py::DelegationDepthPolicy` 语义不同、不合并）；**审批默认查表**领域服务 `approval_lookup.py::ApprovalDefaultLookup`（承载 `DEFAULT_POLICIES` / `LOW_RISK_TOOLS` / 决策集常量与默认查表判定）；**分段续跑判定** `segmented_orchestration.py::decide_next_segment` 与 `SegmentContinuationDecision` 平移至领域层（与 `segmented_execution.py` 同层，12 门续跑判定，与做「单次终止原因 → 是否 PAUSED 映射」的 `domain/task/policy.py::TaskContinuationPolicy` 语义不重叠、不合并），基础设施同名文件 `infrastructure/agent/segmented_orchestration.py` 降为向后兼容 re-export 垫片。委托方 `AgentRuntimeConfig`（pydantic-settings）与审批 JSON 配置解析（`_parse_interrupt_on` / `_policy_from_value` / `_validate_decisions`，依赖 `json`）因框架 / 配置边界依赖按 [ADR-0008](../adr/0008-extract-domain-serialization-to-infrastructure-mappers.md) 保留在 infrastructure。

## Handoff 前置策略（`domain/agent/handoff_policy.py`）

```python
HandoffDecision(frozen dataclass):
    allowed: bool
    next_depth: int
    effective_max_depth: int
    reason: str | None = None

decide_handoff(
    *,
    current_depth: int,
    max_delegation_depth: int,
    workflow_context: WorkflowCollaborationContext | None,
) -> HandoffDecision
```

`decide_handoff(...)` 只承载 handoff depth 与 workflow handoff count 的纯判定：计算 `next_depth`，把配置侧 max depth 与 workflow recursion limit 取更严格值，并在 depth 或 handoff count 超限时返回 `handoff_depth_exceeded` / `handoff_count_exceeded:{next}>{max}`。它不读取 ContextVar，不调用 `DelegationPort`，不构造 `ToolExecutionResult`，不记录 collaboration event，也不修复 handoff model discrepancy；这些运行时上下文与工具适配职责仍属于 `infrastructure/agent/handoff_to_agent_tool.py`。

## 工具调用（`domain/agent/tools.py`）

```python
ToolExecutionResult (frozen dataclass):   # Tool.execute() 的统一返回类型
    content: str                          # 回灌给 LLM 的文本，等价于原 execute() -> str
    metadata: dict[str, Any] = {}         # 工具特有结构化 trace 元数据（默认空 dict）

Tool (ABC):
    name: str               (abstract property)
    description: str        (abstract property)
    parameters: dict        (abstract property，JSON Schema)
    async execute(**kwargs) -> ToolExecutionResult
    cast_params(params) / validate_params(params)
    to_schema() -> dict     # OpenAI function-calling 格式
    async run(ToolCallRequest) -> ToolExecutionResult   # 解析→转换→校验→执行，透传 execute()

ToolRegistry:
    register(tool)
    get(name) -> Tool | None
    has(name) -> bool
    unregister(name)
    get_schemas(tool_names=None) -> list[dict]   # tool_names=None 返回全部
    async execute(tool_call: ToolCallRequest) -> ToolExecutionResult
    create_scoped_view(tool_names: frozenset) -> ScopedToolRegistry

ScopedToolRegistry:           # 创建时快照，仅暴露 tool_names 子集
    get_schemas() -> list[dict]
    async execute(tool_call) -> ToolExecutionResult   # 不在子集内 → ToolPermissionDeniedError
```

`ToolExecutionResult` 是位于 `domain/agent/tools.py`（与 `Tool` ABC 同模块）的 frozen 值对象，作为工具执行的统一返回类型：

- `content` 语义等价于原 `execute() -> str`，是回灌给 LLM 上下文的完整文本；`ToolMessage.content` 与 checkpoint `after_tool_call` 均只取 `.content`（`str`），LLM 可见行为不变。可按 `RESULT_SUMMARY_MAX_LEN` 截断后写入 trace 的 `result_summary`，但原始值始终完整回灌 LLM。
- `metadata` 为工具类型特有的结构化 trace 扩展字段（值类型异构，故用 `dict[str, Any]`，非 API 契约），透传到 `ToolCallTrace.metadata`（见 `docs/architecture.md` trace 章节与 `docs/tools.md` 各工具 metadata）。异常路径不由工具自行构造该值对象——工具仍抛领域异常，由 `ReActAgentAdapter` 统一封装。

## 工具异常（`domain/agent/exceptions.py`）

| 异常 | 场景 |
|---|---|
| `ToolExecutionError` | 工具执行失败 |
| `ToolNotFoundError` | ToolRegistry 中找不到工具名 |
| `ToolParameterValidationError` | 参数校验失败（JSON 解析 / 缺失必填 / 类型不匹配） |
| `ToolPermissionDeniedError` | 工具不在 ScopedToolRegistry 允许集合内 |

## Workspace 领域（`domain/workspace/`）

- `Workspace` Port：10 个受控操作（7 个 I/O：`exists` / `stat` / `read` / `write` / `edit` / `list_dir` / `delete`；3 个纯函数：`resolve_path` / `capabilities` / `display_root_hint`）。
- `LocallyMaterializable` 子协议：仅本地后端实现，供 `ShellExecTool` / `PythonExecTool` 取得宿主 `cwd`。
- `WorkspacePath`、`WorkspaceStatEntry`、`WorkspaceCapabilities`、`WorkspaceBackendKind`（本期仅 `LOCAL_FILESYSTEM`）。
- 领域异常：`WorkspaceConfinementViolation` / `WorkspaceIoError` / `WorkspaceNotFoundError` / `WorkspaceUnsupportedOperationError`。
- `WorkspacePolicy`：路径规范化与越界判断，详见 `docs/spec/workspace/design.md`。
