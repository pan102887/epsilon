# 需求文档：Agent Adapter Refactor v3 Requirements

## 简介

v2 重构（`docs/spec/agent-adapter-refactor-v2/`，commit `feb5ec6`）已完成 system_prompt 注入收口、`Final_Round_Stream_Helper` 抽取、`Unified_Tool_Execution_Pipeline` 统一、`Add_Message_Index_Return` 索引显式化、`Context_Promoted_Field` 序列化升级、HITL resume 时间戳回环、`assistant_delta` 累加语义文档化以及 `AgentTerminationReason` 暴露 `max_rounds` 信号等 8 项问题。全仓 1480 测试通过。

第三轮 Review 在 v2 基础上识别出 5 项遗留优化点，集中在三个相邻领域：

1. **流式协议升级**：v2 `_iter_rounds` 在中间轮次（即非最后一轮的 tool_calls 链路）仍通过 `model_access.chat()` 一次性等待整段响应；同时在 `run` 入口下即使最后一轮是 text 路径也走 chat()。两者合并后的现实结果是：用户在长 tool_calls 链路 + 末轮 text 终止的典型场景下，整个中间链路的"chat 等待时间"叠加放大了 token-to-first-byte 体验差距，与主流 Agent 框架（OpenAI Assistants、LangGraph、Vercel AI SDK 等）"全程 stream"风格不一致。同时 `OpenAICompatibleAdapter.stream()` 当前丢弃 OpenAI SDK 流式分片中的 `delta.tool_calls` 字段，导致工具调用参数无法以增量形式向前端透传，限制了 typewriter 风格的工具调用展示能力。（注：v3 中"中间轮次"语义上一定是 tool_calls 链路；纯文本路径会在任一轮次立即触发 `text` 终止，不构成"中间轮次纯文本"组合。）
2. **超时与预算治理**：v2 工具执行没有任何超时控制——单个工具卡死即整个 Agent Loop 阻塞；Agent 整体也没有 token 预算上限，长跑 Agent 可能产生不可预期的高额推理成本。两者均缺乏与 v2 已落定的 `max_rounds` 治理同等地位的兜底机制。
3. **代码清晰度**：v2 `_iter_rounds` 循环耗尽分支保留了 `last_response.tool_calls` 的判断兜底（"non-pending tool_calls" 分支静默回退到 `terminated_reason="completed"`），实际上该兜底分支仅在 `terminal_round=0` 等数学边界可达，正常路径永不进入；保留该分支会让"循环耗尽路径恰有两种 final 形态"的不变量在阅读上不显式，应通过 `assert` 强制约束并加中文注释。

本期范围：

- **决策 1**：删除 `ReActAgentAdapter` 内部对 `model_access.chat()` 的所有调用，`_iter_rounds` 改为全程通过 `model_access.stream()` 推进，内部累积 `delta_content` / `tool_calls` 至完整 `LLMResponse` 后再继续既有循环语义；保留 `ModelAccessPort.chat()` 端口本身（仍被 `ChatServiceAdapter` 无工具路径与 `LLMSummaryCompactionAdapter` 复用，调研结论详见"调研结论"小节）。
- **决策 2**：扩展 `StreamingChunk` 协议在末尾追加可选 `tool_calls: list[<工具调用增量值对象>] | None = None` 字段（**字段元素值对象类型由 design 阶段决定，详见"调研 3"**；语义契约见术语表 `Streaming_Chunk_Tool_Calls_Field`）；`OpenAICompatibleAdapter.stream()` 透传 OpenAI SDK 流式分片中的 `delta.tool_calls` 至该字段；`AgentStreamEventKind` 新增 `tool_arguments_delta` 取值，`run_events` 在最后一轮真流式产出工具调用时按分片产出 `tool_arguments_delta` 事件以供前端 typewriter 渲染。
- **决策 3**：`AgentConfig` 新增 `tool_timeout_seconds: float | None = None` 全局默认；`Tool` 协议新增可选 `timeout_seconds` 属性允许 per-tool 覆盖；`_execute_tool_call` 通过 `asyncio.wait_for` 包裹工具执行，超时视为 `is_error=True`，`ToolMessage.metadata` 写入 `error=True`（与 v2 工具失败一致），`ToolMessage.content` 为中文 `"工具执行超时（{N}s）"`。
- **决策 4**：`AgentConfig` 新增 `max_total_tokens: int | None = None`；`_iter_rounds` 每轮 `merge_usage` 后检查累计 `total_tokens`（无该 key 时回退到 `prompt_tokens + completion_tokens`），超限时在本轮工具执行后产出 `RoundOutcome(kind="final", terminated_reason="token_budget_exceeded")` 并记录 warning；`AgentTerminationReason` 扩展为 `Literal["completed", "max_rounds", "token_budget_exceeded"]`；四入口透传该 reason 到 `AgentResult.terminated_reason`。
- **决策 5**：`_iter_rounds` 循环耗尽分支用 `assert` 强制约束最后一轮要么 `tool_calls` 非空且 context 末尾是 `ToolMessage`，要么是其他自然终止情形（应在循环内已 return），并加中文注释说明仅 `terminal_round=0` 边界可达；删除原"non-pending tool_calls 静默回退到 completed"分支。

本期不包括：

- 不引入 per-tool retry、circuit breaker、tool-level rate limit；超时只做"中断 + 标记失败"，不做自动重试。
- 不引入 cost / 计费预算（按用户决策推迟）。
- 不引入 Pydantic AI 风格的 `request_limit` / `response_tokens_limit` 等多维预算。
- 不实现自主续跑 / 自动恢复（与 v2 一致）。
- 不升级 `tool_use` 顶层（非 ReAct）流式协议；本期仅治理 ReAct 路径。
- 不修改 `ModelAccessPort.chat()` 端口签名；不删除 `ChatServiceAdapter` 无工具路径与 `LLMSummaryCompactionAdapter` 对该端口的依赖。
- 不修改前端 `epsilon-client/` 代码；前端将隐式获得"末轮纯文本逐字推送"（v2 在 `run` 入口最后一轮 text 路径下走 `chat()` 整段返回，v3 改为 `stream()` 逐字推送）与"最后一轮工具调用 typewriter 增量"两项收益，但不需要也不期望前端做适配性改动。

## 调研结论

### 调研 1：`ModelAccessPort.chat()` 全仓使用面（决策 1 范围）

`grep -n "model_access.chat(" epsilon-boot/src/` 与 `grep -n "\.chat(" epsilon-boot/src/` 命中：

| 调用点 | 路径 | 用途 | 本期处置 |
| --- | --- | --- | --- |
| `react_agent_adapter.py:456` | ReAct Agent Loop `_iter_rounds` | 中间轮次推进 | **本期删除**，改为内部累积 `model_access.stream()` 分片 |
| `chat_service_adapter.py:238` | 顶层聊天无工具路径 | 单次 LLM 调用获取完整回复 | **保留**：决策 1 不涉及顶层聊天，本期不升级该路径 |
| `llm_summary_compaction_adapter.py:66` | LLM 摘要压缩适配器 | 摘要生成请求 | **保留**：摘要生成本身不需要流式，且 ContextCompactionPort 本期不在范围内 |
| `domain/model_access/ports.py:21` | 端口 docstring 示例代码 | 文档 | 与端口同保留 |
| `infrastructure/model_access/openai_compatible_adapter.py:13` | 适配器模块 docstring 示例代码 | 文档 | 保留 |

**结论**：`ModelAccessPort.chat()` 端口本身**保留**，签名不变；本期仅删除 ReAct Agent 内部对 `chat()` 的依赖（`react_agent_adapter.py:456` 一处），由全程 `stream()` + 内部累积替代。这与用户初始决策"评估并删除 ReAct 内部 chat 调用"以及"port 兼容由调研结论决定"的指引一致。

