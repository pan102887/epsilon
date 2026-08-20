# Agent 系统

## ReAct Agent Loop（`infrastructure/agent/react_agent_adapter.py`）

> **模块布局**：`ReActAgentAdapter` 为门面，实现 `AgentPort` 与 `AgentLoopEffects`；其技术关注点按 SRP 拆到基础设施协作模块：`guardrail_runtime_accumulator.py`（guardrail 运行时统计累加器 + ContextVar）、`react_trace_recorder.py`（结构化 trace / OTel 记账）、`react_concurrent_tool_executor.py`（底层同轮多工具执行骨架，依 ADR-0013 留基础设施）、`react_approval_checkpoint.py`（审批中断状态缝合）、`react_runtime_protocols.py`（`ToolExecutionRuntime` / `ApprovalResumeRuntime` 窄协议）、`react_tool_execution_coordinator.py`（工具 dispatch、stream progress、structured events 配对输出）、`react_approval_resume_coordinator.py`（approve/edit/reject 应用与 latest tool call 查找）、`react_final_round_streamer.py`（最终轮 chunk/event 累积、usage 合并、`tool_arguments_delta` 与 finished 输出）。这些都是基础设施层内部协作者，门面在原调用点委托，行为等价、契约不变。

实现 `AgentPort`。每轮执行流程：

```
AgentPort.run(context, config, model_access)
  ┌── 每轮（max_rounds 由 AgentConfig 传入，> 0）─────────┐
  │  1. ContextCompactionPort.compact(messages)          │
  │     → 保留全部 SystemMessage + 最后 N 条非 system    │
  │  2. 序列化为 OpenAI Chat Completions 格式            │
  │  3. ModelAccessPort.chat(ChatRequest)                │
  │  4. 有 tool_calls:                                   │
  │       a. 校验工具名是否在 allowed_tool_names 内       │
  │       b. 不在 → ToolPermissionDeniedError 作为       │
  │              ToolMessage 内容返回（LLM 自我纠正）     │
  │       c. 在 → ScopedToolRegistry.execute(tool_call) │
  │       d. 追加 AssistantMessage + ToolMessage → 下一轮│
  │  5. 无 tool_calls → 返回 AgentResult                 │
  └──────────────────────────────────────────────────────┘
```

支持 `run_streaming(...)`：中间轮次同步执行工具，最终轮次经 `ReactFinalRoundStreamer` 按 SSE 分片产出 `StreamingChunk`。

支持 `run_events(...)`：与 `run_streaming` 并列的结构化事件流入口，最终轮同样委托 `ReactFinalRoundStreamer`，产出
`AgentStreamEvent` 序列（`status` / `assistant_delta` / `assistant_done` /
`tool_start` / `tool_result` / `tool_error` / `approval_required` / `error`）。

> `assistant_delta` 事件的 `content` 字段语义为"累加文本片段"。当中间轮次模型直接返回纯文本回复时，单个 `assistant_delta` 可能携带整段文本；当最后一轮通过 `model_access.stream(...)` 真流式产出时，多个 `assistant_delta` 串接才是完整文本。客户端应按累加方式渲染，不要把每个 `assistant_delta` 视为固定长度的分片。该行为是合规的，不需要前端改动。

## 流式工具调用 ID 兼容恢复

OpenAI Chat Completions 的流式工具调用按 `delta.tool_calls[i].index` 聚合同一个工具调用；标准 Provider 应在首个 delta 中返回非空 `tool_call.id`，后续 delta 可以只追加 `function.arguments`。部分 OpenAI-compatible Provider 或中间代理会在流式分片中遗漏 `id`，此时如果已经完整累积出工具名和参数，`OpenAICompatibleAdapter` 可按配置生成本地合成 id，避免 ReAct Agent 因 `InvalidToolCallIdError(source="stream_finished")` 中断。

配置项按 Provider 前缀分别控制：

```properties
MODEL_QWEN_STREAM_TOOL_CALL_ID_STRATEGY=recover
MODEL_ZHIPU_STREAM_TOOL_CALL_ID_STRATEGY=recover
MODEL_CLIPROXY_STREAM_TOOL_CALL_ID_STRATEGY=recover
MODEL_OPENAI_STREAM_TOOL_CALL_ID_STRATEGY=raise
```

策略语义：

