# 需求文档：Agent 适配器重构（ReAct 三循环合并与代码质量收口）

## 简介

后端 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`（当前约 746 行）实现了 ReAct Agent 的同步、流式与事件流三种执行入口（`run`、`run_streaming`、`run_events`），以及审批恢复入口（`resume` → `_continue_after_tools`）。在功能持续叠加（HITL、Token 压缩、Prompt 注册表、上下文构建）后，模块出现了多处与本期重构相关的代码债务：

1. 三个入口循环结构高度相似但各自独立维护；`_continue_after_tools` 与 `run` 的循环主体几乎逐行重复。
2. 适配器代码在多处直接 `context._messages.append(AssistantMessage(...))`，绕过 `ConversationContext` 的公开 API，破坏封装。
3. `run_streaming` 在中间轮次（含工具调用）以同步 `model_access.chat()` 阻塞执行，期间不向客户端产出任何流式分片，长任务下用户体验为"长时间静默"。
4. `TaskAgentAdapter._extract_trace` 在解析阶段才用 `time.time() * 1000` 生成 `TraceEntry.timestamp_ms`，而非事件实际发生时刻；当事件密集发生而轨迹延后提取时，时间戳失真。
5. `_execute_tool_call` 用 `except Exception` 吞掉异常并将 `str(e)` 作为工具结果回灌给 LLM，但**未输出任何日志**，工具失败在线上不可观测。
6. `run_streaming` 与 `run_events` 在生成 HITL 中断元数据时使用 `[action.__dict__ for action in approval.actions]`，而 `PendingActionRequest.allowed_decisions` 是 `frozenset`，下游 JSON 序列化会失败；项目内已有 `infrastructure/agent/approval_state_store.py:approval_interrupt_to_dict` 提供正确序列化形态，但被绕开。
7. `domain/agent/value_objects.py:AgentConfig.system_prompt` 字段已声明却无任何消费方读取，属于死字段，存在解读歧义。
8. `infrastructure/task/task_agent_adapter.py` 的 `execute()` 每次都无条件 `context.add_system_message(system_prompt)`，会话被复用时会累积重复 system 消息。
9. `_apply_approval_decisions` 中 `respond` 决策分支存在，但当前所有 `ApprovalPolicy` 配置路径（默认表 `_DEFAULT_POLICIES` + `HITL_INTERRUPT_ON` 解析）均未将 `respond` 加入 `allowed_decisions`，因此该分支在生产上不可达；要么补齐文档与配置说明，要么删除死代码。

本期为**纯内部质量重构**，目标是：把三个执行入口的轮次推进归并到统一的"轮次结果生成器"，把私有访问换成 `ConversationContext` 公开 API，补齐工具失败可观测性，修正流式 HITL 元数据序列化，纠正 `TraceEntry` 时间戳来源，修复 `TaskAgentAdapter` 的系统消息累积，并对 `system_prompt` 死字段、`respond` 死分支两个二选一项给出方案候选，留待设计阶段二选一落定。

本期范围：

- 在 `ReActAgentAdapter` 内部抽取统一的轮次推进生成器，把 `run` / `run_streaming` / `run_events` / `_continue_after_tools` 都改为消费同一组轮次结果。
- 给 `ConversationContext` 增加（或暴露）支持携带 `tool_calls` 的助手消息追加 API，并在 `infrastructure/` 中替换全部直接 `context._messages.append(...)` 调用点。
- 在 `run_streaming` 中间轮次为客户端补充心跳 / 工具进度分片，避免长任务静默。
- 在 `_execute_tool_call` 增加工具失败 warning 日志（保留对 LLM 回灌 `str(e)` 的现有语义）。
- 在 `run_streaming` 与 `run_events` 的 HITL 元数据中复用与 `approval_interrupt_to_dict` 相同的 actions 序列化形态。
- 修正 `TaskAgentAdapter._extract_trace` 的 `timestamp_ms` 来源，让时间戳反映事件发生时刻。
- 修正 `TaskAgentAdapter.execute` 的系统消息注入，使其对会话复用幂等。
- `AgentConfig.system_prompt` 二选一：要么在 `ReActAgentAdapter` 中以幂等方式注入到 `ConversationContext`，要么彻底移除该字段。
- HITL `respond` 决策二选一：要么补齐配置路径与中文文档说明，要么删除 `_apply_approval_decisions` 中的死分支。

本期不包括：

- 不变更对外 HTTP / SSE 接口契约；现有事件类型 `assistant_delta` / `assistant_done` / `tool_start` / `tool_result` / `tool_error` / `approval_required` / `status` 不删不改语义，仅允许在 `run_streaming` 长任务中追加心跳类分片。
- 不调整审批策略语义、`allowed_decisions` 校验规则、HITL 恢复决策约束；仅对死代码 / 死字段做唯一一次方案落定。
- 不调整模型路由、Provider 注册或 Prompt 注册表机制。
- 不变更 `ContextCompactionPort` 的策略实现或开关；只在结构上让重构后的 `_iter_rounds` 仍然兼容压缩链路。
- 不修改前端代码；仅当前端能消费到的元数据形态从"会反序列化失败的 dict"变为"可正确反序列化的 dict"，属于隐式修复。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `infrastructure/agent/react_agent_adapter.py` 中的 `ReActAgentAdapter`，实现 `AgentPort`，提供 `run` / `run_streaming` / `run_events` / `resume` 四个执行入口。本期重构主体。 |
| Agent 轮次结果 | `Round_Outcome` | 重构后由内部生成器产出的一个轮次结束时的结构化结果，至少携带 `kind ∈ {"text", "tool_calls", "approval", "final"}` 以及该轮次的 LLM 响应、累计 usage、（可选）审批载荷与（可选）已收集的待执行工具调用。`run` / `run_streaming` / `run_events` / `resume` 均消费同一组 `Round_Outcome`。 |
| 轮次推进生成器 | `Round_Iterator` | `ReAct_Agent_Adapter` 内部的统一异步生成器，负责"构建上下文 → 调模型 → 判断 tool_calls / 审批 / 终止"的单轮推进，并以 `Round_Outcome` 形式产出。它替代当前 `run`、`run_streaming`、`run_events`、`_continue_after_tools` 各自独立的循环主体。 |
| 对话上下文 | `ConversationContext` | `domain/chat/context.py` 中的 `ConversationContext` 值对象，作为对话消息容器。本期需新增暴露用于"追加携带工具调用的助手消息"的公开 API。 |
| 助手工具调用消息追加 API | `Add_Assistant_Tool_Calls_Method` | `ConversationContext` 上的公开方法（命名候选 `add_assistant_message_with_tool_calls`），用于以一次调用追加 `AssistantMessage(content=..., tool_calls=[...])`。代码外部不得继续访问 `_messages`。 |
| HITL 审批中断 | `Approval_Interrupt` | `ApprovalInterrupt` 值对象，描述一次需要人工审批的暂停状态，包含 `actions: tuple[PendingActionRequest, ...]`、`allowed_decisions: frozenset[str]` 等。 |
| HITL 流式中断元数据 | `Approval_Stream_Metadata` | `run_streaming` 与 `run_events` 在产出"等待审批"分片或事件时附加的 `metadata` 字段，必须 JSON 可序列化，actions 形态需与 `approval_interrupt_to_dict` 一致。 |
| 审批中断序列化器 | `Approval_Interrupt_Serializer` | `infrastructure/agent/approval_state_store.py:approval_interrupt_to_dict` 中提供的 actions 序列化形态（`allowed_decisions` 通过 `sorted(...)` 转 list），是本特性中流式分片必须复用或对齐的参考实现。 |
| 流式心跳分片 | `Heartbeat_Chunk` | `run_streaming` 在中间轮次产出的非终止 `StreamingChunk`（`finished=False`），用于告知客户端"中间轮次仍在工作"。`metadata` 至少包含轮次号与一个心跳类型标记，且 `delta_content` 不破坏既有最终内容拼接语义。 |
| 工具进度分片 | `Tool_Progress_Chunk` | `run_streaming` 在中间轮次执行工具前后产出的 `StreamingChunk`，用于把"开始执行工具 X"、"工具 X 执行完成"等里程碑透出给客户端，与 `run_events` 中已有的 `tool_start` / `tool_result` 语义对齐但仍是 `StreamingChunk` 形态。 |
| 工具失败日志 | `Tool_Failure_Log` | `_execute_tool_call` 捕获到工具异常时新增的 warning 级日志，至少记录工具名、`tool_call_id`、异常摘要；不记录工具入参完整文本以避免泄露凭证。 |
| 任务 Agent 适配器 | `Task_Agent_Adapter` | `infrastructure/task/task_agent_adapter.py:TaskAgentAdapter`，本期需要修复 `_extract_trace` 时间戳来源以及 `execute()` 的系统消息累积问题。 |
| 任务执行轨迹条目 | `Trace_Entry` | `domain/task/value_objects.py:TraceEntry`，由 `Task_Agent_Adapter._extract_trace` 产出，本期 `timestamp_ms` 必须反映事件发生时刻。 |
| 系统消息幂等注入 | `System_Prompt_Idempotent_Injection` | 当 `ConversationContext` 中已存在首条 `SystemMessage` 时，不再追加；与 `ChatServiceAdapter._ensure_system_prompt` 已有模式一致。 |
| Agent 配置 | `AgentConfig` | `domain/agent/value_objects.py:AgentConfig`，本期对其 `system_prompt` 死字段做"消费"或"移除"二选一。 |
| HITL 回复决策 | `HITL_Respond_Decision` | `_apply_approval_decisions` 中 `decision.type == "respond"` 分支，本期对其做"补配置路径并文档化"或"删除死分支"二选一。 |

## 需求

### 需求 1：抽取统一的轮次推进生成器替换三循环复制

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者，我希望 `run` / `run_streaming` / `run_events` / `resume` 共享同一套轮次推进逻辑，以便后续修复或新增轮次行为只需改一处，避免再次出现 `_continue_after_tools` 与 `run` 的逐行复制。

#### 验收标准

1. THE `ReAct_Agent_Adapter` SHALL 提供单一的内部 `Round_Iterator`，由它统一负责"上下文构建 → 模型调用 → tool_calls / 审批 / 终止判定"的逐轮推进。
2. THE `Round_Iterator` SHALL 以 `Round_Outcome` 为产出单元，且 `Round_Outcome.kind` 取值范围限于 `{"text", "tool_calls", "approval", "final"}`。
3. WHEN `Round_Iterator` 在某一轮的模型响应不含 tool_calls 且未达到 `max_rounds`, THE `Round_Iterator` SHALL 产出 `Round_Outcome(kind="text")` 并终止。
4. WHEN `Round_Iterator` 在某一轮的模型响应含 tool_calls 且无任何 `Approval_Interrupt`, THE `Round_Iterator` SHALL 产出 `Round_Outcome(kind="tool_calls")`。
5. WHEN `Round_Iterator` 在某一轮的模型响应含 tool_calls 且 `_collect_pending_actions` 返回非空, THE `Round_Iterator` SHALL 产出 `Round_Outcome(kind="approval")`，载荷包含已构造的 `ApprovalRequiredPayload`，并终止。
6. WHEN `Round_Iterator` 推进到 `config.max_rounds` 仍未终止, THE `Round_Iterator` SHALL 产出 `Round_Outcome(kind="final")` 表示"已到上限，由调用方决定如何收尾"。
7. THE `ReActAgentAdapter.run` SHALL 通过消费 `Round_Iterator` 实现，不得保留独立的轮次循环主体。
8. THE `ReActAgentAdapter.run_streaming` SHALL 通过消费 `Round_Iterator` 实现，不得保留独立的轮次循环主体。
9. THE `ReActAgentAdapter.run_events` SHALL 通过消费 `Round_Iterator` 实现，不得保留独立的轮次循环主体。
10. THE `ReActAgentAdapter.resume` SHALL 通过消费 `Round_Iterator`（在恢复轮次起点之后）实现，原 `_continue_after_tools` SHALL 被移除或退化为对 `Round_Iterator` 的薄包装。
11. THE 重构 SHALL NOT 改变 `run` / `run_streaming` / `run_events` / `resume` 已有的对外行为：返回值字段、`AgentResult.status` 取值、`StreamingChunk` 形态、`AgentStreamEvent` 的 `kind` 取值集合保持不变。
12. THE `ReActAgentAdapter` 模块 SHALL NOT 在重构后保留两份及以上"上下文构建 → 模型调用 → tool_calls 分支"的循环主体复制。

### 需求 2：以公开 API 取代对 `ConversationContext._messages` 的私有访问

**用户故事：** 作为 `ConversationContext` 的维护者，我希望基础设施层不再直接读写 `_messages`，以便上下文容器的内部表示后续可以演进而不破坏调用方。

#### 验收标准

1. THE `ConversationContext` SHALL 暴露一个公开方法 `Add_Assistant_Tool_Calls_Method`，用于以一次调用追加 `AssistantMessage(content=..., tool_calls=[...])`。
2. THE `Add_Assistant_Tool_Calls_Method` SHALL 接受 `content: str` 与 `tool_calls: list[ToolCallRequest]` 参数，并在内部构造 `AssistantMessage` 后追加到消息列表。
3. THE `Add_Assistant_Tool_Calls_Method` SHALL 拥有中文 docstring，说明用途、参数与与 `add_assistant_message` 的差异。
4. WHEN 任何 `infrastructure/` 代码需要追加携带 `tool_calls` 的助手消息, THE 代码 SHALL 调用 `Add_Assistant_Tool_Calls_Method`，禁止访问 `ConversationContext._messages`。
5. THE `ReActAgentAdapter` 当前直接 `context._messages.append(AssistantMessage(...))` 的全部 4 处调用点（`run` 主循环、`run_streaming` 主循环、`run_events` 主循环、`_continue_after_tools` / 重构后等价位置）SHALL 替换为 `Add_Assistant_Tool_Calls_Method` 调用。
6. FOR ALL `epsilon-boot/src/infrastructure/` 下的生产代码, THE 代码 SHALL NOT 出现对 `ConversationContext._messages` 的直接读写。

### 需求 3：流式中间轮次补充心跳 / 工具进度，避免长任务静默

**用户故事：** 作为通过 SSE 调用 `run_streaming` 的前端用户，我希望即使中间轮次工具执行较慢，也能持续看到进度信号，以便区分"在工作"和"挂死"。

#### 验收标准

1. WHEN `ReActAgentAdapter.run_streaming` 进入一个中间轮次, THE `ReAct_Agent_Adapter` SHALL 至少产出一个 `Heartbeat_Chunk`，告知客户端轮次开始。
2. WHEN `ReActAgentAdapter.run_streaming` 在中间轮次开始执行某个工具调用, THE `ReAct_Agent_Adapter` SHALL 产出一个 `Tool_Progress_Chunk`，其 `metadata` 至少包含 `round`、`tool_name`、`tool_call_id`、`phase="start"`。
3. WHEN `ReActAgentAdapter.run_streaming` 在中间轮次完成某个工具调用, THE `ReAct_Agent_Adapter` SHALL 产出一个 `Tool_Progress_Chunk`，其 `metadata` 至少包含 `round`、`tool_name`、`tool_call_id`、`phase="end"`。
4. THE `Heartbeat_Chunk` 与 `Tool_Progress_Chunk` SHALL 具有 `finished=False`，不得提前终止流。
5. THE `Heartbeat_Chunk` 与 `Tool_Progress_Chunk` 的 `delta_content` SHALL 为空字符串，确保前端按"内容拼接"逻辑消费时不会引入额外文本。
6. THE 既有"最终轮次产出 finished=True 分片"、"中间轮次直接命中纯文本回复时产出单一 finished=True 分片"、"中间轮次进入 HITL 时产出 metadata=approval 分片" 三条主路径 SHALL 保持现有外部语义。
7. THE 心跳与工具进度分片 SHALL NOT 被纳入 `total_usage` 累加。
8. THE `Tool_Progress_Chunk.metadata` SHALL NOT 包含工具入参完整正文，避免在长流中泄露凭证或大文本。

### 需求 4：`Trace_Entry.timestamp_ms` 反映事件发生时刻

**用户故事：** 作为复盘任务执行轨迹的运维者，我希望 `Trace_Entry.timestamp_ms` 是事件发生当时的时间戳，以便据此分析工具调用耗时分布。

#### 验收标准

1. THE `Task_Agent_Adapter` SHALL 在 `Round_Iterator` 推进的每一次"模型返回 tool_calls"事件发生时刻记录该事件的发生时间戳，而不是在 `_extract_trace` 阶段统一调用 `time.time()`。
2. THE `Task_Agent_Adapter` SHALL 在每一次"工具结果产生"事件发生时刻记录该事件的发生时间戳，而不是在 `_extract_trace` 阶段统一调用 `time.time()`。
3. THE `Trace_Entry.timestamp_ms` SHALL 来源于事件发生时刻的时间戳，而不是 `_extract_trace` 调用时刻。
4. WHEN `_extract_trace` 处理同一批新增消息, THE 不同 `Trace_Entry` 的 `timestamp_ms` SHALL 允许彼此不同，反映事件发生顺序与间隔。
5. THE 时间戳 SHALL 仍以毫秒整数形式提供，保持 `Trace_Entry.timestamp_ms` 字段类型不变。
6. THE 改动 SHALL NOT 改变 `Trace_Entry` 现有字段集合（`step` / `action` / `detail` / `timestamp_ms`）。

### 需求 5：工具失败必须可观测

**用户故事：** 作为后端 SRE，我希望工具执行失败时即使被吞掉异常并回灌给 LLM，也能在日志里看到失败信号，以便定位线上工具异常。

#### 验收标准

1. WHEN `ReActAgentAdapter._execute_tool_call` 捕获到工具执行抛出的异常, THE `ReAct_Agent_Adapter` SHALL 输出至少一条 warning 级 `Tool_Failure_Log`。
2. THE `Tool_Failure_Log` SHALL 至少包含工具名 `tool_call.name`、`tool_call_id` 与异常类名/异常摘要 `str(e)`。
3. THE `Tool_Failure_Log` SHALL NOT 记录工具入参 `tool_call.arguments` 的完整文本，避免泄露密钥或大文本。
4. WHEN 工具执行被 `_ensure_tool_authorized` 抛出 `ToolPermissionDeniedError`, THE `ReAct_Agent_Adapter` SHALL 同样输出 warning 级 `Tool_Failure_Log`，标明权限拒绝原因。
5. THE `_execute_tool_call` 现有的"将 `str(e)` 作为工具结果回灌给 LLM"的语义 SHALL 保持不变。
6. THE 日志输出 SHALL 通过模块级 `logger` 完成，不得 `print`。

### 需求 6：流式 HITL 中断元数据可被 JSON 序列化

**用户故事：** 作为审批面板前端调用方，我希望流式接口返回的"等待审批"分片元数据可以被标准 JSON 解析，以便正确渲染待审批动作列表。

#### 验收标准

1. WHEN `ReActAgentAdapter.run_streaming` 触发 HITL 中断, THE `Approval_Stream_Metadata.actions` SHALL 复用与 `Approval_Interrupt_Serializer` 一致的形态，每个 action 至少包含 `tool_call_id` / `tool_name` / `arguments` / `allowed_decisions` / `reason`，其中 `allowed_decisions` 为 `sorted(list)` 形态。
2. WHEN `ReActAgentAdapter.run_events` 触发 HITL 中断, THE `Approval_Stream_Metadata.actions` SHALL 复用与 `Approval_Interrupt_Serializer` 一致的形态，约束同上一条。
3. THE `ReAct_Agent_Adapter` SHALL NOT 在 `run_streaming` 与 `run_events` 中使用 `[action.__dict__ for action in approval.actions]` 直接生成 actions 列表。
4. THE `Approval_Stream_Metadata` SHALL 通过标准 `json.dumps(...)` 在不传入自定义 `default` 的情况下序列化成功。
5. THE `Approval_Stream_Metadata` 与 `Approval_Interrupt_Serializer` 的 actions 形态差异 SHALL 通过提取共享 helper（位于 `infrastructure/agent/`）消除，而不是在两处独立维护两份字典生成代码。
6. THE `Approval_Stream_Metadata` 现有的其他字段（如 `session_id` / `approval_id` / `round` / `status`）SHALL 保持原语义不变。

### 需求 7：`AgentConfig.system_prompt` 死字段二选一落定（已落定为方案 A）

**用户故事：** 作为 `AgentConfig` 的消费方，我希望 `system_prompt` 字段要么真的被消费，要么不再存在，以便配置语义无歧义。

> 决策：本需求已由用户落定为**方案 A（消费方案）**，并补充关键约束：**每个 Agent 必须拥有独立的 `system_prompt`**——即不同 `AgentConfig` 实例之间的 `system_prompt` 互不共享、互不污染，由 `Round_Iterator` 在该 Agent 首轮模型调用前以幂等方式注入到该 Agent 自己的 `ConversationContext`。方案 B（移除）保留在文档中仅供历史追溯，不再作为本期实现选项。

#### 验收标准（最终态：方案 A）

1. WHEN `ReActAgentAdapter.run` / `run_streaming` / `run_events` / `resume` 进入第一轮模型调用之前, THE `ReAct_Agent_Adapter` SHALL 以 `System_Prompt_Idempotent_Injection` 的方式将**当前 Agent 自己的** `AgentConfig.system_prompt` 注入到 `ConversationContext`。
2. IF `ConversationContext` 已存在任何 `SystemMessage`, THEN THE `ReAct_Agent_Adapter` SHALL NOT 再追加 `AgentConfig.system_prompt`。
3. THE `AgentConfig.system_prompt` SHALL 保留在值对象中，并在 docstring 中明确"每个 Agent 拥有独立的 system_prompt，由 `ReAct_Agent_Adapter` 在该 Agent 首轮前幂等注入"。
4. THE 注入实现 SHALL 与 `ChatServiceAdapter._ensure_system_prompt` 现有判定模式（首条/任一 system 消息存在则跳过）保持语义一致。
5. THE `AgentConfig.system_prompt` 字段 SHALL 是 per-agent 独立配置项，不同 `AgentConfig` 实例之间的值互不共享、互不影响（值对象天然不可变，自动满足）。
6. WHEN 多 Agent 委派场景下子 Agent 拥有独立 `ConversationContext`（不与父 Agent 共享上下文）, THE `ReAct_Agent_Adapter` SHALL 注入子 Agent 自己的 `system_prompt`，而不是父 Agent 的 `system_prompt`。
7. WHEN 多 Agent 委派场景下子 Agent 复用父 Agent 的 `ConversationContext`（已含 system 消息）, THE `ReAct_Agent_Adapter` SHALL 遵循幂等规则不再追加，避免父子 system 提示词冲突。
8. THE 重构 SHALL NOT 在仓库中同时存在"`system_prompt` 字段被声明"与"无任何代码读取该字段"的死字段状态。
9. THE 设计阶段 SHALL 在 `design.md` 中显式记录方案 A 的具体注入点（`Round_Iterator` 入口处）与"每 Agent 独立 system_prompt"的实现细节。

### 需求 8：`Task_Agent_Adapter` 系统消息注入对会话复用幂等

**用户故事：** 作为复用同一 `session_id` 多次发起任务的调用方，我希望系统提示词不会随每次执行重复堆积，以便上下文不被冗余 system 消息污染。

#### 验收标准

1. WHEN `TaskAgentAdapter.execute` 加载到一个**已存在**的 `ConversationContext`（即通过 `task.session_id` 命中已有会话）, THE `Task_Agent_Adapter` SHALL 仅在该上下文不含任何 `SystemMessage` 时才追加 `system_prompt`。
2. WHEN `TaskAgentAdapter.execute` 加载到一个**新建**的 `ConversationContext`（`task.session_id is None` 或会话不存在）, THE `Task_Agent_Adapter` SHALL 追加一次 `system_prompt`。
3. FOR ALL 同一 `session_id` 的连续两次 `execute()` 调用, THE 上下文中 `SystemMessage` 数量 SHALL NOT 因第二次调用而增加。
4. THE 幂等判定逻辑 SHALL 与 `ChatServiceAdapter._ensure_system_prompt` 的判定模式保持一致（基于"是否存在 system 消息"，而非基于消息内容比对）。
5. WHEN 已有会话上下文中 `system_prompt` 内容与本次 `build_system_prompt(task)` 结果不一致, THE `Task_Agent_Adapter` SHALL 仍然不追加新的 `SystemMessage`，并 SHALL 输出一条 info 级日志记录"已复用既有 system 消息"。
6. THE 改动 SHALL NOT 改变首次新建上下文场景下的 `Trace_Entry` 内容、`TaskResult` 字段或 `AgentResult` 用量统计。

### 需求 9：HITL `Respond` 决策分支二选一落定（已落定为方案 B）

**用户故事：** 作为 HITL 维护者，我希望 `respond` 决策分支要么真的可达并被文档化，要么彻底删除，以避免长期保留不可达分支。

> 决策：本需求已由用户落定为**方案 B（删除死分支）**。理由：当前所有 `ApprovalPolicy` 配置路径（默认表 `_DEFAULT_POLICIES` + `HITL_INTERRUPT_ON` 解析）均未将 `respond` 加入 `allowed_decisions`，分支不可达；删除可降低 HITL 心智模型复杂度并消除潜在歧义，未来如确需 `respond` 可重新引入。方案 A 保留在文档中仅供历史追溯。

#### 验收标准（最终态：方案 B）

1. THE `_apply_approval_decisions` SHALL 移除 `decision.type == "respond"` 分支，及与之关联的 `ApprovalRespondNotAllowedError` 引用（若仅在该分支使用，则连同异常类一并删除；若被其他模块引用，则保留异常类但删除该分支的 raise 调用点）。
2. THE `ApprovalDecisionType` `Literal` 类型 SHALL 移除 `"respond"` 取值；同时 `StaticApprovalPolicyProvider._VALID_DECISIONS` SHALL 同步移除 `"respond"`。
3. THE `docs/` 中如有"`respond`"字样的 HITL 策略说明 SHALL 同步更新或删除，确保文档与代码一致。
4. THE 移除 SHALL 同步删除依赖 `respond` 的死代码、不可达测试、以及任何遗留的 `respond` 相关注释。
5. THE 仓库 SHALL NOT 在重构落地后同时存在"`_apply_approval_decisions` 处理 `respond`"且"无任何配置路径能让 `respond` 进入 `allowed_decisions`"的死分支状态。
6. THE 设计阶段 SHALL 在 `design.md` 中显式记录方案 B 的删除清单（具体涉及的代码位置、类型定义、测试、文档）。

## 非功能需求

1. THE 所有改动 SHALL 仅位于 `epsilon-boot/` 后端目录之内；前端 `epsilon-client/` 不在本期改动范围。
2. THE 所有新增公开类、公开函数与公开方法 SHALL 配备符合 `docs/steering/code-documentation.md` 的中文 docstring。
3. THE 重构 SHALL 遵循 `docs/steering/ddd-architecture.md` 的依赖方向：`domain/chat/context.py` 不得反向依赖 `infrastructure/`；`Round_Outcome`、`Round_Iterator` 等内部类型若仅服务于 `ReAct_Agent_Adapter`，应放在 `infrastructure/agent/` 内。
4. WHEN 本期需要新增配置项, THE 配置 SHALL 写入 `epsilon-boot/config.properties` 并附中文注释，禁止仅写入 `.env`（遵循 `docs/steering/config-source.md`）。
5. THE 后端依赖管理操作（如调整 `pyproject.toml`）SHALL 通过 `uv` 完成，禁止使用 `pip` / `poetry` / `pipenv` / `conda`（遵循 `docs/steering/uv-package-manager.md`）。
6. THE 重构 SHALL NOT 引入对外 HTTP / SSE 接口契约的破坏性变更：`run` 返回的 `AgentResult` 字段集、`run_streaming` 产出的 `StreamingChunk` 现有字段语义、`run_events` 产出的 `AgentStreamEvent.kind` 取值集合保持兼容。
7. THE 重构 SHALL 在 CI 中保持既有单元测试与集成测试通过，新增行为（心跳分片、工具失败日志、`Trace_Entry` 时间戳来源、`Approval_Stream_Metadata` 形态、`Task_Agent_Adapter` 幂等系统消息）SHALL 各自补充至少一条针对性测试。
8. THE 重构 SHALL NOT 增加生产路径上的额外模型调用次数；`Round_Iterator` 在每一轮的模型调用次数 SHALL 与重构前 `run` / `run_streaming` / `run_events` 各自原有的次数一致。
9. THE 重构 SHALL NOT 改变审批语义、模型路由策略、上下文压缩策略与 Prompt 注册表行为。

## 范围之外（Out of Scope）

1. 不重写 `AgentPort` 协议或新增 Port；本期是 Adapter 层内部重构。
2. 不引入新的事件类型；`AgentStreamEvent.kind` 集合保持现有取值，仅在 `StreamingChunk` 形态下追加 `Heartbeat_Chunk` 与 `Tool_Progress_Chunk`。
3. 不调整 `ContextCompactionPort` 实现或开关，不调整模型路由配置，不调整 Prompt 注册表机制。
4. 不变更前端代码；前端可能因 `Approval_Stream_Metadata` 修复而隐式获得"可正确反序列化的元数据"，但不需要也不期望前端做适配性改动。
5. 不变更 HITL 审批语义、`allowed_decisions` 校验规则；`respond` 决策已落定为方案 B（删除）。
6. 不在本期对 `system_prompt` 做"字段保留 + 同时编排方手动注入"的混合方案；已落定为方案 A（消费），且每个 Agent 拥有独立 `system_prompt`。
7. 不引入"全局共享 system_prompt"或"父子 Agent 共享 system_prompt"等跨 Agent 复用机制；每个 `AgentConfig` 实例的 `system_prompt` 字段独立、互不污染。