### 调研 2：`OpenAICompatibleAdapter.stream()` 与底层 SDK 的 tool_calls 流式能力

阅读 `infrastructure/model_access/openai_compatible_adapter.py`：

- 当前 `stream()` 实现仅透传 `delta.content` 到 `StreamingChunk.delta_content`，**未**透传 `delta.tool_calls`。
- 底层 OpenAI Python SDK 在流式响应中支持 `chunk.choices[0].delta.tool_calls`（`tool_calls.delta.function.arguments` 为参数分片，`tool_calls[i].index` 标识同一工具调用的连续分片），见 OpenAI 官方流式 API 文档。
- 仓库中**没有** Anthropic provider 实现（仅 `OpenAICompatibleAdapter` 一类），因此本期决策 2 仅需升级 OpenAI 兼容适配器；Anthropic 的 `input_json_delta` 事件类型留给未来引入 Anthropic provider 时再处理。

**结论**：底层 SDK 已具备 tool_calls 流式增量能力，是 adapter 层未透传——本期需要在 `OpenAICompatibleAdapter.stream()` 内部把 `delta.tool_calls` 解析并累积/透传到 `StreamingChunk.tool_calls`（新字段，决策 2）。

### 调研 3：`StreamingChunk.tool_calls` 字段值对象类型的归属层

`StreamingChunk.tool_calls` 字段的**语义契约**（"携带本分片新观察到的工具调用增量数据；`finished=True` 分片携带累积完整列表语义"）属于 requirement 层；但**字段元素的具体值对象类型**（是直接复用 `domain/agent/value_objects.py:ToolCallRequest`，还是新增专用增量值对象——例如以 `index` / `id` / `name` / `arguments_delta` 4 字段表达更细粒度的分片增量）属于 design 阶段权衡：直接复用 `ToolCallRequest` 会迫使 adapter 在每个分片产出"完整 arguments JSON"，与"按字符增量推送"的目标不一致；新增专用增量值对象则可同时表达"分片增量"与"`finished=True` 完整重组"两种语义。

**结论**：requirement 层**只规定语义契约**（`tool_calls` 字段在 `None` / 增量分片 / `finished=True` 完整重组三种状态下的语义），**不绑定**字段元素的具体值对象类型；具体类型由 design 阶段决定（design.md 当前选定为新增的 `StreamingToolCallDelta` 增量值对象，requirement 层不复述其字段定义）。下文术语表与 AC 在描述该字段时统一以 `<工具调用增量值对象>` 占位，避免与 design 选型耦合。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 全程 stream 推进路径 | `Stream_Only_Path` | v3 重构后 ReAct Agent 内部不再调用 `model_access.chat()`，`_iter_rounds` 每轮通过 `model_access.stream(...)` 推进；流式分片在生成器内部累积为完整的 `LLMResponse`（`content` 由 `delta_content` 拼接、`tool_calls` 由分片重组）后再驱动既有的 "tool_calls / text / approval" 分支判断。`run` / `run_streaming` / `run_events` / `resume` 四入口均依赖该路径。该路径下每轮严格触发 1 次 `model_access.stream(...)`，与 v2 每轮 1 次 `model_access.chat(...)` 在调用次数上一致。 |
| StreamingChunk 工具调用字段 | `Streaming_Chunk_Tool_Calls_Field` | `domain/model_access/value_objects.py:StreamingChunk` 末尾新增可选字段 `tool_calls: list[<工具调用增量值对象>] \| None = None`，默认 `None` 表示该分片不携带工具调用相关数据；非 `None` 时表示该分片携带工具调用增量数据。**字段元素的具体值对象类型由 design 阶段决定**（详见"调研 3"，design.md 当前选定为新增的 `StreamingToolCallDelta`，requirement 层不绑定具体类型，亦不复述其字段定义）。语义契约：(a) 当 `finished=False` 时，`tool_calls` 非 `None` 表示本分片新观察到工具调用增量（如 `id` / `name` 首次出现，或 `arguments` 增量子串），多分片之间按 OpenAI SDK `delta.tool_calls.index` 语义可由 adapter / 下游消费者重组；(b) 当 `finished=True` 且分片包含完整工具调用时，`tool_calls` 为按 `index` 重组后的累积完整列表，保证下游消费者即使丢弃中间增量分片，也能从 `finished=True` 分片获得等价于 `LLMResponse.tool_calls` 语义的完整数据；(c) `frozen=True` 不变。 |
| 工具调用参数增量事件 | `Tool_Arguments_Delta_Event` | `AgentStreamEventKind` 新增取值 `"tool_arguments_delta"`：在最后一轮 `model_access.stream(...)` 真流式产出工具调用且 SDK 提供 arguments 增量时，`run_events` 通过 `_stream_events_final_round` 按分片产出 `AgentStreamEvent(kind="tool_arguments_delta", tool_call_id=..., tool_name=..., arguments=<增量字符串>, metadata={"round": ...})` 事件，供前端 typewriter 渲染工具入参。本期仅在最后一轮（即 `Final_Round_Stream_Helper` 之内）产出该事件；中间轮次累积期间不产出。 |
| 全局工具超时 | `Tool_Timeout_Global` | `AgentConfig` 末尾新增可选字段 `tool_timeout_seconds: float \| None = None`，默认 `None` 表示不启用超时。当配置为正数时，`ReActAgentAdapter._execute_tool_call` 内部以该值为默认超时阈值，通过 `asyncio.wait_for` 包裹工具执行调用。 |
| 工具级超时覆盖 | `Tool_Timeout_Per_Tool` | `domain/agent/tools.py:Tool` 抽象基类新增可选属性 `timeout_seconds: float \| None`（默认 `None`）允许具体工具在子类内 override；当某工具实例的 `timeout_seconds` 不为 `None` 时，`_execute_tool_call` 优先使用工具级值而非全局 `tool_timeout_seconds`。两者全部为 `None` 时不启用超时，与 v2 行为一致。 |
| 工具超时失败语义 | `Tool_Timeout_Failure_Semantics` | 当工具执行触发 `asyncio.TimeoutError` 时，`_execute_tool_call` 视为工具失败（`is_error=True`）：(a) `ToolMessage.metadata` 写入 `error=True`（与 v2 `Tool_Failure_Metadata_Flag` 一致）；(b) `ToolMessage.content` 为中文字符串 `"工具执行超时（{N}s）"`，其中 `{N}` 为实际生效的超时秒数（`Tool_Timeout_Per_Tool` 优先，否则 `Tool_Timeout_Global`）；(c) 同时通过 `_log_tool_failure` 输出 warning，`reason="timeout"`；(d) 失败结果回灌给 LLM 让模型据此决策。该路径**不**触发 `ApprovalInterrupt`。 |
| Token 预算上限 | `Token_Budget_Limit` | `AgentConfig` 末尾新增可选字段 `max_total_tokens: int \| None = None`，默认 `None` 表示不启用 token 预算检查。当配置为正整数时，`_iter_rounds` 每轮 `merge_usage` 后立即检查累计预算口径（详见 `Token_Budget_Computation_Rule`），超限即跳出循环并以 `Token_Budget_Exceeded_Reason` 终止。 |
| Token 预算计算口径 | `Token_Budget_Computation_Rule` | 累计 token 预算口径取 `total_usage.get("total_tokens")`；当该键不存在或为 0 时回退到 `total_usage.get("prompt_tokens", 0) + total_usage.get("completion_tokens", 0)`。该口径在每轮 `merge_usage(total_usage, builder_result.usage, response.usage)` 完成后立即评估；当评估结果 `> config.max_total_tokens` 即视为超限。 |
| Token 预算超限终止原因 | `Token_Budget_Exceeded_Reason` | `AgentTerminationReason` 在 v2 `Literal["completed", "max_rounds"]` 基础上扩展为 `Literal["completed", "max_rounds", "token_budget_exceeded"]`。当 `_iter_rounds` 在某轮检测到累计 token 超过 `Token_Budget_Limit` 时，产出 `RoundOutcome(kind="final", terminated_reason="token_budget_exceeded", ...)`；四个入口透传到 `AgentResult.terminated_reason` 与 `StreamingChunk.metadata.terminated_reason` / `AgentStreamEvent.metadata.terminated_reason`。 |
| Token 预算超限告警 | `Token_Budget_Exceeded_Warning` | `_iter_rounds` 在即将产出 `terminated_reason="token_budget_exceeded"` 的 `RoundOutcome` 之前，输出一条 `logger.warning("Agent Loop 累计 token 超过 max_total_tokens 预算", extra={...})`，至少携带 `round_num`、`accumulated_total_tokens`、`max_total_tokens`；不记录 `tool_call.arguments` / `delta_content` 全文。该告警与 `Max_Rounds_Termination_Warning` 互斥（一次终止只可能命中其一）。 |
| 终止边界断言 | `Terminal_Round_Boundary_Assert` | `_iter_rounds` 循环耗尽分支用 `assert` 强制约束：当 `last_response is not None` 时，`bool(last_response.tool_calls) and isinstance(messages[-1], ToolMessage)` 必须为 True。配套中文注释说明：常规路径下"中间轮次返回纯文本"或"中间轮次需要审批"已在循环体内 `yield text/approval; return`，唯一可达本分支的情形是"循环体跑完所有 N 轮，最后一轮返回 tool_calls 且工具已被外层执行回写"——其他组合仅在 `terminal_round=0` 等数学边界可达，本期通过 assert 让该不变量显式化，删除 v2 残留的 "non-pending tool_calls 静默回退到 completed" 分支。 |
| 中间轮次内部累积 | `Mid_Round_Stream_Aggregation` | `_iter_rounds` 中间轮次的 `model_access.stream(...)` 调用被生成器内部完整消费——`async for chunk in model_access.stream(req)` 累积所有 `delta_content` 拼接为 `content`，把所有 `chunk.tool_calls` 按 OpenAI SDK index 语义合并去重为 `list[ToolCallRequest]`，最后取 `chunk.finished=True` 分片的 `usage` 作为本轮 usage。**中间轮次累积期间不向上层产出任何分片或事件**——`run_streaming` / `run_events` 在中间轮次的对外行为（heartbeat / tool_progress / status / tool_start / tool_result）保持与 v2 字面一致。这保证 v3 的"全程 stream"是内部实现升级，不破坏既有流式协议时序。 |
| ReAct 内 chat 调用零命中 | `ReAct_Internal_Chat_Zero_Reference` | 落地后 `grep -rn 'model_access\.chat(' epsilon-boot/src/infrastructure/agent/` 命中数为 0；同时 `grep -rn 'await\s\+model_access\.chat(' epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 命中数为 0。`ChatServiceAdapter` 与 `LLMSummaryCompactionAdapter` 不在该 grep 范围内，仍保留 `chat()` 调用。 |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter`，本期 v3 重构主体。 |
| 任务 Agent 适配器 | `Task_Agent_Adapter` | `infrastructure/task/task_agent_adapter.py:TaskAgentAdapter`，本期通过 `agent.run(...)` 间接受益于 `terminated_reason="token_budget_exceeded"` 的传播，自身代码改动最小（仅消费 `AgentResult.terminated_reason` 时增加新取值的处理）。 |
| 聊天服务适配器 | `Chat_Service_Adapter` | `infrastructure/chat/chat_service_adapter.py:ChatServiceAdapter`，本期不修改其内部 `model_access.chat()` 调用（仅 ReAct 内部切流）；如未来需要将无工具路径也升级为流式可在后续 spec 中处理。 |
| 对话上下文 | `ConversationContext` | `domain/chat/context.py:ConversationContext`，本期不新增字段。 |
| 流式响应分片 | `StreamingChunk` | `domain/model_access/value_objects.py:StreamingChunk`，本期末尾追加可选字段 `tool_calls`。 |
| Agent 流式事件 | `AgentStreamEvent` | `domain/agent/value_objects.py:AgentStreamEvent`，本期 `kind` 取值集合扩展（追加 `tool_arguments_delta`）。 |
| Agent 终止原因 | `AgentTerminationReason` | `domain/agent/value_objects.py:AgentTerminationReason`，本期 `Literal` 取值集合扩展，由 `["completed", "max_rounds"]` 变为 `["completed", "max_rounds", "token_budget_exceeded"]`。 |
| Agent 配置 | `AgentConfig` | `domain/agent/value_objects.py:AgentConfig`，本期末尾追加两项可选字段 `tool_timeout_seconds` 与 `max_total_tokens`。 |
| 工具协议 | `Tool` | `domain/agent/tools.py:Tool` 抽象基类，本期新增可选属性 `timeout_seconds: float \| None`，默认 `None`，由具体工具子类按需 override。 |

