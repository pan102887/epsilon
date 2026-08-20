# 需求文档：Agent Adapter Refactor v2 Requirements

## 简介

v1 重构（`docs/spec/agent-adapter-refactor/`）已完成 ReAct Agent 三入口的循环主体合并、`ConversationContext.add_assistant_message_with_tool_calls` 公开 API、`_log_tool_failure` 工具失败可观测、`approval_payload_to_metadata` 流式 HITL 元数据序列化、`_stamp_event` + `event_timestamps` 索引、`TaskAgentAdapter` 系统消息幂等以及 `respond` 死分支删除等结构性问题。全仓 1401 测试通过。

第二轮 Review 又识别出 8 项遗留问题，集中在三个相邻领域：

1. **三入口的"轮次推进"复用仍不彻底**：`run_streaming` / `run_events` 入口仍各自调用 `_ensure_agent_system_prompt`，与 `_iter_rounds` 内部的注入形成"两层重复"；最后一轮流式调用的 "build → ChatRequest → stream → usage 合并" 在四个位置近似复制（约 80 行）；`run_events` 的工具执行块完全绕过 `_execute_tool_call`，存在两份并行实现。
2. **领域模型的隐式扩展**：`event_timestamps` 与 `session_id` 通过 `setattr` 在运行时挂在 `ConversationContext` 实例上，未参与 `to_dict()` / `from_dict()` 序列化，导致 HITL resume 反序列化后时间戳丢失；调用方依赖 `context.message_count - 1` 隐式约定 "新增消息一定在末尾"，索引语义脆弱。
3. **`run` 路径的边界行为**：当 `run` 跑满 `max_rounds` 且最后一轮仍是 `tool_calls` 时，工具结果已写回 context，但 `AgentResult.content` 静默置空且调用方无法感知"轮数超限"信号——这阻碍长跑 / 自主续跑 agent 的实现；`run_events` 的 `assistant_delta` 在中间轮次 text kind 下携带整段文本，与 streaming 真分片语义不一致，需要明确文档化"累加渲染"语义。

本期范围：

- 把 `system_prompt` 注入收口为 `_iter_rounds` 单一调用点；`max_rounds == 1` 分支不进 `_iter_rounds`，需显式注入并加注释。
- 抽取 `_stream_final_round` / `_stream_events_final_round` 私有方法，消除最后一轮流式四处复制；`max_rounds == 1` 分支也复用同一方法。
- 让 `_execute_tool_call` 接受可选 `event_emitter` 回调或返回 `(result, is_error)`，`run_events` 复用同一执行流水线；`ToolMessage.metadata` 在工具失败时携带 `error=True`，让事件流与 LLM 上下文都能识别失败。
- 让 `add_assistant_message_with_tool_calls` 与 `add_tool_result` 直接返回新消息索引（int），消除 `message_count - 1` 的隐式索引依赖。
- 把 `event_timestamps: dict[int, int]` 与 `session_id: str | None` 设为 `ConversationContext` 的正式可选字段，参与 `to_dict()` / `from_dict()` 序列化，并兼容旧格式（缺失字段视为默认值）；删除全部 `setattr` / `getattr` 用法。HITL resume 路径下时间戳通过 `context_snapshot` 自然恢复。
- 在 `domain/agent` 公开类型中明确 `assistant_delta` 的"累加片段"语义（A 路线，文档化），不引入额外模型调用。
- `_iter_rounds` 在循环耗尽且最后一轮是 `tool_calls` 时，**不**追加额外 `chat()` 回灌；改为通过 `RoundOutcome` 携带 `terminated_reason="max_rounds"`，由四个入口统一透传到 `AgentResult.terminated_reason`，让调用方（顶层编排 / 自主续跑循环 / 用户层）显式感知"轮数超限"信号并自行决策续跑或终止。同时记录一条 `Max_Rounds_Termination_Warning` 警告日志便于线上观测。该方案对齐 OpenAI Assistants（`incomplete_details.reason="max_completion_tokens"` 等）、LangGraph（`GraphRecursionError`）、CrewAI（`max_iter` failed）等业内主流方案的共识——把超限信号原样暴露给调用方。

本期不包括：

