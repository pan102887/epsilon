# 需求文档：Long Task Continuation Phase 2

## 简介

阶段一 `long-task-continuation-phase1` 已完成并通过独立 spec evaluator 复核：`Chat_Flow` 与 `Task_Flow` 已能显式暴露 `Agent_Termination_Reason`、`Can_Continue_Flag` 和 `Paused_State`，并提供人工 `Continue_Request`。继续执行时会复用既有 `Conversation_Context`，不追加新的 user message，不调大单段轮次限制；`Task_Flow` 继续时还会保留原始 `Tool_Access_Boundary`，不可重建时拒绝继续。

本特性实现 `docs/plan.md` 的阶段二：分段执行。目标是在阶段一能力之上，把一次长任务编排为多个受控的 `Agent_Run_Segment`，由外层 `Segmented_Run` 统一管理全局预算、自动续跑、人工续跑与停止条件。阶段二的核心不是放大 `CHAT_MAX_TOOL_ROUNDS` 或 `TASK_AGENT_MAX_ROUNDS`，而是保留单段限制，把阶段边界变成可控执行节拍。

业内主流做法通常不会把长任务简单塞进一次无限循环：OpenAI Responses / Background 模式强调长耗时任务的外层状态管理和工具调用上限；Anthropic Messages API 通过 `stop_reason` 暴露 `max_tokens`、`pause_turn` 等停止信号；LangGraph 通过 recursion limit、checkpoint 和 HITL 管理长流程；Temporal 等 durable execution 方案把长任务拆成有预算、可恢复、可观察的执行单元。阶段二采用其中对当前项目风险最低的子集：请求内有限分段编排，不引入后台 durable runtime。

本期范围包括：

- 为 `Chat_Flow` 与 `Task_Flow` 引入 `Segmented_Run` 与 `Agent_Run_Segment` 的上层语义。
- 在外层增加总段数、总 token、总耗时、连续暂停次数、无进展次数和重复工具调用等停止条件。
- 支持配置控制的自动续跑；默认关闭，启用后仍必须遵守预算、风险和进展判断。
- 保留人工续跑入口；高风险、审批等待、不可重建、无进展或预算命中场景应转为人工决策。
- 在 HTTP JSON、SSE 和前端 UI 中暴露段状态、预算摘要和停止原因。
- 在阶段二实现前先补齐阶段一前端静态验证闭环。

本期明确不包括：