## Functional Requirements

### 需求 1：`ReAct_Agent_Adapter` 内部全程 stream，删除对 `model_access.chat()` 的依赖

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者与最终用户，我希望 ReAct Agent 内部不再使用同步 `chat()`，全程通过 `stream()` 推进，以便最后一轮 text 路径下用户感知 token-to-first-byte 显著降低（逐字推送替代整段返回）、与主流 Agent 框架（OpenAI Assistants / LangGraph / Vercel AI SDK）"全程 stream"风格对齐，同时减少 `_iter_rounds` 对底层模型调用方式的耦合面。

#### Acceptance Criteria

1. THE `ReAct_Agent_Adapter._iter_rounds` SHALL NOT 调用 `model_access.chat(...)`；本方法在循环体内 SHALL 仅通过 `model_access.stream(...)` 与底层模型交互。
2. WHEN `_iter_rounds` 处于循环体内某轮, THE `ReAct_Agent_Adapter` SHALL 通过 `Mid_Round_Stream_Aggregation` 把该轮 `model_access.stream(req)` 产出的所有 `StreamingChunk` 完整消费并在内部累积成等价的 `LLMResponse`（字段：`content` 由所有非 `finished` 分片的 `delta_content` 顺序拼接得到；`tool_calls` 由所有分片的 `tool_calls` 按 OpenAI SDK `index` 语义合并；`usage` 取 `finished=True` 分片的 `usage`，缺失视为 `{}`；`model` 取 `chat_request.model` 或回退到 `config.model`；`latency_ms` 取从发起 `stream()` 到 `finished=True` 分片到达的 monotonic 毫秒差），再驱动既有的 `tool_calls` / `text` / `approval` 分支判断。
3. THE `Mid_Round_Stream_Aggregation` SHALL NOT 在中间轮次累积期间向上层产出任何 `StreamingChunk` 或 `AgentStreamEvent`；`run_streaming` / `run_events` 在中间轮次对外的事件时序（heartbeat、tool_progress、status、tool_start、tool_result/tool_error 等）SHALL 与 v2 字面一致。
4. THE `ReAct_Agent_Adapter._stream_final_round` 与 `ReAct_Agent_Adapter._stream_events_final_round` SHALL 保留 v2 接口与产出语义不变；这两个方法本来就基于 `stream()`，本期不修改其内部 `stream()` 调用方式。
5. WHEN 重构落地后, FOR ALL `epsilon-boot/src/infrastructure/agent/` 下的生产代码, THE `ReAct_Internal_Chat_Zero_Reference` SHALL 成立：`grep -rn 'model_access\.chat(' epsilon-boot/src/infrastructure/agent/` 零命中。
6. THE 重构 SHALL NOT 删除或修改 `domain/model_access/ports.py:ModelAccessPort.chat(...)` 端口签名；该端口 SHALL 保留以兼容 `Chat_Service_Adapter` 无工具路径与 `LLMSummaryCompactionAdapter` 的使用。
7. WHEN `_iter_rounds` 中某轮 `model_access.stream(...)` 抛出异常（包括 `ModelTimeoutError` / `ModelRateLimitError` / `ModelAccessError`）, THE `_iter_rounds` SHALL 让异常透传给入口的 `async for` 循环，与 v2 透传语义一致；不在生成器内部捕获或转换。
8. THE 重构 SHALL 保留 v2 已落定的 NFR-1 模型调用次数语义（仅替换调用类型）：`run` 在 `config.max_rounds == N` 时由 N 次 `chat()` 改为 N 次 `stream()`；`run_streaming` / `run_events` 由 N-1 次 `chat()` + 1 次 `stream()` 改为 N 次 `stream()`（中间 N-1 次 + 最后一轮 1 次）。
9. THE 重构 SHALL NOT 改变 `AgentResult` / `RoundOutcome` 的对外字段集合；中间轮次累积只是 `_iter_rounds` 的实现细节。
10. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充覆盖测试：(a) "第 1 轮即返回 text 终止"场景下 `model_access.chat` mock 被调用 0 次、`model_access.stream` 恰好被调用 1 次（验证全程 stream 接管，且 text 路径立即终止符合 NFR-3 的术语精确性约束——不存在"中间轮次纯文本"组合）；(b) "中间轮次 tool_calls 累积"场景（`max_rounds=N` 且每轮均返回 tool_calls 直至 `max_rounds` 命中或最后一轮文本终止）下，`Mid_Round_Stream_Aggregation` 累积出的 `LLMResponse.tool_calls` 与"等价 chat() 一次返回"的 tool_calls 列表语义相等（`id` / `name` / `arguments` 全等），且 `model_access.chat` mock 被调用 0 次、`model_access.stream` 被调用 N 次；(c) 累积后 `LLMResponse.content` 与所有 `delta_content` 拼接结果相等；(d) `usage` 等于 `finished=True` 分片的 usage；(e) 中间轮次期间不产出对外 `StreamingChunk` / `AgentStreamEvent`。

