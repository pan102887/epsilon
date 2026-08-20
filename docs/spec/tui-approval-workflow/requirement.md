# 需求文档：本地 TUI 审批与高风险工具闭环（tui-approval-workflow）

## 简介

### 背景

后端 HITL v1（见 `docs/spec/human-in-the-loop/`）已经具备完整的审批中断能力：`ReActAgentAdapter` 在执行高风险工具前会中断，产出 `approval_required` 事件与 `ApprovalRequiredPayload`，并通过 `ApprovalStateStorePort` 持久化 `ApprovalInterrupt`；`ChatServicePort.resume_approval` 与 HTTP `POST /api/chat/sessions/{sid}/approvals/{aid}/resume` 支持提交 `ApprovalDecision(approve|edit|reject)` 恢复执行；`react_agent_adapter.py` 的 `resume()` 已完整实现三种决策的应用。

但本地 coding-agent 的终端界面（Textual TUI，`src/application/cli/tui.py`）尚未闭环：`approval_required` 事件当前仅被渲染为一段静态文本提示，告诉用户"请通过审批恢复接口提交决策"，无法在 TUI 内直接决策；斜杠命令层仅有 `/run approve <run_id>` 这一条走后台 Run 服务的临时通路，且硬编码 `tool_call_id="__tui_approval__"`、只支持 `approve`，不支持 `edit`/`reject`，也无法作用于 inline 主对话流。

同时存在一个隐藏的关键路径缺口：inline 主对话流（`stream_main_agent_events` → `ChatServicePort.stream_chat_events`）在中断后没有对称的流式恢复入口——`CliRuntime` 只有 `resume_approval_run`（走后台 Run 服务，返回快照而非事件流），无法在同一 inline 事件模型下续播 `assistant`/`tool` 事件。因此必须先补齐 inline 流式恢复通路，TUI 审批面板才能真正闭环。

### 动机

落地 `TODO.md` P0.1「本地 coding-agent：TUI 审批与高风险工具闭环」，让本地终端用户无需切换到 HTTP API 即可在 TUI 内对每个待审批动作做 approve/edit/reject 决策、编辑并校验 JSON 参数、查看与切换本地审批策略，并在恢复后继续观察后续执行与再次中断，形成可用闭环。

### 范围内行为

- 在 TUI 中以交互式模态面板（Textual ModalScreen）替换现有的纯文本 `approval_required` 提示，逐个待审批动作展示工具名、风险标签、参数与允许的决策集合。
- 支持对每个待审批动作选择 approve / edit / reject，收集为有序 `ApprovalDecision` 序列后走 inline 流式恢复通路提交。
- `edit` 决策提供 JSON 参数编辑区，提交前做 JSON 校验，失败时原地展示失败原因且不关闭面板。
- 新增 `ChatServicePort.stream_resume_approval` 流式恢复入口与 `CliRuntime.resume_main_agent_events`，与 `stream_main_agent_events` 对称，返回 `AgentStreamEvent` 流。
- 恢复后在同一事件模型下续播 `assistant`/`tool` 事件，并支持再次进入 `approval_required` 中断形成闭环。
- `/approval` 斜杠命令：查看当前审批模式、列出本会话未过期 pending approval、切换本地会话级审批策略。
- 恢复流程产出 `ApprovalTrace` 记录，衔接 P0.3 结构化 trace。

### 范围外行为（明确排除）

