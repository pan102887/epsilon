# ReAct Adapter 基线

本文记录 `docs/spec/p0-adapter-slimming/tasks.md` 任务 1.1 对
`ReActAgentAdapter` 的当前职责基线。

快照目标：`epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`

当前近似规模：2502 行（任务 1.1 前通过 `wc -l` 记录）。
Wave 2 ReAct 协作者集成后：2461 行（任务 4.4 检查点记录）。

## 当前角色

`ReActAgentAdapter` 是基础设施层门面，实现 `AgentPort`，同时作为
`AgentLoopOrchestrator` 使用的具体 `AgentLoopEffects` 实现。领域编排器已经
负责轮次推进与纯终止决策；adapter 仍负责运行时副作用、具体工具访问、
checkpoint I/O、trace 持久化、guardrail 运行时状态、审批状态缝合，以及
stream/event DTO 转换。

## 职责簇

### 公开 Agent 入口

- `run(...)`：同步消费 `RoundOutcome` 并返回 `AgentResult`。
- `resume(...)`：应用 HITL 决策后，从审批中断轮次继续执行。
- `run_streaming(...)`：输出 `StreamingChunk`，包括 heartbeat 与工具进度分片。
- `run_events(...)`：输出结构化 `AgentStreamEvent`，包括 status、tool events、
  approval events、assistant deltas 与 final done events。

这四个方法是 `AgentPort` 公开表面，本 P0 瘦身切片不改变签名。

### AgentLoopEffects 实现

adapter 实现 `domain.agent.agent_loop_orchestration.AgentLoopOrchestrator`
所需的 effects：

- `prepare_runtime(...)`
- `perform_model_round(...)`
- `record_assistant_with_tool_calls(...)`
- `resolve_approval_policies(...)`
- `save_interrupt(...)`
- `prepare_tool_calls_for_execution(...)`
- `checkpoint_model_completed(...)`
- `checkpoint_approval_interrupt(...)`
- `record_terminated(...)`

这些方法把领域编排连接到基础设施关注点：context build、模型流式累积、
OTel span、审批策略查询、guardrail runtime 更新、checkpoint 写入、warning
日志与 context mutation。

### 工具执行

当前工具执行主要集中在 `_execute_tool_call(...)`、
`_prepare_tool_calls_for_execution(...)` 与三个并发分发薄方法中：

- 根据 `AgentConfig.allowed_tool_names` 做工具授权。
- 工具执行前做 workflow capability 检查。
- 执行 tool abuse detection 并在阻断时构造工具结果。
- 执行 guardrail before/after 评估并写 Run guardrail observation。
- 做工具执行前 checkpoint replay 检查与执行后 ledger 写入。
- 从注册工具 metadata 与 `AgentConfig` 解析 timeout。
- 调用具体 `ToolRegistry.execute(...)`。
- 将权限拒绝、超时、普通工具异常、`HandoffPerformed` 翻译为
  `ToolExecutionResult` 或短路结果。
- 插入 `ToolMessage`、变更 metadata、记录事件时间戳。
- 通过 `ConcurrentToolExecutor` 执行同轮并发工具 dispatch/progress/events。

### 审批恢复

审批恢复当前集中在 `_apply_approval_decisions(...)`、
`_record_rejected_tool_call(...)` 与 `_latest_tool_calls_by_id(...)`：

- 校验决策数量、顺序与 allowed decision。
- 从 context 或 interrupt actions 重建原始 tool call。
- 处理 approve/edit/reject 分支。
- 解析人工编辑后的 JSON，并调用注册工具的参数 cast/validate。
- approve/edit 后通过 `_execute_tool_call(...)` 执行工具。
- reject 时写入错误 `ToolMessage`。
- 对 reject 分支保持 checkpoint replay/write 行为。
- latest tool call 查找委托给 `ApprovalCheckpointStitcher`。

### 最终轮 Streaming 与 Events

`_stream_final_round(...)` 与 `_stream_events_final_round(...)` 负责中间 ReAct
轮次之后的最终模型流式调用：

- 通过 `ContextBuilderPort` 构造上下文消息。
- 构造 `ChatRequest`。
- 调用 `model_access.stream(...)`。
- 合并 builder usage 与 final chunk usage。
- 可选使用 `_RoundStreamAccumulator` 捕获响应以保持 trace 等价。
- 为 `run_streaming(...)` 构造 final `StreamingChunk`。
- 为 `run_events(...)` 映射 `assistant_delta`、`tool_arguments_delta` 与
  `assistant_done` 事件。

### Guardrail、Checkpoint 与 Trace 委托

adapter 已委托部分细节给既有协作者，但副作用顺序仍由本文件控制：

- `ReActTraceRecorder`：trace shaping 与 trace-store 写入。
- `ApprovalCheckpointStitcher`：approval payload 序列化、pending action 收集、
  context snapshot 缝合与 latest tool-call 查找。