- `recover`：当流式工具调用缺失 id 且工具名、参数完整时，生成 `call_synthetic_<request_nonce>_<index>` 形式的本地合成 id，并继续执行工具。
- `raise`：保持严格协议校验，缺失 id 时抛 `InvalidToolCallIdError`。官方 OpenAI Provider 默认使用该策略，因为官方协议应返回 id。

兼容恢复只发生在模型接入适配层；`ToolCallRequest.id`、`PendingActionRequest.tool_call_id`、`ToolMessage.tool_call_id` 仍保持非空契约。合成 id 会同时写入 assistant `tool_calls`、工具执行入参和后续 `ToolMessage.tool_call_id`，保证审批、trace 与历史上下文可以继续按同一个 id 关联。

每次恢复都会输出 WARN 结构化日志，字段包含 `source=stream_finished`、`provider`、`model`、`tool_name`、`tool_call_index`、`raw_id_value`、`synthetic_id`、`recovery_strategy=recover`。日志不记录 API key、完整用户消息、完整 system prompt 或完整工具参数。发生恢复的 finished chunk 会携带轻量 metadata：`tool_call_id_recovered=true` 与 `synthetic_tool_call_count=<N>`。

## 任务型 Agent（`infrastructure/task/task_agent_adapter.py`）

`TaskAgentAdapter` 实现 `TaskAgentPort`，但 execute / continue / approval resume 的用例编排已下沉到 `application/task/TaskApplicationService`。组合根把 `TaskApplicationService` 与 `TaskTraceWorkflow` 注入 adapter；adapter 通过结构协议消费应用服务，不直接导入 application，未注入时保留兼容路径。

应用服务负责：

- session load/save、execute/continue/resume 的上下文推进与分段续跑聚合
- approval load → expired/count/order/allowed → consume → agent resume 的顺序
- 使用 `TaskTraceWorkflow` 做 trace shaping
- 使用 `domain/task/result_mapping.py::TaskResultMapper` 把 `AgentResult` 纯映射为 `TaskResult`
- 附加风险门 metadata、`segment_metadata`、`can_continue` 与 approval 状态

adapter 保留基础设施边界，将 `Task` 转换为 Agent 可执行形式：

- `Task.goal` → UserMessage 放入 ConversationContext
- `Task.tool_names` → 配合 `ToolRegistry.create_scoped_view(...)` 决定 `AgentConfig.allowed_tool_names`
- `Task.model` → `AgentConfig.model`
- `Task.delegation_depth` → 传递给子 Agent
- `Task.session_id` 非空时通过 `SessionContextStorePort.load/save` 与已有对话关联；为 `None` 时在临时上下文中一次性执行不落库
- 最大轮次由 `TASK_AGENT_MAX_ROUNDS` 控制，`config.properties` 默认 0（不限制轮次，≤0 归一化为不可达大数哨兵，由 token 预算/工具超时兜底）
- 经回调调用 `AgentPort.run()` / `AgentPort.resume(...)`
- prompt 构造、tool schema、model registry、`AgentConfig`、TraceStore 持久化与基础设施异常兜底仍在 adapter 边界

`TaskResult` 字段：`content` / `status` / `model` / `prompt_id` / `usage` / `trace: list[TraceEntry]` / `latency_ms` / `terminated_reason` / `can_continue` / `segment_metadata` / `approval_id`。

## 多 Agent 委派与 handoff

```
Agent A 调用 DelegateToAgentTool / DelegateParallelTool / HandoffToAgentTool
  → HandoffToAgentTool 对 handoff 调用 domain/agent/handoff_policy.py::decide_handoff(...)
  → DelegationAdapter 检查 delegation_depth（max = AGENT_MAX_DELEGATION_DEPTH，默认 3）
  → 若处于 workflow Run 且 role capability 开启，先按当前 active_role 校验 delegation/handoff 能力
  → AgentRegistryPort.get(agent_name) → 获取命名 Agent 配置（NamedAgentConfig）
  → delegate：TaskAgentPort.execute(Task(delegation_depth + 1))
  → handoff：目标 Agent 接管当前 ReAct 上下文并返回 handoff 结果
  → 结果作为 ToolMessage 返回给 Agent A；workflow handoff 会额外写入 RunEvent 与 workflow_run_state.handoff_state
```

`decide_handoff(...)` 只做 depth 与 workflow handoff count 的纯判定；`HandoffToAgentTool` 仍在 infrastructure 中读取父上下文 ContextVar、调用 `DelegationPort.handoff(...)`、构造错误 `ToolExecutionResult`、记录 collaboration limit/step/handoff 事件，并在成功时抛 `HandoffPerformed` 信号。该切片不修复既有 handoff model discrepancy。

