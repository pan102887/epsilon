# 需求文档：Agent Quality Assessment 2 — P0 性能/正确性/数据一致性强化

## 简介

### 背景

`epsilon-boot` 后端在 `agent-adapter-refactor-v3`（`commit 9350315`）落地后已在 ReAct Agent 内部完成"全程 stream + token 预算 + 工具超时 + 终止边界 assert"等多项治理，并保留 v2 已有的 HITL 审批语义（`ApprovalInterrupt` / `ApprovalStateStore`）。结合最近一轮针对业界主流 Agent 框架（OpenAI Assistants v2、Anthropic Messages API / Claude Code、LangGraph ToolNode + RedisSaver、Vercel AI SDK 等）的对标评估，识别出 3 项与"业界主流方案在生产环境下的隐式契约"存在偏差的 P0 缺口：

1. **同轮多个 `tool_calls` 串行执行**：`infrastructure/agent/react_agent_adapter.py` 的 `run` / `resume` / `run_streaming` / `run_events` 四个入口在消费 `RoundOutcome.kind == "tool_calls"` 分支时，均使用 `for tool_call in outcome.tool_calls:` + `await self._execute_tool_call(...)` 同步串行处理（命中行：727、835、1052、1147）。OpenAI Assistants v2 / Anthropic Messages API / LangGraph 都假定客户端可以并发执行同轮工具调用，IO 重的工具混合执行时串行处理会显著放大端到端延迟。
2. **滑动窗口压缩不识别 `tool_calls` / `ToolMessage` 配对**：`infrastructure/chat/sliding_window_compaction_adapter.py` 当前实现仅保留所有 system + 最近 N 条非 system 消息，**完全不识别** `AssistantMessage(tool_calls)` 与对应 `ToolMessage(tool_call_id)` 的配对关系。当窗口边界恰好切在二者之间时，OpenAI / Anthropic 服务端会以 400 拒绝请求（`tool_call_id 没有上文 assistant` 或 `assistant 的 tool_calls 没有对应 tool result`）。LangGraph `trim_messages(strategy="last")` 默认即保证此配对，Anthropic SDK 的 message validator 也是强约束。
3. **Redis 会话上下文非原子读改写**：`infrastructure/session/redis_session_context_adapter.py` 的 `save` 直接 `set(key, data, ex=ttl)`，`load → 业务修改 → save` 整体非原子；同一 `session_id` 在并发请求下互相覆盖丢更新。`LocalFileSessionContextAdapter` 已通过文件锁 + 原子替换达到等价语义，而 Redis 后端是裸写。LangGraph `RedisSaver` / OpenAI thread / Cursor checkpointer 均使用 `WATCH/MULTI/EXEC` 乐观锁或 Lua 脚本保护 CAS 周期。

### 动机

- **延迟**：在含 IO 工具组合（HTTP / SQL / Workspace 文件）的同轮 `tool_calls` 中，将串行改为 `asyncio.gather` 可在典型场景下把同轮总耗时由 `Σ tᵢ` 降为 `max(tᵢ)`，预计 2-5x 改善。
- **正确性**：滑动窗口边界配对保护是上线主流商业 LLM 的"硬门槛"，不修复将持续出现 400 错误率，影响连续多轮对话的可用性。
- **数据一致性**：Redis 后端的乐观锁让 `RedisSessionContextAdapter` 与 `LocalFileSessionContextAdapter` 在并发写入语义上对等，避免"切到 Redis 后悄悄丢更新"的运行时缺陷。

### 范围（In Scope）

1. ReAct Agent Loop 四入口同轮 `tool_calls` 由串行改为 `asyncio.gather` 并发执行；保留每个工具的事件配对顺序、HITL 决策应用顺序、工具失败回灌语义。
2. `Sliding_Window_Compaction_Adapter` 在裁剪时识别 `tool_calls` / `ToolMessage` 配对，整组保留或整组丢弃；保持 system 全保留语义。
3. `Redis_Session_Context_Adapter` 引入 CAS 周期保护（`WATCH / MULTI / EXEC` 或 Lua 任选），`SessionContextStorePort` 兼容扩展且 `LocalFileSessionContextAdapter` 同步实现以保持后端对等。
4. 新增的乐观锁配置项（重试次数 / 冲突上抛阈值等）写入 `epsilon-boot/config.properties`。
5. 三项改造同步补齐单测 / property-based 测试，与现有 `test/infrastructure/agent/test_react_agent_*` 与 `test/infrastructure/session/test_local_file_session_context_adapter_unit.py` 风格一致。

### 非目标（Out of Scope）