- `ConcurrentToolExecutor`：同轮工具并发骨架、stream progress 与结构化 tool events。
- `guardrail_runtime_accumulator`：guardrail runtime 与 tool-abuse `ContextVar` 状态。
- guardrail metadata 构造与 Run observation 写入。
- Run checkpoint context 读取与 checkpoint sink 写入。

### Context Mutation 与事件时间戳

adapter 仍直接修改 `ConversationContext`：

- 幂等注入 system prompt。
- 记录带 tool calls 的 assistant message。
- 记录工具结果消息与 metadata。
- 写入 `event_timestamps`，供后续 task trace 提取真实事件时刻。

## 允许移动的职责

以下职责可在本 P0 瘦身中移动，但必须保持行为等价并遵循 `tasks.md` 顺序：

- 仍位于 `src/infrastructure/agent/` 的工具执行协调逻辑，例如围绕窄 runtime
  protocol 的并发 dispatch/progress/event 包装。
- 仍位于 `src/infrastructure/agent/` 的审批恢复协调逻辑，例如围绕窄 runtime
  protocol 的 approve/edit/reject 顺序执行。
- 仍位于 `src/infrastructure/agent/` 的最终轮 stream/event 映射逻辑，只接收
  context、config、model access port、round 与 usage 等必要输入。
- 被证明无基础设施状态、无 I/O、无具体运行时协作者依赖的纯 metadata 构造或映射逻辑。
- 为测试 patch/import 点保留的薄兼容委托方法。

## 禁止移动到 Domain 的职责

以下职责不得在本 P0 切片中进入 domain：

- OpenTelemetry span、current span events、span status 与 OTel-specific attributes。
- `ContextVar` 运行时状态，例如当前 guardrail runtime accumulator 与 tool-abuse detector。
- `ToolRegistry` 访问、注册工具 metadata lookup、参数 cast/validate 与具体工具执行。
- checkpoint I/O，包括 `get_run_checkpoint_context()`、checkpoint sink 读写、
  replay ledger 状态处理与具体 execution-key 持久化语义。
- approval state 持久化 I/O 与具体 approval store 交互。
- 通过 `RunGuardrailRecorderPort` 写 Run guardrail observation。
- 与基础设施可观测性绑定的具体日志副作用。
- 直接模型 stream 调用或 context-builder I/O，除非仍通过既有 Port 留在基础设施。
- `AgentPort` 或 `AgentLoopEffects` 签名变更；除非后续 ADR 明确批准该边界变化。

## 既有协作者

构造期协作者：

- `ToolRegistry`：注册工具查询与具体工具执行。
- `ContextBuilderPort`：模型请求消息构造。
- `ApprovalPolicyPort`：HITL 审批策略查询。
- `ApprovalStateStorePort`：审批中断持久化。
- `trace_store`：trace 持久化后端，由 `ReActTraceRecorder` 包装。
- `guardrail_policy`：模型/工具 guardrail 评估。
- `RunGuardrailRecorderPort`：Run 级 guardrail observation 写入。
- `RunEventStorePort`：workflow capability 检查与拒绝事件。

基础设施内部协作者：

- `AgentLoopOrchestrator`：领域轮次编排服务。
- `ApprovalCheckpointStitcher`：approval payload、pending action、context snapshot
  与 latest tool-call 缝合。
- `ConcurrentToolExecutor`：同轮并发工具 dispatch、stream progress 与结构化事件。
- `ReActTraceRecorder`：model、approval、tool-call 与 error trace shaping 及写入。
- `_RoundStreamAccumulator`：stream chunk 累积为 `LLMResponse`。
- `ToolAbuseDetector`：每次运行的工具滥用检测。
- `_GuardrailRuntimeAccumulator`：每次运行的 guardrail runtime 统计与 checkpoint
  execution-key 记忆。

直接使用的领域与运行时协作者：

- `AgentConfig`、`AgentResult`、`AgentStreamEvent`、`ApprovalInterrupt`、
  `ApprovalDecision`、`ApprovalRequiredPayload`、`PendingActionRequest`。
- `ConversationContext` 与 `ToolMessage`。
- `ModelAccessPort`、`ChatRequest`、`LLMResponse`、`StreamingChunk`、
  `ToolCallRequest`。
- `get_run_checkpoint_context()` 与 `get_run_execution_context()`。
- `ToolExecutionKey`、`ToolLedgerStatus`、`ToolReplayPolicy`、
  `ToolResultLedgerEntry`、`ToolSideEffectLevel` 等 Run checkpoint 值对象。
- Workflow capability 相关值对象与 enforcement runtime。

## 后续切片基线备注

- adapter 已经把轮次推进委托给 `AgentLoopOrchestrator`；后续不应把 loop control
  拉回 adapter。
- `ConcurrentToolExecutor`、`ApprovalCheckpointStitcher` 与 `ReActTraceRecorder`
  已存在，但 adapter 仍持有围绕它们的 sequencing 与大量副作用。
- 后续行数变化以 2502 行为基线记录；Wave 2 后 adapter 净减少 41 行。
- 本文只是基线快照；不直接勾选任务，也不修改生产代码。
