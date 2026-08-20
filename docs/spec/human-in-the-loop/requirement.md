# 需求文档：Human-in-the-loop 工具审批

## 简介

### 背景

当前 `epsilon-boot` 已有自研 ReAct Agent Runtime：

- `ChatServiceAdapter` 根据 `CHAT_TOOL_CALLING_ENABLED` 与工具 schema 决定是否进入 `AgentPort`。
- `ReActAgentAdapter` 在每轮模型返回 `tool_calls` 后，先校验 `AgentConfig.allowed_tool_names`，再通过 `ToolRegistry.execute(...)` 执行工具。
- `AgentStreamEvent` 已能向 CLI/TUI 输出 `tool_start`、`tool_result`、`tool_error`、`assistant_delta`、`assistant_done` 等结构化事件。
- `SessionContextStorePort` 已支持 file / redis 会话上下文持久化，可作为审批中断后恢复的会话锚点。

LangChain Deep Agents 的 human-in-the-loop 文档给出了本特性需要借鉴的工程语义：通过 `interrupt_on` 为敏感工具配置中断审批；决策类型包括 `approve`、`edit`、`reject`、`respond`；中断后必须依赖 checkpointer 保存状态，并用相同 `thread_id` 恢复；同一轮存在多个待审批工具调用时，提交的决策必须与 `action_requests` 顺序一一对应。相关 permissions / subagents 页面还强调：权限应按工具风险定制，路径权限只覆盖内置文件工具，自定义工具、命令执行与子 Agent 需要额外策略。

本项目不直接迁移到 LangGraph / Deep Agents Runtime。本期采用“借鉴语义，接入现有运行时”的方案：在自研 `ReActAgentAdapter`、`ChatServicePort`、FastAPI 与 SSE 事件链路中加入主 Agent 工具审批中断协议，使敏感工具在执行前先暂停，由外部用户通过 HTTP 恢复接口明确批准、修改或拒绝。

参考资料：

- <https://docs.langchain.com/oss/python/deepagents/human-in-the-loop>
- <https://docs.langchain.com/oss/python/deepagents/permissions>
- <https://docs.langchain.com/oss/python/deepagents/event-streaming>
- <https://docs.langchain.com/oss/python/deepagents/streaming>
- <https://docs.langchain.com/oss/python/deepagents/subagents>

### 范围

**纳入 v1 范围（In Scope）**：

- 在 `domain/agent/` 定义 HITL 领域模型、审批策略、待审批动作、审批决策、审批中断状态与审批状态存储端口。
- 在基础设施层实现审批策略解析、审批状态持久化，以及主 Agent ReAct Loop 的中断 / 恢复流程。
- 在 `ReActAgentAdapter` 的同步、流式、事件流路径中，在敏感工具执行前触发审批中断。
- 在 `ChatServicePort` 与 `ChatServiceAdapter` 中支持“完成响应”和“等待审批”两种状态，以及带审批决策的恢复执行。
- 在 FastAPI 中新增审批恢复 API；在现有 `/api/chat` 同步和 SSE 路径中输出可被客户端识别的审批中断结果。
- 通过 `config.properties` 新增 HITL 配置项，默认关闭，开启后按工具风险分级触发审批。
- 增加单元测试覆盖审批策略、批量工具调用顺序、上下文持久化、HTTP/SSE 协议、恢复执行与错误处理。
- 更新 `docs/agent.md`、`docs/api.md`、`docs/tools.md` 或等价文档，说明 HITL 位置、协议和默认策略。

**不纳入 v1 范围（Out of Scope）**：