### 需求 2：`Streaming_Chunk_Tool_Calls_Field` 与 `Tool_Arguments_Delta_Event` 落地

**用户故事：** 作为前端工程师与最终用户，我希望最后一轮模型生成工具调用时也能像 typewriter 一样按字符增量看到工具入参，而不是等整段 `arguments` JSON 一次性弹出，以便长入参（大段 SQL、长 prompt）的等待体验改善。

#### Acceptance Criteria

1. THE `domain/model_access/value_objects.py:StreamingChunk` SHALL 在末尾追加可选字段 `tool_calls: list[<工具调用增量值对象>] | None = None`，默认 `None`，语义契约见术语表 `Streaming_Chunk_Tool_Calls_Field`；**字段元素的具体值对象类型由 design 阶段决定**（详见"调研 3"），requirement 层不绑定具体类型；既有字段（`delta_content` / `finished` / `usage` / `metadata`）类型与默认值不变；`frozen=True` 不变。
2. THE `infrastructure/model_access/openai_compatible_adapter.py:OpenAICompatibleAdapter.stream` SHALL 解析 OpenAI Python SDK 流式分片中 `chunk.choices[0].delta.tool_calls` 字段，并按 `tool_calls[i].index` 语义将多分片的 `function.name` / `function.arguments` 增量在 adapter 内部状态机中重组；当 SDK 流中观察到工具调用分片时，将"该分片新观察到的工具调用列表"或"参数增量列表"写入产出的 `StreamingChunk.tool_calls`（具体规约由 design 阶段决定，但需求层面约束"非 `None` 即代表本分片携带 tool_calls 相关数据"）。
3. THE `OpenAICompatibleAdapter.stream` SHALL 在 `finished=True` 分片产出时（已观察到工具调用结束）把累积完整的工具调用列表一次性写入 `StreamingChunk.tool_calls`，每个元素的"完整 arguments"语义须可由下游消费者无歧义获取（无论是直接以"完整 JSON 字符串"形式呈现，还是以"`arguments_delta` 累计可拼接为完整 JSON"形式呈现，由 design 阶段在所选值对象上规约），保证下游消费者即使丢弃中间增量分片，也能从 `finished=True` 分片**语义等价**地重组出与 `LLMResponse.tool_calls`（类型 `list[ToolCallRequest]`，本期不变）一致的工具调用列表（按 `(id, name, arguments)` 三元组逐一相等）。具体重组规则与 `StreamingChunk.tool_calls` 字段元素的值对象类型由 design 决定，requirement 层不绑定。
4. THE `domain/agent/value_objects.py:AgentStreamEventKind` SHALL 在 `Literal[...]` 取值集合末尾追加 `"tool_arguments_delta"`；既有取值（`status` / `assistant_delta` / `assistant_done` / `tool_start` / `tool_result` / `tool_error` / `approval_required` / `error`）保持不变。
5. WHEN `ReAct_Agent_Adapter._stream_events_final_round` 在最后一轮 `model_access.stream(...)` 中观察到包含 `tool_calls` 的 `StreamingChunk` 且 `chunk.finished == False`, THE 方法 SHALL 按分片产出 `AgentStreamEvent(kind="tool_arguments_delta", tool_call_id=..., tool_name=..., arguments=<本分片新增 arguments 增量字符串>, metadata={"round": round_num})` 事件；同一 `tool_call_id` 的多个分片 SHALL 严格按 SDK 产出顺序。
6. WHEN `ReAct_Agent_Adapter._stream_events_final_round` 在最后一轮 `chunk.finished == True` 且分片携带完整 `tool_calls` 列表, THE 方法 SHALL 在 `assistant_done` 之前为每个完成的工具调用产出一条 `AgentStreamEvent(kind="tool_start", tool_name=..., tool_call_id=..., arguments=<完整 arguments JSON>, metadata={"round": round_num})`；当且仅当 SDK 在 `finished=True` 之前已通过 `tool_arguments_delta` 流出全部增量时，可以省略 `tool_start` 中重复的 arguments（具体由 design 决定）。
7. THE `AgentStreamEvent.kind="tool_arguments_delta"` 事件 SHALL 不携带 `usage` 字段（保持 `None`），不携带 `content` 字段（保持空字符串）。
8. THE `_stream_final_round`（用于 `run_streaming` 的文本流式入口）SHALL NOT 改变现有 `StreamingChunk` 产出协议——可以选择：(a) 完全忽略 `chunk.tool_calls`（与 v2 行为一致，最后一轮工具调用通过 `finished=True` 分片中的 `tool_calls` 一次性透传给前端）；或 (b) 透传 `chunk.tool_calls` 到产出的 `StreamingChunk.tool_calls`。需求层面强制 `_stream_final_round` 至少满足 (a)，是否启用 (b) 由 design 决定。
9. THE 重构 SHALL 在 `test/infrastructure/model_access/` 下补充 `OpenAICompatibleAdapter.stream` 的工具调用增量测试：mock OpenAI SDK 流式响应分多个分片返回 `delta.tool_calls`（典型分片：第 1 片 `{"index": 0, "id": "...", "type": "function", "function": {"name": "..."}}`；第 2-N 片 `{"index": 0, "function": {"arguments": "{\"k1\""}}` / `{"index": 0, "function": {"arguments": "\":1}"}}`；最后一片 `finish_reason="tool_calls"`），验证 adapter 产出的 `StreamingChunk.tool_calls` 在 `finished=True` 分片携带的累积完整列表，可按 `(id, name, arguments)` 三元组语义等价地重组出与"等价 chat() 一次返回的 `LLMResponse.tool_calls`"相同的工具调用列表（具体断言形式与字段比较细节由 design 规约决定）。
10. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充 `_stream_events_final_round` 的 `tool_arguments_delta` 测试：mock 一条多分片 stream，断言 (a) 收到 ≥1 条 `tool_arguments_delta` 事件；(b) 各 `tool_arguments_delta.arguments` 顺序拼接结果等于完整 `arguments` JSON 字符串；(c) 末尾仍产出 `assistant_done` 事件；(d) `tool_start` 事件按 design 决定的策略产出（要么在每条 tool_call 完整时产出一条，要么省略，二选一保持自洽）。

