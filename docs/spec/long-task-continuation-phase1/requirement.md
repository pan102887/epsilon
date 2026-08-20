# 需求文档：Long Task Continuation Phase 1

## 简介

当前系统已经在底层 Agent 运行结果中具备 `Agent_Termination_Reason`，可表达 `completed`、`max_rounds` 与 `token_budget_exceeded`。但这些信号尚未完整透传到 `Chat_Response`、`Task_Result`、HTTP API、SSE 事件和前端 UI，导致调用方仍可能把阶段边界误解为普通完成。

本特性实现 `docs/plan.md` 的阶段一：可见化与可继续。目标是让系统明确区分“任务完成”和“到达阶段边界”，并为 `Chat_Flow` 与 `Task_Flow` 提供人工触发的 `Continue_Request`。继续执行时应沿用已保存的 `Conversation_Context`，不追加新的用户消息，不调大单段轮次上限，也不引入自动续跑、后台 Run 管理或持久化检查点。

本期范围包括：

- 将 `Agent_Termination_Reason` 映射到聊天、任务、HTTP、SSE 与前端。
- 当命中 `max_rounds` 或 `token_budget_exceeded` 时返回 `Paused_State`，并暴露 `can_continue`。
- 为因 `max_rounds` 或 `token_budget_exceeded` 暂停且满足 Continue_Precondition 的 `Chat_Flow` 与 `Task_Flow` 提供手动继续入口。
- 修正暂停时的上下文保存语义，避免写入误导性的空最终助手消息。
- 保留当前单段轮次限制与现有 HITL 审批恢复语义。

本期明确不包括：

- 自动续跑、多段全局预算、后台 `run_id`、暂停/取消 Run 管理。
- 服务重启后的检查点恢复、工具调用去重补偿。
- 调整底层 ReAct Loop 的 `max_rounds` 判定策略或工具执行策略。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 阶段一可继续能力 | Phase_One_Continuation | `docs/plan.md` 第一阶段能力，聚焦终止原因可见化与人工继续执行。 |
| Agent 运行 | Agent_Run | 一次由 `AgentPort` 执行的 ReAct 推进过程，可能自然完成、等待审批或命中阶段边界。 |
| Agent 终止原因 | Agent_Termination_Reason | Agent_Run 停止原因，当前包括 `completed`、`max_rounds` 与 `token_budget_exceeded`。 |
| 暂停状态 | Paused_State | Agent_Run 命中 `max_rounds` 或 `token_budget_exceeded` 后对上层暴露的非完成状态。 |
| 可继续标记 | Can_Continue_Flag | 响应字段，表示调用方是否可以对同一会话发起 Continue_Request。 |
| 继续请求 | Continue_Request | 调用方要求系统基于已保存 Conversation_Context 继续下一段执行的请求。 |
| 继续前置条件 | Continue_Precondition | Continue_Request 被接受前必须满足的状态条件，例如同一会话存在可继续的工具结果上下文。 |
| 对话上下文 | Conversation_Context | `ConversationContext` 保存的完整 system/user/assistant/tool 消息历史。 |
| 聊天流程 | Chat_Flow | 通过 `/api/chat` 与聊天服务执行的同步或流式对话流程。 |
| 任务流程 | Task_Flow | 通过 `/api/task/execute` 与任务 Agent 执行的结构化任务流程。 |
| 聊天响应 | Chat_Response | `ChatResponseVO` 及其 HTTP JSON 映射。 |
| 任务结果 | Task_Result | `TaskResult` 及其 HTTP JSON 映射。 |
| SSE 事件 | SSE_Event | `stream=true` 时从 `/api/chat` 返回给客户端的 Server-Sent Events 数据。 |
| 前端聊天界面 | Frontend_Chat_UI | `epsilon-client` 中展示聊天消息、流式结果和继续动作的界面。 |
| 前端任务界面 | Frontend_Task_UI | `epsilon-client` 中展示任务结果、状态、轨迹和继续动作的界面。 |
| 审批等待状态 | Approval_Required_State | HITL 工具审批中断状态，由现有审批恢复接口处理，不属于 Paused_State。 |
| 空最终助手消息 | Empty_Final_Assistant_Message | 暂停时由上层错误追加的空 `AssistantMessage(content="")`，会误导后续上下文构建。 |
| 单段轮次限制 | Segment_Round_Limit | `CHAT_MAX_TOOL_ROUNDS` 或 `TASK_AGENT_MAX_ROUNDS` 配置的单次 Agent_Run 最大推进轮次。 |
| Token 预算限制 | Token_Budget_Limit | 可选的单次 Agent_Run 累计 token 上限，命中时对应 `token_budget_exceeded`。 |
| 配置项 | Configuration_Key | `config.properties` 与环境变量中的运行参数。 |
| 工具访问边界 | Tool_Access_Boundary | 一次 Agent_Run 被允许使用的工具集合；继续执行不得比原始执行段拥有更多工具权限。 |