1. 不引入 MCP 协议、不接入多模态、不引入 Anthropic prompt cache、不实现 plan-execute / multi-agent supervisor 等 P1+ 路线项。
2. 不重写顶层 `ChatServiceAdapter` 无工具路径，不升级 `LLMSummaryCompactionAdapter` 调用模式。
3. 不修改 `ApprovalInterrupt` / `ApprovalDecision` / `ApprovalStateStore` 字段集合或 HITL 序列化语义；并发改造仅在审批语义之上提速。
4. 不引入工具级别的 retry / circuit breaker / rate limit；并发执行只是把 v3 已落定的 `Tool_Timeout_Failure_Semantics` 由串行改为并发。
5. 不修改 `ContextCompactionPort` 端口签名；不耦合 `LLMSummaryCompactionAdapter` 路径——LLM 摘要策略不在本期修复对象内。
6. 不修改 `AgentTerminationReason`、`StreamingChunk`、`AgentStreamEvent` 的字段集合（除事件次序内部约束外，本期不新增字段）。
7. 不修改前端 `epsilon-client/`；前端隐式获得低延迟收益。
8. 不引入新的会话存储后端（如 SQLite / Postgres）；仅在 Redis 与 Local File 两个既有后端上做 CAS 对等。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 同轮并发工具执行 | `Concurrent_Tool_Execution` | `Re_Act_Agent_Adapter` 的 `run` / `resume` / `run_streaming` / `run_events` 四入口在消费 `RoundOutcome.kind == "tool_calls"` 分支时，对该轮 `outcome.tool_calls` 列表内的多个 `Tool_Call_Request` 通过 `asyncio.gather` 并发执行 `_execute_tool_call`，替代既有 `for tool_call in outcome.tool_calls:` + `await` 同步串行循环；并发只在"同轮内多个独立 tool_calls"范围内启用，跨轮仍保持顺序。 |
| 工具事件配对约束 | `Tool_Event_Pair_Adjacency` | 在并发执行同轮多个 `Tool_Call_Request` 时，对外可见的事件流（`run_events` 的 `tool_start` → `tool_result` / `tool_error`，以及 `run_streaming` 的 `tool_progress(start)` → `tool_progress(end)`）必须满足"同一 `tool_call_id` 的起止事件成对相邻"——即不可先把所有 `tool_start` 全部 yield 完，再 yield 所有 `tool_result`。具体实现机制（如以 task 包装并按完成顺序产出，或在每个 task 内部同时持有 start/result 后整组 yield）由 design 阶段决定，requirement 层只规定可观测约束。 |
| 工具调用请求 | `Tool_Call_Request` | `domain/agent/value_objects.py:ToolCallRequest`，包含 `id` / `name` / `arguments` 三元组；`Concurrent_Tool_Execution` 中的并发单元。 |
| HITL 决策应用顺序 | `Hitl_Decision_Application_Order` | `Re_Act_Agent_Adapter._apply_approval_decisions` 必须严格按 `interrupt.actions` 的顺序逐个处理对应 `ApprovalDecision`（`approve` / `edit` / `reject`），不进入 `Concurrent_Tool_Execution`；保留 v3 既有的 `ApprovalDecisionCountMismatchError` / `ApprovalDecisionOrderMismatchError` 校验语义。 |
| 工具失败回灌语义 | `Tool_Failure_Feedback_Semantics` | `Tool_Call_Request` 因鉴权 / 执行异常 / 超时（`Tool_Timeout_Failure_Semantics`）失败时，`_execute_tool_call` 仍返回 `(content, True)` 并把结果作为 `ToolMessage` 写回 `Conversation_Context`，由模型在下一轮自我决策；本期 `Concurrent_Tool_Execution` 不得改变此契约——并发分支下任一 `Tool_Call_Request` 的失败都不得影响同轮其他 `Tool_Call_Request` 的回灌。 |
| ReAct Agent 适配器 | `Re_Act_Agent_Adapter` | `infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter`，需求 1 的主体修改对象。 |
| 滑动窗口压缩适配器 | `Sliding_Window_Compaction_Adapter` | `infrastructure/chat/sliding_window_compaction_adapter.py:SlidingWindowCompactionAdapter`，需求 2 的主体修改对象；实现 `Context_Compaction_Port`。 |
| 上下文压缩端口 | `Context_Compaction_Port` | `domain/chat/ports.py:ContextCompactionPort`，本期不修改端口签名。 |
| Assistant 工具调用消息 | `Assistant_Tool_Calls_Message` | `domain/chat/context.py:AssistantMessage`，当 `tool_calls` 非空时同时携带文本内容（可能为空）与工具调用列表；其每个 `tool_calls[i].id` 须在后续 `Tool_Result_Message` 中以 `tool_call_id` 引用配对。 |
| 工具结果消息 | `Tool_Result_Message` | `domain/chat/context.py:ToolMessage`，以 `tool_call_id` 字段引用对应 `Assistant_Tool_Calls_Message.tool_calls[i].id`；OpenAI / Anthropic API 拒绝缺失上下文 assistant 的 ToolMessage。 |
| 系统消息 | `System_Message` | `role == "system"` 的 `BaseMessage`；`Sliding_Window_Compaction_Adapter` 一律全保留，本期约束不变。 |
| 工具配对完整组 | `Tool_Pair_Group` | 一段连续消息序列：起点为单条 `Assistant_Tool_Calls_Message`，紧随其后是该 assistant `tool_calls` 中**全部** `id` 对应的 `Tool_Result_Message`（顺序不强制 1:1 紧邻，但全集必须出现且不被打断）。`Sliding_Window_Compaction_Adapter` 在窗口裁剪时把 `Tool_Pair_Group` 视为不可分割的整体——要么整组保留，要么整组丢弃。 |
| 配对保护裁剪 | `Pairing_Aware_Trimming` | `Sliding_Window_Compaction_Adapter.compact_messages` 在保留所有 `System_Message` 的前提下，对剩余非 system 消息执行配对感知的滑动窗口裁剪：保证最终保留集合中的每条 `Tool_Result_Message` 都有对应 `Assistant_Tool_Calls_Message`，且每条被保留的 `Assistant_Tool_Calls_Message` 的所有 `tool_calls[i].id` 在保留集合中都有 `Tool_Result_Message`。任一约束不满足即整组 `Tool_Pair_Group` 丢弃。 |
| 半组工具消息禁止穿越 | `No_Half_Tool_Group_Pass_Through` | `Pairing_Aware_Trimming` 在窗口边界遇到不完整 `Tool_Pair_Group` 时（即 assistant 与 tool 拆分在窗口两侧），整组丢弃；不允许"半组"作为最终结果出现在压缩后的消息列表中。该决策可记录 debug log 以便排查，但不向调用方报错。 |
| 会话上下文存储端口 | `Session_Context_Store_Port` | `domain/chat/ports.py:SessionContextStorePort`，本期允许在末尾追加可选方法（如 `load_for_update` / `save_if_unchanged`），但旧方法 `save` / `load` / `delete` 签名不变；新增方法在 `Local_File_Session_Context_Adapter` 与 `Redis_Session_Context_Adapter` 上必须同时实现以保持后端对等。 |
| Redis 会话上下文适配器 | `Redis_Session_Context_Adapter` | `infrastructure/session/redis_session_context_adapter.py:RedisSessionContextAdapter`，需求 3 的主体修改对象。 |
| 本地文件会话上下文适配器 | `Local_File_Session_Context_Adapter` | `infrastructure/session/local_file_session_context_adapter.py:LocalFileSessionContextAdapter`，需求 3 配套修改对象（如 `Session_Context_Store_Port` 新增方法时必须同步实现以保持对等）。 |
| 会话乐观锁周期 | `Session_Optimistic_Lock_Cycle` | 一次完整的 `load → 业务修改 → save` 三步业务周期；要求"在该周期开始时观察到的 Redis key 版本"与"提交 save 时 Redis key 仍为同一版本"原子可比。`Redis_Session_Context_Adapter` 通过 `WATCH/MULTI/EXEC` 或 Lua（CAS-by-version）实现该原子性；该名词不绑定具体技术路径，由 design 阶段决定。 |
| 会话写入冲突 | `Session_Write_Conflict` | 在一次 `Session_Optimistic_Lock_Cycle` 提交时，Redis key 因被其他 writer 写入导致版本不匹配的事件；表现为 `WATCH` 下 `MULTI/EXEC` 返回 `None`，或 Lua CAS 返回失败标记。 |
| 会话冲突重试上限 | `Session_Conflict_Retry_Max` | 一次 `Session_Optimistic_Lock_Cycle` 因 `Session_Write_Conflict` 触发的最大自动重试次数，由配置键 `SESSION_REDIS_CONFLICT_RETRY_MAX`（写入 `epsilon-boot/config.properties`）控制；默认值 ≥ 0，超过该次数仍冲突时由调用方感知（具体为静默回退、抛出特定异常还是 noop 由 design 决定）。 |
| 会话冲突错误 | `Session_Conflict_Error` | 当 `Session_Conflict_Retry_Max` 耗尽仍发生 `Session_Write_Conflict` 时由 `Redis_Session_Context_Adapter` 抛出的领域级异常（具体类名与所属模块由 design 决定，`requirement` 层仅规定语义）；调用方可据此选择上层重试或反馈用户。 |
| 对话上下文 | `Conversation_Context` | `domain/chat/context.py:ConversationContext`，本期不修改字段。 |
| 配置文件 | `Config_Properties` | `epsilon-boot/config.properties`，按 `docs/steering/config-source.md` 约束本期所有新增配置项必须写入此文件，禁止仅放 `.env`。 |