- 前端 Web 控制台的 HITL 审批面板、工具事件时间线、diff 与 artifact 浏览（属于 P1.4 / `frontend-hitl-trace`），本特性不涉及 `epsilon-client/`。
- 后端审批策略解析引擎、`ApprovalInterrupt` 持久化、`react_agent_adapter.py` 的 `resume()` 决策应用逻辑本身（HITL v1 已实现），本特性只复用不重写。
- 后台 Run 服务的审批恢复（`resume_approval_run`）改造；`/run approve` 临时通路保留现状，不在本特性内扩展。
- `HITL_INTERRUPT_ON` 全局配置源与默认风险分级的调整（`config.properties` 层面），本特性只读取现有策略。
- 新增 `ArtifactTrace`、`.epsilon/` 目录规范（属于 P0.2 / P0.3 其余部分）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 审批面板 | `Approval_Screen` | TUI 中新增的 Textual ModalScreen，逐个待审批动作展示信息并收集人工决策，本特性范围内的交互主体。 |
| 待审批动作 | `Pending_Action` | 单个等待人工审批的工具调用，对应领域值对象 `PendingActionRequest`，携带 `tool_call_id`、`tool_name`、`arguments`、`allowed_decisions`、`reason`。 |
| 审批决策 | `Approval_Decision` | 人工对单个待审批动作的裁决，对应领域值对象 `ApprovalDecision`，`type` 取 `approve`/`edit`/`reject`，携带 `tool_call_id`、可选 `edited_action`、可选 `message`。 |
| 决策类型 | `Approval_Decision_Type` | 决策取值域，对应 `ApprovalDecisionType = Literal["approve", "edit", "reject"]`。 |
| 允许决策集合 | `Allowed_Decisions` | 单个待审批动作允许的决策类型集合，来源于 `PendingActionRequest.allowed_decisions` 与 `ApprovalPolicy.allowed_decisions`。 |
| 编辑动作 | `Edited_Action` | `edit` 决策携带的人工编辑后工具动作，对应领域值对象 `EditedAction(name, arguments)`，`arguments` 为 JSON 字符串。 |
| 审批中断载荷 | `Approval_Required_Payload` | Agent 返回给上层的审批中断信息，对应 `ApprovalRequiredPayload`，在事件流中以 `AgentStreamEvent(kind="approval_required")` 的 `metadata` 承载。 |
| 结构化事件 | `Agent_Stream_Event` | inline 事件流的统一事件模型，对应 `AgentStreamEvent`，`kind` 取 `status`/`assistant_delta`/`assistant_done`/`tool_start`/`tool_result`/`tool_error`/`approval_required`/`error`/`tool_arguments_delta`。 |
| 聊天服务端口 | `Chat_Service_Port` | 聊天编排能力端口 `ChatServicePort`，本特性新增流式恢复方法 `stream_resume_approval`。 |
| 流式恢复入口 | `Stream_Resume_Approval` | `Chat_Service_Port` 新增的流式审批恢复方法，接收审批决策并返回 `Agent_Stream_Event` 流。 |
| CLI 运行时 | `Cli_Runtime` | 面向 CLI/TUI 的运行时门面 `CliRuntime`，本特性新增 `resume_main_agent_events` 与 `stream_main_agent_events` 对称。 |
| 审批恢复请求 | `Approval_Resume_Request` | 审批恢复请求值对象 `ApprovalResumeRequestVO`，携带 `session_id`、`approval_id`、有序 `decisions`、可选 `model`。 |
| 审批状态存储端口 | `Approval_State_Store_Port` | 审批中断状态存储端口 `ApprovalStateStorePort`，本特性复用 `list_pending_by_session` 查询本会话未过期审批。 |
| 审批中断摘要 | `Approval_Interrupt_Summary` | 轻量审批中断摘要值对象 `ApprovalInterruptSummary`，供 `/approval` 与恢复列表展示。 |
| 审批策略 | `Approval_Policy` | 工具审批策略值对象 `ApprovalPolicy`，携带 `tool_name`、`interrupt`、`allowed_decisions`、`risk_label`。 |
| 审批策略端口 | `Approval_Policy_Port` | 审批策略查询端口 `ApprovalPolicyPort`，由 `StaticApprovalPolicyProvider` 实现，提供 `policy_for(tool_name)`。 |
| 本地审批模式 | `Approval_Mode` | 本会话级审批模式，存于 `TuiSessionState.approval_mode`，取值 `ask`/`auto`/`manual`（见需求 6 定义），仅可更严或跳过低风险，不得绕过后端高风险红线。 |
| 会话状态 | `Tui_Session_State` | 单个 TUI 进程的可变会话状态 `TuiSessionState`，含 `session_id`、`model`、`approval_mode`。 |
| 斜杠命令路由 | `Slash_Command_Router` | TUI 斜杠命令解析器 `SlashCommandRouter`，本特性新增 `/approval` 命令。 |
| 审批 trace | `Approval_Trace` | 审批中断结构化追踪值对象 `ApprovalTrace`，携带 `round_num`、`approval_id`、`actions_summary`、`timestamp_epoch`，衔接 P0.3。 |
| 高风险工具 | `High_Risk_Tool` | 默认需中断审批的工具集合：`write_file`/`edit_file`/`shell_exec`/`python_exec`/`delegate_to_agent`/`http_request`，由 `StaticApprovalPolicyProvider` 定义。 |

## 需求

### 需求 1：inline 流式审批恢复通路

**用户故事：** 作为本地 TUI 用户，我希望在 inline 主对话流被审批中断后，能通过同一事件模型的流式接口提交决策并续播执行，以便无需切换到后台 Run 服务或 HTTP API 即可完成恢复。

#### 验收标准