### 需求 3：`Tool_Timeout_Global` 与 `Tool_Timeout_Per_Tool` 治理

**用户故事：** 作为运维工程师与 `ReAct_Agent_Adapter` 的维护者，我希望任何单个工具卡死都不会让整个 Agent Loop 阻塞——既有全局默认超时兜底（让所有工具调用都受治理），也允许单个高风险/高耗时工具按需 override（如长时间 SQL 查询工具）。

#### Acceptance Criteria

1. THE `domain/agent/value_objects.py:AgentConfig` SHALL 在末尾追加可选字段 `tool_timeout_seconds: float | None = None`；默认 `None` 表示**不**启用工具超时（与 v2 行为一致）；`__post_init__` SHALL 在该字段非 `None` 时校验 `tool_timeout_seconds > 0`，否则抛出 `ValueError("tool_timeout_seconds 必须大于 0")`。`AgentConfig` `frozen=True` 与 `kw_only=True` 不变。
2. THE `domain/agent/tools.py:Tool` 抽象基类 SHALL 新增可选属性 `timeout_seconds: float | None`，作为 `@property` 默认实现 `return None`（子类可 override 也可不 override，未 override 时视为继承全局值）；新属性附中文 docstring 说明"工具级超时阈值，None 表示沿用 `AgentConfig.tool_timeout_seconds`；非 None 时优先于全局值"。
3. THE `ReAct_Agent_Adapter._execute_tool_call` SHALL 在调用 `_tool_registry.execute(tool_call)` 前解析有效超时阈值：(a) 通过 `_tool_registry.get(tool_call.name)` 获取工具实例，读取 `tool.timeout_seconds`；(b) 若 `tool.timeout_seconds` 非 `None` 则用工具级值；(c) 否则用 `config.tool_timeout_seconds`；(d) 若两者均为 `None` 则不启用超时（与 v2 行为一致，直接 `await _tool_registry.execute(...)`）。
4. WHEN 有效超时阈值非 `None`, THE `ReAct_Agent_Adapter._execute_tool_call` SHALL 通过 `await asyncio.wait_for(self._tool_registry.execute(tool_call), timeout=<有效阈值>)` 包裹工具执行调用。
5. WHEN 工具执行超过有效超时阈值（捕获到 `asyncio.TimeoutError`）, THE `ReAct_Agent_Adapter._execute_tool_call` SHALL 满足 `Tool_Timeout_Failure_Semantics`：(a) `is_error=True`；(b) `ToolMessage.metadata` 写入 `error=True`（与 v2 工具失败 `Tool_Failure_Metadata_Flag` 一致）；(c) `ToolMessage.content` 为中文 `f"工具执行超时（{timeout_seconds}s）"`，其中 `timeout_seconds` 为实际生效的超时秒数；(d) 通过 `_log_tool_failure(tool_call, exc, "timeout")` 输出 warning，`reason="timeout"`；(e) 返回值 `(content, True)`。
6. WHEN 工具执行抛出非 `asyncio.TimeoutError` 的异常（含 `ToolPermissionDeniedError` 与运行期 `Exception`）, THE `ReAct_Agent_Adapter._execute_tool_call` SHALL 保持 v2 既有失败语义（已通过 `_log_tool_failure` 与 `metadata["error"] = True` 处理）。
7. THE `Tool_Timeout_Failure_Semantics` SHALL NOT 触发 `ApprovalInterrupt`；超时仅作为工具失败回灌给 LLM，由模型据此自我决策（继续调用 / 改用其他工具 / 给最终回复）。
8. WHEN `run_events` 入口在最后一轮 `_stream_events_final_round` 之外（即在中间轮次工具执行）触发超时, THE `run_events` SHALL 产出 `kind="tool_error"` 事件（由 `is_error=True` 路径选择），`content` 为中文超时信息，`metadata` 含 `round`；不产出独立的 `tool_timeout` 事件 kind。
9. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充测试：(a) `tool_timeout_seconds=None` 且 `tool.timeout_seconds=None` 时不启用 `wait_for`，慢工具正常完成；(b) 全局超时 `tool_timeout_seconds=0.1` + 慢工具 `await asyncio.sleep(1.0)` → 触发 `TimeoutError` → `is_error=True` + `ToolMessage.metadata["error"]==True` + `ToolMessage.content == "工具执行超时（0.1s）"` + `_log_tool_failure` warning `reason="timeout"`；(c) per-tool override：全局 5.0 / 工具 0.1 / 工具 sleep 1.0 → 触发超时（用工具级值）；(d) per-tool override：全局 0.1 / 工具 5.0 / 工具 sleep 1.0 → 不超时（工具级值优先）；(e) 超时不触发 `ApprovalInterrupt`。
10. THE 重构 SHALL NOT 改变 `Tool` 既有抽象方法（`name` / `description` / `parameters` / `execute`）签名；`timeout_seconds` 是新增的可选 `@property`，不破坏现有具体工具实现。

### 需求 4：`Token_Budget_Limit` 与 `Token_Budget_Exceeded_Reason` 治理

**用户故事：** 作为运维工程师与产品经理，我希望对 Agent 单次执行的累计 token 用量有一个明确的硬上限——一旦超出预算就让 Agent 优雅停止并把超限信号交给调用方，避免长跑 Agent 在意外情况下产生不可控的高额推理成本。

#### Acceptance Criteria