## 需求

### 需求 1：`Concurrent_Tool_Execution` 在四入口同轮 `tool_calls` 启用并发

**用户故事：** 作为 ReAct Agent 的最终用户与 `Re_Act_Agent_Adapter` 维护者，我希望同一轮内的多个 `Tool_Call_Request` 不再串行 `await`，而是通过 `asyncio.gather` 并发执行，以便 IO 重的工具组合（HTTP / SQL / Workspace 读写）的同轮总耗时由 `Σ tᵢ` 降为 `max(tᵢ)`，与 OpenAI Assistants / Anthropic Messages / LangGraph 等主流框架的客户端契约对齐，同时不破坏 HITL、工具失败回灌、事件配对等任一既有语义。

#### 验收标准

1. THE `Re_Act_Agent_Adapter.run` SHALL 把当前 `for tool_call in outcome.tool_calls: await self._execute_tool_call(...)` 串行循环（`react_agent_adapter.py:727`）改为通过 `asyncio.gather` 并发调度同一 `outcome.tool_calls` 中的所有 `Tool_Call_Request`，使该轮总耗时与"耗时最长的单个 `Tool_Call_Request`"同阶。
2. THE `Re_Act_Agent_Adapter.resume` SHALL 在 `_apply_approval_decisions` 完成后续 `_iter_rounds` 推进时，对 `RoundOutcome.kind == "tool_calls"` 分支应用 `Concurrent_Tool_Execution`（`react_agent_adapter.py:835`）；`_apply_approval_decisions` 内部仍 SHALL 严格按 `Hitl_Decision_Application_Order` 串行处理，**不**进入并发。
3. THE `Re_Act_Agent_Adapter.run_streaming` SHALL 在 `RoundOutcome.kind == "tool_calls"` 分支（`react_agent_adapter.py:1052`）应用 `Concurrent_Tool_Execution`，并保持 `Tool_Event_Pair_Adjacency`：对每个 `Tool_Call_Request` 产出的 `tool_progress(start)` 与 `tool_progress(end)` `StreamingChunk` 必须以"成对相邻"形式 yield，不得"先 yield 全部 start，再 yield 全部 end"；可以按完成顺序错峰 yield，但同一 `tool_call_id` 的 start/end 之间禁止穿插其他 `tool_call_id` 的 start/end。
4. THE `Re_Act_Agent_Adapter.run_events` SHALL 在 `RoundOutcome.kind == "tool_calls"` 分支（`react_agent_adapter.py:1147`）应用 `Concurrent_Tool_Execution`，并保持 `Tool_Event_Pair_Adjacency`：对每个 `Tool_Call_Request` 产出的 `tool_start` 与 `tool_result` / `tool_error` `AgentStreamEvent` 必须成对相邻；同一 `tool_call_id` 的起止事件之间禁止穿插其他 `tool_call_id` 的起止事件。
5. THE `Concurrent_Tool_Execution` SHALL 保持 `Tool_Failure_Feedback_Semantics` 不变：(a) 任一 `Tool_Call_Request` 抛出 `ToolPermissionDeniedError` / 运行期 `Exception` / `asyncio.TimeoutError`（`Tool_Timeout_Failure_Semantics`）时，必须以 `(content, True)` 形式回灌 `Tool_Result_Message`，并通过 `_log_tool_failure` 输出 warning（不记录 `arguments` 全文）；(b) 同轮中失败的 `Tool_Call_Request` 不得让其他 `Tool_Call_Request` 提前终止或丢失结果——`asyncio.gather` 的实现 SHALL 选择 `return_exceptions=True` 形态或语义等价的"逐个 task await"形态，由 design 决定。
6. THE `Concurrent_Tool_Execution` SHALL NOT 触发任何与 `Approval_Interrupt` 相关的越权：当 v3 `Hitl_Approval_Mechanism` 已对当前 `outcome.tool_calls` 收集出非空待审批动作时，`_iter_rounds` 已通过 `RoundOutcome.kind == "approval"` 分支提前 return，不会进入 `Concurrent_Tool_Execution` 路径；本需求不得引入"已审批通过的 `Tool_Call_Request` 与 `reject` / `edit` 决策并发交叉"的执行序。
7. THE `Concurrent_Tool_Execution` SHALL 保持对 `Conversation_Context` 写回的最终一致：所有同轮 `Tool_Call_Request` 完成后，`context.get_messages()` 末尾应包含与 `outcome.tool_calls` 等量的 `Tool_Result_Message`，且每条 `tool_call_id` 与 `outcome.tool_calls[i].id` 一一映射；`Conversation_Context.event_timestamps` 序列化语义保持 v3 既有契约。
8. THE `Concurrent_Tool_Execution` SHALL 保持 v3 已落定的 `Tool_Timeout_Failure_Semantics`：每个并发 `Tool_Call_Request` 仍受 `AgentConfig.tool_timeout_seconds` / `Tool.timeout_seconds` 包裹（`asyncio.wait_for`），并发本身不绕过超时；超时仍按 `is_error=True` 路径回灌。
9. THE `Concurrent_Tool_Execution` SHALL NOT 改变同轮工具的输入参数（`tool_call.arguments`）；`asyncio.gather` 调度 SHALL 在每个 task 内独立调用 `_execute_tool_call(context, tool_call, config)`，task 之间不得共享可变状态导致竞态。
10. WHEN `outcome.tool_calls` 长度为 1, THE `Re_Act_Agent_Adapter` SHALL 退化为与 v3 串行路径行为字面等价的单 task 执行（不因引入 `gather` 改变可观测时序）；并发开销不得因 1 个 task 引入显著开销（design 阶段可在 fast path 直接 await 单 call）。
11. THE 重构 SHALL 在 `test/infrastructure/agent/` 下补充 `Concurrent_Tool_Execution` 覆盖测试：(a) "同轮 3 个工具均为 `await asyncio.sleep(0.5)`"场景下端到端耗时显著低于 1.5s（量化阈值由 design 与本地 CI 抗噪要求决定，requirement 层仅约束"显著低于串行总和"）；(b) "同轮 3 个工具，1 个抛 `ToolPermissionDeniedError`、1 个抛 `RuntimeError`、1 个正常返回"场景下，`Conversation_Context` 末尾按 `outcome.tool_calls` 顺序产出 3 条 `Tool_Result_Message`，且 `metadata["error"]` 标记与 v3 串行路径一一相同；(c) `run_events` 同轮多工具下 `tool_start` / `tool_result` 严格满足 `Tool_Event_Pair_Adjacency`（基于 property-based / fuzzing：随机数量与随机耗时的工具组合下，事件流按 `tool_call_id` 分组连续）；(d) `run_streaming` 同轮多工具下 `tool_progress` 同样满足成对相邻；(e) `resume` 路径下并发与 `Hitl_Decision_Application_Order` 串行决策共存——审批决策处理仍按 `interrupt.actions` 顺序逐条执行，决策应用完成后续 `_iter_rounds` 推进的并发 `tool_calls` 才进入 `Concurrent_Tool_Execution`。