## 需求

### 需求 1：上层响应显式暴露暂停状态

**用户故事：** 作为 API 调用方，我希望 Chat_Response 与 Task_Result 明确暴露 Agent_Termination_Reason，以便区分真正完成与阶段边界暂停。

#### 验收标准

1. THE Chat_Response SHALL include Agent_Termination_Reason with default value `completed`.
2. THE Chat_Response SHALL include Can_Continue_Flag with default value `false`.
3. THE Task_Result SHALL include Agent_Termination_Reason with default value `completed`.
4. THE Task_Result SHALL include Can_Continue_Flag with default value `false`.
5. WHEN Agent_Run ends with Agent_Termination_Reason `max_rounds`, THE Chat_Response SHALL expose Paused_State instead of ordinary completed semantics.
6. WHEN Agent_Run ends with Agent_Termination_Reason `max_rounds`, THE Task_Result SHALL expose Paused_State instead of ordinary success semantics.
7. WHEN Agent_Run ends with Agent_Termination_Reason `token_budget_exceeded`, THE Chat_Response and Task_Result SHALL expose Paused_State instead of ordinary completed or success semantics.
8. WHILE Agent_Run IN Approval_Required_State, WHEN upper layers build Chat_Response, THE Chat_Response SHALL preserve Approval_Required_State and keep Agent_Termination_Reason as `completed`.

### 需求 2：HTTP 与 SSE 契约透传终止原因

**用户故事：** 作为前端客户端，我希望 HTTP JSON 与 SSE_Event 都携带暂停信息，以便正确展示“可继续”而不是普通完成。

#### 验收标准

1. THE Chat_Flow SHALL map Paused_State to HTTP JSON field `status="paused"`.
2. THE Chat_Flow SHALL include `terminated_reason` and `can_continue` in synchronous HTTP JSON responses.
3. THE Task_Flow SHALL map Paused_State to HTTP JSON field `status="paused"`.
4. THE Task_Flow SHALL include `terminated_reason` and `can_continue` in HTTP JSON responses.
5. WHEN SSE_Event represents final output for Paused_State, THE SSE_Event SHALL include `finished=true`, `status="paused"`, `terminated_reason`, and `can_continue`.
6. WHEN SSE_Event represents final output for normal completion, THE SSE_Event SHALL either omit `terminated_reason` or set it to `completed`, and SHALL set Can_Continue_Flag to `false` when present.
7. FOR ALL SSE_Event payloads that are not chat chunks, THE Frontend_Chat_UI SHALL ignore them unless their shape explicitly matches a supported control payload.

### 需求 3：聊天流程支持手动继续

**用户故事：** 作为聊天用户，我希望在对话因阶段边界暂停后点击继续，以便不重新输入同一目标也能推进下一段执行。

#### 验收标准

1. THE Chat_Flow SHALL provide a Continue_Request endpoint for an existing session.
2. WHEN Continue_Request is accepted by Chat_Flow, THE Chat_Flow SHALL load the existing Conversation_Context.
3. WHEN Continue_Request is accepted by Chat_Flow, THE Chat_Flow SHALL NOT append a new user message to Conversation_Context.
4. WHEN Continue_Request is accepted by Chat_Flow, THE Chat_Flow SHALL start a new Agent_Run using the existing Segment_Round_Limit.
5. WHEN the continued Agent_Run ends with `completed`, THE Chat_Response SHALL return `status="completed"` and Can_Continue_Flag `false`.
6. WHEN the continued Agent_Run again ends with Paused_State and Continue_Precondition is satisfied, THE Chat_Response SHALL return `status="paused"` and Can_Continue_Flag `true`.
7. IF Continue_Precondition is not satisfied, THEN THE Chat_Flow SHALL reject Continue_Request with a client-visible conflict response.

### 需求 4：任务流程支持手动继续

**用户故事：** 作为任务用户，我希望结构化任务暂停后可以继续执行，以便长任务不需要从头重跑。

#### 验收标准

1. THE Task_Flow SHALL provide a Continue_Request endpoint for an existing task session.
2. WHEN Continue_Request is accepted by Task_Flow, THE Task_Flow SHALL load the existing Conversation_Context.
3. WHEN Continue_Request is accepted by Task_Flow, THE Task_Flow SHALL NOT append the original task goal again as a user message.
4. WHEN Continue_Request is accepted by Task_Flow, THE Task_Flow SHALL start a new Agent_Run using the existing Segment_Round_Limit.
5. WHEN the continued Agent_Run ends with `completed`, THE Task_Result SHALL return success semantics and Can_Continue_Flag `false`.
6. WHEN the continued Agent_Run again ends with Paused_State and Continue_Precondition is satisfied, THE Task_Result SHALL return Paused_State and Can_Continue_Flag `true`.
7. IF Continue_Precondition is not satisfied, THEN THE Task_Flow SHALL reject Continue_Request with a client-visible conflict response.
8. WHEN Continue_Request is accepted by Task_Flow, THE Task_Flow SHALL preserve the original Task_Flow Tool_Access_Boundary.
9. IF Task_Flow cannot determine the original Tool_Access_Boundary without broadening it, THEN THE Task_Flow SHALL reject Continue_Request.