- 后台 `run_id`、后台 worker、Run 状态查询、取消、暂停队列管理。
- 持久化检查点、服务重启恢复、网络断开恢复。
- 工具调用副作用去重、补偿事务或 exactly-once 执行保障。
- 基于模型自评的复杂智能调度、成本金额计费或动态任务分类策略。
- 引入 LangGraph、Temporal、Dapr Workflow 等新运行时依赖。
- 修改底层 ReAct Loop 的 `max_rounds` 判定策略或工具执行策略。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 阶段二分段执行 | Phase_Two_Segmented_Execution | `docs/plan.md` 第二阶段能力，聚焦请求内有限分段、自动续跑与全局预算。 |
| Agent 运行 | Agent_Run | 一次由 `AgentPort` 执行的 ReAct 推进过程，可能自然完成、等待审批或命中阶段边界。 |
| Agent 执行段 | Agent_Run_Segment | `Segmented_Run` 中的一次 Agent_Run；每段都使用既有单段轮次限制。 |
| 分段运行 | Segmented_Run | 由一个初始 Agent_Run 和零到多个 Continue_Request 驱动的多段长任务编排过程。 |
| 段序号 | Segment_Index | 当前 Agent_Run_Segment 在 Segmented_Run 中的从 1 开始的序号。 |
| 段数量 | Segment_Count | 当前 Segmented_Run 已执行的 Agent_Run_Segment 总数。 |
| Agent 终止原因 | Agent_Termination_Reason | 单段 Agent_Run 停止原因，当前包括 `completed`、`max_rounds` 与 `token_budget_exceeded`。 |
| 分段停止原因 | Segment_Stop_Reason | Segmented_Run 停止继续进入下一段的外层原因，例如完成、预算耗尽、无进展、重复工具调用、等待人工确认。 |
| 暂停状态 | Paused_State | Agent_Run 命中 `max_rounds` 或 `token_budget_exceeded` 后对上层暴露的非完成状态。 |
| 可继续标记 | Can_Continue_Flag | 响应字段，表示调用方是否可以对同一会话发起 Continue_Request。 |
| 继续请求 | Continue_Request | 调用方要求系统基于已保存 Conversation_Context 继续下一段执行的请求。 |
| 继续前置条件 | Continue_Precondition | Continue_Request 被接受前必须满足的状态条件，例如会话存在可继续工具结果上下文。 |
| 自动续跑 | Auto_Continuation | 系统在无需用户额外点击的情况下，基于 Continue_Request 自动进入下一段执行。 |
| 人工续跑 | Manual_Continuation | 用户显式触发 Continue_Request 进入下一段执行。 |
| 分段执行策略 | Segment_Execution_Policy | 控制 Segmented_Run 是否允许自动续跑、最多续跑次数、预算和停止条件的策略。 |
| 分段预算用量 | Segment_Budget_Usage | Segmented_Run 累计的段数、token 用量、耗时和停止条件计数。 |
| 全局预算限制 | Global_Budget_Limit | 跨多个 Agent_Run_Segment 统计的总 token、总耗时、最大续跑次数等外层限制。 |
| 进展信号 | Progress_Signal | 判断一个 Agent_Run_Segment 是否产生有效推进的信号，例如新增 ToolMessage、trace、usage 或最终内容。 |
| 无进展段 | No_Progress_Segment | 未产生 Progress_Signal 的 Agent_Run_Segment。 |
| 重复工具调用 | Repeated_Tool_Call | 相邻或连续执行段中出现相同工具名和等价参数摘要的工具调用模式。 |
| 风险门禁 | Risk_Gate | 自动续跑前的保护条件；高风险、审批等待、工具边界不可重建或成本风险场景必须停止自动续跑。 |
| 对话上下文 | Conversation_Context | `ConversationContext` 保存的完整 system/user/assistant/tool 消息历史。 |
| 聊天流程 | Chat_Flow | 通过 `/api/chat` 与聊天服务执行的同步或流式对话流程。 |
| 任务流程 | Task_Flow | 通过 `/api/task/execute` 与任务 Agent 执行的结构化任务流程。 |
| HTTP 响应 | HTTP_Response | Chat_Flow 或 Task_Flow 返回给客户端的 JSON 响应体。 |
| SSE 事件 | SSE_Event | `stream=true` 时从 Chat_Flow 返回给客户端的 Server-Sent Events 数据。 |
| 前端聊天界面 | Frontend_Chat_UI | `epsilon-client` 中展示聊天消息、流式结果和继续动作的界面。 |
| 前端任务界面 | Frontend_Task_UI | `epsilon-client` 中展示任务结果、状态、轨迹和继续动作的界面。 |
| 审批等待状态 | Approval_Required_State | HITL 工具审批中断状态，由现有审批恢复接口处理，不属于自动续跑场景。 |
| 单段轮次限制 | Segment_Round_Limit | `CHAT_MAX_TOOL_ROUNDS` 或 `TASK_AGENT_MAX_ROUNDS` 配置的单次 Agent_Run 最大推进轮次。 |
| 工具访问边界 | Tool_Access_Boundary | 一次 Agent_Run 被允许使用的工具集合；继续执行不得比原始执行段拥有更多工具权限。 |
| 配置项 | Configuration_Key | `config.properties` 与环境变量中的运行参数。 |
| 前端静态验证 | Frontend_Static_Verification | 针对 `epsilon-client` 的有效 ESLint 与 TypeScript 验证结果。 |

## 需求

### 需求 1：阶段二实现前补齐阶段一验证准入

**用户故事：** 作为维护者，我希望阶段二开始实现前先获得可信的阶段一前端静态验证结果，以便避免在未知前端错误上继续叠加自动续跑能力。

#### 验收标准

1. THE Phase_Two_Segmented_Execution SHALL require Frontend_Static_Verification to be executable before frontend-facing changes are implemented.
2. THE Frontend_Static_Verification SHALL include an effective ESLint command using the repository's local frontend dependency set.
3. THE Frontend_Static_Verification SHALL include an effective TypeScript `tsc --noEmit` command using the repository's local frontend dependency set.
4. IF Frontend_Static_Verification reveals regressions from Phase_One_Continuation behavior, THEN THE Phase_Two_Segmented_Execution SHALL pause and require those regressions to be fixed before adding frontend-facing phase two behavior.
5. IF backend focused regressions for Chat_Flow stream Continue_Request 409 mapping or Task_Flow Can_Continue_Flag consistency fail, THEN THE Phase_Two_Segmented_Execution SHALL pause and route fixes to Phase_One_Continuation behavior before proceeding.