### 需求 2：`Sliding_Window_Compaction_Adapter` 引入 `Pairing_Aware_Trimming`

**用户故事：** 作为运维与最终用户，我希望连续多轮带工具调用的对话不再因为窗口边界切在 `Assistant_Tool_Calls_Message` 与 `Tool_Result_Message` 之间而触发 OpenAI / Anthropic 的 400 错误，让长会话保持高可用，与 LangGraph `trim_messages(strategy="last")` 等业界主流方案对齐。

#### 验收标准

1. THE `Sliding_Window_Compaction_Adapter.compact_messages` SHALL 保留 `System_Message` 全保留语义（与现状一致）；本期约束不变。
2. THE `Sliding_Window_Compaction_Adapter.compact_messages` SHALL 在裁剪非 system 消息时执行 `Pairing_Aware_Trimming`：(a) 识别每条 `Tool_Result_Message` 的 `tool_call_id` 对应到上文最近一条 `Assistant_Tool_Calls_Message.tool_calls[i].id`；(b) 把 `Assistant_Tool_Calls_Message` + 其覆盖的全部 `Tool_Result_Message` 视作不可分割的 `Tool_Pair_Group`。
3. WHEN `compact_messages` 决定保留某条 `Tool_Result_Message`, THE `Sliding_Window_Compaction_Adapter` SHALL 同时保留其对应的 `Assistant_Tool_Calls_Message`；若该 `Assistant_Tool_Calls_Message` 在窗口边界外则按 `No_Half_Tool_Group_Pass_Through` 整组丢弃该 `Tool_Result_Message`。
4. WHEN `compact_messages` 决定保留某条 `Assistant_Tool_Calls_Message`, THE `Sliding_Window_Compaction_Adapter` SHALL 同时保留其全部 `tool_calls[i].id` 对应的 `Tool_Result_Message`；若有任意一条 `Tool_Result_Message` 缺失或在窗口边界外则按 `No_Half_Tool_Group_Pass_Through` 整组丢弃该 `Assistant_Tool_Calls_Message`。
5. FOR ALL `Tool_Pair_Group` 跨越窗口边界的情形, THE `Sliding_Window_Compaction_Adapter` SHALL 整组丢弃，**不**允许"半组"出现在压缩后输出；可通过模块级 `logger.debug` 输出"丢弃 N 条 due to pairing"以便排查，但不向调用方报错、不抛异常。
6. THE `Pairing_Aware_Trimming` SHALL 不破坏滑动窗口的"最近 N 条非 system"语义边界——当窗口允许的非 system 配额在配对保护下被整组丢弃后未被填满，`Sliding_Window_Compaction_Adapter` SHALL NOT 主动向更早的历史扩展窗口去补足；最终保留的非 system 消息条数允许 ≤ `max_messages`。
7. THE `Sliding_Window_Compaction_Adapter` SHALL 不修改 `Context_Compaction_Port` 端口签名；`compact` / `compact_messages` 的对外签名保持当前形式；新增的配对保护 SHALL 仅作为内部实现细节。
8. THE `Pairing_Aware_Trimming` SHALL NOT 影响 `LLM_Summary_Compaction_Adapter` 路径——本期需求仅修改 `Sliding_Window_Compaction_Adapter`，对其他 `Context_Compaction_Port` 实现零侵入；任何与 LLM 摘要相关的耦合改动均不在范围内。
9. WHEN 输入 `messages` 为空列表, THE `Sliding_Window_Compaction_Adapter.compact_messages` SHALL 返回空列表（与 v3 行为一致）。
10. WHEN 输入 `messages` 中不含任何 `Tool_Result_Message`, THE `Sliding_Window_Compaction_Adapter.compact_messages` SHALL 退化为 v3 既有"system 全保留 + 最近 N 条非 system"裁剪，且最终输出与 v3 字面等价。
11. THE 重构 SHALL 在 `test/infrastructure/chat/` 下补充 `Pairing_Aware_Trimming` 测试：(a) 窗口边界恰好切在 `Assistant_Tool_Calls_Message` 与对应 `Tool_Result_Message` 之间——验证整组被丢弃，结果不含"孤儿 ToolMessage"；(b) `Assistant_Tool_Calls_Message.tool_calls` 含 3 个 id，对应 3 条 `Tool_Result_Message`，其中 1 条恰落在窗口外——验证整组丢弃；(c) 多组 `Tool_Pair_Group` 串联，最近一组完全在窗口内、上一组跨边界——验证最近完整组保留、上一组整组丢弃；(d) property-based：随机生成包含若干 `Tool_Pair_Group` 的消息序列与随机 `max_messages`，断言最终保留集合中 (i) 每条 `Tool_Result_Message` 的 `tool_call_id` 一定能在保留集合的某条 `Assistant_Tool_Calls_Message.tool_calls` 中找到，(ii) 每条 `Assistant_Tool_Calls_Message.tool_calls` 的全部 `id` 都在保留集合的 `Tool_Result_Message` 中出现，(iii) 系统消息全保留；(e) 不含工具调用的纯文本会话场景下输出与 v3 字面相等。