- 不引入真流式 typewriter 渲染（中 4 选 A，仅文档化）。
- 不修改 `_log_tool_failure` 已有行为。
- 不新增工具或 Provider，不修改前端代码。
- 不变更 HITL 审批语义、模型路由、Prompt 注册表。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 唯一 system_prompt 注入点 | `Single_System_Prompt_Injection_Site` | 重构后 `system_prompt` 的唯一生产调用点位于 `_iter_rounds` 入口；`run` / `run_streaming` / `run_events` 三个入口处的 `_ensure_agent_system_prompt` 调用全部删除。`max_rounds == 1` 分支不进 `_iter_rounds`，必须**显式**调用 `_ensure_agent_system_prompt` 并加注释说明"该分支不经 `_iter_rounds`，需独立保证幂等注入"。 |
| 最后一轮流式辅助方法 | `Final_Round_Stream_Helper` | 抽取的两个私有方法的统称：`_stream_final_round(context, config, model_access, base_usage) -> AsyncIterator[StreamingChunk]`（用于 `run_streaming`）与 `_stream_events_final_round(context, config, model_access, base_usage, round_num) -> AsyncIterator[AgentStreamEvent]`（用于 `run_events`）。负责构建上下文、组装 `ChatRequest`、调用 `model_access.stream(...)`、合并 usage，并产出最终 `finished=True` 分片或 `assistant_done` 事件。`max_rounds == 1` 分支与中间轮次耗尽后的"最后一轮"分支**复用同一方法**。 |
| 统一工具执行流水线 | `Unified_Tool_Execution_Pipeline` | 重构后 `run_events` 的工具执行不再保留独立的 "鉴权 → 执行 → 异常 → add_tool_result → _stamp_event" 实现，统一通过 `_execute_tool_call` 完成。具体落地形式有两种候选（设计阶段二选一，本期需求层面只约束"复用同一管线"）：(a) `_execute_tool_call` 接受可选 `event_emitter: Callable[[AgentStreamEvent], Awaitable[None]] \| None` 回调，由其内部产出 `tool_start` / `tool_result` / `tool_error` 事件；(b) `_execute_tool_call` 返回 `(result: str, is_error: bool)`，由 `run_events` 在外侧根据 `is_error` 选择 `tool_result` 或 `tool_error`。 |
| 工具失败元数据标记 | `Tool_Failure_Metadata_Flag` | 工具执行失败（含 `ToolPermissionDeniedError` 与运行期异常）时，写入 `ConversationContext` 的 `ToolMessage.metadata` 中携带 `error=True`，使事件流与 LLM 上下文均可识别失败状态。该标记会改变 `ToolMessage.to_dict()` 输出（`metadata` 由空变为非空），需在测试中显式覆盖。 |
| 追加消息返回索引 | `Add_Message_Index_Return` | `ConversationContext.add_assistant_message_with_tool_calls` 与 `ConversationContext.add_tool_result` 的返回类型由 `None` 变更为 `int`，返回值为新追加消息在 `_messages` 中的索引（即追加后的 `len(_messages) - 1`）。`add_assistant_message` / `add_user_message` / `add_system_message` 不在打戳路径上，**不强制**修改返回类型。 |
| ConversationContext 升级字段 | `Context_Promoted_Field` | `ConversationContext` 上由 `setattr` 隐式挂载的两个属性升级为正式可选字段：(a) `event_timestamps: dict[int, int]`，默认 `{}`，记录 `message_index → 事件发生时刻毫秒整数`；(b) `session_id: str \| None`，默认 `None`，记录该上下文所属的会话 ID。两者必须参与 `to_dict()` 与 `from_dict()` 序列化，缺失时视为默认值（向后兼容旧格式）。 |
| 审批中断时间戳回环 | `Approval_Interrupt_Timestamp_Roundtrip` | HITL 中断时，`ApprovalInterrupt.context_snapshot = context.to_dict()` 已序列化 `event_timestamps`；`resume` 路径反序列化 `consumed.context_snapshot` 得到的新 context 必须自动恢复 `event_timestamps`，使得 resume 后 `_extract_trace` 读取的中断前事件时间戳与中断前一致，而不是 resume 时刻。 |
| assistant_delta 累加语义 | `Assistant_Delta_Cumulative_Semantics` | `AgentStreamEventKind.assistant_delta` 注释明确语义为"累加文本片段，可能为整段也可能为分块；客户端按累加渲染"。`run_events` 在中间轮次 text kind 下产出的 `assistant_delta` 携带整段文本不视为缺陷，无需通过额外 `model_access.stream()` 重发。 |
| Agent 终止原因 | `AgentTerminationReason` | 新增 `Literal["completed", "max_rounds"]` 类型别名，定义在 `domain/agent/value_objects.py`。`"completed"` 表示模型自然给出最终回复或工具调用循环正常收尾；`"max_rounds"` 表示循环达到 `config.max_rounds` 上限时最后一轮仍返回 `tool_calls`、工具已被执行但模型尚未对工具结果给出最终回复。该类型仅刻画"为何停止"，与 `AgentRunStatus`（`completed` / `approval_required`）正交：`status="approval_required"` 时 `terminated_reason` 为 `"completed"` 或缺省（HITL 中断不属于轮数超限）。 |
| AgentResult 终止原因字段 | `Terminated_Reason_Field` | `AgentResult` 新增可选字段 `terminated_reason: AgentTerminationReason = "completed"`。`run` / `run_streaming` / `run_events` / `resume` 在循环耗尽且最后一轮 `kind == "tool_calls"` 时 SHALL 把该字段置为 `"max_rounds"`；其他自然终止路径保持默认 `"completed"`。调用方（顶层编排 / 自主续跑循环）SHALL 通过该字段判断是否需要续跑或向用户提示"轮数超限"。 |
| RoundOutcome 终止原因字段 | `Round_Outcome_Terminated_Reason` | `RoundOutcome`（`infrastructure/agent/round_outcome.py`）新增可选字段 `terminated_reason: AgentTerminationReason = "completed"`，仅在 `kind == "final"` 时有意义；`_iter_rounds` 循环耗尽且最后一轮为 `tool_calls` 时产出 `RoundOutcome(kind="final", terminated_reason="max_rounds", ...)`，其它情形保持 `"completed"`。该字段供四个入口透传到 `AgentResult.terminated_reason`。 |
| max_rounds 命中告警 | `Max_Rounds_Termination_Warning` | `_iter_rounds` 在循环耗尽且最后一轮 `kind == "tool_calls"` 时（即将产出 `terminated_reason="max_rounds"` 的 `RoundOutcome` 之前）输出一条 `logger.warning("Agent Loop 达到 max_rounds 仍存在未消费 tool_calls", extra={...})`，至少携带 `round_num` 与本轮 `tool_call` 数量；不记录工具入参完整文本。该警告与是否触发回灌无关，仅作为"轮数超限"线上观测信号；`run_streaming` / `run_events` / `resume` 在同样输入下若同样命中也产出该警告（行为统一）。 |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter`，本期 v2 重构主体。 |
| 任务 Agent 适配器 | `Task_Agent_Adapter` | `infrastructure/task/task_agent_adapter.py:TaskAgentAdapter`，本期通过 `Context_Promoted_Field` 替换 `getattr(context, "_event_timestamps", ...)` 读取方式。 |
| 聊天服务适配器 | `Chat_Service_Adapter` | `infrastructure/chat/chat_service_adapter.py:ChatServiceAdapter`，本期通过 `Context_Promoted_Field` 替换 `setattr(context, "session_id", ...)` 写入方式。 |
| 对话上下文 | `ConversationContext` | `domain/chat/context.py:ConversationContext`，本期承载 `event_timestamps` 与 `session_id` 两个新正式字段。 |

## Functional Requirements

### 需求 1：system_prompt 注入收口为 `_iter_rounds` 单一调用点

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者，我希望 `system_prompt` 注入只发生在一个生产代码位置，以便后续修改注入语义只需改一处，避免再次出现"入口注入 + 循环内注入"的两层重复。

#### Acceptance Criteria

1. THE `ReAct_Agent_Adapter` SHALL 在 `_iter_rounds` 入口（首次进入循环之前）保留唯一一处生产调用 `_ensure_agent_system_prompt(context, config)`。
2. WHEN 重构落地后, THE `ReAct_Agent_Adapter.run_streaming` 与 `ReAct_Agent_Adapter.run_events` 入口处 SHALL NOT 再调用 `_ensure_agent_system_prompt`。
3. WHEN `config.max_rounds == 1` 且不进入 `_iter_rounds`, THE `ReAct_Agent_Adapter` SHALL 在该分支显式调用 `_ensure_agent_system_prompt(context, config)` 一次，并在调用点附近加注释说明"该分支不经 `_iter_rounds`，需独立保证幂等注入"。
4. THE `Single_System_Prompt_Injection_Site` SHALL 维持原有幂等语义：`config.system_prompt` 为空跳过；`context.get_messages()` 中已存在任何 `role == "system"` 的消息时跳过。
5. THE 重构 SHALL NOT 改变 v1 的 system_prompt 幂等性回归测试结果；`grep -n "_ensure_agent_system_prompt" src/infrastructure/agent/react_agent_adapter.py` SHALL 在生产代码路径中只出现 2 处（`_iter_rounds` 入口与 `max_rounds == 1` 分支），不含定义本身。

### 需求 2：抽取 `Final_Round_Stream_Helper` 消除最后一轮流式四处复制

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者，我希望最后一轮流式调用只在一个私有方法中实现，以便后续调整 usage 合并、metadata 透传或上下文构建时只需改一处。

#### Acceptance Criteria

1. THE `ReAct_Agent_Adapter` SHALL 提供私有方法 `_stream_final_round(context, config, model_access, base_usage) -> AsyncIterator[StreamingChunk]`，封装"build → ChatRequest → stream → 合并 usage → 产出 finished 分片"的完整逻辑。
2. THE `ReAct_Agent_Adapter` SHALL 提供私有方法 `_stream_events_final_round(context, config, model_access, base_usage, round_num) -> AsyncIterator[AgentStreamEvent]`，封装"build → ChatRequest → stream → 产出 assistant_delta + assistant_done"的完整逻辑。
3. WHEN `ReAct_Agent_Adapter.run_streaming` 进入 `config.max_rounds == 1` 分支, THE `ReAct_Agent_Adapter` SHALL 通过 `_stream_final_round` 完成流式产出。
4. WHEN `ReAct_Agent_Adapter.run_streaming` 进入"中间轮次耗尽后的最后一轮", THE `ReAct_Agent_Adapter` SHALL 通过 `_stream_final_round` 完成流式产出。
5. WHEN `ReAct_Agent_Adapter.run_events` 进入 `config.max_rounds == 1` 分支, THE `ReAct_Agent_Adapter` SHALL 通过 `_stream_events_final_round` 完成流式产出。
6. WHEN `ReAct_Agent_Adapter.run_events` 进入"中间轮次耗尽后的最后一轮", THE `ReAct_Agent_Adapter` SHALL 通过 `_stream_events_final_round` 完成流式产出。
7. THE 重构 SHALL 消除 `react_agent_adapter.py` 中四处 "build → ChatRequest → stream → usage 合并" 的近似复制（共约 80 行）。
8. THE 重构 SHALL NOT 改变 `run_streaming` 与 `run_events` 已有的对外语义：`StreamingChunk` 字段集合、`AgentStreamEvent.kind` 取值集合、usage 累加逻辑保持不变。
9. THE 重构 SHALL NOT 改变模型调用次数：`run_streaming` 与 `run_events` 在 `config.max_rounds == N` 时仍然是 N-1 次 `chat()` + 1 次 `stream()`；`max_rounds == 1` 时仅 1 次 `stream()`。

### 需求 3：`run_events` 复用 `_execute_tool_call` 工具执行流水线

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者，我希望 `run_events` 与 `run` / `run_streaming` 共用同一份工具执行流水线，以便修改工具执行行为只需改一处；同时希望工具失败状态通过 `ToolMessage.metadata` 标记，使事件流与 LLM 上下文一致识别失败。

#### Acceptance Criteria

1. THE `ReAct_Agent_Adapter` SHALL 通过 `_execute_tool_call`（或其等价签名）作为唯一的工具执行入口，覆盖 `run` / `run_streaming` / `run_events` 三个入口的工具执行步骤。
2. THE `run_events` SHALL NOT 保留第二份独立的 "鉴权 → 执行 → 异常 → add_tool_result → _stamp_event" 实现。
3. THE `Unified_Tool_Execution_Pipeline` SHALL 通过以下两种候选之一落地（设计阶段二选一）：
   - (a) `_execute_tool_call` 接受可选 `event_emitter: Callable[[AgentStreamEvent], Awaitable[None]] \| None` 回调，由其内部产出 `tool_start` / `tool_result` / `tool_error` 事件；
   - (b) `_execute_tool_call` 返回 `(result: str, is_error: bool)`，由 `run_events` 在外侧根据 `is_error` 选择 `tool_result` 或 `tool_error`。
4. WHEN 工具执行抛出 `ToolPermissionDeniedError`, THE `ReAct_Agent_Adapter` SHALL 在写入 `ConversationContext` 的 `ToolMessage.metadata` 中携带 `error=True`。
5. WHEN 工具执行抛出 `Exception`（非 `ToolPermissionDeniedError`）, THE `ReAct_Agent_Adapter` SHALL 在写入 `ConversationContext` 的 `ToolMessage.metadata` 中携带 `error=True`。
6. WHEN 工具执行成功, THE `ReAct_Agent_Adapter` SHALL NOT 在 `ToolMessage.metadata` 中写入 `error` 标记（缺省即非错误）。
7. THE `run` / `run_streaming` / `run_events` 三个入口产出的 `ToolMessage` 序列化形态 SHALL 在失败/成功上保持一致：失败时 `to_dict()` 输出含 `metadata: {"error": true}`，成功时输出不含 `metadata` 键（与既有 `to_dict()` 跳过空 metadata 的语义一致）。
8. THE 重构 SHALL 保留 v1 已落地的 `_log_tool_failure` warning 日志行为，不降级也不修改其字段集合。
9. THE 重构 SHALL 保留"将 `str(e)` 作为工具结果回灌给 LLM"的语义不变。

### 需求 4：`Add_Message_Index_Return` 消除 `message_count - 1` 隐式索引

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者，我希望追加消息的 API 直接返回新消息的索引，以便不再依赖"`add_xxx` 之后消息一定在末尾"的隐式约定，让索引语义显式化。

#### Acceptance Criteria

1. THE `ConversationContext.add_assistant_message_with_tool_calls` SHALL 返回新追加消息在 `_messages` 中的索引（int 类型，等于追加后 `len(_messages) - 1`）。
2. THE `ConversationContext.add_tool_result` SHALL 返回新追加消息在 `_messages` 中的索引（int 类型，等于追加后 `len(_messages) - 1`）。
3. THE `ConversationContext.add_assistant_message` / `add_user_message` / `add_system_message` 的返回类型 SHALL 保持 `None` 不变（不在打戳路径上，本期不强制改造）。
4. WHEN `ReAct_Agent_Adapter` 在工具执行后需要为 `ToolMessage` 打戳, THE 调用点 SHALL 通过 `context.add_tool_result(...)` 的返回值获取索引，而非读取 `context.message_count - 1`。
5. WHEN `ReAct_Agent_Adapter` 在 `_record_assistant_with_tool_calls` 中追加 AssistantMessage 后需要打戳, THE 调用点 SHALL 通过 `context.add_assistant_message_with_tool_calls(...)` 的返回值获取索引，而非读取 `context.message_count - 1`。
6. FOR ALL `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中的代码, THE 文件 SHALL NOT 出现 `context.message_count - 1` 表达式。
7. THE `ConversationContext.to_dict` 与 `ConversationContext.from_dict` 的输出/输入字段集合 SHALL NOT 因本需求而改变（仅返回值类型变更，序列化形态不变）。
8. THE 重构 SHALL 在 `test/domain/chat/` 下补充覆盖"两个新返回值类型为 int 且等于追加后索引"的单元测试。