1. THE `domain/agent/value_objects.py:AgentConfig` SHALL 在末尾追加可选字段 `max_total_tokens: int | None = None`；默认 `None` 表示**不**启用 token 预算（与 v2 行为一致）；`__post_init__` SHALL 在该字段非 `None` 时校验 `max_total_tokens > 0`，否则抛出 `ValueError("max_total_tokens 必须大于 0")`。`AgentConfig` `frozen=True` 与 `kw_only=True` 不变。
2. THE `domain/agent/value_objects.py:AgentTerminationReason` SHALL 由 `Literal["completed", "max_rounds"]` 扩展为 `Literal["completed", "max_rounds", "token_budget_exceeded"]`；新增取值 `"token_budget_exceeded"` 附中文 docstring 说明"循环达到 `config.max_total_tokens` 上限时本轮结束后立即终止，不再发起更多模型调用；调用方应据此决策是否升档预算续跑或告知用户"。
3. WHEN `config.max_total_tokens` 非 `None`, THE `_iter_rounds` SHALL 在每轮 `merge_usage(total_usage, builder_result.usage, response.usage)` 完成后立即按 `Token_Budget_Computation_Rule` 评估累计 token 用量；当评估结果 `> config.max_total_tokens` 即视为超限。
4. WHEN `_iter_rounds` 在某轮 `merge_usage` 后检测到累计 token 超限, THE `_iter_rounds` SHALL 完成本轮所有副作用（`tool_calls` 路径下：把 AssistantMessage 写回 context、yield `tool_calls` outcome 让 caller 执行工具并回写 ToolMessage 后再终止；`text` 路径下：直接终止，无需额外副作用），然后产出 `RoundOutcome(kind="final", round_num=本轮号, response=last_response, total_usage=..., terminated_reason="token_budget_exceeded")` 并 return；**不**进入下一轮模型调用。
5. WHEN `_iter_rounds` 即将产出 `terminated_reason="token_budget_exceeded"` 的 `RoundOutcome` 时, THE `ReAct_Agent_Adapter` SHALL 输出一条 `Token_Budget_Exceeded_Warning` 日志（`logger.warning("Agent Loop 累计 token 超过 max_total_tokens 预算", extra={...})`），`extra` 至少包含 `round_num`、`accumulated_total_tokens`、`max_total_tokens`；不记录 `tool_call.arguments` / `delta_content` 全文。
6. WHEN 预算检查发生在中间轮次 tool_calls 路径, THE `_iter_rounds` SHALL 选择"先 yield tool_calls outcome 让 caller 执行工具回写 ToolMessage，再在外层 generator 触发终止"——具体语义：在该轮工具执行回写后，下一次 `_iter_rounds.__anext__` 不再进入新一轮 chat，而是直接 yield `terminated_reason="token_budget_exceeded"` 的 `final` outcome 并 return。设计阶段可选择"在 yield tool_calls 时同时记录预算超限标记，下次循环开头检查"或"在 yield tool_calls 后通过 `total_usage.update(...)` 检查"任一实现，但需求层面约束："超限检查发生在工具执行后，让 caller 完整看到本轮工具结果"。
7. WHEN 预算检查发生在中间轮次 text 路径（模型直接给出最终回复且本轮 usage 把累计推过预算）, THE `_iter_rounds` SHALL 优先按 `text` 自然终止路径产出 `RoundOutcome(kind="text", terminated_reason="completed", ...)` 而**不**改写为 `"token_budget_exceeded"`——因为 `text` 路径已得到模型最终回复，无需以"超限"信号替代"已完成"信号。需求层面约束：`token_budget_exceeded` 只在"超限发生时模型仍未给出最终文本回复"的语义下命中。
8. WHEN `config.max_total_tokens` 非 `None` 且 `_iter_rounds` 在中间轮次命中 `Token_Budget_Limit` 而 `Max_Rounds_Termination` 也理论上可达, THE `_iter_rounds` SHALL 优先按 `terminated_reason="token_budget_exceeded"` 产出（token 预算检查发生在每轮 `merge_usage` 之后即时触发，而 `max_rounds` 是循环耗尽语义；实际上预算超限会在轮次耗尽前触发，因此两者天然互斥）。`Token_Budget_Exceeded_Warning` 与 `Max_Rounds_Termination_Warning` 在同一次执行中 SHALL 不同时出现。
9. THE `infrastructure/agent/round_outcome.py:RoundOutcome.terminated_reason` 与 `domain/agent/value_objects.py:AgentResult.terminated_reason` 字段类型 SHALL 同步随 `AgentTerminationReason` 扩展为 3 取值；既有"末尾追加可选字段"形式的字段定义不变。
10. WHEN `ReActAgentAdapter.run` / `ReActAgentAdapter.run_streaming` / `ReActAgentAdapter.run_events` / `ReActAgentAdapter.resume` 消费到 `outcome.terminated_reason == "token_budget_exceeded"` 的 `RoundOutcome`, THE 入口 SHALL 把该 reason 透传到 `AgentResult.terminated_reason` / `StreamingChunk.metadata["terminated_reason"]` / `AgentStreamEvent.metadata["terminated_reason"]`；流式入口在该分支 SHALL 跳过 `_stream_*_final_round`，与 v2 `max_rounds` 命中处理对称（不再发起最后一轮 `model_access.stream(...)`）。
11. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充 `run` / `run_streaming` / `run_events` 三入口在 `Token_Budget_Limit` 命中场景的覆盖测试：(a) `run`：mock `model_access.stream` 让第 1 轮返回 tool_calls + usage 已超出 `max_total_tokens` → 工具被执行 + `AgentResult.terminated_reason == "token_budget_exceeded"` + `Token_Budget_Exceeded_Warning` warning 仅 1 条；(b) `run_streaming`：超限分支跳过 `_stream_final_round`，最后一个 `StreamingChunk.metadata["terminated_reason"] == "token_budget_exceeded"`；(c) `run_events`：超限分支最后一个事件为 `kind="assistant_done"` 且 `metadata["terminated_reason"] == "token_budget_exceeded"`；(d) 边界：text 路径下即使最后一轮 usage 把累计推过预算，仍按 `terminated_reason="completed"`（NFR-5 HITL 兼容性下决策 4-7）；(e) 边界：`max_total_tokens=None` 时禁用预算检查，行为与 v2 一致；(f) `max_total_tokens` 与 `max_rounds` 共存时优先 `token_budget_exceeded`（互斥校验）。

### 需求 5：`Terminal_Round_Boundary_Assert` 强化循环耗尽不变量

**用户故事：** 作为 `ReAct_Agent_Adapter` 的维护者，我希望 `_iter_rounds` 循环耗尽分支的不变量（"最后一轮一定是 tool_calls 且 context 末尾是 ToolMessage"）通过 `assert` 显式表达，而不是用一个静默回退到 `terminated_reason="completed"` 的兜底分支隐藏起来，以便后续 reviewer 阅读时能立刻确认该路径的所有可能 outcome。

#### Acceptance Criteria

1. THE `_iter_rounds` 循环耗尽分支 SHALL 删除 v2 残留的"non-pending tool_calls 静默回退到 `terminated_reason='completed'`"兜底（即 `react_agent_adapter.py` 第 545-552 行的 `# 其他循环耗尽分支：保持 completed` 那一段产出）。
2. THE `_iter_rounds` 循环耗尽分支 SHALL 保留 `last_response is None` 的极端边界判断（`terminal_round=0` 等数学边界路径），并在该分支直接 `return`（不产出 outcome）；附中文注释说明该分支仅在数学边界可达。
3. WHEN `last_response is not None`, THE `_iter_rounds` SHALL 在循环耗尽分支用 `assert bool(last_response.tool_calls) and bool(messages) and isinstance(messages[-1], ToolMessage), "<中文断言失败信息>"` 强制约束最后一轮一定是 tool_calls 且工具已被外层执行回写。
4. THE `Terminal_Round_Boundary_Assert` 配套中文注释 SHALL 至少说明：(a) 自然终止路径（`text` / `approval`）已在循环体内 `yield ... return`，不会进入循环耗尽分支；(b) 唯一可达本分支的情形是"`max_rounds` 命中且最后一轮 tool_calls"；(c) 其他组合（如最后一轮 tool_calls 但 context 末尾不是 ToolMessage）只在 `terminal_round=0` 或调用方未正确执行工具回写等数学边界可达，本期通过 assert 让该不变量显式化。
5. WHEN `Terminal_Round_Boundary_Assert` 失败, THE `_iter_rounds` SHALL 抛出 `AssertionError`（透传给入口的 `async for` 循环）；不静默吞掉。
6. THE `_iter_rounds` SHALL 在 assert 通过后产出 `RoundOutcome(kind="final", round_num=effective_terminal, response=last_response, total_usage=dict(total_usage), terminated_reason="max_rounds")` 并记录 `Max_Rounds_Termination_Warning`（与 v2 既有行为一致）。
7. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充 assert 行为测试：(a) `terminal_round=0` 边界（`run_streaming` / `run_events` 设置 `terminal_round=config.max_rounds - 1` 且 `max_rounds=1` 实际不进入 `_iter_rounds` 主循环）→ 验证 `last_response is None` 分支直接 return，不抛 assert；(b) 正常 `max_rounds` 命中场景（最后一轮 tool_calls + caller 已执行工具）→ assert 通过 + 产出 `terminated_reason="max_rounds"`；(c) **故意构造**"最后一轮 tool_calls 但 caller 不执行工具回写"的人工测试场景（直接绕过 `_execute_tool_call`） → assert 抛 `AssertionError`，验证不变量被强制表达。
8. THE 重构 SHALL NOT 改变正常路径下 v2 已有的 `max_rounds` 命中行为（仅删除一个不可达兜底分支 + 新增 1 个 assert）。