### 需求 3：`Redis_Session_Context_Adapter` 引入 `Session_Optimistic_Lock_Cycle`

**用户故事：** 作为运维与产品经理，我希望同一 `session_id` 在并发 chat 请求下不再发生丢更新——`Redis_Session_Context_Adapter` 的 `load → 业务修改 → save` 周期具备与 `Local_File_Session_Context_Adapter` 文件锁路径等价的并发写入安全语义，与 LangGraph `RedisSaver` / OpenAI thread / Cursor checkpointer 等业界主流 checkpointer 对齐。

#### 验收标准

1. THE `Redis_Session_Context_Adapter` SHALL 在 `Session_Context_Store_Port` 既有方法之外，提供配套的 `Session_Optimistic_Lock_Cycle` 入口（由 design 决定具体方法名，可候选为 `load_for_update` + `save_if_unchanged`、上下文管理器、或 `compare_and_swap` 形态），实现"读取时记录版本、提交时校验版本"的 CAS 周期；底层 SHALL 通过 `WATCH/MULTI/EXEC` 或等价 Lua 脚本完成原子提交，禁止仅依赖应用层时间戳比较。
2. THE `Session_Context_Store_Port` SHALL 仅以"末尾追加可选方法"方式扩展接口；既有 `save` / `load` / `delete` 方法签名 SHALL 保持不变，且在单写者场景下行为不退化（即旧调用方不主动接入 CAS 时，行为至少与 v3 等价）。
3. THE `Local_File_Session_Context_Adapter` SHALL 同步实现 `Session_Context_Store_Port` 的全部新增方法以保持后端对等；其底层可复用既有 `EXCLUSIVE` 文件锁 + `Temp_File_Atomic_Rename` 实现 CAS 语义，无需引入额外依赖。
4. WHEN 一次 `Session_Optimistic_Lock_Cycle` 在提交时触发 `Session_Write_Conflict`, THE `Redis_Session_Context_Adapter` SHALL 在 `Session_Conflict_Retry_Max` 上限内重新执行 `load → 业务修改 → save` 周期；具体重试机制由 design 决定（adapter 自动重试 / 上抛由调用方重试 / 二者结合），但需求层面约束"对调用方而言，冲突状态不得静默丢更新"。
5. WHEN `Session_Conflict_Retry_Max` 耗尽仍发生 `Session_Write_Conflict`, THE `Redis_Session_Context_Adapter` SHALL 抛出 `Session_Conflict_Error` 让调用方感知；该异常 SHALL 通过模块级 `logger.error` 记录最少必要字段（`session_id` / `error_class` / `retry_count`），不记录 `Conversation_Context` 全文。
6. THE `Session_Conflict_Retry_Max` 配置项 SHALL 写入 `Config_Properties`（键名建议 `SESSION_REDIS_CONFLICT_RETRY_MAX`，默认值由 design 决定但 ≥ 0）；禁止仅放 `.env`。其他相关阈值（如 CAS 超时、Lua 脚本预编译开关等）若需要也 SHALL 一并写入 `Config_Properties`。
7. THE `Redis_Session_Context_Adapter` SHALL 保留既有 TTL 行为：CAS 提交分支在最终 `SET` 时仍带 `ex=ttl_seconds`；`Session_Optimistic_Lock_Cycle` 不破坏 `key_prefix` 与 `ttl_seconds` 既有语义。
8. THE `Redis_Session_Context_Adapter` SHALL 保留既有错误日志范式：`save` / `load` / `delete` 中的 `aioredis.RedisError` 仍按 v3 `logger.error` 格式记录后透传，不因 CAS 改造缩减日志字段。
9. THE `Session_Optimistic_Lock_Cycle` SHALL NOT 引入对 `domain/` 层的反向依赖：CAS 实现细节（WATCH/MULTI/EXEC、Lua、版本号字段命名等）必须封装在 `Redis_Session_Context_Adapter` 内部；`Session_Context_Store_Port` 仅暴露与领域无关的"读取-提交"语义，不暴露 Redis 特有概念。
10. THE 重构 SHALL 在 `test/infrastructure/session/` 下补充 `Session_Optimistic_Lock_Cycle` 覆盖测试：(a) 单写者顺序读改写——CAS 周期成功提交，且写入内容与 v3 等价；(b) 双写者交错——通过 `asyncio.gather` 并发触发两个 `load → 修改 → save` 周期，断言最终 Redis 中存储的 `Conversation_Context` 是二者之一的完整结果（不丢失任一方的关键字段，不出现混合写入），且至少一方观察到 `Session_Write_Conflict` 并按 `Session_Conflict_Retry_Max` 重试；(c) 重试耗尽——构造持续冲突场景，断言 `Session_Conflict_Error` 被抛出且日志格式正确；(d) `Local_File_Session_Context_Adapter` 同方法的对等行为测试，验证 Port 层后端对等；(e) `SESSION_REDIS_CONFLICT_RETRY_MAX` 配置加载测试，验证写入 `Config_Properties` 且未出现"仅 .env 才生效"的反规范。
11. THE 重构 SHALL NOT 修改 `Conversation_Context` 字段集合或 `to_dict` / `from_dict` 序列化形式；CAS 周期对领域模型零侵入。

