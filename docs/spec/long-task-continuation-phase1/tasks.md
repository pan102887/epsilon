# 实现计划：Long Task Continuation Phase 1

## 概述

本计划按 DDD / 六边形分层自内向外推进：先扩展领域值对象、异常和 Port，再实现 Chat / Task 适配器的暂停翻译与手动继续，随后补齐 HTTP/SSE 契约和前端展示，最后以属性测试、单元测试、路由测试和集成测试验证第一阶段能力。

本期不新增 DDL、不新增强制配置项、不实现自动续跑、后台 Run、持久化检查点、全局预算或服务端同会话并发锁。

## Tasks

- [x] 1.1 修改聊天领域值对象与继续异常
  - 在 `epsilon-boot/src/domain/chat/value_objects.py` 中修改 `ChatResponseStatus = Literal["completed", "approval_required", "paused"]`
  - 在 `ChatResponseVO` 中追加字段 `terminated_reason: AgentTerminationReason = "completed"`、`can_continue: bool = False`，并从 `domain.agent.value_objects` 导入 `AgentTerminationReason`
  - 在 `epsilon-boot/src/domain/chat/value_objects.py` 中新增 `@dataclass(frozen=True) class ChatContinueRequestVO`，字段为 `session_id: str`、`stream: bool = False`、`model: str | None = None`，`__post_init__` 校验 `session_id` 非空
  - 在 `epsilon-boot/src/domain/chat/exceptions.py` 中新增 `ContinuationUnavailableError(BizException)`，`code=60041`，构造参数 `session_id: str`、`reason: str`，保存同名属性并输出中文业务消息
  - _需求: 1, 2, 3, 5, 7, 8_

- [x] 1.2 编写聊天领域值对象与异常测试
  - 在 `epsilon-boot/test/domain/chat/test_continuation_value_objects_unit.py` 中创建 pytest 用例，验证 `ChatResponseVO` 默认 `terminated_reason="completed"`、`can_continue=False`，允许 `status="paused"`，且 `ChatContinueRequestVO(session_id="")` 抛 `ValueError`
  - 在同一文件中验证 `ContinuationUnavailableError` 的 `code`、`session_id`、`reason` 和中文 `message`
  - 在 `epsilon-boot/test/domain/chat/test_chat_response_paused_property.py` 中使用 Hypothesis 生成 `terminated_reason in {"max_rounds", "token_budget_exceeded"}`，验证 paused 响应保留原因并可表达 `can_continue`
  - **验证: 需求 1, 2, 3, 5, 7, 8**

- [x] 1.3 修改任务领域值对象
  - 在 `epsilon-boot/src/domain/task/value_objects.py` 中从 `domain.agent.value_objects` 导入 `AgentTerminationReason`
  - 在 `TaskStatus` 中新增 `PAUSED = "paused"`，并更新中文 docstring 说明任务可暂停
  - 在 `TaskResult` 中追加字段 `terminated_reason: AgentTerminationReason = "completed"`、`can_continue: bool = False`
  - 在 `epsilon-boot/src/domain/task/value_objects.py` 中新增 `@dataclass(frozen=True) class TaskContinueRequest`，字段为 `session_id: str`、`model: str | None = None`，`__post_init__` 校验 `session_id` 非空
  - _需求: 1, 2, 4, 5, 7, 8_

- [x] 1.4 编写任务领域值对象测试
  - 在 `epsilon-boot/test/domain/task/test_task_paused_result_unit.py` 中创建 pytest 用例，验证 `TaskStatus.PAUSED.value == "paused"`、`TaskResult` 默认终止字段、paused 结果可携带 `terminated_reason` 与 `can_continue`
  - 在同一文件中验证 `TaskContinueRequest(session_id="")` 抛 `ValueError`
  - **验证: 需求 1, 2, 4, 5, 7, 8**