### 需求 2：引入分段运行状态

**用户故事：** 作为 API 调用方，我希望 Chat_Flow 与 Task_Flow 能表达当前长任务执行到第几段，以便观察长任务的推进节奏和停止位置。

#### 验收标准

1. THE Phase_Two_Segmented_Execution SHALL model a Segmented_Run as one or more Agent_Run_Segment entries for the same Conversation_Context.
2. THE Agent_Run_Segment SHALL expose Segment_Index.
3. THE Segmented_Run SHALL expose Segment_Count.
4. THE HTTP_Response SHALL include Segment_Index and Segment_Count when Phase_Two_Segmented_Execution is active.
5. THE HTTP_Response SHALL provide default-compatible Segment_Index and Segment_Count values for callers that do not enable Auto_Continuation.
6. WHEN Agent_Run ends with Agent_Termination_Reason `completed`, THE Segmented_Run SHALL expose Segment_Stop_Reason `completed`.
7. WHEN Agent_Run ends with Paused_State and no next segment is started, THE Segmented_Run SHALL expose a Segment_Stop_Reason explaining why execution stopped.
8. FOR ALL added public response fields, THE Phase_Two_Segmented_Execution SHALL preserve existing Phase_One_Continuation fields `status`, `terminated_reason`, and `can_continue`.

### 需求 3：保留阶段一继续语义与单段边界

**用户故事：** 作为系统维护者，我希望阶段二复用阶段一 Continue_Request 语义，以便自动续跑不会改变上下文、轮次和工具权限边界。

#### 验收标准

1. WHEN Segmented_Run starts an Agent_Run_Segment after the first segment, THE Segmented_Run SHALL use Continue_Request semantics.
2. WHEN Continue_Request semantics are used by Segmented_Run, THE Chat_Flow SHALL NOT append a new user message to Conversation_Context.
3. WHEN Continue_Request semantics are used by Segmented_Run, THE Task_Flow SHALL NOT append the original task goal again as a user message.
4. FOR ALL Agent_Run_Segment executions, THE Agent_Run_Segment SHALL use the existing Segment_Round_Limit for the corresponding flow.
5. THE Phase_Two_Segmented_Execution SHALL NOT increase `CHAT_MAX_TOOL_ROUNDS` because of Auto_Continuation.
6. THE Phase_Two_Segmented_Execution SHALL NOT increase `TASK_AGENT_MAX_ROUNDS` because of Auto_Continuation.
7. WHEN Task_Flow starts a continued Agent_Run_Segment, THE Task_Flow SHALL preserve Tool_Access_Boundary.
8. IF Tool_Access_Boundary cannot be reconstructed without broadening it, THEN THE Segmented_Run SHALL stop and expose Segment_Stop_Reason `tool_boundary_unavailable`.

### 需求 4：配置分段执行策略与全局预算

**用户故事：** 作为运维开发者，我希望通过配置控制自动续跑和全局预算，以便不同部署环境可以按成本和风险承受能力启用阶段二能力。

#### 验收标准

1. THE Segment_Execution_Policy SHALL include an Auto_Continuation enable flag.
2. THE Auto_Continuation enable flag SHALL default to disabled.
3. THE Segment_Execution_Policy SHALL include maximum continuation count.
4. THE Segment_Execution_Policy SHALL include optional total token budget.
5. THE Segment_Execution_Policy SHALL include optional total duration budget.
6. THE Segment_Execution_Policy SHALL include maximum consecutive paused segment count.
7. THE Segment_Execution_Policy SHALL include maximum No_Progress_Segment count.
8. THE Segment_Execution_Policy SHALL include maximum Repeated_Tool_Call count.
9. THE Configuration_Key defaults SHALL be written in `epsilon-boot/config.properties` rather than `.env`.
10. IF a Global_Budget_Limit is configured as disabled, THEN THE Segmented_Run SHALL not enforce that specific Global_Budget_Limit.
11. IF any configured Global_Budget_Limit is invalid, THEN THE Phase_Two_Segmented_Execution SHALL reject startup or configuration loading with a client-visible configuration error consistent with existing configuration behavior.

### 需求 5：自动续跑只在安全且有进展时发生