### 需求 5：`event_timestamps` 与 `session_id` 升级为 `ConversationContext` 正式字段

**用户故事：** 作为 `ConversationContext` 的维护者，我希望 `event_timestamps` 与 `session_id` 通过显式字段承载并参与序列化，以便 HITL resume 反序列化后能自动恢复时间戳，且全仓不再依赖 `setattr` / `getattr` 这种隐式属性传递。

#### Acceptance Criteria

1. THE `ConversationContext` SHALL 新增正式字段 `event_timestamps: dict[int, int]`，默认值为空 dict，含义为 `message_index → 事件发生时刻毫秒整数`。
2. THE `ConversationContext` SHALL 新增正式字段 `session_id: str | None`，默认值为 `None`，含义为该上下文所属的会话 ID。
3. THE `ConversationContext.to_dict` SHALL 在序列化输出中包含 `event_timestamps` 与 `session_id`；当字段为默认值（空 dict / None）时**允许**省略以保持紧凑序列化（设计阶段决定具体策略，但反序列化必须双向兼容）。
4. THE `ConversationContext.from_dict` SHALL 接受不含 `event_timestamps` 字段的旧格式数据，缺失时视为空 dict（向后兼容）。
5. THE `ConversationContext.from_dict` SHALL 接受不含 `session_id` 字段的旧格式数据，缺失时视为 `None`（向后兼容）。
6. THE `ReAct_Agent_Adapter._stamp_event` SHALL 通过 `context.event_timestamps[index] = int(time.time() * 1000)` 写入正式字段，删除 `setattr(context, "_event_timestamps", ...)` 与 `getattr(context, "_event_timestamps", None)` 调用。
7. THE `Task_Agent_Adapter._extract_trace`（或等价位置）SHALL 通过 `context.event_timestamps` 读取正式字段，删除 `getattr(context, "_event_timestamps", {}) or {}` 调用。
8. THE `Chat_Service_Adapter` 现有 4 处 `setattr(context, "session_id", request.session_id)` SHALL 替换为对正式字段 `context.session_id = request.session_id` 的直接赋值。
9. FOR ALL `epsilon-boot/src/` 下的生产代码, THE 仓库 SHALL NOT 出现 `setattr(context, ...)`、`getattr(context, "_event_timestamps", ...)` 或 `getattr(context, "session_id", ...)` 的调用。
10. THE 重构 SHALL 在 `test/domain/chat/` 下补充 `to_dict` / `from_dict` 往返测试，覆盖：(a) 包含 `event_timestamps` 与 `session_id` 的新格式；(b) 不含两字段的旧格式（默认值还原）；(c) 仅含其中一个字段的混合旧格式。