1. THE `Chat_Service_Port` SHALL 暴露 `Stream_Resume_Approval` 方法，接收 `Approval_Resume_Request` 并返回 `Agent_Stream_Event` 的异步迭代器。
2. WHEN `Stream_Resume_Approval` 被调用且恢复执行自然完成，THE `Chat_Service_Port` SHALL 依次产出与 `stream_chat_events` 同构的 `Agent_Stream_Event`（含 `assistant_delta`、`assistant_done`）。
3. WHEN `Stream_Resume_Approval` 被调用且恢复后再次触发工具审批中断，THE `Chat_Service_Port` SHALL 产出新的 `kind="approval_required"` 的 `Agent_Stream_Event`，其 `metadata` 携带新的 `session_id`、`approval_id` 与待审批动作集合。
4. THE `Cli_Runtime` SHALL 暴露 `resume_main_agent_events` 方法，接收 `session_id`、`approval_id`、有序 `Approval_Decision` 序列与可选模型，委托 `Stream_Resume_Approval` 并逐个转发 `Agent_Stream_Event`，其方法签名与流式语义与 `stream_main_agent_events` 对称。
5. IF 提交的 `Approval_Decision` 序列的 `tool_call_id` 与目标审批中断的待审批动作不匹配，THEN THE `Chat_Service_Port` SHALL 通过既有 HITL v1 校验路径拒绝恢复，且不得以 `approve` 语义静默执行任何 `Pending_Action`。
6. THE `Stream_Resume_Approval` SHALL 复用后端既有 `resume_approval` 的中断消费与决策应用逻辑，不得在恢复路径重复实现 `approve`/`edit`/`reject` 的动作应用。

### 需求 2：交互式审批面板

**用户故事：** 作为本地 TUI 用户，我希望在收到审批中断时看到一个交互式面板逐条展示待审批动作及其风险，以便清楚了解 Agent 想执行什么再做决策，而不是只看到一段静态提示文本。

#### 验收标准

1. WHEN TUI 收到 `kind="approval_required"` 的 `Agent_Stream_Event`，THE `Approval_Screen` SHALL 作为 Textual 模态屏幕打开，替代当前的纯文本 `approval_required` 提示渲染。
2. FOR ALL 待审批动作 IN `Approval_Required_Payload.actions`，THE `Approval_Screen` SHALL 展示该 `Pending_Action` 的 `tool_name`、`risk_label`（来自 `Approval_Policy`）、`arguments` 与 `Allowed_Decisions`。
3. THE `Approval_Screen` SHALL 提供键盘优先的决策入口，对每个 `Pending_Action` 允许选择 approve / edit / reject。
4. IF 某个 `Pending_Action` 的 `Allowed_Decisions` 不包含某决策类型，THEN THE `Approval_Screen` SHALL 禁止对该动作提交该决策类型。
5. WHILE `Approval_Screen` IN 多动作待决策状态，WHEN 用户完成对当前 `Pending_Action` 的决策，THE `Approval_Screen` SHALL 逐条推进到下一个未决策的 `Pending_Action`，直至全部动作均有决策。
6. WHEN 全部 `Pending_Action` 均已收集到决策，THE `Approval_Screen` SHALL 构造与动作顺序一致的有序 `Approval_Decision` 序列并交由 `Cli_Runtime` 的 `resume_main_agent_events` 提交。

### 需求 3：edit 决策的 JSON 参数编辑与校验

**用户故事：** 作为本地 TUI 用户，我希望在选择 edit 决策时能编辑工具参数并在提交前得到 JSON 校验反馈，以便修正参数而不会因非法 JSON 导致恢复失败。

#### 验收标准

1. WHEN 用户对某个 `Pending_Action` 选择 edit，THE `Approval_Screen` SHALL 展示一个预填该动作当前 `arguments`（JSON 字符串）的可编辑区。
2. WHEN 用户提交 edit 编辑区内容，THE `Approval_Screen` SHALL 对编辑后文本执行 JSON 解析校验。
3. IF edit 编辑区内容不是合法 JSON，THEN THE `Approval_Screen` SHALL 在面板内原地展示解析失败原因，且不得关闭面板、不得推进到下一个 `Pending_Action`、不得提交该 `Approval_Decision`。
4. WHEN edit 编辑区内容通过 JSON 校验，THE `Approval_Screen` SHALL 构造 `type="edit"` 的 `Approval_Decision`，其 `Edited_Action.name` 等于原 `Pending_Action.tool_name`、`Edited_Action.arguments` 等于校验通过的 JSON 文本。

### 需求 4：恢复后续播与再次中断

**用户故事：** 作为本地 TUI 用户，我希望提交决策恢复后能继续在同一界面观察后续的 assistant 文本与工具事件，并在再次触发高风险工具时再次进入审批面板，以便形成可反复中断-恢复的闭环。

#### 验收标准