## Non-Functional Requirements

### NFR-1 模型调用次数语义（全程 stream 后无回灌）

- THE `ReActAgentAdapter.run` SHALL 在 `config.max_rounds == N` 时由 v2 的 N 次 `chat()` 改为 N 次 `stream()`（中间轮次每轮 1 次 + 最后一轮 1 次）；当 `max_rounds` 命中且最后一轮 tool_calls 时**不**追加额外 `stream()`，仅通过 `AgentResult.terminated_reason="max_rounds"` 暴露超限信号（与 v2 一致）。
- THE `ReActAgentAdapter.run_streaming` SHALL 在 `config.max_rounds == N` 时由 v2 的 N-1 次 `chat()` + 1 次 `stream()` 改为 N 次 `stream()`（中间 N-1 次 + 最后一轮 1 次）；`max_rounds == 1` 时仍仅 1 次 `stream()`；`max_rounds` 命中时跳过最后一轮 stream，与 v2 对称。
- THE `ReActAgentAdapter.run_events` SHALL 与 `run_streaming` 相同的 stream 调用次数。
- THE 全程 stream 升级 SHALL NOT 引入任何"recovery chat / recovery stream"——超限信号仍通过 `terminated_reason` 暴露（v2 共识方案延续）。
- THE `Token_Budget_Limit` 命中时, THE 入口 SHALL 跳过最后一轮 `_stream_*_final_round`，与 `max_rounds` 命中分支对称：`run_streaming` / `run_events` 不发起最后一轮 stream；`run` / `resume` 不进入下一轮 stream。

### NFR-2 不变量保持（仅末尾追加可选字段）

- THE `AgentResult` 字段集合 SHALL 仅以"末尾追加可选字段"形式扩展（本期无新增）；既有字段类型与默认值不变；`frozen=True` 不变。
- THE `RoundOutcome` 字段集合 SHALL 仅以"末尾追加可选字段"形式扩展（本期无新增字段，但 `terminated_reason` 字段类型随 `AgentTerminationReason` 同步扩展）。
- THE `StreamingChunk` 字段集合 SHALL 仅以"末尾追加可选字段"形式扩展：新增 `tool_calls: list[<工具调用增量值对象>] | None = None`（字段元素值对象类型由 design 阶段决定，详见"调研 3"，requirement 层不绑定）；既有字段类型与默认值不变；`frozen=True` 不变。
- THE `AgentStreamEventKind` 取值集合 SHALL 仅扩展（追加 `"tool_arguments_delta"`），不删除既有取值；`AgentStreamEvent` 字段集合不变。
- THE `AgentConfig` 字段集合 SHALL 仅以"末尾追加可选字段"形式扩展：新增 `tool_timeout_seconds: float | None = None` 与 `max_total_tokens: int | None = None`；既有字段（`system_prompt` / `tool_schemas` / `model` / `max_rounds` / `prompt_id` / `allowed_tool_names`）类型与默认值不变；`frozen=True` 与 `kw_only=True` 不变。
- THE `AgentTerminationReason` 类型 SHALL 由 `Literal["completed", "max_rounds"]` 扩展为 `Literal["completed", "max_rounds", "token_budget_exceeded"]`；既有取值不变。
- THE `Tool` 抽象基类 SHALL 仅以"新增可选 `@property`"形式扩展：新增 `timeout_seconds: float | None`，默认 `return None`；既有抽象方法 `name` / `description` / `parameters` / `execute` 签名不变；既有具体工具子类无需修改即可继续工作。
- THE `ConversationContext` 字段集合 SHALL 不变（v3 不修改领域上下文）。

### NFR-3 现有测试与新增测试

- THE 现有 1480 测试 SHALL 全部继续通过；除非该测试覆盖的是被删除的 ReAct 内部 `chat()` 路径——此时 SHALL 在 PR 内同步把 mock 由 `model_access.chat` 改为 `model_access.stream`（语义等价改写）。
- THE 新增测试 SHALL 至少覆盖以下 5 类场景：
  - 流式整段聚合等价性（`Mid_Round_Stream_Aggregation` 累积出的 `LLMResponse` 与等价 chat() 一次返回的语义相等）；
  - `tool_arguments_delta` 事件按分片产出且顺序拼接等于完整 arguments；
  - 工具超时返回值与失败标记一致性（global / per-tool / 二者兼有 / 二者均缺失）；
  - token 预算命中（命中时停止下一轮 stream、warning 仅 1 条、`terminated_reason` 透传到三入口）；
  - `Terminal_Round_Boundary_Assert` 在正常路径与人为构造路径下分别通过/失败。
- THE 测试场景命名 SHALL 避免自相矛盾的术语；具体而言："中间轮次纯文本"在语义上不可达——纯文本响应在任何轮次（包括第 1 轮）出现时都会立即被 `_iter_rounds` 的 `text` 分支识别并 `yield text/return`，因此不存在"纯文本但还要继续到下一轮"的中间轮次。涉及多轮场景的测试名称 SHALL 改用如下二选一的精确表达：(a) "中间轮次 tool_calls 累积"——多轮 tool_calls 链路下的中间轮次累积测试；(b) "第 1 轮即返回 text 终止"——第 1 轮纯文本即结束的最短路径测试。设计文档与测试矩阵不得使用"max_rounds=N（N≥2）+ 中间轮次纯文本"这种自相矛盾的组合作为单一测试场景。

### NFR-4 日志规范

- WHEN 工具执行超时, THE `_log_tool_failure` SHALL 输出 warning，`reason="timeout"`，字段集合与 v2 工具失败一致（含 `tool_name` / `tool_call_id` / `reason` / `exc_type=TimeoutError` / `exc_msg=str(exc)`）；不记录 `tool_call.arguments` 完整文本。
- WHEN 触发 `Token_Budget_Exceeded_Warning`, THE `ReAct_Agent_Adapter` SHALL 输出 `logger.warning("Agent Loop 累计 token 超过 max_total_tokens 预算", extra={...})`，`extra` 至少包含 `round_num`、`accumulated_total_tokens`、`max_total_tokens`；不记录 `tool_call.arguments` 完整文本与 `delta_content` 全文。
- THE `Token_Budget_Exceeded_Warning` 与 `Max_Rounds_Termination_Warning` SHALL 通过模块级 `logger` 完成，不得 `print`。
- THE v2 已落地的 `_log_tool_failure` warning 行为 SHALL NOT 因本期重构而被降级或字段缩减。

### NFR-5 HITL 与 v2 共存兼容

- WHEN `_execute_tool_call` 因超时返回 `(content, True)`, THE `ReAct_Agent_Adapter` SHALL NOT 触发 `ApprovalInterrupt`；超时仅作为工具失败回灌，与 v2 工具失败语义一致。
- WHEN `_iter_rounds` 在中间轮次某轮命中 `Token_Budget_Limit` 但该轮 `_collect_pending_actions` 收集到非空待审批动作（即 `kind="approval"`）, THE `_iter_rounds` SHALL 优先按 v2 `approval` 路径产出 `RoundOutcome(kind="approval", ...)`（HITL 中断不属于"超限"，由 `AgentResult.status="approval_required"` 单独表达），不改写为 `terminated_reason="token_budget_exceeded"`。具体口径：超限检查发生在工具执行**之后**；当本轮命中 `approval` 时尚未执行工具，循环已通过 `yield approval; return` 退出，预算检查不参与。
- THE HITL resume 路径（`ReActAgentAdapter.resume`）SHALL 复用 v3 全程 stream 主路径；`resume` 后续轮次也走 `Stream_Only_Path`，与 v2 一致地透传 `terminated_reason`。
- THE `ApprovalInterrupt.context_snapshot` 与 `event_timestamps` 序列化语义 SHALL 与 v2 完全一致，本期不修改。