### 需求 6：HITL resume 路径下 `event_timestamps` 通过快照自然恢复

**用户故事：** 作为 HITL resume 路径的调用方，我希望中断前已发生的 AssistantMessage(tool_calls) / ToolMessage 的事件时间戳在 resume 后恢复为中断前的时刻，而不是 resume 时刻，以便 `Trace_Entry.timestamp_ms` 在 resume 前后保持时间序连续。

#### Acceptance Criteria

1. THE `ApprovalInterrupt.context_snapshot` SHALL 由 `context.to_dict()` 生成；其输出 SHALL 包含 `event_timestamps`（在该字段非空时）。
2. WHEN `ReActAgentAdapter.resume` 反序列化 `consumed.context_snapshot` 还原 `ConversationContext` 实例, THE 还原后的实例 SHALL 自动恢复 `event_timestamps` 字段（通过需求 5 的 `from_dict` 兼容路径完成）。
3. THE `_apply_approval_decisions` 在 reject 分支调用 `_stamp_event` 时, THE 写入位置 SHALL 是反序列化得到的新 context 的 `event_timestamps` 正式字段（而非 `setattr` 挂载的隐式属性）。
4. WHEN HITL resume 完整往返（中断 → 持久化 → 恢复 → `_extract_trace`）执行后, THE `Trace_Entry.timestamp_ms` 中针对中断前事件的时间戳 SHALL 等于中断前 `_stamp_event` 写入的毫秒整数，而不是 resume 时刻。
5. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充 HITL resume 时间戳回环测试：在中断前注入已知 `event_timestamps`，经过 `to_dict` → 持久化 → `from_dict` → resume 后，验证 `Trace_Entry.timestamp_ms` 与中断前一致。