- 不把现有 ReAct Runtime 全量迁移到 LangGraph / Deep Agents。
- 不实现 Web 前端 `epsilon-client` 的审批弹窗；本期只提供 HTTP/SSE 协议。
- 不实现 TUI 内的完整交互式 `approve` / `edit` / `reject` / `respond` 表单；TUI v1 只需能展示等待审批提示与 `approval_id`。
- 不实现子 Agent 内部工具调用的审批传播；v1 只审批主 Agent 对 `delegate_to_agent` 工具本身的调用。
- 不新增组织级审批流、RBAC、多审批人会签或审计后台。
- 不为命令执行实现 OS 级沙箱；HITL 是工具执行前控制，不替代 Workspace、工具授权、工具参数校验或容器 / 系统权限。
- 不改变现有 `ToolRegistry` 的工具 schema 协议，不要求工具实现者改写所有工具入参格式。
- 不实现跨进程实时推送通道；恢复由客户端显式调用 resume API 触发。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 人工审批 | `Human_Approval` | Agent 在执行敏感工具前暂停并等待用户决策的机制。 |
| 审批策略 | `Approval_Policy` | 描述某个工具是否需要人工审批，以及允许哪些审批决策的运行期配置。 |
| 中断配置 | `Interrupt_On_Config` | 与 LangChain `interrupt_on` 对齐的工具名到审批配置映射，项目内由 `config.properties` 与默认策略生成。 |
| 待审批动作 | `Pending_Action_Request` | 某一条模型返回的 `ToolCallRequest` 在执行前形成的审批项，包含工具名、调用 ID、原始参数、允许决策和展示信息。 |
| 审批决策 | `Approval_Decision` | 用户针对一个 `Pending_Action_Request` 提交的决策。v1 默认支持 `approve`、`edit`、`reject`；`respond` 仅作为 ask-user 类工具的预留决策。 |
| 审批中断 | `Approval_Interrupt` | Agent Loop 暂停状态，表示当前会话存在一批待审批动作，必须先处理这些动作才能继续执行。 |
| 审批恢复 | `Approval_Resume` | 客户端提交一组 `Approval_Decision` 后，Agent Loop 从上次中断点继续执行的过程。 |
| 审批线程 | `Approval_Thread` | 与 LangChain `thread_id` 语义对应，本项目 v1 以 `session_id` 为会话锚点，并用 `approval_id` 区分同一会话中的一次中断批次。 |
| 审批状态存储 | `Approval_State_Store` | 持久化 `Approval_Interrupt` 所需上下文和待审批动作的端口，确保服务可在中断后恢复。 |
| 批量审批 | `Batched_Approval` | 同一模型响应中多个敏感 `tool_calls` 一起暂停，并要求用户按原始顺序提供决策。 |
| 允许决策 | `Allowed_Decisions` | 某个工具审批时允许用户选择的决策类型集合。 |
| 编辑后动作 | `Edited_Action` | `edit` 决策中用户修改后的工具参数。v1 中工具名必须等于原工具名，防止通过编辑绕过审批策略。 |
| 人工回复 | `Human_Response` | `respond` 决策对应的人工文本，作为工具调用的 `ToolMessage` 内容回传给模型。v1 不对现有写文件、命令执行、网络请求和委派工具默认开放。 |
| 审批响应状态 | `Approval_Response_Status` | `/api/chat` 与 resume API 返回中的状态标识，取值至少包括 `completed` 与 `approval_required`。 |
| 审批事件 | `Approval_Event` | 面向 SSE / 结构化事件流的审批中断事件，用于展示待审批动作、审批恢复结果和错误。 |

## 需求

### 需求 1：审批策略配置与默认风险分级

**用户故事：** 作为平台维护者，我希望通过配置声明哪些工具需要人工审批以及允许哪些决策，以便在不改代码的情况下按工具风险调整 Agent 执行边界。

#### 验收标准