- [x] 1.5 扩展 Chat 与 Task 领域 Port
  - 在 `epsilon-boot/src/domain/chat/ports.py` 的 TYPE_CHECKING 导入中加入 `ChatContinueRequestVO`
  - 在 `ChatServicePort` 中新增 `async def continue_chat(self, request: "ChatContinueRequestVO") -> "ChatResponseVO": ...`
  - 在 `ChatServicePort` 中新增 `def stream_continue_chat_events(self, request: "ChatContinueRequestVO") -> AsyncIterator["AgentStreamEvent"]: ...`
  - 在 `epsilon-boot/src/domain/task/ports.py` 的 TYPE_CHECKING 导入中加入 `TaskContinueRequest`
  - 在 `TaskAgentPort` 中新增 `async def continue_task(self, request: "TaskContinueRequest") -> TaskResult: ...`
  - _需求: 3, 4, 8_

- [x] 1.6 编写 Port 签名静态测试
  - 在 `epsilon-boot/test/domain/chat/test_continuation_ports_unit.py` 中创建测试，使用 `inspect.signature` 验证 `ChatServicePort.continue_chat` 与 `stream_continue_chat_events` 的参数名、返回注解和方法存在
  - 在 `epsilon-boot/test/domain/task/test_task_continue_port_unit.py` 中创建测试，验证 `TaskAgentPort.continue_task` 的参数名、返回注解和方法存在
  - **验证: 需求 3, 4, 8**

- [x] 2.1 重构 ChatServiceAdapter 的 Agent 配置与暂停翻译
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中导入 `ToolMessage`、`AgentResult`、`ChatContinueRequestVO`、`ContinuationUnavailableError`
  - 新增 `def _make_agent_config(self, model: str | None) -> AgentConfig`，统一使用 `self._system_prompt`、`self._tool_schemas`、`self._max_tool_rounds`、`self._prompt_id`
  - 新增 `@staticmethod def _can_continue_from_context(context: ConversationContext) -> bool`，逻辑为 `bool(messages) and isinstance(messages[-1], ToolMessage)`
  - 新增 `_to_chat_response(...) -> ChatResponseVO`，把 `AgentResult.status == "approval_required"` 映射为审批响应，把 `terminated_reason == "completed"` 映射为普通完成，把 `max_rounds` / `token_budget_exceeded` 映射为 `status="paused"`、`reply=""`、`can_continue=_can_continue_from_context(context)`
  - 修改 `chat(...)` 的 Agent 路径复用 `_make_agent_config` 和 `_to_chat_response`；暂停时只保存上下文，不追加 `AssistantMessage(content="")`
  - _需求: 1, 2, 3, 5, 7, 8_

- [x] 2.2 编写 ChatServiceAdapter 同步暂停测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_service_paused_unit.py` 中创建 fake `AgentPort.run`，分别返回 `AgentResult(terminated_reason="max_rounds")` 和 `AgentResult(terminated_reason="token_budget_exceeded")`
  - 验证 `chat(...)` 返回 `ChatResponseVO.status == "paused"`、保留 `terminated_reason`、按尾部 `ToolMessage` 计算 `can_continue`
  - 验证保存后的 `ConversationContext` 尾部不出现新增空 `AssistantMessage(content="")`
  - 验证 `approval_required` 分支仍返回 `status="approval_required"` 且 `terminated_reason="completed"`
  - **验证: 需求 1, 2, 5, 7, 8**

- [x] 2.3 实现 ChatServiceAdapter 手动继续入口
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中新增 `async def _run_agent_on_existing_context(self, *, session_id: str, context: ConversationContext, model: str | None) -> ChatResponseVO`
  - 在 `_run_agent_on_existing_context(...)` 中解析 `ModelAccessPort`，调用 `_make_agent_config(model)` 和 `self._agent.run(context, config, model_access)`，再通过 `_to_chat_response(...)` 保存并返回
  - 新增 `async def continue_chat(self, request: ChatContinueRequestVO) -> ChatResponseVO`：加载上下文、设置 `context.session_id`，校验 `_can_continue_from_context(context)`，不追加 user message，失败时抛 `ContinuationUnavailableError`
  - 修改 `resume_approval(...)` 的完成/再次暂停翻译复用 `_to_chat_response(...)`，避免审批恢复后命中轮数上限时伪装完成
  - _需求: 3, 5, 7, 8_

- [x] 2.4 编写 ChatServiceAdapter 继续测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_service_continue_unit.py` 中创建有效上下文：system/user/assistant(tool_calls)/tool，调用 `continue_chat(...)`
  - 验证继续请求不追加新的 user message，传给 fake Agent 的上下文 user 数量保持不变
  - 验证最新消息不是 `ToolMessage`、会话为空或不存在时抛 `ContinuationUnavailableError`
  - 验证继续后再次 `max_rounds` 返回 `status="paused"` 且 `can_continue=True`，继续后 `completed` 返回 `status="completed"` 且 `can_continue=False`
  - **验证: 需求 3, 5, 7, 8**