### 需求 5：暂停时保存可继续上下文

**用户故事：** 作为后续继续执行的调用方，我希望暂停时保存的 Conversation_Context 保留真实工具结果且不写入伪最终回复，以便下一段 Agent_Run 能从正确状态继续。

#### 验收标准

1. WHEN Chat_Flow receives Paused_State, THE Chat_Flow SHALL save Conversation_Context after tool results have been written.
2. WHEN Task_Flow receives Paused_State, THE Task_Flow SHALL save Conversation_Context after tool results have been written.
3. WHEN Chat_Flow receives Paused_State, THE Chat_Flow SHALL NOT append Empty_Final_Assistant_Message.
4. WHEN Task_Flow receives Paused_State, THE Task_Flow SHALL NOT append Empty_Final_Assistant_Message.
5. FOR ALL Continue_Request operations, THE Continue_Precondition SHALL require Conversation_Context to contain a continuation-safe latest state.
6. WHEN Paused_State is exposed and Continue_Precondition is satisfied, THE Can_Continue_Flag SHALL be `true`.
7. WHEN Paused_State is exposed and Continue_Precondition is not satisfied, THE Can_Continue_Flag SHALL be `false`.
8. IF Conversation_Context has been cleared or does not contain a continuation-safe latest state, THEN THE Continue_Request SHALL be rejected.

### 需求 6：前端展示暂停态与继续动作

**用户故事：** 作为前端用户，我希望聊天和任务界面能清楚显示任务已暂停且可以继续，以便我决定是否追加执行段。

#### 验收标准

1. WHEN Frontend_Chat_UI receives Paused_State, THE Frontend_Chat_UI SHALL render a visible paused indicator.
2. WHEN Frontend_Chat_UI receives Can_Continue_Flag `true`, THE Frontend_Chat_UI SHALL provide a continue action for the same session.
3. WHEN Frontend_Chat_UI triggers Continue_Request, THE Frontend_Chat_UI SHALL NOT send a new user message.
4. WHEN Frontend_Task_UI receives Paused_State, THE Frontend_Task_UI SHALL render a visible paused indicator.
5. WHEN Frontend_Task_UI receives Can_Continue_Flag `true`, THE Frontend_Task_UI SHALL provide a continue action for the same session.
6. WHEN Frontend_Task_UI triggers Continue_Request, THE Frontend_Task_UI SHALL keep the existing task goal input unchanged and SHALL use the continuation endpoint.
7. FOR ALL Frontend_Chat_UI and Frontend_Task_UI paused displays, THE Agent_Termination_Reason SHALL be visible to the user in readable wording.

### 需求 7：Token 预算终止原因可见化

**用户故事：** 作为运维开发者，我希望聊天和任务入口能透传 Token_Budget_Limit 命中后的终止原因，以便在不改变轮次限制的前提下识别 token 预算边界。

#### 验收标准

1. WHEN Agent_Run ends with Agent_Termination_Reason `token_budget_exceeded`, THE Chat_Response SHALL expose Paused_State consistently with `max_rounds`.
2. WHEN Agent_Run ends with Agent_Termination_Reason `token_budget_exceeded`, THE Task_Result SHALL expose Paused_State consistently with `max_rounds`.
3. WHEN Token_Budget_Limit is disabled, THE Agent_Run SHALL behave as it does today for token budget checks.
4. IF Configuration_Key already provides Token_Budget_Limit for a flow, THEN THE Chat_Flow or Task_Flow SHALL pass that configured limit into Agent_Run.
5. THE Phase_One_Continuation SHALL NOT require new global budget orchestration for Token_Budget_Limit.

### 需求 8：兼容性与阶段边界

**用户故事：** 作为维护者，我希望第一阶段只补齐可见化和手动继续，不扩大到自动长任务运行时，以便控制实现风险。

#### 验收标准

1. THE Phase_One_Continuation SHALL preserve the existing Segment_Round_Limit values.
2. THE Phase_One_Continuation SHALL NOT introduce automatic continuation.
3. THE Phase_One_Continuation SHALL NOT introduce background run management or `run_id`.
4. THE Phase_One_Continuation SHALL NOT modify Approval_Required_State resume semantics.
5. THE Phase_One_Continuation SHALL preserve existing successful Chat_Flow behavior when Agent_Termination_Reason is `completed`.
6. THE Phase_One_Continuation SHALL preserve existing successful Task_Flow behavior when Agent_Termination_Reason is `completed`.
7. FOR ALL new public fields, THE Phase_One_Continuation SHALL provide default-compatible values for existing callers.
8. FOR ALL Continue_Request operations, THE Phase_One_Continuation SHALL NOT broaden Tool_Access_Boundary.