**命名 Agent**：`NamedAgentConfig` 包含 `name` / `description` / `system_prompt` / 可选 `tool_names` / 可选 `model`；通过 `AgentRegistryAdapter` 注册后按名称查找。

**循环依赖解法**：`ToolRegistry → DelegateToAgentTool/HandoffToAgentTool/DelegateParallelTool → DelegationPort → TaskAgentPort → AgentPort → ToolRegistry`。解法：先创建不含委派系工具的 ToolRegistry，再把三类工具的注册放到异步资源 `delegate_tool_registration` 中，在所有 Port 已就绪后追加注册，保持依赖图为 DAG。

## 上下文压缩（`infrastructure/chat/sliding_window_compaction_adapter.py`）

实现 `ContextCompactionPort`：

- 输入为空 → 返回空列表
- 保留全部 `SystemMessage`
- 非 system 消息保留最后 N 条（N = `CHAT_MAX_MESSAGES`，默认 50）
- `ConversationContext.get_messages()` 始终返回完整未截断列表，截断在每轮 LLM 调用前由此 adapter 完成

## Agent 注册与发现

`AgentRegistryPort` → `AgentRegistryAdapter`：内部字典管理命名 Agent 配置，支持 `register` / `get` / `has` / `list_names`。

`AgentPort` 与 `TaskAgentPort` 分离：
- `AgentPort`：底层 ReAct Loop，接受 ConversationContext + AgentConfig
- `TaskAgentPort`：面向任务的入口，接受 `Task` 值对象，封装上下文创建/加载逻辑

## 顶层聊天编排（`infrastructure/chat/chat_service_adapter.py`）

`ChatServicePort` 的对外实现仍是 `infrastructure/chat/chat_service_adapter.py::ChatServiceAdapter`，但会话上下文和 continue/resume 用例编排已拆到 application：

```
chat(ChatRequestVO) / stream_chat(ChatRequestVO)
  → ChatSessionContextWorkflow.load_for_chat()
      加载 ConversationContext、写 session_id、幂等注入 system prompt、追加用户消息
  → _resolve_model_access(model) → (ModelAccessPort, model_str)
      有 model → model_registry.get_adapter_for_model(model)
      无 model → model_registry.get_default_model() → get_adapter_for_model()
  → 根据 tool_calling_enabled 选择路径：
      有工具 & 启用 → AgentPort.run(...)（委托 Agent Loop）
      其他         → ContextCompactionPort.compact + ModelAccessPort.chat 直接调用
  → ChatSessionContextWorkflow.save_context_and_index() 保存完整未压缩 ConversationContext 并刷新索引
  → ChatResponseVO / SSE stream
```

`ChatSessionContextWorkflow` 负责 session load、`session_id`、system prompt 幂等注入、save + index、preview 与 `prompt_id` 追踪。`ChatApplicationService` 负责 `continue_chat` 可继续性校验、approval resume 的 load / expired / decision order/count/allowed / consume / `AgentPort.resume(...)` 顺序，以及分段路径 `run_segmented_chat_on_context(...)` / `stream_segmented_chat_on_context(...)` 的风险门、保存时机、自动续跑和 `SegmentRunMetadata` 聚合。流式分段路径由 application 产出 `SegmentStreamFrame` 业务帧，`ChatServiceAdapter` 只翻译为既有 `AgentStreamEvent`/SSE 线格式。`ChatServiceAdapter` 仍保留 `chat-default` 读取 + workspace guidance 追加、模型解析、direct LLM path、stream/chunk/event 包装与 approval metadata，并通过组合根注入应用服务而不直接导入 application。其中 `chat-default` 的「加载 + workspace guidance 追加 + `prompt_id` 提取」已由 `ddd-followup-refinements` 切片 B 收敛为单一来源 `infrastructure/chat/chat_default_prompt.py::resolve_chat_default_system_prompt`，由 `ChatServiceAdapter.__init__` 与组合根 `_create_chat_service` 共同调用（行为等价，消除两处重复）。

> **系统提示词提示**：`CHAT_SYSTEM_PROMPT` 已废弃，启动期检测到该键会 fail-fast。当前系统提示词由 `PROMPT_CHAT_DEFAULT_VERSION` 选择 `prompts/chat-default/v<N>.md`，`ChatServiceAdapter` 构造期通过 `append_workspace_path_guidance()` 幂等追加 "所有文件路径使用工作区相对的 POSIX 路径" 提示。