- [x] 2. 检查点 — 领域与 Chat 同步/继续能力
  - 在 `epsilon-boot` 目录运行 `uv run --frozen pytest -q`
  - 确认领域、Port、Chat 同步暂停和继续相关测试全部通过；如出现非本期范围的既有失败，记录失败用例和原因后向用户确认

- [x] 2.5 实现 ChatServiceAdapter 结构化流暂停与继续事件
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中修改 `stream_chat(...)`：当最终 `StreamingChunk.metadata["terminated_reason"]` 为 `max_rounds` 或 `token_budget_exceeded` 时，保存上下文但不追加空 assistant，并透传 metadata；普通完成保持原行为
  - 修改 `stream_chat_events(...)`：`assistant_done` 时读取 `event.metadata["terminated_reason"]`，暂停时保存上下文但不追加空 assistant，最终事件 metadata 补充 `status="paused"`、`terminated_reason`、`can_continue`
  - 新增 `def stream_continue_chat_events(self, request: ChatContinueRequestVO) -> AsyncIterator[AgentStreamEvent]`，入口加载上下文并校验最新 `ToolMessage`，不追加 user message，通过 `self._agent.run_events(...)` 继续流式执行
  - 确保直接 LLM 调用路径仍只产生普通 completed final，不暴露 paused
  - _需求: 2, 3, 5, 7, 8_