### 需求 7：`assistant_delta` 累加语义文档化（A 路线）

**用户故事：** 作为 `run_events` 的客户端实现者，我希望 `assistant_delta` 的语义被明确为"累加片段，可能整段也可能分块；客户端按累加渲染"，以便 SDK 能正确处理"中间轮次 text kind 整段返回"与"最后一轮 stream 真分片"两种情形。

#### Acceptance Criteria

1. THE `domain/agent/value_objects.py`（或 `domain/agent/ports.py`，以代码实际位置为准）中 `AgentStreamEventKind.assistant_delta` 注释 SHALL 明确语义为"累加文本片段，可能为整段（中间轮次直接命中纯文本回复时）也可能为分块（最后一轮 stream 真分片）；客户端应按累加方式渲染"。
2. THE `docs/agent.md` SHALL 同步更新对 `assistant_delta` 的说明，与代码注释保持一致；如果 `docs/api.md` 涉及 `assistant_delta` 描述，也 SHALL 同步更新。
3. THE 重构 SHALL NOT 引入额外的 `model_access.stream(...)` 调用以"模拟分片"——`run_events` 的中间轮次 text kind 仍然产出整段 `assistant_delta`。
4. THE 重构 SHALL NOT 改变 `run_events` 现有测试的断言（即整段 `assistant_delta` 不视为缺陷）。
5. THE 文档更新 SHALL 同时说明"中间轮次 text kind 整段返回"是合规行为，避免后续 reviewer 再次将其标为缺陷。