**用户故事：** 作为用户，我希望系统在任务仍有明确进展且风险可控时自动推进下一段，以便减少机械点击继续的操作。

#### 验收标准

1. WHEN Auto_Continuation is disabled, THE Segmented_Run SHALL NOT automatically start a continued Agent_Run_Segment.
2. WHEN Auto_Continuation is enabled and Agent_Run ends with Paused_State, THE Segmented_Run SHALL evaluate Continue_Precondition before starting the next Agent_Run_Segment.
3. IF Can_Continue_Flag is `false`, THEN THE Segmented_Run SHALL NOT start Auto_Continuation.
4. IF Global_Budget_Limit has been reached, THEN THE Segmented_Run SHALL NOT start Auto_Continuation.
5. IF Risk_Gate requires manual review, THEN THE Segmented_Run SHALL NOT start Auto_Continuation.
6. IF the latest Agent_Run_Segment is a No_Progress_Segment beyond the configured threshold, THEN THE Segmented_Run SHALL NOT start Auto_Continuation.
7. IF Repeated_Tool_Call exceeds the configured threshold, THEN THE Segmented_Run SHALL NOT start Auto_Continuation.
8. WHEN Auto_Continuation starts a next Agent_Run_Segment, THE Segmented_Run SHALL record that Auto_Continuation was attempted.
9. WHEN Auto_Continuation completes multiple Agent_Run_Segment executions in one request, THE HTTP_Response SHALL expose cumulative Segment_Budget_Usage.

### 需求 6：定义保守的进展与反循环停止条件

**用户故事：** 作为维护者，我希望阶段二具备基础反循环能力，以便自动续跑不会在无效工具调用或重复暂停中无限推进。

#### 验收标准

1. THE Progress_Signal SHALL be true when an Agent_Run_Segment adds at least one new ToolMessage.
2. THE Progress_Signal SHALL be true when an Agent_Run_Segment adds at least one new TraceEntry.
3. THE Progress_Signal SHALL be true when an Agent_Run_Segment increases token usage.
4. THE Progress_Signal SHALL be true when an Agent_Run_Segment produces final assistant content.
5. IF none of the Progress_Signal conditions are true, THEN THE Agent_Run_Segment SHALL be classified as No_Progress_Segment.
6. THE Repeated_Tool_Call detection SHALL compare tool name and normalized argument digest rather than raw message object identity.
7. IF consecutive Paused_State count reaches the configured threshold, THEN THE Segmented_Run SHALL stop with Segment_Stop_Reason `consecutive_paused_limit`.
8. IF No_Progress_Segment count reaches the configured threshold, THEN THE Segmented_Run SHALL stop with Segment_Stop_Reason `no_progress`.
9. IF Repeated_Tool_Call count reaches the configured threshold, THEN THE Segmented_Run SHALL stop with Segment_Stop_Reason `repeated_tool_call`.

### 需求 7：人工续跑和风险门禁

**用户故事：** 作为用户，我希望高风险、长耗时或不确定场景停止自动续跑并保留人工继续按钮，以便我能决定是否承担下一段成本和风险。

#### 验收标准

1. WHEN Agent_Run returns Approval_Required_State, THE Segmented_Run SHALL stop Auto_Continuation.
2. WHEN Agent_Run returns Approval_Required_State, THE Chat_Flow SHALL preserve existing approval resume semantics.
3. WHEN Risk_Gate stops Auto_Continuation and Continue_Precondition is satisfied, THE HTTP_Response SHALL keep Can_Continue_Flag `true`.
4. WHEN Risk_Gate stops Auto_Continuation, THE HTTP_Response SHALL expose a readable Segment_Stop_Reason.
5. WHEN Global_Budget_Limit stops Auto_Continuation and Continue_Precondition is satisfied, THE HTTP_Response SHALL keep Manual_Continuation available.
6. IF Continue_Precondition is not satisfied, THEN THE HTTP_Response SHALL expose Can_Continue_Flag `false` regardless of Manual_Continuation intent.
7. THE Phase_Two_Segmented_Execution SHALL NOT treat Approval_Required_State as Paused_State.
8. THE Phase_Two_Segmented_Execution SHALL NOT bypass existing HITL approval policy.

### 需求 8：HTTP 与 SSE 契约透传分段信息

**用户故事：** 作为前端客户端，我希望 JSON 和 SSE 都能获得分段状态，以便在同步和流式模式下展示一致的长任务进度。

#### 验收标准