## 质量属性 / NFR

### NFR-1 并发性能（`Concurrent_Tool_Execution`）

- WHEN 同一轮 `outcome.tool_calls` 含 `K` 个 IO 重 `Tool_Call_Request`（每个耗时约 `t`）, THE `Re_Act_Agent_Adapter` 端到端该轮工具执行耗时 SHALL 接近 `max(tᵢ)` + 调度开销，不再随 `K` 线性增长；本期不强求绝对数值 SLA，但需在测试中以"显著低于 `Σ tᵢ`"为断言形式（design 决定具体阈值与抗噪策略）。
- THE `Concurrent_Tool_Execution` SHALL NOT 引入额外的网络往返或对 `Conversation_Context` 的额外序列化开销。

### NFR-2 OpenAI / Anthropic API 接受率（`Pairing_Aware_Trimming`）

- WHEN 长会话连续触发 `Sliding_Window_Compaction_Adapter` 裁剪, THE 服务端发起的下一次 `model_access.stream(...)` SHALL NOT 因 `tool_calls` / `Tool_Result_Message` 配对断裂收到 OpenAI / Anthropic 400 错误（"tool_call_id 没有上文 assistant" / "assistant 的 tool_calls 没有对应 tool result"）。
- THE `Pairing_Aware_Trimming` SHALL 在 `max_messages` 配额受配对保护影响而被消耗时优先保证"配对完整"而非"配额填满"。