### 需求 8：`AgentResult.terminated_reason` 暴露 `max_rounds` 命中信号（业内共识方案）

**用户故事：** 作为 `run` / `run_streaming` / `run_events` 的调用方（含未来的自主长跑 / 续跑编排），我希望当 `max_rounds` 命中且最后一轮仍是 `tool_calls` 时，能从 `AgentResult` 中直接读到一个明确的"轮数超限"信号，由我自行决策续跑、降级或终止；而**不希望** Agent 内部自作主张地额外调用一次模型来"补救"——后者会掩盖超限信号、阻碍长跑续跑、并叠加额外推理成本。

> 本需求对齐 OpenAI Assistants（`incomplete_details.reason="max_completion_tokens"` 等）、LangGraph（`GraphRecursionError`）、CrewAI（`max_iter` failed）、AutoGPT 等业内主流方案的共识——把超限信号原样暴露给调用方，不在 Agent 内部做"recovery chat"。

#### Acceptance Criteria

1. THE `domain/agent/value_objects.py` SHALL 新增类型别名 `AgentTerminationReason = Literal["completed", "max_rounds"]`，附带中文 docstring 说明：`"completed"` 表示模型自然给出最终回复或工具调用循环正常收尾；`"max_rounds"` 表示循环达到 `config.max_rounds` 上限时最后一轮仍返回 `tool_calls`、工具已被执行但模型尚未对工具结果给出最终回复。
2. THE `AgentResult` SHALL 新增可选字段 `terminated_reason: AgentTerminationReason = "completed"`，并在 `Attributes` docstring 中说明：调用方应据此决策续跑或终止；`status="approval_required"` 时该字段保持 `"completed"`（HITL 中断由 `status` 单独表达，不属于"轮数超限"）。
3. THE `AgentResult` SHALL 维持 `frozen=True` 与既有字段集合（`content` / `model` / `usage` / `latency_ms` / `status` / `approval`）不变；`terminated_reason` 作为带默认值的新增字段加在末尾，不破坏既有构造调用。
4. THE `RoundOutcome`（`infrastructure/agent/round_outcome.py`）SHALL 新增可选字段 `terminated_reason: AgentTerminationReason = "completed"`，仅在 `kind == "final"` 时具有非默认值；其他 kind 保持默认。
5. WHEN `_iter_rounds` 推进到 `effective_terminal` 仍未自然终止，且最后一轮 `RoundOutcome.kind == "tool_calls"`、工具已通过外部回写完成（context 末尾为 `ToolMessage`）, THE `_iter_rounds` SHALL yield `RoundOutcome(kind="final", round_num=effective_terminal, response=last_response, total_usage=..., terminated_reason="max_rounds")`，**不**追加任何额外 `model_access.chat(...)` 或 `model_access.stream(...)` 调用；模型调用次数严格等于循环体内已完成的次数。
6. WHEN `_iter_rounds` 在循环耗尽时最后一轮 `kind == "text"`（无 tool_calls）, THE `_iter_rounds` SHALL 保持现有行为，产出 `RoundOutcome(kind="final", terminated_reason="completed", ...)`，不进入 `max_rounds` 分支。
7. WHEN `_iter_rounds` 在循环耗尽时最后一轮命中 `kind == "approval"`, THE `_iter_rounds` SHALL 直接 yield `RoundOutcome(kind="approval", ...)` 并 return；`terminated_reason` 字段保持默认 `"completed"`（HITL 中断不属于"轮数超限"，由 `AgentResult.status="approval_required"` 单独表达）。
8. WHEN `_iter_rounds` 即将产出 `terminated_reason="max_rounds"` 的 `RoundOutcome` 时, THE `ReAct_Agent_Adapter` SHALL 输出一条 `Max_Rounds_Termination_Warning` 日志，至少携带 `round_num` 与本轮 `tool_call` 数量；不记录工具入参完整文本（NFR-7）。该警告对四个入口（`run` / `run_streaming` / `run_events` / `resume`）行为一致。
9. WHEN `ReActAgentAdapter.run` 消费到 `RoundOutcome(kind="final", terminated_reason="max_rounds")`, THE `run` SHALL 把该 reason 透传到 `AgentResult.terminated_reason`，并保持 `AgentResult.status == "completed"`、`AgentResult.content == last_response.content`（最后一轮 tool_calls 响应的 content，通常为空字符串）、`AgentResult.usage` 等于循环体内累计 usage。
10. WHEN `ReActAgentAdapter.run_streaming` / `ReActAgentAdapter.run_events` 在中间轮次耗尽分支检测到 `outcome.kind == "final"` 且 `outcome.terminated_reason == "max_rounds"`, THE 入口 SHALL **不**调用 `_stream_*_final_round`（即不再发起 `model_access.stream(...)`）；`run_streaming` SHALL 直接产出 `StreamingChunk(delta_content="", finished=True, usage=outcome.total_usage, metadata={"terminated_reason": "max_rounds"})`；`run_events` SHALL 直接产出 `AgentStreamEvent(kind="assistant_done", usage=outcome.total_usage, metadata={"round": outcome.round_num, "terminated_reason": "max_rounds"})`。
11. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充 `run` / `run_streaming` / `run_events` 三入口在 `max_rounds` 命中且最后一轮 tool_calls 场景的覆盖测试：验证 (a) 模型调用次数严格等于"循环体内已完成的次数"——`run` 为 `max_rounds` 次 chat、`run_streaming` / `run_events` 为 `max_rounds` 次 chat（不再发起最后一轮 stream）；(b) `AgentResult.terminated_reason == "max_rounds"` 且 `AgentResult.status == "completed"`；(c) `Max_Rounds_Termination_Warning` warning 被记录 1 次，`extra` 含 `round_num` 与 `tool_call_count`，且日志内容**不含** `tool_call.arguments` 完整文本；(d) 边界：最后一轮 `kind == "text"` → `terminated_reason == "completed"`；(e) 边界：最后一轮 `kind == "approval"` → `AgentResult.status == "approval_required"` 且 `terminated_reason == "completed"`；(f) `resume` 在 `max_rounds` 命中且最后一轮 tool_calls 时同样产出 `terminated_reason == "max_rounds"`。