## 后台 Run 编排

后台长任务由 `application/run/` 和 `infrastructure/run/` 承担，不改变底层 ReAct Agent Loop：

- `RunApplicationService`：adapter-neutral 应用服务，负责 create/query/events/stream/cancel/continue/approval resume。
- `RunExecutionCoordinator`：把 `RunSnapshot` 转换为 `ChatRequestVO` / `ChatContinueRequestVO` 或 `Task` / `TaskContinueRequest`，并把 Chat/Task 结果转换为 `domain/run/outcome.py::RunExecutionOutcome`。
- `domain/run/outcome.py`：承载 `RunExecutionOutcome` 与 `decide_run_outcome_persistence(...)`，把 outcome status 纯判定为 RunStore mutation 与终态 RunEventType，缺失 `approval_id` 的 awaiting approval 会保守转为 failed。
- `RunWorker`：从 `RunStorePort.claim_next()` 获取 queued run，执行一个 segment，调用 domain outcome decision 后执行 store/event 写入；保留 claim、lease、heartbeat、progress、取消检查、日志和 metrics 等 runtime 职责。
- `RunWorkerManager`：管理 worker 任务、wake_up 信号和 lost lease 扫描；通过 `infrastructure/run/worker_contracts.py` 的 `RunSegmentExecutor` / `RunRecoverySweep` / `RunRuntimeMetricsSink` 协议接收组合根注入的应用协作者，不导入 application concrete classes。

Run 的 continue 路径只调用 `continue_chat` 或 `continue_task`，不重复追加原始 user message。审批恢复由 `RunApprovalResumer` 按 `RunKind` 分派到 Chat/Task 的 `resume_approval(...)`，guardrail `require_approval` 也复用同一 HITL 链路。TUI/agent adapter 直接调用 `RunApplicationService`；FastAPI `/api/runs*` 只是薄 HTTP adapter。

Run runtime 已具备 bounded checkpoint recovery：模型调用、工具结果、审批中断和段边界会写入 checkpoint/ledger，恢复时复用已持久化状态并避免重复执行已完成工具；当 checkpoint 或 child-run reconciliation 状态不足以确认命运时，系统进入 `lost` 或保守可恢复失败态，而不是伪装成功或重放未知副作用。

Run 内的 ReAct 执行还会把 guardrail 观测通过 `RunGuardrailRecorder` 写入 `GUARDRAIL_EVALUATED` / `GUARDRAIL_BLOCKED` 事件和 `RunSnapshot.guardrail_summary`；workflow Run 会把 phase、handoff、role capability rejection、collaboration 与 child-run 状态写入 `workflow_run_state` / `collaboration_summary`。

## Human-in-the-loop 工具审批

HITL 位于 ReAct Loop 中模型返回 assistant `tool_calls` 之后、任何工具执行之前。开启 `HITL_ENABLED=true` 后，`ReActAgentAdapter` 先校验 `AgentConfig.allowed_tool_names`，再按 `ApprovalPolicyPort` 判断是否需要中断；权限拒绝仍优先于人工审批，不能通过审批绕过工具作用域。

触发审批时，Agent 追加包含原始 `tool_calls` 的 `AssistantMessage`，创建 `ApprovalInterrupt`，通过 `ApprovalStateStorePort` 保存 `ConversationContext.to_dict()` 快照、待审批动作、轮次、模型和累计 usage，然后返回 `status="approval_required"`。快照包含 assistant tool_calls，但不包含待审批工具的 `ToolMessage`；恢复时使用同一 `session_id` 与 `approval_id` 读取并消费状态，再按顺序应用 `approve/edit/reject` 决策并继续 ReAct Loop。

本项目借鉴 LangChain Deep Agents 的 `interrupt_on`、decision 和 checkpointer 语义，但不依赖 Deep Agents 执行图，也不迁移现有自研 Runtime。HITL 审批覆盖主 ReAct Loop 的工具执行前控制，包括 guardrail `require_approval`、`delegate_to_agent` / `handoff_to_agent` 等委派系工具本身，以及 Run 级审批恢复；子 Agent 内部仍按各自上下文和工具边界独立运行，不提供组织级审批流。HITL 不能替代 Workspace、`allowed_tool_names`、workflow role capability、工具参数校验、网络访问控制、命令沙箱或操作系统隔离。