### NFR-3 并发写入下的最终状态正确性（`Session_Optimistic_Lock_Cycle`）

- WHEN 同一 `session_id` 在并发 chat 请求下经历多次 `load → 修改 → save`, THE `Redis_Session_Context_Adapter` 最终存储的 `Conversation_Context` SHALL 是某次完整周期的完整结果，**不**出现"两次写入字段交叉混合"或"后写者无声丢弃前写者增量"的情形。
- THE `Local_File_Session_Context_Adapter` 在新增对等方法下 SHALL 表现出与 `Redis_Session_Context_Adapter` 等价的并发安全语义，构成 `Session_Context_Store_Port` 的后端对等保证。

### NFR-4 观测与日志规范

- THE `Concurrent_Tool_Execution` SHALL 保留 v3 的 `_log_tool_failure` warning 字段集合（`tool_name` / `tool_call_id` / `reason` / `exc_type` / `exc_msg`）；并发改造不得记录 `tool_call.arguments` 全文。
- THE `Pairing_Aware_Trimming` 整组丢弃 SHALL 通过 `logger.debug` 记录"丢弃组数 / 丢弃消息总数"，不以 warning/error 干扰常规巡检。
- THE `Session_Optimistic_Lock_Cycle` 的冲突重试 SHALL 通过 `logger.info` 记录 `session_id` + `retry_count` + `outcome`（success / retry / give_up），冲突耗尽以 `logger.error` 记录最少必要字段（不记录 `Conversation_Context` 全文）。
- 各路径下的 OTel 范式（`tool_call_id` 全链路、`event_timestamps` 序列化、`prompt_id` 进 OTel）SHALL 保持 v3 既有契约。

### NFR-5 HITL 与既有审批语义兼容