1. THE `Approval_Policy` SHALL 位于 `domain/agent/` 或其稳定领域模型中，表达工具名、是否中断、`Allowed_Decisions` 与可读风险说明。
2. THE `Interrupt_On_Config` SHALL 支持按工具名配置 `true`、`false` 或显式 `Allowed_Decisions`，语义与 LangChain Deep Agents 的 `interrupt_on` 保持一致。
3. THE `config.properties` SHALL 新增 `HITL_ENABLED`、`HITL_INTERRUPT_ON`、`HITL_STATE_TTL_SECONDS` 或等价配置项，且配置来源遵循 `docs/steering/config-source.md`。
4. THE `HITL_ENABLED` 默认值 SHALL 为 `false`，以保持现有 HTTP API、CLI/TUI 与测试行为向后兼容。
5. WHEN `HITL_ENABLED=false`, THE `ReActAgentAdapter` SHALL 保持现有行为，不产生任何审批中断。
6. WHEN `HITL_ENABLED=true` 且工具匹配 `Approval_Policy`, THE `ReActAgentAdapter` SHALL 在执行该工具前触发 `Approval_Interrupt`。
7. THE 默认 `Approval_Policy` SHALL 将 `write_file`、`edit_file`、`shell_exec`、`python_exec`、`delegate_to_agent` 视为敏感工具，并默认允许 `approve/reject`。
8. THE 默认 `Approval_Policy` SHALL 将 `http_request` 视为敏感工具，并默认允许 `approve/edit/reject`，以便用户可修改 URL、method、headers 或 body 后再执行。
9. THE 默认 `Approval_Policy` SHALL 将 `read_file`、`list_dir`、`web_fetch`、`web_search` 视为低风险工具，默认不触发审批。
10. THE 默认 `Approval_Policy` SHALL NOT 对现有工具默认开放 `respond`；`respond` 仅可由未来明确的 ask-user 类工具通过配置启用。
11. FOR ALL 未注册工具或未授权工具, THE `ToolPermissionDeniedError` / `ToolNotFoundError` SHALL 继续优先于 `Human_Approval` 生效，不得通过审批绕过既有工具权限。

### 需求 2：领域模型与审批状态持久化

**用户故事：** 作为后端开发者，我希望审批中断状态以领域模型表达并持久化，以便服务暂停后可以用同一个会话恢复执行。

#### 验收标准

1. THE `domain/agent/` SHALL 定义 `Pending_Action_Request`、`Approval_Decision`、`Approval_Interrupt`、`Approval_Resume`、`Allowed_Decisions` 等值对象，且不得依赖 `infrastructure/`、FastAPI、Pydantic Settings 或具体存储 SDK。
2. THE `Approval_State_Store` SHALL 作为领域 Port 定义，提供保存、加载、删除待审批状态的能力。
3. WHEN `Approval_Interrupt` 被创建, THE `Approval_State_Store` SHALL 保存 `session_id`、`approval_id`、待审批动作列表、当前 `ConversationContext` 快照、工具调用轮次元数据与过期时间。
4. WHEN `Approval_Interrupt` 被创建, THE 保存的 `ConversationContext` SHALL 包含模型刚返回的 assistant `tool_calls` 消息，但 SHALL NOT 提前追加任何待审批工具的 `ToolMessage`。
5. WHEN 客户端提交 `Approval_Resume`, THE `Approval_State_Store` SHALL 通过 `session_id` 与 `approval_id` 加载同一批次状态。
6. IF `Approval_State_Store` 找不到对应状态、状态已过期或状态已被消费, THEN THE 系统 SHALL 返回中文可读错误，并不得重新执行任何工具。
7. WHEN `Approval_Resume` 成功完成并 Agent Loop 进入下一阶段, THE `Approval_State_Store` SHALL 删除或标记消费对应 `Approval_Interrupt`，避免重复恢复。
8. THE `Approval_State_Store` SHALL 优先复用项目既有 file / redis 持久化风格；本期不得引入新的数据库系统。

### 需求 3：Agent Loop 中断与恢复语义

**用户故事：** 作为 Agent 调用方，我希望敏感工具在执行前暂停，用户确认后再继续原来的 ReAct Loop，以便模型上下文和工具执行顺序保持一致。

#### 验收标准