## Non-Functional Requirements

### NFR-1 模型调用次数严格不变（无 v2 例外）

- THE `ReActAgentAdapter.run` SHALL 在 `config.max_rounds == N` 时执行 N 次 `chat()`（中间轮次每轮 1 次 + 最后一轮 1 次），与 v1 一致；当 `max_rounds` 命中且最后一轮 tool_calls 时**不**追加额外 `chat()`，仅通过 `AgentResult.terminated_reason="max_rounds"` 暴露超限信号。
- THE `ReActAgentAdapter.run_streaming` SHALL 在 `config.max_rounds == N` 时执行 N-1 次 `chat()` + 1 次 `stream()`；`max_rounds == 1` 时仅 1 次 `stream()`；当 `max_rounds` 命中且最后一轮 tool_calls 时**不**调用 `_stream_*_final_round`，仅执行 N 次 `chat()`、不发起 `stream()`，并通过 `AgentResult.terminated_reason="max_rounds"` 暴露超限信号。
- THE `ReActAgentAdapter.run_events` SHALL 在 `config.max_rounds == N` 时执行 N-1 次 `chat()` + 1 次 `stream()`；`max_rounds == 1` 时仅 1 次 `stream()`；`max_rounds` 命中时同 `run_streaming`：N 次 `chat()`、不发起 `stream()`，通过 `terminated_reason` 暴露。
- THE `Final_Round_Stream_Helper` 抽取 SHALL NOT 引入任何额外的模型调用次数。
- 本期 SHALL NOT 引入任何"recovery chat"或"补救 stream"以掩盖超限信号——这与业内主流方案（OpenAI Assistants `incomplete` 状态、LangGraph `GraphRecursionError`）的共识保持一致。

### NFR-2 不变量保持（含 v2 受控扩展）

- THE `AgentResult.status` 取值集合 SHALL 不变（仍为 `Literal["completed", "approval_required"]`）。
- THE `AgentResult` 字段集合 SHALL 仅以"末尾追加可选字段"形式扩展：新增 `terminated_reason: AgentTerminationReason = "completed"`；既有字段（`content` / `model` / `usage` / `latency_ms` / `status` / `approval`）类型与默认值不变；`frozen=True` 不变。
- THE `RoundOutcome` 字段集合 SHALL 仅以"末尾追加可选字段"形式扩展：新增 `terminated_reason: AgentTerminationReason = "completed"`；其他字段不变。
- THE `AgentStreamEvent.kind` 取值集合 SHALL 不变（仅注释/文档语义在需求 7 中明确）。
- THE `StreamingChunk` 字段集合 SHALL 不变；`max_rounds` 命中分支产出的 `StreamingChunk` 通过 `metadata.terminated_reason` 透传超限信号（`metadata` 字段已存在，仅写入新键，不引入字段）。
- THE `ToolMessage` 字段集合 SHALL 不变；仅 `metadata` 在工具失败时由空变为 `{"error": True}`，该变化通过 `to_dict()` 已有的"非空 metadata 才输出"语义自然透出。
- THE 新增 `AgentTerminationReason` 类型别名 SHALL 仅引入 `Literal["completed", "max_rounds"]` 两个取值，未来扩展（如 `"context_window_exceeded"` / `"timeout"`）属于本期 v2 之后的演进。

### NFR-3 DDD 边界

- THE `event_timestamps` 与 `session_id` 升级为 `ConversationContext` 正式字段后, THE 字段定义 SHALL 仍位于 `domain/chat/context.py`，不得反向引入对 `infrastructure/` 的依赖。
- THE 字段类型 SHALL 仅使用 Python 标准库（`dict[int, int]` / `str | None`），不得引入 ORM、Pydantic Settings、Redis SDK 等基础设施类型。
- THE `Round_Outcome`、`Final_Round_Stream_Helper`、`Unified_Tool_Execution_Pipeline` 等内部抽象 SHALL 继续位于 `infrastructure/agent/`，遵循 v1 已落地的归属约定。