- THE `Concurrent_Tool_Execution` SHALL NOT 引入并发越权：`_iter_rounds` 已通过 `RoundOutcome.kind == "approval"` 在审批路径提前 return；进入 `tool_calls` 分支时所有动作已经审批通过（或本就不需审批），并发执行不会与未审批的 `Tool_Call_Request` 交叉。
- THE `Hitl_Decision_Application_Order` SHALL 在 `_apply_approval_decisions` 中保持严格顺序处理；`approve` / `edit` / `reject` 决策不进入并发。
- THE `ApprovalInterrupt` / `ApprovalDecision` / `ApprovalStateStore` 的字段集合与序列化语义 SHALL 不变。

### NFR-6 既有测试与新增测试

- THE 现有测试矩阵 SHALL 全部继续通过；如某测试覆盖的是被替换的"串行 `for tool_call in outcome.tool_calls`"路径，应在 PR 内同步把断言改写为基于 `Tool_Event_Pair_Adjacency` 的等价语义断言。
- THE 新增测试 SHALL 至少覆盖：
  - 同轮多工具并发耗时显著低于串行总和；
  - 同轮多工具任一失败不影响其余 `Tool_Call_Request` 回灌；
  - `run_events` / `run_streaming` 工具事件成对相邻（property-based）；
  - 滑动窗口边界配对保护（窗口边界恰好切在 assistant tool_calls 与 ToolMessage 之间整组丢弃）；
  - `tool_calls` 配对 property-based 验证（每条 ToolMessage 都能找到上文 assistant，每条 assistant tool_calls 全集都能找到 ToolMessage）；
  - Redis CAS 双写者并发不丢更新；
  - `Session_Conflict_Retry_Max` 耗尽抛出 `Session_Conflict_Error`；
  - `Local_File_Session_Context_Adapter` 同方法的对等测试。

## 已知约束与兼容性

1. **DDD 分层**：`Session_Context_Store_Port` 新增方法 SHALL 定义在 `domain/chat/ports.py`，仅暴露领域语义；CAS / WATCH / Lua 等技术细节封装在 `infrastructure/session/redis_session_context_adapter.py` 内部。`Re_Act_Agent_Adapter` 的并发改造与 `Sliding_Window_Compaction_Adapter` 的配对保护改造均仅落在 `infrastructure/`，不向 `domain/` 增加对外部依赖的反向感知。
2. **配置源**：所有新增配置项（`SESSION_REDIS_CONFLICT_RETRY_MAX` 等）SHALL 写入 `Config_Properties`（`epsilon-boot/config.properties`）；`.env` 仅作为本地覆盖，不作为新增配置项的首选位置。
3. **依赖管理**：本期若需引入新的 Python 依赖（如 Lua 脚本预编译辅助库等），SHALL 通过 `uv add` 安装并更新 `pyproject.toml` / `uv.lock`，禁止 `pip install`。
4. **代码文档**：所有新增公开类、方法、`@property` SHALL 附带中文 docstring，复杂 CAS 算法 / 配对保护逻辑 SHALL 在 docstring 中补充背景说明。
5. **Port 兼容性**：`Session_Context_Store_Port` 既有 `save` / `load` / `delete` 签名 SHALL 保持不变；`Local_File_Session_Context_Adapter` 与 `Redis_Session_Context_Adapter` 同时实现新增方法以保持后端对等。
6. **HITL 顺序**：`_apply_approval_decisions` 内部 SHALL 保持 v3 严格顺序处理（`zip(interrupt.actions, decisions, strict=True)`）；并发改造不得越界进入审批决策应用。
7. **审批语义**：`ApprovalInterrupt.context_snapshot` / `event_timestamps` 序列化、`allowed_decisions` 校验规则、`ApprovalDecisionCountMismatchError` / `ApprovalDecisionOrderMismatchError` 错误类型 SHALL 不变。
8. **`AgentTerminationReason` / `StreamingChunk` / `AgentStreamEvent`**：本期不新增字段也不调整既有字段类型；仅在 `run_events` / `run_streaming` 内部调整事件 yield 顺序以满足 `Tool_Event_Pair_Adjacency`。
9. **observability**：`tool_call_id` 全链路、`event_timestamps`、`prompt_id` 进 OTel、tool failure warning 不带 `arguments` 等 v3 已落定的可观测性范式 SHALL 在三项改造中全部保留。
10. **测试范式**：参考 `test/infrastructure/agent/test_react_agent_*` 与 `test/infrastructure/session/test_local_file_session_context_adapter_unit.py` 的现有单测 + property-based 风格补齐覆盖。

---

> 下一步：交给 spec-designer 输出 `docs/spec/agent-quality-assessment-2/design.md`，对每项需求落地具体的实现路径（包含但不限于 `asyncio.gather` 包装策略与事件 yield 调度、配对保护的扫描算法、Redis CAS 的 `WATCH/MULTI/EXEC` 或 Lua 二选一决策与重试拓扑、`Session_Context_Store_Port` 新增方法签名、配置键默认值、对应单测/property-based 测试用例骨架）。