1. WHEN `ReActAgentAdapter` 收到模型返回的 `tool_calls`, THE adapter SHALL 先按 `AgentConfig.allowed_tool_names` 完成授权校验，再筛选需要审批的工具调用。
2. WHEN 同一轮存在一个或多个需要审批的 `tool_calls`, THE adapter SHALL 创建单个 `Approval_Interrupt`，其中 `Pending_Action_Request` 顺序与模型返回的 `tool_calls` 顺序一致。
3. WHILE `Approval_Interrupt` 未恢复, THE adapter SHALL NOT 执行任何待审批工具。
4. WHEN 用户对某个 `Pending_Action_Request` 提交 `approve`, THE adapter SHALL 使用原始工具名与原始参数执行该工具。
5. WHEN 用户提交 `edit`, THE adapter SHALL 使用 `Edited_Action.args` 替换原始参数执行工具，且 `Edited_Action.name` 必须等于原工具名。
6. WHEN 用户提交 `reject`, THE adapter SHALL 跳过工具执行，并向 `ConversationContext` 追加一个描述用户拒绝原因的 `ToolMessage`。
7. WHEN 工具的 `Allowed_Decisions` 包含 `respond` 且用户提交 `respond`, THE adapter SHALL 跳过工具执行，并将 `Human_Response` 作为该工具调用的 `ToolMessage` 内容。
8. FOR ALL `Approval_Decision` 列表, THE adapter SHALL 校验决策数量与 `Pending_Action_Request` 数量一致，且顺序逐项对应；不一致时 SHALL 拒绝恢复。
9. FOR ALL `edit` 决策, THE adapter SHALL 校验编辑后的参数仍满足对应工具 schema；校验失败时 SHALL 拒绝恢复且不得执行工具。
10. WHEN 审批恢复后所有待审批工具都处理完成, THE adapter SHALL 继续下一轮模型调用，直到得到最终助手回复、再次中断或达到最大轮次。
11. IF 审批恢复后再次触发敏感工具调用, THEN THE adapter SHALL 创建新的 `approval_id` 与新的 `Approval_Interrupt`。
12. THE 中断 / 恢复流程 SHALL 不改变非敏感工具的既有执行语义。

### 需求 4：同步、流式与事件流接口兼容

**用户故事：** 作为 API 调用方，我希望所有交互模式都能识别审批中断，并用统一状态模型恢复执行，以便不同客户端实现一致。

#### 验收标准

1. THE `ChatResponseVO` 或等价返回模型 SHALL 能表达 `Approval_Response_Status`，取值至少包括 `completed` 与 `approval_required`。
2. WHEN 同步 `/api/chat` 调用完成, THE HTTP 响应 SHALL 返回 `status="completed"`，并保留现有 `session_id`、`reply`、`model`、`usage`、`prompt_id` 字段。
3. WHEN 同步 `/api/chat` 调用触发 `Approval_Interrupt`, THE HTTP 响应 SHALL 返回 `status="approval_required"`、`session_id`、`approval_id`、待审批动作列表与允许决策，而不是阻塞等待人工输入。
4. WHEN SSE `/api/chat` 调用触发 `Approval_Interrupt`, THE SSE SHALL 发送一个 `approval_required` 事件后结束本次流，且不得发送误导性的最终助手完成事件。
5. THE `AgentStreamEventKind` SHALL 新增 `approval_required` 或等价事件类型，用于结构化呈现审批中断；`approval_resumed` 与 `approval_rejected` 可作为实现细节事件，但 v1 不强制客户端依赖。
6. WHEN 不支持结构化审批事件的旧文本流调用方使用 `stream_chat(...)`, THE 系统 SHALL 以兼容方式返回一条明确的中文提示，说明当前会话等待人工审批并给出 `approval_id`。
7. THE `ChatServicePort` SHALL 提供恢复审批的接口或等价能力，使 API router 与后续 CLI runtime 都能复用同一应用编排逻辑。