### NFR-6 静态扫描清单

- WHEN PR 完成后, THE `grep -rn 'model_access\.chat(' epsilon-boot/src/infrastructure/agent/` SHALL 在生产代码中零结果（`ReAct_Internal_Chat_Zero_Reference`）。
- WHEN PR 完成后, THE `grep -rn 'await\s\+model_access\.chat(' epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` SHALL 零结果。
- WHEN PR 完成后, THE `chat_service_adapter.py:238` 与 `llm_summary_compaction_adapter.py:66` 处的 `model_access.chat(...)` 调用 SHALL 保留（不在本期扫描清单的零命中目标内）。
- WHEN PR 完成后, THE `grep -rn 'last_response\.tool_calls' epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 在循环耗尽分支应仅出现在 `assert` 表达式内，不应再出现"if/else 分支判断"用法。

### NFR-7 性能口径

- WHEN 模型在最后一轮返回纯文本回复（即 `text` 路径经由 `_stream_final_round` / `_stream_events_final_round`）, THE 用户感知 token-to-first-byte SHALL **不劣于** v2（说明：v3 中"中间轮次返回纯文本"在语义上不可达——纯文本响应在任一轮次出现都会立即触发 `text` 终止路径；因此 v3 的逐字推送收益主要体现在最后一轮 text 路径上，由整段返回 → 逐字推送，体感延迟显著降低；本期不做硬性数值 SLA）。
- WHEN 最后一轮模型生成工具调用且 SDK 提供 `delta.tool_calls` 增量, THE `run_events` 客户端 SHALL 收到 ≥1 条 `tool_arguments_delta` 事件，使前端能实现 typewriter 风格的 arguments 展示。
- THE `Mid_Round_Stream_Aggregation` SHALL NOT 引入额外的网络往返（中间轮次仍是 1 次 stream HTTP 请求 + 1 次内部累积，与 v2 1 次 chat HTTP 请求性能等价）。

## Out of Scope

1. 不实现 per-tool retry / circuit breaker / tool-level rate limit；超时只做"中断 + 标记失败 + 回灌 LLM"，不自动重试。
2. 不引入 cost / 计费预算；`max_total_tokens` 仅按 token 数量限制，不按金额限制（按用户决策推迟）。
3. 不引入 Pydantic AI 风格的 `request_limit`（请求次数预算）/ `response_tokens_limit`（响应 token 预算）等多维预算（按用户决策推迟）。
4. 不实现自主续跑 / 自动恢复（与 v2 一致，`terminated_reason` 仅作为信号暴露给调用方，不在 Agent 内部续跑）。
5. 不升级 `tool_use` 顶层（非 ReAct）流式协议；`tool_use` 体系本期不在范围内，未来可作为独立 spec。
6. 不修改 `ModelAccessPort.chat()` 端口签名；不删除 `Chat_Service_Adapter` 无工具路径与 `LLMSummaryCompactionAdapter` 对该端口的依赖；`Chat_Service_Adapter` 自身的"无工具路径升级为流式"留待后续 spec。
7. 不修改前端 `epsilon-client/` 代码；前端将隐式获得"末轮纯文本逐字推送"（v2 `run` 入口最后一轮 text 整段返回 → v3 逐字推送）与"最后一轮工具调用 typewriter 增量"两项收益。
8. 不修改 HITL 审批语义、`allowed_decisions` 校验规则、`ApprovalInterrupt` / `ApprovalRequiredPayload` / `ApprovalDecision` 字段集合。
9. 不修改 `ContextCompactionPort` 实现/开关、模型路由配置、Prompt 注册表机制。
10. 不在本期为 `AgentTerminationReason` 引入 `"completed"` / `"max_rounds"` / `"token_budget_exceeded"` 之外的取值（如 `"context_window_exceeded"` / `"timeout"` / `"user_cancelled"`）；这些属于未来扩展。
11. 不在本期把 v2 已落定的 `Final_Round_Stream_Helper` 抽象重新调整或合并；`_stream_final_round` 与 `_stream_events_final_round` 保留 v2 接口。
12. 不引入 Anthropic 等其他 provider 的 `input_json_delta` 流式工具调用解析；本期决策 2 的 adapter 升级仅覆盖 `OpenAICompatibleAdapter`。
13. 不引入工具实例缓存机制——`_execute_tool_call` 通过 `_tool_registry.get(tool_call.name)` 解析工具实例时按现状每次查表，如有性能优化空间留待未来 spec。
14. 不修改 `Tool.run` 的"JSON 解析 → cast_params → validate_params → execute"流水线；超时仅包裹 `execute` 调用所在的 `_tool_registry.execute(...)` 整体。

## 关键决策记录

本节记录用户在 v3 启动前已锁定的 5 项决策。每项决策与上文功能需求一一对应。

| 决策 | 用户选择 | 对应需求 | 备注 |
| --- | --- | --- | --- |
| 决策 1：高 1 ReAct 内 `chat()` 非真流式（v2 `run` 末轮 text + 中间轮次 tool_calls 均走整段 chat()） | **B：全程 stream + 内部累积，删除 ReAct 内部 chat 调用** | 需求 1 | 与主流方案对齐；保留 `ModelAccessPort.chat()` 端口本身（仍被 `Chat_Service_Adapter` 无工具路径与 `LLMSummaryCompactionAdapter` 复用），仅删除 ReAct 内部 1 处调用。调研结论详见"调研 1"小节。原始问题标签"中间轮次纯文本非真流式"在精确语义下不可达——纯文本响应在任一轮次出现都会立即触发 `text` 终止；v2 的实际症结是 ReAct 内部全程 `chat()` 调用。 |
| 决策 2：中 4 `tool_arguments_delta` | **落地** | 需求 2 | 与决策 1 同步推进；扩展 `StreamingChunk.tool_calls` 字段 + 新增 `AgentStreamEventKind.tool_arguments_delta` 取值 + `OpenAICompatibleAdapter.stream` 透传 OpenAI SDK 的 `delta.tool_calls`。调研结论详见"调研 2"小节（底层 SDK 已支持，仅 adapter 待补）。 |
| 决策 3：中 1 工具 timeout 粒度 | **b：全局 + per-tool override** | 需求 3 | `AgentConfig.tool_timeout_seconds` 全局默认；`Tool.timeout_seconds` 可选属性允许 override；超时视为工具失败 `is_error=True`，`metadata={"error": True}`，content 为中文 `"工具执行超时（{N}s）"`。 |
| 决策 4：中 2 token 预算 | **a：仅 `max_total_tokens`** | 需求 4 | `AgentConfig.max_total_tokens`；每轮 `merge_usage` 后检查累计；超限即在本轮工具执行后产出 `terminated_reason="token_budget_exceeded"`；`AgentTerminationReason` 扩展为 3 取值；不引入 cost / request 次数预算。 |
| 决策 5：中 3 不可达分支 | **b：assert + 注释** | 需求 5 | `_iter_rounds` 循环耗尽分支用 `assert` 强制约束最后一轮一定是 tool_calls + ToolMessage 已写回；删除 v2 残留的 "non-pending tool_calls 静默回退到 completed" 兜底。 |