1. WHEN `Approval_Screen` 提交决策并触发 `resume_main_agent_events`，THE TUI SHALL 在关闭审批面板后按既有 `Agent_Stream_Event` 渲染逻辑续播后续 `assistant_delta`、`assistant_done`、`tool_start`、`tool_result`、`tool_error` 事件。
2. WHEN 恢复流在续播过程中再次产出 `kind="approval_required"` 的 `Agent_Stream_Event`，THE TUI SHALL 再次打开 `Approval_Screen` 处理新的 `Approval_Required_Payload`。
3. WHILE 恢复流处于进行中，WHEN 用户触发取消，THE TUI SHALL 复用既有 inline 请求取消路径中止当前恢复流，且不得使会话进入不可恢复状态。
4. IF 恢复流产出 `kind="error"` 的 `Agent_Stream_Event`，THEN THE TUI SHALL 以既有错误渲染方式展示错误内容并结束本轮续播。

### 需求 5：/approval 命令查看与本会话审批信息

**用户故事：** 作为本地 TUI 用户，我希望用 `/approval` 命令查看当前审批模式与本会话未处理的待审批批次，以便在不发起新对话的情况下了解审批现状。

#### 验收标准

1. WHEN 用户输入不带参数的 `/approval`，THE `Slash_Command_Router` SHALL 返回当前会话的 `Approval_Mode` 与本会话未过期 pending approval 概览。
2. THE `Slash_Command_Router` SHALL 通过 `Approval_State_Store_Port.list_pending_by_session` 获取本会话（`Tui_Session_State.session_id`）的 `Approval_Interrupt_Summary` 列表，不得消费或删除任何未过期审批状态。
3. FOR ALL `Approval_Interrupt_Summary` IN 本会话未过期审批列表，THE `Slash_Command_Router` SHALL 展示其 `approval_id`、`tool_names` 与过期时间。
4. IF 本会话不存在未过期审批中断，THEN THE `Slash_Command_Router` SHALL 返回明确的"暂无待处理审批"提示而非空输出。
5. THE `Slash_Command_Router` SHALL 将 `/approval` 命令登记进 `/help` 帮助文本。

### 需求 6：/approval 切换本地审批策略

**用户故事：** 作为本地 TUI 用户，我希望用 `/approval` 命令切换本会话的审批模式，以便在自己承担风险时收紧或适度放宽低风险工具的审批，但绝不能绕过后端对高风险工具的强制审批。

#### 验收标准

1. THE `Approval_Mode` SHALL 取值于 `ask`（默认，按后端策略中断）、`manual`（更严：对所有可中断工具均要求人工审批）、`auto`（放宽：自动放行后端判定为非高风险的低风险工具）三者之一。
2. WHEN 用户输入 `/approval mode <value>` 且 `<value>` 为合法 `Approval_Mode`，THE `Slash_Command_Router` SHALL 更新 `Tui_Session_State.approval_mode` 并返回切换后的模式。
3. IF `<value>` 不是合法 `Approval_Mode`，THEN THE `Slash_Command_Router` SHALL 返回用法提示并保持 `Tui_Session_State.approval_mode` 不变。
4. THE `Approval_Mode` SHALL 仅作用于本地会话决策行为，且不得使任何被后端 `Approval_Policy` 判定为 `interrupt=True` 的 `High_Risk_Tool` 在未获人工 `approve`/`edit` 决策的情况下被执行。
5. WHILE `Tui_Session_State.approval_mode` IN `auto`，WHEN 收到仅含后端判定为非高风险动作的 `Approval_Required_Payload`，THE TUI MAY 自动以 `approve` 决策提交而不打开 `Approval_Screen`；WHILE 该载荷含任一 `High_Risk_Tool`，THE TUI SHALL 打开 `Approval_Screen` 要求人工决策。
6. THE `Approval_Mode` 的判定 SHALL 依据 `Approval_Policy_Port.policy_for(tool_name)` 返回的 `Approval_Policy`，不得在 TUI 侧硬编码工具风险分级。

### 需求 7：审批恢复的结构化 trace

**用户故事：** 作为平台维护者，我希望 TUI 触发的审批恢复能产出结构化审批记录，以便后续审计与结构化 trace（P0.3）能消费一致的审批事件。

#### 验收标准

1. WHEN 一次审批中断在恢复路径被处理，THE 后端恢复流程 SHALL 产出 `Approval_Trace` 记录，其 `approval_id` 等于被恢复的审批批次 ID，`actions_summary` 覆盖本批次全部 `Pending_Action` 的可读摘要。
2. THE `Approval_Trace` SHALL 复用既有 `ApprovalTrace` 值对象与 HITL v1 的 trace 写入路径，不得在本特性新增并行的审批追踪结构。
3. THE `Approval_Trace` 的写入 SHALL 不改变 `Approval_Screen` 决策语义与恢复结果，即 trace 记录失败不得阻断审批恢复主流程。