### 需求 5：HTTP API 恢复协议

**用户故事：** 作为前端或外部集成方，我希望可以通过明确的 HTTP API 提交审批决策并恢复 Agent 执行，以便未来 Web 前端可以在不理解内部 Agent 状态的情况下接入 HITL。

#### 验收标准

1. THE FastAPI application SHALL 新增一个审批恢复端点，例如 `POST /api/chat/sessions/{session_id}/approvals/{approval_id}/resume` 或等价路径。
2. THE 审批恢复请求体 SHALL 包含按顺序排列的 `Approval_Decision` 列表，且每个决策包含 `type` 与该类型所需字段。
3. WHEN 请求体中的决策数量与待审批动作数量不一致, THE API SHALL 返回 HTTP 400 与中文错误消息。
4. WHEN 请求体中的决策类型不在对应 `Pending_Action_Request.allowed_decisions` 内, THE API SHALL 返回 HTTP 400 与中文错误消息。
5. WHEN `edit` 决策缺少 `edited_action.args` 或试图修改工具名, THE API SHALL 返回 HTTP 400 与中文错误消息。
6. WHEN `respond` 决策缺少人工回复内容或对应工具未允许 `respond`, THE API SHALL 返回 HTTP 400 与中文错误消息。
7. WHEN 审批状态不存在、已过期或已消费, THE API SHALL 返回 HTTP 404 或 HTTP 409，并携带中文错误消息。
8. WHEN 恢复执行再次触发新的审批中断, THE API SHALL 返回 `status="approval_required"`、新的 `approval_id` 与待审批动作，而不是丢失新状态。
9. WHEN 恢复执行最终完成, THE API SHALL 返回 `status="completed"` 以及与普通聊天一致的 `reply`、`model`、`usage`、`prompt_id` 字段。

### 需求 6：CLI/TUI v1 兼容提示

**用户故事：** 作为终端用户，我希望 TUI 在遇到审批中断时至少能清楚告诉我当前会话等待审批，以便我可以转到 HTTP/API 客户端或后续 TUI 版本完成审批。

#### 验收标准

1. WHEN TUI 收到 `approval_required` 事件, THE TUI SHALL 展示工具名、压缩后的参数、允许决策、`session_id` 与 `approval_id`。
2. THE TUI v1 SHALL NOT 必须提供交互式 `approve`、`edit`、`reject`、`respond` 表单。
3. THE TUI v1 SHALL 以中文提示说明“当前请求等待人工审批，请通过审批恢复接口提交决策”或等价信息。
4. THE TUI v1 SHALL 不在普通工具事件中泄露 `Approval_State_Store` 的内部物理路径或持久化细节。

### 需求 7：委派工具与子 Agent 边界

**用户故事：** 作为多 Agent 编排维护者，我希望 `delegate_to_agent` 不能绕过主 Agent 的人工审批边界，同时不把子 Agent 内部审批传播强行塞入 v1。

#### 验收标准

1. WHEN 主 Agent 调用 `delegate_to_agent` 且该工具被 `Approval_Policy` 标记为敏感, THE 主 Agent SHALL 在委派前触发审批。
2. THE v1 SHALL NOT 要求子 Agent 内部工具调用继承或传播主 Agent 的 `Approval_Interrupt`。
3. THE `Approval_Interrupt` 元数据 SHOULD 能标识当前审批来自主 Agent 的普通工具调用或 `delegate_to_agent` 工具调用。
4. THE 默认策略 SHALL 避免给 `delegate_to_agent` 暴露比主 Agent 更宽的免审批能力。

### 需求 8：安全、审计与日志

**用户故事：** 作为安全运维，我希望所有审批中断、恢复、拒绝和异常都可被审计，但不会把敏感信息直接写入日志或返回给模型。

#### 验收标准