1. THE HTTP_Response SHALL include `segment_index` when Phase_Two_Segmented_Execution is active.
2. THE HTTP_Response SHALL include `segment_count` when Phase_Two_Segmented_Execution is active.
3. THE HTTP_Response SHALL include `auto_continue_attempted` when Phase_Two_Segmented_Execution is active.
4. THE HTTP_Response SHALL include `segment_stop_reason` when Segmented_Run stops.
5. THE HTTP_Response SHALL include `budget_usage` when Segment_Budget_Usage is available.
6. WHEN SSE_Event represents the end of an Agent_Run_Segment, THE SSE_Event SHALL emit a control payload for segment completion.
7. WHEN SSE_Event represents final output for the whole Segmented_Run, THE SSE_Event SHALL preserve the existing `finished=true` final payload behavior.
8. FOR ALL SSE_Event control payloads, THE Frontend_Chat_UI SHALL NOT append control payload content to assistant message text.
9. IF Auto_Continuation performs multiple Agent_Run_Segment executions in one stream, THEN THE SSE_Event sequence SHALL expose each segment boundary before the final Segmented_Run completion event.

### 需求 9：前端展示分段状态与预算摘要

**用户故事：** 作为前端用户，我希望聊天和任务界面能展示执行段、自动续跑状态和停止原因，以便理解系统是在继续推进、等待我确认还是预算耗尽。

#### 验收标准

1. WHEN Frontend_Chat_UI receives Segment_Index or Segment_Count, THE Frontend_Chat_UI SHALL display the current segmented execution state.
2. WHEN Frontend_Task_UI receives Segment_Index or Segment_Count, THE Frontend_Task_UI SHALL display the current segmented execution state.
3. WHEN Frontend_Chat_UI receives Segment_Stop_Reason, THE Frontend_Chat_UI SHALL display a readable stop reason.
4. WHEN Frontend_Task_UI receives Segment_Stop_Reason, THE Frontend_Task_UI SHALL display a readable stop reason.
5. WHEN Frontend_Chat_UI receives Segment_Budget_Usage, THE Frontend_Chat_UI SHALL display a concise budget usage summary.
6. WHEN Frontend_Task_UI receives Segment_Budget_Usage, THE Frontend_Task_UI SHALL display a concise budget usage summary.
7. WHEN Auto_Continuation is in progress in streaming mode, THE Frontend_Chat_UI SHALL indicate that the system is continuing automatically.
8. WHEN Can_Continue_Flag is `true`, THE Frontend_Chat_UI and Frontend_Task_UI SHALL preserve the existing Manual_Continuation action.
9. WHEN Auto_Continuation is disabled, THE Frontend_Chat_UI and Frontend_Task_UI SHALL NOT trigger Continue_Request without a user action.

### 需求 10：阶段边界、兼容性与可测试性

**用户故事：** 作为维护者，我希望阶段二保持明确边界和可回归验证，以便后续阶段三后台 Run 与阶段四检查点可以在稳定基础上演进。

#### 验收标准

1. THE Phase_Two_Segmented_Execution SHALL NOT introduce background `run_id` management.
2. THE Phase_Two_Segmented_Execution SHALL NOT introduce persistent checkpoint recovery.
3. THE Phase_Two_Segmented_Execution SHALL NOT introduce service-restart recovery for in-flight Segmented_Run.
4. THE Phase_Two_Segmented_Execution SHALL NOT introduce new workflow runtime dependencies.
5. THE Phase_Two_Segmented_Execution SHALL preserve existing successful Chat_Flow behavior when Agent_Termination_Reason is `completed` in the first Agent_Run_Segment.
6. THE Phase_Two_Segmented_Execution SHALL preserve existing successful Task_Flow behavior when Agent_Termination_Reason is `completed` in the first Agent_Run_Segment.
7. FOR ALL new domain value objects, THE Phase_Two_Segmented_Execution SHALL provide deterministic construction and validation rules.
8. FOR ALL new public modules, classes, functions, and methods, THE Phase_Two_Segmented_Execution SHALL include Chinese docstrings consistent with repository documentation rules.
9. THE Phase_Two_Segmented_Execution SHALL be verifiable with focused backend tests for budget, auto continuation, stop reasons, Chat_Flow, Task_Flow, HTTP_Response and SSE_Event behavior.
10. THE Phase_Two_Segmented_Execution SHALL be verifiable with Frontend_Static_Verification after frontend changes are introduced.