### NFR-4 向后兼容序列化

- THE `ConversationContext.from_dict` SHALL 接受不含 `event_timestamps` 字段的旧数据（视为空 dict）。
- THE `ConversationContext.from_dict` SHALL 接受不含 `session_id` 字段的旧数据（视为 `None`）。
- THE `ConversationContext.from_dict` SHALL 同时接受 v1 已存在的旧格式（仅含 `messages` 字段，可能含被忽略的 `max_messages`）。
- WHEN 旧格式数据通过 `from_dict` 反序列化后再 `to_dict`, THE 输出 SHALL NOT 因新增字段为默认值而引入序列化失败或字段污染。

### NFR-5 docstring 中文规范

- THE 所有新增/修改的公开方法（`add_assistant_message_with_tool_calls` 返回值变更、`add_tool_result` 返回值变更、`_stream_final_round`、`_stream_events_final_round`）SHALL 配备符合 `docs/steering/code-documentation.md` 的中文 docstring。
- THE 新增类型别名 `AgentTerminationReason` SHALL 在定义处附带中文 docstring，列出每个取值的语义。
- THE `AgentResult` 与 `RoundOutcome` 新增的 `terminated_reason` 字段 SHALL 在类 docstring 的 `Attributes` 段显式说明含义、默认值与"何时为 `"max_rounds"`"的判定规则。
- THE `ConversationContext` 新增字段 `event_timestamps` 与 `session_id` SHALL 在类 docstring 与 `Attributes` 段中显式描述。

### NFR-6 静态扫描

- WHEN PR 完成后, THE `grep -rn 'setattr(context,' epsilon-boot/src/` SHALL 在生产代码中零结果。
- WHEN PR 完成后, THE `grep -rn 'context.message_count - 1' epsilon-boot/src/infrastructure/agent/` SHALL 零结果。
- WHEN PR 完成后, THE `grep -rn 'getattr(context, "_event_timestamps"' epsilon-boot/src/` SHALL 零结果。
- WHEN PR 完成后, THE `grep -rn 'getattr(context, "session_id"' epsilon-boot/src/` SHALL 零结果。

### NFR-7 日志规范

- WHEN 即将产出 `terminated_reason="max_rounds"` 的 `RoundOutcome`, THE `ReAct_Agent_Adapter` SHALL 输出一条 `logger.warning("Agent Loop 达到 max_rounds 仍存在未消费 tool_calls", extra={...})`，`extra` 至少包含 `round_num`、`tool_call_count`。
- THE `Max_Rounds_Termination_Warning` SHALL NOT 记录工具入参（`tool_call.arguments`）完整文本，避免泄露凭证或大文本。
- THE `Max_Rounds_Termination_Warning` SHALL 通过模块级 `logger` 完成，不得 `print`。
- THE v1 已落地的 `_log_tool_failure` warning 行为 SHALL NOT 因本期重构而被降级或字段缩减。

## Out of Scope

1. 不引入真流式 typewriter 渲染或 SDK 端"按字打印"语义；需求 7 仅文档化 `assistant_delta` 累加语义（A 路线）。
2. 不修改 `_log_tool_failure` 的字段集合、日志级别或其调用链；本期保留 v1 行为。
3. 不新增工具或 Provider，不调整 `ContextCompactionPort` 实现/开关，不调整模型路由配置，不调整 Prompt 注册表机制。
4. 不修改前端 `epsilon-client/` 代码；前端将隐式获得"`assistant_delta` 语义文档"与"HITL resume 时间戳更准确"两项收益，但不需要也不期望前端做适配性改动。
5. 不变更 HITL 审批语义、`allowed_decisions` 校验规则；v1 已落定 `respond` 决策方案 B（删除），本期 v2 不重新引入。
6. 不再调整 `system_prompt` 字段语义；v1 已落定方案 A（消费），本期 v2 仅收口注入调用点为单一位置。
7. 不引入对 `BaseMessage` / `AssistantMessage` / `ToolMessage` 字段集合的变更；`Tool_Failure_Metadata_Flag` 仅写入既有的 `metadata` 字段，不新增字段。
8. 不引入新的 `AgentStreamEventKind` 取值；`Unified_Tool_Execution_Pipeline` 复用现有 `tool_start` / `tool_result` / `tool_error`。
9. 不在本期对 `add_assistant_message` / `add_user_message` / `add_system_message` 强制添加返回值（这三个不在打戳路径上）；`Add_Message_Index_Return` 仅约束打戳路径上的两个公开方法。
10. 不在本期实现"基于 `terminated_reason` 的自主续跑循环"——`AgentResult.terminated_reason="max_rounds"` 的下游消费者（顶层编排续跑逻辑、前端提示横幅等）属于后续演进；本期仅暴露信号，不做续跑。
11. 不在本期为 `AgentTerminationReason` 引入 `"completed"` / `"max_rounds"` 之外的取值（如 `"context_window_exceeded"` / `"timeout"` / `"user_cancelled"`）；这些属于未来扩展。