1. WHEN 创建 `Approval_Interrupt`, THE 基础设施层 SHALL 记录结构化日志，至少包含 `session_id`、`approval_id`、工具名列表、动作数量与当前 ReAct 轮次。
2. WHEN 收到 `Approval_Resume`, THE 基础设施层 SHALL 记录结构化日志，至少包含 `session_id`、`approval_id`、决策类型列表与恢复结果。
3. FOR ALL 日志字段, THE 系统 SHALL 对工具参数做长度限制与敏感键脱敏，敏感键至少包含 `api_key`、`password`、`secret`、`token`、`authorization`。
4. THE 返回给 LLM 的拒绝 / 人工回复 `ToolMessage` SHALL 不包含审批状态存储路径、内部异常堆栈或部署密钥。
5. WHEN 审批状态已过期、重复恢复或决策不合法, THE 系统 SHALL 记录 warning 级别日志并返回可读错误。
6. THE HITL 机制 SHALL 不替代 `Workspace` 边界、`allowed_tool_names`、工具自身参数校验或 OS/容器权限；文档 SHALL 明确这些边界。

### 需求 9：测试与兼容性

**用户故事：** 作为维护者，我希望 HITL 的关键语义有自动化测试覆盖，并且关闭 HITL 后现有行为不回归，以便后续迭代可控。

#### 验收标准

1. THE 测试 SHALL 覆盖 `HITL_ENABLED=false` 时现有 Agent Loop 工具执行行为保持不变。
2. THE 测试 SHALL 覆盖单个敏感工具触发 `Approval_Interrupt` 且工具未提前执行。
3. THE 测试 SHALL 覆盖多个敏感工具调用被合并为一个 `Batched_Approval`，并要求决策顺序匹配。
4. THE 测试 SHALL 覆盖 `approve`、`edit`、`reject` 三类 v1 决策的执行结果和写入 `ConversationContext` 的 `ToolMessage` 内容。
5. THE 测试 SHALL 覆盖 `respond` 不对现有敏感工具默认开放，以及 ask-user 类工具配置允许时的预留语义。
6. THE 测试 SHALL 覆盖非法决策数量、非法决策类型、非法 JSON 参数、修改工具名、状态过期或重复恢复等错误路径。
7. THE 测试 SHALL 覆盖 FastAPI 审批恢复端点的成功与失败响应。
8. THE 测试 SHALL 覆盖 `AgentStreamEvent` 中 `approval_required` 事件的字段完整性。
9. THE 测试 SHALL 覆盖同步 `/api/chat` 的 `completed` 与 `approval_required` 状态联合响应。
10. THE 验证命令 SHALL 使用 `uv run --frozen pytest ...`，工作目录为 `epsilon-boot/`，遵循 `docs/steering/uv-package-manager.md`。

### 需求 10：文档与运维说明

**用户故事：** 作为部署和使用者，我希望文档清楚说明 HITL 的开启方式、默认策略和协议字段，以便正确接入和排障。

#### 验收标准

1. THE 项目文档 SHALL 更新 `docs/agent.md` 或等价文档，说明 HITL 在 ReAct Loop 中的位置。
2. THE 项目文档 SHALL 更新 `docs/api.md` 或等价文档，说明审批中断响应和恢复端点。
3. THE 项目文档 SHALL 更新 `docs/tools.md` 或等价文档，说明各工具默认审批策略。
4. THE `config.properties` SHALL 为所有新增 HITL 配置项提供中文注释和默认值说明。
5. THE 文档 SHALL 明确 HITL 与 LangChain Deep Agents 的关系：本项目借鉴 `interrupt_on` / decision / checkpointer 语义，但不直接依赖 Deep Agents 执行图。
6. THE 文档 SHALL 明确 v1 / v2 边界：v1 不包含 Web 弹窗、完整 TUI 审批表单、子 Agent 内部审批传播和组织级审批流。
7. THE 文档 SHALL 明确 HITL 的安全边界：审批只发生在工具执行前，不能替代 Workspace、工具权限、网络访问控制、命令沙箱或操作系统隔离。