- [x] 2.6 编写 ChatServiceAdapter 流式暂停测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_service_stream_paused_unit.py` 中创建 fake `AgentPort.run_events`，产出 `assistant_done` 且 metadata 含 `terminated_reason="max_rounds"`
  - 验证 `stream_chat_events(...)` 最终事件包含 `status="paused"`、`terminated_reason`、`can_continue`，保存上下文时不追加空 assistant
  - 验证 `stream_continue_chat_events(...)` 对有效上下文不追加 user message，对无效上下文抛 `ContinuationUnavailableError`
  - 在同一文件中覆盖 `stream_chat(...)` 的 `StreamingChunk.metadata["terminated_reason"]` 暂停路径
  - **验证: 需求 2, 3, 5, 7, 8**

- [x] 3.1 实现 TaskAgentAdapter 暂停翻译与继续入口
  - 在 `epsilon-boot/src/infrastructure/task/task_agent_adapter.py` 中导入 `AgentResult`、`ToolMessage`、`TaskContinueRequest`、`ContinuationUnavailableError`
  - 新增 `_make_agent_config(self, *, system_prompt: str, tool_schemas: list[dict[str, Any]], model_name: str) -> AgentConfig`，统一保留 `max_rounds=self._max_rounds` 与 `prompt_id=self._task_template_prompt_id`
  - 新增 `@staticmethod def _can_continue_from_context(context: ConversationContext) -> bool`，最新消息为 `ToolMessage` 时返回 True
  - 新增 `_to_task_result(self, *, agent_result: AgentResult, trace: list[TraceEntry]) -> TaskResult`，`terminated_reason == "completed"` 返回 `TaskStatus.SUCCESS`，否则返回 `TaskStatus.PAUSED`、`content=""`、保留 `terminated_reason` 和 `can_continue`
  - 修改 `execute(...)`：首次注入 system message 时写入 `metadata["task_allowed_tool_names"]`，`task.tool_names is None` 时显式保存 `None`，否则保存排序后的工具名列表；Agent 成功返回后通过 `_to_task_result(...)` 翻译；有 `session_id` 时暂停和完成都保存上下文；失败分支仍返回 `TaskStatus.FAILED`
  - 新增 `async def continue_task(self, request: TaskContinueRequest) -> TaskResult`：加载上下文，要求存在 system message 且最新消息为 `ToolMessage`，不追加 task goal/user message；从 system message metadata 读取 `task_allowed_tool_names` 并据此调用 `ToolRegistry.get_schemas(...)`
  - 当 `task_allowed_tool_names` metadata 缺失、类型非法或指定工具不存在时，`continue_task(...)` 抛 `ContinuationUnavailableError("缺少可继续的工具访问边界")`，不得退化为全量工具
  - _需求: 1, 2, 4, 5, 7, 8_

- [x] 3.2 编写 TaskAgentAdapter 暂停与继续测试
  - 在 `epsilon-boot/test/infrastructure/task/test_task_agent_paused_unit.py` 中创建 fake `AgentPort.run`，验证 `execute(...)` 对 `max_rounds` / `token_budget_exceeded` 返回 `TaskStatus.PAUSED`、保留终止原因、保存上下文
  - 验证暂停保存后的上下文不追加空 `AssistantMessage(content="")`
  - 验证 `continue_task(...)` 不追加新的 user message，并沿用 `self._max_rounds` 构造 `AgentConfig`
  - 验证 `execute(...)` 持久化 `SystemMessage.metadata["task_allowed_tool_names"]`；当原始 `Task.tool_names` 为子集时，`continue_task(...)` 只重建同一子集的工具 schema
  - 验证缺失 `task_allowed_tool_names` metadata 时 `continue_task(...)` 抛 `ContinuationUnavailableError`，不会扩大为全量工具
  - 验证空会话、缺少 system message、最新消息不是 `ToolMessage` 时抛 `ContinuationUnavailableError`
  - 在 `epsilon-boot/test/infrastructure/task/test_task_continuation_context_property.py` 中用 Hypothesis 覆盖 Property 2、3 的消息数量/尾部消息不变量
  - 在 `epsilon-boot/test/infrastructure/task/test_task_continue_tool_boundary_property.py` 中用 Hypothesis 覆盖 Property 7：继续执行的工具集合不得宽于原始 `Tool_Access_Boundary`
  - **验证: 需求 1, 2, 4, 5, 7, 8**

- [x] 4.1 扩展 Chat HTTP/SSE 契约和继续路由
  - 在 `epsilon-boot/src/application/api/routers/chat.py` 中给 `ChatResponseBody` 追加 `terminated_reason: str = "completed"`、`can_continue: bool = False`
  - 在同步 `/api/chat` 与 `resume_approval(...)` 响应映射中填充 `response.terminated_reason` 和 `response.can_continue`
  - 新增 `class ChatContinueRequestBody(BaseModel)`，字段为 `stream: bool = False`、`model: str | None = None`
  - 新增 `@router.post("/api/chat/sessions/{session_id}/continue", response_model=None) async def continue_chat(...)`：非流式调用 `service.continue_chat(ChatContinueRequestVO(...))`，流式调用 `service.stream_continue_chat_events(...)` 并返回 `EventSourceResponse`
  - 修改 SSE `_event_generator` 的 `assistant_done` 映射：当 event metadata 包含 paused 信息时输出 `{"delta_content":"","finished":true,"status":"paused","terminated_reason": "...","can_continue": ...}`，普通完成保持原 final chunk
  - 修改 `_biz_error_response(...)`，将 `ContinuationUnavailableError` 映射为 HTTP 409
  - _需求: 2, 3, 5, 7, 8_

- [x] 4.2 编写 Chat 路由测试
  - 在 `epsilon-boot/test/application/routers/test_chat_continue_router_unit.py` 中创建 FastAPI + fake `ChatServicePort`，验证 `POST /api/chat/sessions/{session_id}/continue` JSON 模式返回 `terminated_reason`、`can_continue`
  - 验证 stream 模式下 paused `assistant_done` SSE data 含 `finished=true`、`status="paused"`、`terminated_reason`、`can_continue`
  - 验证 `ContinuationUnavailableError` 返回 HTTP 409 和 `{code, message}`
  - 在既有 `test/application/routers/test_chat_router_hitl_unit.py` 或新文件中补回归，确认审批 resume 响应也透传新增字段且审批事件不被 paused 逻辑污染
  - **验证: 需求 2, 3, 5, 7, 8**

- [x] 4.3 扩展 Task HTTP 契约和继续路由
  - 在 `epsilon-boot/src/application/api/routers/task.py` 中导入 `BizException`、`ContinuationUnavailableError`、`TaskContinueRequest`
  - 给 `TaskExecuteResponseBody` 追加 `terminated_reason: str = "completed"`、`can_continue: bool = False`
  - 在 `execute_task(...)` 响应映射中填充 `result.terminated_reason` 和 `result.can_continue`
  - 新增 `class TaskContinueRequestBody(BaseModel)`，字段为 `model: str | None = None`
  - 新增 `@router.post("/api/task/sessions/{session_id}/continue", response_model=None) async def continue_task(...)`，调用 `service.continue_task(TaskContinueRequest(...))`
  - 新增或复用本地 `_biz_error_response(...)`，把 `ContinuationUnavailableError` 映射为 HTTP 409，`ValueError` 仍返回 400
  - _需求: 2, 4, 5, 7, 8_

- [x] 4.4 编写 Task 路由测试
  - 在 `epsilon-boot/test/application/routers/test_task_continue_router_unit.py` 中创建 FastAPI + fake `TaskAgentPort`，验证 `POST /api/task/sessions/{session_id}/continue` 返回 `status="paused"`、`terminated_reason`、`can_continue`
  - 在既有 `epsilon-boot/test/application/routers/test_task_router_unit.py` 中补充 `/api/task/execute` 的新增字段映射回归
  - 验证 `ContinuationUnavailableError` 返回 HTTP 409 和 `{code, message}`，`TaskContinueRequest(session_id="")` 对应 400
  - **验证: 需求 2, 4, 5, 7, 8**

- [x] 5.1 扩展前端 API 类型、继续请求和 SSE 解析
  - 在 `epsilon-client/src/lib/chat-api.ts` 中新增 `export type TerminationReason = "completed" | "max_rounds" | "token_budget_exceeded"`
  - 给 `ChatResponse` 追加 `prompt_id?: string`、`status?: "completed" | "paused" | "approval_required"`、`terminated_reason?: TerminationReason`、`can_continue?: boolean`
  - 给 `StreamChunk` 追加 `status?: "completed" | "paused" | "approval_required"`、`terminated_reason?: TerminationReason`、`can_continue?: boolean`
  - 给 `TaskExecuteResponse` 追加 `prompt_id: string`、`terminated_reason: TerminationReason`、`can_continue: boolean`
  - 修改 `streamChat(...)` SSE 解析，只把 JSON 中 `typeof finished === "boolean"` 的数据交给 `onChunk`，避免 `{"prompt_id": ...}` 被拼接进消息
  - 新增 `streamContinueChat(sessionId, model, onChunk, onDone, onError): AbortController`，请求 `POST /api/chat/sessions/{sessionId}/continue` 且 body 为 `{stream: true, model}`
  - 新增 `continueTask(sessionId: string, model?: string): Promise<TaskExecuteResponse>`，请求 `POST /api/task/sessions/{sessionId}/continue`
  - _需求: 2, 3, 4, 6, 7, 8_

- [x] 5.2 扩展 useChat 的暂停状态和继续动作
  - 在 `epsilon-client/src/hooks/use-chat.ts` 中导入 `streamContinueChat` 与 `TerminationReason`
  - 给 `ChatMessage` 追加 `status?: "completed" | "paused"`、`terminatedReason?: TerminationReason`、`canContinue?: boolean`
  - 给 `UseChatReturn` 追加 `continueLast: (model?: string) => void`
  - 修改 `sendMessage(...)` 的 chunk 处理：拼接 `chunk.delta_content ?? ""`，当 final chunk 带 `status="paused"` 时更新当前 assistant message 的 `status`、`terminatedReason`、`canContinue`
  - 实现 `continueLast(model?)`：找到最后一个 `role="assistant"` 且 `canContinue` 的消息，复用 `streamContinueChat`，不追加 user message，只追加或复用一个 assistant 响应占位，并在 loading 时禁止重复触发
  - _需求: 3, 5, 6, 7, 8_

- [x] 5. 检查点 — 后端路由与前端 API/Hook
  - 在 `epsilon-boot` 目录运行 `uv run --frozen pytest -q`
  - 在 `epsilon-client` 目录运行 `npm run lint`
  - 确认后端全量测试与前端 lint 全部通过；如出现非本期范围的既有失败，记录失败用例和原因后向用户确认

- [x] 5.3 实现聊天暂停态 UI 与继续按钮
  - 在 `epsilon-client/src/components/chat/message-bubble.tsx` 中扩展 `MessageBubbleProps`，新增 `isLoading: boolean`、`onContinue?: () => void`
  - 在助手消息 `message.status === "paused"` 时渲染可见暂停徽标，并用中文文案展示 `message.terminatedReason` 对应的可读原因
  - 当 `message.canContinue` 为 true 时渲染“继续”按钮，点击调用 `onContinue`，`isLoading` 时禁用
  - 在 `epsilon-client/src/components/chat/message-list.tsx` 中新增 `isLoading`、`onContinueLast` props，并传给 `MessageBubble`
  - 在 `epsilon-client/src/components/chat/chat-panel.tsx` 中从 `useChat` 解构 `continueLast`，用 `selectedModel || undefined` 包装为回调传给 `MessageList`
  - _需求: 3, 6, 7, 8_

- [x] 5.4 实现任务暂停态 UI 与继续按钮
  - 在 `epsilon-client/src/components/task/task-workspace.tsx` 中导入 `continueTask`
  - 新增 `handleContinue`：在 `result?.can_continue` 且非 loading 时调用 `continueTask(sessionId, selectedModel || undefined)` 并更新 `result`
  - 在结果区 `Status` 周围展示 paused 状态徽标，并把 `result.terminated_reason` 映射成中文可读原因
  - 当 `result.can_continue` 为 true 时渲染“继续任务”按钮，`isLoading` 时禁用，避免重复点击
  - 保持 completed / failed / human_intervention_required 的既有展示可读，不把继续不可用错误映射为任务失败
  - _需求: 4, 6, 7, 8_

- [x] 5.5 编写前端最低验证任务
  - 在 `epsilon-client` 中运行 `npm run lint`，验证 `chat-api.ts`、`use-chat.ts`、`message-bubble.tsx`、`message-list.tsx`、`chat-panel.tsx`、`task-workspace.tsx` 无 ESLint / TypeScript 静态错误
  - 手动检查 `streamChat` 解析逻辑只对包含 boolean `finished` 的 JSON 调用 `onChunk`，确认 `prompt_id` 事件不会进入聊天消息拼接
  - **验证: 需求 2, 3, 4, 6, 7, 8**

- [x] 6.1 编写一期端到端集成测试
  - 在 `epsilon-boot/test/application/test_long_task_phase1_integration.py` 中创建 fake Agent / fake session store / fake router service 集成测试
  - 覆盖 Chat 同步执行命中 `max_rounds` 后返回 `status="paused"`、`terminated_reason="max_rounds"`、`can_continue=True`
  - 覆盖 Chat 继续请求不追加 user message，继续完成后返回 `status="completed"`、`can_continue=False`
  - 覆盖 Task 执行命中 `max_rounds` 后返回 `status="paused"`，继续后返回 `success`
  - 覆盖 Chat 与 Task 的暂停上下文尾部均无新增空 `AssistantMessage(content="")`
  - **验证: 需求 1, 2, 3, 4, 5, 7, 8**

- [x] 6. 检查点 — 第一阶段全量验收
  - 在 `epsilon-boot` 目录运行 `uv run --frozen pytest -q`
  - 在 `epsilon-client` 目录运行 `npm run lint`
  - 对照 `docs/spec/long-task-continuation-phase1/requirement.md` 的需求 1-8 与本文件所有任务，确认无未覆盖项
  - 通过后等待 `spec_evaluator` 评审；只有评审 PASS 后才勾选已实现任务并进入最终 `summary.md`

## 备注

- 任务顺序遵循领域层 → Port → 基础设施适配器 → HTTP 路由 → 前端 → 集成验证。
- 本期不新增 `config.properties` 必填配置，不修改 `.env`，不引入新依赖。
- 暂停态的可继续判断统一以“最新消息是 `ToolMessage`”为准；Task 继续额外要求上下文存在 system message。
- `review-log.md` 仅在实现与评审阶段追加记录；本任务拆解阶段不写入评审结论。
