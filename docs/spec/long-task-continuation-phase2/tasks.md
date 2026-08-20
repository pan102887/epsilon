# 实现计划：Long Task Continuation Phase 2

## 概述

本计划按 DDD / 六边形分层自内向外推进：先补齐阶段一前端验证准入，再实现分段执行领域值对象、进展分析与停止决策 helper，随后扩展 Chat / Task 适配器、配置装配、HTTP/SSE 契约和前端展示，最后以属性测试、单元测试、路由测试和集成测试验证阶段二能力。

本期不新增 DDL、不新增后台 `run_id`、不引入持久化检查点、不引入新工作流运行时、不修改底层 ReAct Loop 的 `max_rounds` 判定策略。

## Tasks

- [x] 1.1 补齐阶段一前端静态验证准入
  - 在 `epsilon-client` 中确认本地依赖可用，优先使用仓库已有 `bun.lock` 对应的 `bun install --frozen-lockfile`，若项目实际选择 npm 则记录原因并使用等价 lockfile 策略
  - 运行有效的 `bun run lint` 或 `npm run lint`，确认不再调用系统 ESLint 6.4.0
  - 运行有效的 `bunx tsc --noEmit --pretty false` 或 `npx tsc --noEmit --pretty false`
  - 如验证暴露阶段一前端回归，暂停阶段二实现并先修复阶段一行为
  - _需求: 1, 10_

- [x] 1.2 编写阶段一准入验证记录测试/文档检查
  - 在 `epsilon-boot/test/application/test_long_task_phase2_frontend_contract_static.py` 中创建 pytest 静态测试
  - 验证 `docs/spec/long-task-continuation-phase1/summary.md` 和本期 `tasks.md` 均记录前端 ESLint / TypeScript 验证准入
  - 验证 `epsilon-client/package.json` 的 `lint` 脚本为 `eslint .`
  - **验证: 需求 1, 10**

- [x] 1.3 创建分段执行领域值对象
  - 在 `epsilon-boot/src/domain/agent/segmented_execution.py` 中创建模块级中文 docstring
  - 新增 `SegmentStopReason = Literal[...]`，取值为 `completed`、`auto_disabled`、`approval_required`、`max_continuations_reached`、`total_token_budget_reached`、`total_duration_budget_reached`、`consecutive_paused_limit`、`no_progress`、`repeated_tool_call`、`tool_boundary_unavailable`、`continue_precondition_failed`、`risk_gate_required`
  - 新增 `@dataclass(frozen=True) class SegmentExecutionPolicy`，字段和默认值严格按 `design.md`：`auto_continue_enabled: bool = False`、`max_continuations: int = 3`、`max_total_tokens: int | None = None`、`max_duration_seconds: float | None = None`、`max_consecutive_paused: int = 2`、`max_no_progress_segments: int = 2`、`max_repeated_tool_calls: int = 2`
  - 新增 `SegmentBudgetUsage.plus_segment(...) -> SegmentBudgetUsage` 与 `to_dict(self) -> dict[str, int | float]`
  - 新增 `SegmentProgressSnapshot.has_progress` 属性
  - 新增 `SegmentRunMetadata.to_http_dict(self) -> dict[str, object]`
  - 所有公开类/方法使用中文 docstring，校验负数、0 阈值和非法预算
  - _需求: 2, 4, 5, 6, 8, 10_

- [x] 1.4 编写分段执行领域值对象测试
  - 在 `epsilon-boot/test/domain/agent/test_segmented_execution_value_objects_unit.py` 中创建 pytest 用例
  - 验证 `SegmentExecutionPolicy` 默认值、非法 `max_continuations < 0`、非法 token/duration、非法阈值抛 `ValueError`
  - 验证 `SegmentBudgetUsage.plus_segment(...)` 累加段数、续跑次数、token、elapsed、连续 paused、无进展和重复工具计数
  - 验证 `SegmentProgressSnapshot.has_progress` 对 ToolMessage、trace、usage、final content 任一条件为真时返回 True
  - 在 `epsilon-boot/test/domain/agent/test_segment_execution_policy_property.py` 中使用 Hypothesis 覆盖合法/非法策略边界和 Property 4、5
  - **验证: 需求 2, 4, 5, 6, 8, 10**

- [x] 1.5 扩展 Chat_Response 与 Task_Result 分段元数据
  - 在 `epsilon-boot/src/domain/chat/value_objects.py` 中导入 `SegmentRunMetadata`
  - 给 `ChatResponseVO` 追加 `segment_metadata: SegmentRunMetadata = field(default_factory=SegmentRunMetadata)`
  - 在 `epsilon-boot/src/domain/task/value_objects.py` 中导入 `SegmentRunMetadata`
  - 给 `TaskResult` 追加 `segment_metadata: SegmentRunMetadata = field(default_factory=SegmentRunMetadata)`
  - 保持既有构造默认兼容，未传入分段字段时仍可构造阶段一响应
  - _需求: 2, 3, 8, 10_

- [x] 1.6 编写 Chat_Response / Task_Result 分段元数据测试
  - 在 `epsilon-boot/test/domain/chat/test_chat_response_segment_metadata_unit.py` 中验证 `ChatResponseVO` 默认 `segment_metadata.segment_index == 1`、`segment_count == 1`、`segment_stop_reason == "completed"`
  - 在 `epsilon-boot/test/domain/task/test_task_result_segment_metadata_unit.py` 中验证 `TaskResult` 默认分段元数据和显式 paused + `auto_disabled` 元数据
  - **验证: 需求 2, 8, 10**

- [x] 2.1 实现分段进展分析工具
  - 在 `epsilon-boot/src/infrastructure/agent/segmented_progress.py` 中创建模块级中文 docstring
  - 新增 `total_tokens_from_usage(usage: dict[str, int]) -> int`，优先取 `usage["total_tokens"]`，否则返回 `prompt_tokens + completion_tokens`，缺失 key 视为 0
  - 新增 `normalized_tool_call_digest(tool_name: str, arguments: str) -> str`，按设计使用 JSON 规范化和 sha256 hex
  - 新增 `analyze_segment_progress(...) -> tuple[SegmentProgressSnapshot, str | None]`，统计 `pre_message_count` 之后的新增消息、`ToolMessage` 数、trace 数、token_delta、final content 和最后一个工具调用 digest
  - 对 `AssistantMessage.tool_calls` 读取工具名和参数；没有工具调用时返回前一个 digest 不变或 `None`
  - _需求: 5, 6, 10_

- [x] 2.2 编写分段进展分析测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_segment_progress_unit.py` 中创建 pytest 用例
  - 验证 usage total token 计算、JSON 参数顺序不同但 digest 相同、非法 JSON 走原始字符串
  - 验证新增 `ToolMessage`、trace、usage、final content 均可产生 Progress_Signal
  - 在 `epsilon-boot/test/infrastructure/agent/test_segment_progress_property.py` 中使用 Hypothesis 覆盖 digest 稳定性和 Property 6
  - **验证: 需求 5, 6, 10**

- [x] 2.3 实现分段停止决策工具
  - 在 `epsilon-boot/src/infrastructure/agent/segmented_orchestration.py` 中创建模块级中文 docstring
  - 新增 `@dataclass(frozen=True) class SegmentContinuationDecision`，字段 `should_continue: bool`、`stop_reason: SegmentStopReason`
  - 新增 `decide_next_segment(...) -> SegmentContinuationDecision`，参数和判定顺序严格按 `design.md`
  - 判断顺序必须为 completed、approval、can_continue false、tool boundary unavailable、auto disabled、max continuations、token budget、duration budget、consecutive paused、no progress、repeated tool call、otherwise continue
  - _需求: 4, 5, 6, 7, 10_

- [x] 2.4 编写分段停止决策测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_segmented_orchestration_unit.py` 中创建 pytest 用例
  - 逐项验证 `completed`、`approval_required`、`continue_precondition_failed`、`tool_boundary_unavailable`、`auto_disabled`、`max_continuations_reached`、`total_token_budget_reached`、`total_duration_budget_reached`、`consecutive_paused_limit`、`no_progress`、`repeated_tool_call`、`risk_gate_required`
  - 使用 enabled policy + 未命中限制 + `can_continue=True` 验证 `should_continue=True`
  - **验证: 需求 4, 5, 6, 7, 10**

- [x] 3.1 扩展 Chat / Task 分段配置
  - 在 `epsilon-boot/src/infrastructure/chat/chat_config.py` 中给 `ChatConfig` 追加 `segment_auto_continue_enabled`、`segment_max_continuations`、`segment_max_total_tokens`、`segment_max_duration_seconds`、`segment_max_consecutive_paused`、`segment_max_no_progress_segments`、`segment_max_repeated_tool_calls`
  - 在 `ChatConfig` 中新增 `def to_segment_policy(self) -> SegmentExecutionPolicy`
  - 在 `epsilon-boot/src/infrastructure/task/task_config.py` 中给 `TaskAgentConfig` 追加同名 `segment_*` 字段并新增 `to_segment_policy(...)`
  - 在 `epsilon-boot/config.properties` 中新增 `CHAT_SEGMENT_*` 和 `TASK_AGENT_SEGMENT_*` 默认键，自动续跑默认 `false`，token/duration 默认 `0`
  - 非法阈值通过 `ConfigurationError` 或领域值对象 `ValueError` fail-fast；`0` token/duration 映射为 `None`
  - _需求: 1, 4, 5, 10_

- [x] 3.2 编写分段配置测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_segment_config.py` 中验证默认 policy、config.properties 读取、`0 -> None` 映射和非法配置
  - 在 `epsilon-boot/test/infrastructure/task/test_task_segment_config.py` 中验证默认 policy、config.properties 读取、`0 -> None` 映射和非法配置
  - 在 `epsilon-boot/test/application/test_long_task_phase2_frontend_contract_static.py` 中补充验证 `config.properties` 包含全部新增默认键
  - **验证: 需求 1, 4, 5, 10**

- [x] 3. 检查点 — 领域值对象、进展决策与配置
  - 在 `epsilon-boot` 目录运行 `uv run --frozen pytest -q`
  - 确认领域、基础设施 helper 和配置测试全部通过；如出现非本期范围的既有失败，记录失败用例和原因后向用户确认

- [x] 4.1 扩展容器装配与 Port 类型声明
  - 在 `epsilon-boot/src/application/container_config.py` 中把 `chat_config.to_segment_policy()` 传入 `ChatServiceAdapter(segment_policy=...)`
  - 在 `_create_task_agent()` 中把 `task_agent_config.to_segment_policy()` 传入 `TaskAgentAdapter(segment_policy=...)`
  - 在 `epsilon-boot/src/domain/chat/ports.py` 中为 `ChatServicePort` 增加可选结构化流分段方法声明：`def stream_segmented_chat_events(self, request: "ChatRequestVO") -> AsyncIterator["AgentStreamEvent"]: ...` 和 `def stream_segmented_continue_chat_events(self, request: "ChatContinueRequestVO") -> AsyncIterator["AgentStreamEvent"]: ...`
  - 保持既有 `chat`、`continue_chat`、`stream_chat_events`、`stream_continue_chat_events` 签名不变
  - _需求: 2, 3, 4, 8, 10_

- [x] 4.2 实现 Chat 同步分段编排
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中导入 `SegmentExecutionPolicy`、`SegmentBudgetUsage`、`SegmentRunMetadata`、`analyze_segment_progress`、`decide_next_segment`
  - 修改 `ChatServiceAdapter.__init__(..., segment_policy: SegmentExecutionPolicy | None = None)`，默认 `SegmentExecutionPolicy()`
  - 新增 `async def _run_segmented_chat(self, request: ChatRequestVO) -> ChatResponseVO`
  - 新增 `async def _continue_segmented_chat(self, request: ChatContinueRequestVO, *, initial_response: ChatResponseVO | None = None) -> ChatResponseVO`
  - 修改 `chat(...)` 的 tool calling 路径调用 `_run_segmented_chat(...)`；直接 LLM 路径只返回默认 `SegmentRunMetadata`
  - 修改 `continue_chat(...)` 调用 `_continue_segmented_chat(...)`
  - 每段结束后保存上下文、分析进展、累计 usage/elapsed、调用 `decide_next_segment(...)`，需要继续时复用 `_run_agent_on_existing_context(...)`，不得追加 user message 或改变 `self._max_tool_rounds`
  - _需求: 2, 3, 4, 5, 6, 7, 10_

- [x] 4.3 编写 Chat 同步分段测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_segmented_execution_unit.py` 中创建 fake Agent / session store
  - 覆盖 auto disabled：首段 `max_rounds` 后 `segment_stop_reason="auto_disabled"`、`segment_count=1`
  - 覆盖 auto enabled：`max_rounds -> completed` 返回 completed，`segment_count=2`、`auto_continue_attempted=True`、user message 数不增加
  - 覆盖 `max_continuations_reached`、token budget、duration budget、审批停止，且每段 AgentConfig.max_rounds 等于 `self._max_tool_rounds`
  - 在 `epsilon-boot/test/infrastructure/chat/test_segmented_chat_context_property.py` 中用 Hypothesis 覆盖 Property 1、2、4、5
  - **验证: 需求 2, 3, 4, 5, 6, 7, 10**

- [x] 4.4 实现 Chat 流式分段事件
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中新增 `def stream_segmented_chat_events(self, request: ChatRequestVO) -> AsyncIterator[AgentStreamEvent]`
  - 新增 `def stream_segmented_continue_chat_events(self, request: ChatContinueRequestVO) -> AsyncIterator[AgentStreamEvent]`
  - 每段先透传 `assistant_delta`、`approval_required`、`assistant_done` 等既有事件；段结束后额外 yield 一个 `AgentStreamEvent(kind="assistant_done", metadata={"event_type": "segment_done", ...})` 或等价控制事件，metadata 包含 `segment_index`、`segment_count`、`segment_stop_reason`、`budget_usage`
  - 若 `decide_next_segment(...)` 返回 continue，下一段调用 `stream_continue_chat_events(ChatContinueRequestVO(...))`
  - 若停止，最终 `assistant_done` metadata 包含阶段一 final payload 字段和分段字段
  - 确保审批事件停止自动续跑且不保存空 assistant
  - _需求: 3, 5, 6, 7, 8, 9, 10_

- [x] 4.5 编写 Chat 流式分段测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_segmented_stream_unit.py` 中创建 fake `AgentPort.run_events`
  - 验证 `segment_done` 控制事件包含 `event_type="segment_done"`、`finished=False`、分段字段和预算字段
  - 验证 `max_rounds -> completed` 流式自动续跑产生两段边界和最终 completed payload
  - 验证审批事件停止自动续跑，不被映射为 paused
  - 验证控制事件不改变上下文 user message 数，覆盖 Property 2、7
  - **验证: 需求 3, 5, 6, 7, 8, 9, 10**

- [x] 5.1 重构 TaskAgentAdapter 单段执行入口
  - 在 `epsilon-boot/src/infrastructure/task/task_agent_adapter.py` 中新增 `async def _execute_single_task_segment(self, task: Task) -> TaskResult`
  - 新增 `async def _continue_single_task_segment(self, request: TaskContinueRequest) -> TaskResult`
  - 将现有 `execute(...)` 的单段逻辑移动到 `_execute_single_task_segment(...)`
  - 将现有 `continue_task(...)` 的单段逻辑移动到 `_continue_single_task_segment(...)`
  - 保持原有暂停翻译、工具边界校验、trace 提取、usage、上下文保存和异常处理行为不变
  - _需求: 2, 3, 7, 10_

- [x] 5.2 实现 Task 分段编排
  - 在 `epsilon-boot/src/infrastructure/task/task_agent_adapter.py` 中导入分段值对象、进展分析和决策 helper
  - 修改 `TaskAgentAdapter.__init__(..., segment_policy: SegmentExecutionPolicy | None = None)`，默认 `SegmentExecutionPolicy()`
  - 新增 `async def _run_segmented_task(self, task: Task) -> TaskResult`
  - 新增 `async def _continue_segmented_task(self, request: TaskContinueRequest) -> TaskResult`
  - 修改 `execute(...)` 调用 `_run_segmented_task(...)`，`continue_task(...)` 调用 `_continue_segmented_task(...)`
  - 当 `task.session_id is None` 时只执行首段，返回默认分段元数据，不自动续跑
  - 自动续跑时构造 `TaskContinueRequest(session_id=task.session_id, model=task.model)`，不得追加 task goal/user message
  - 合并所有段的 `trace` 和 `usage`，最终 `TaskResult.segment_metadata` 反映累计段状态
  - _需求: 2, 3, 4, 5, 6, 7, 10_

- [x] 5.3 编写 Task 分段执行测试
  - 在 `epsilon-boot/test/infrastructure/task/test_task_segmented_execution_unit.py` 中创建 fake Agent / ToolRegistry / session store
  - 覆盖无 `session_id` 时不自动续跑
  - 覆盖 auto disabled、`max_rounds -> completed`、多段 trace/usage 合并、`max_rounds` 不增加
  - 覆盖 `max_continuations_reached`、token budget、duration budget、审批或 human intervention 保持不被自动吞掉
  - **验证: 需求 2, 3, 4, 5, 6, 7, 10**

- [x] 5.4 编写 Task 工具边界与反循环属性测试
  - 在 `epsilon-boot/test/infrastructure/task/test_task_continue_tool_boundary_property.py` 中使用 Hypothesis 覆盖 continued Agent_Run_Segment 的工具 schema 不宽于 `SystemMessage.metadata["task_allowed_tool_names"]`
  - 在 `epsilon-boot/test/infrastructure/task/test_task_segmented_stop_reason_unit.py` 中覆盖 `tool_boundary_unavailable`、`no_progress`、`repeated_tool_call` 停止
  - 验证工具边界不可重建时不调用 `AgentPort.run`
  - 覆盖 Property 3、5、6
  - **验证: 需求 3, 5, 6, 7, 10**

- [x] 6.1 扩展 HTTP 响应模型与映射 helper
  - 在 `epsilon-boot/src/application/api/routers/chat.py` 中新增 `class BudgetUsageBody(BaseModel)`，字段为 `segment_count`、`continuation_count`、`total_tokens`、`elapsed_ms`、`consecutive_paused_count`、`no_progress_count`、`repeated_tool_call_count`
  - 给 `ChatResponseBody` 追加 `segment_index: int = 1`、`segment_count: int = 1`、`auto_continue_attempted: bool = False`、`segment_stop_reason: str = "completed"`、`budget_usage: BudgetUsageBody = BudgetUsageBody()`
  - 在 `epsilon-boot/src/application/api/routers/task.py` 中新增或复用同结构 `BudgetUsageBody`，给 `TaskExecuteResponseBody` 追加同样字段
  - 新增本地映射 helper，例如 `_budget_usage_body(metadata: SegmentRunMetadata) -> BudgetUsageBody` 和 `_segment_fields(...)`
  - 如 `epsilon-boot/src/application/routers/chat.py`、`epsilon-boot/src/application/routers/task.py` 仍作为兼容镜像存在，同步修改
  - _需求: 2, 8, 10_

- [x] 6. 检查点 — Chat/Task 分段编排与 HTTP 模型
  - 在 `epsilon-boot` 目录运行 `uv run --frozen pytest -q`
  - 确认 Chat、Task、配置、领域和 HTTP 模型相关测试全部通过；如出现非本期范围的既有失败，记录失败用例和原因后向用户确认

- [x] 6.2 扩展 Chat 路由 SSE 分段契约
  - 在 `epsilon-boot/src/application/api/routers/chat.py` 的同步 `/api/chat`、审批恢复、同步 continue 响应映射中填充分段字段
  - 在 `/api/chat` stream 路径中优先调用 `service.stream_segmented_chat_events(chat_request)`，不存在该方法时回退既有 `stream_chat_events`
  - 在 `/api/chat/sessions/{session_id}/continue` stream 路径中优先调用 `service.stream_segmented_continue_chat_events(continue_request)`
  - 修改 `_event_generator`：当 event metadata `event_type == "segment_done"` 时输出 `{"event_type":"segment_done","finished":false,...}` 控制 payload，不作为最终 chunk
  - 最终 `assistant_done` payload 保留阶段一 `finished=true`、`status`、`terminated_reason`、`can_continue`，并追加 `segment_index`、`segment_count`、`auto_continue_attempted`、`segment_stop_reason`、`budget_usage`
  - _需求: 2, 7, 8, 9, 10_

- [x] 6.3 扩展 Task 路由分段契约
  - 在 `epsilon-boot/src/application/api/routers/task.py` 中修改 `/api/task/execute` 和 `/api/task/sessions/{session_id}/continue` 响应映射，填充分段字段
  - 保持 `ContinuationUnavailableError` 仍返回 HTTP 409，`ValueError` 仍返回 HTTP 400
  - 如兼容镜像路由存在，同步修改 `epsilon-boot/src/application/routers/task.py`
  - _需求: 2, 7, 8, 10_

- [x] 6.4 编写 Chat / Task 路由分段测试
  - 在 `epsilon-boot/test/application/routers/test_chat_segmented_router_unit.py` 中创建 FastAPI + fake `ChatServicePort`
  - 验证同步 `/api/chat`、同步 continue、stream `/api/chat`、stream continue 均透传分段字段和预算字段
  - 验证 `segment_done` 控制 payload `finished=false` 且最终 payload `finished=true`
  - 在 `epsilon-boot/test/application/routers/test_task_segmented_router_unit.py` 中创建 fake `TaskAgentPort`，验证 task execute / continue 分段字段映射和 409 保持不变
  - 覆盖 Property 7、8
  - **验证: 需求 2, 7, 8, 9, 10**

- [x] 7.1 扩展前端 API 类型与 SSE 解析
  - 在 `epsilon-client/src/lib/chat-api.ts` 中新增 `export type SegmentStopReason = ...`，取值与后端 `SegmentStopReason` 一致
  - 新增 `export interface BudgetUsage` 和 `export interface SegmentMetadata`
  - 让 `ChatResponse`、`TaskExecuteResponse`、`StreamChunk` 扩展分段字段
  - 修改 `readStream(...)`：`event_type === "segment_done"` 的 payload 交给 `onChunk` 更新元数据，但 `delta_content` 默认为 `""` 且不得拼接为正文
  - 保持只处理 `typeof parsed.finished === "boolean"` 的 payload，继续忽略 `prompt_id` 等非 chunk 数据
  - _需求: 8, 9, 10_

- [x] 7.2 扩展 useChat 分段状态
  - 在 `epsilon-client/src/hooks/use-chat.ts` 中扩展 `ChatMessage`：`segmentIndex?: number`、`segmentCount?: number`、`autoContinueAttempted?: boolean`、`segmentStopReason?: SegmentStopReason`、`budgetUsage?: BudgetUsage`
  - 修改 `applyChunkToAssistant(...)`：当 `chunk.event_type === "segment_done"` 或 chunk 包含分段字段时，只更新当前 assistant message 的分段元数据；只有普通 delta 才追加 `delta_content`
  - 当 streaming final payload 带 paused / completed 状态时保持阶段一状态更新逻辑
  - `continueLast(...)` 继续禁用旧 paused message 的 `canContinue`，新 assistant 占位继承/展示后续分段状态
  - _需求: 8, 9, 10_

- [x] 7.3 扩展聊天分段 UI
  - 在 `epsilon-client/src/components/chat/message-bubble.tsx` 中展示段状态、自动续跑状态、可读 `segmentStopReason` 和预算摘要
  - 在 `epsilon-client/src/components/chat/message-list.tsx` 和 `epsilon-client/src/components/chat/chat-panel.tsx` 中透传新增 message 字段，不新增说明性大段文本
  - 自动续跑中展示轻量状态，不触发用户动作；`canContinue` 为 true 时保留阶段一继续按钮
  - _需求: 8, 9, 10_

- [x] 7.4 扩展任务分段 UI
  - 在 `epsilon-client/src/components/task/task-workspace.tsx` 中读取 `TaskExecuteResponse` 的分段字段
  - 在任务结果区展示当前段数、可读 `segment_stop_reason`、`budget_usage` 摘要和 `auto_continue_attempted`
  - 保持 `continueTask(...)` 人工继续按钮语义；自动续跑默认关闭时前端不得主动调用继续接口
  - _需求: 7, 8, 9, 10_

- [x] 7.5 编写前端分段验证任务
  - 在 `epsilon-client` 中运行 `bun run lint` 或 `npm run lint`
  - 在 `epsilon-client` 中运行 `bunx tsc --noEmit --pretty false` 或 `npx tsc --noEmit --pretty false`
  - 手动检查 `readStream(...)` 对 `event_type="segment_done"` 的 payload 不追加正文，确认 Property 8
  - **验证: 需求 1, 8, 9, 10**

- [x] 8.1 编写阶段二端到端集成测试
  - 在 `epsilon-boot/test/application/test_long_task_phase2_integration.py` 中创建 fake Agent / fake session store / fake router service 集成测试
  - 覆盖 Chat 同步 `max_rounds -> max_rounds -> completed`
  - 覆盖 Task 同步 `max_rounds -> completed`
  - 覆盖自动续跑关闭、最大续跑次数、token budget、无进展、重复工具调用、审批停止
  - 验证每个继续段不追加 user message，AgentConfig.max_rounds 不变
  - **验证: 需求 2, 3, 4, 5, 6, 7, 10**

- [x] 8.2 编写阶段二静态契约测试
  - 在 `epsilon-boot/test/application/test_long_task_phase2_frontend_contract_static.py` 中补充静态测试
  - 验证 `docs/spec/long-task-continuation-phase2/requirement.md`、`design.md`、`tasks.md` 均声明不引入后台 `run_id`、checkpoint 或新 workflow runtime
  - 验证 `epsilon-client/src/lib/chat-api.ts` 的 `SegmentStopReason` 取值包含后端设计所有 stop reason
  - 验证 `epsilon-boot/config.properties` 含全部 `CHAT_SEGMENT_*` 与 `TASK_AGENT_SEGMENT_*` 键
  - **验证: 需求 1, 4, 8, 9, 10**

- [x] 8. 检查点 — 第二阶段全量验收
  - 在 `epsilon-boot` 目录运行 `uv run --frozen pytest -q`
  - 在 `epsilon-client` 目录运行 `bun run lint` 或 `npm run lint`
  - 在 `epsilon-client` 目录运行 `bunx tsc --noEmit --pretty false` 或 `npx tsc --noEmit --pretty false`
  - 对照 `requirement.md` 的需求 1-10、`design.md` 的 Property 1-8 与本文件所有任务，确认无未覆盖项
  - 通过后等待 `spec_evaluator` 评审；只有评审 PASS 后才勾选已实现任务并进入最终 `summary.md`

## 备注

- 任务顺序遵循准入验证 → 领域层 → 基础设施 helper → 配置/容器 → Chat → Task → HTTP/SSE → 前端 → 集成验证。
- 本期不新增数据库迁移、DDL、后台 run、持久化 checkpoint 或新工作流依赖。
- 自动续跑默认关闭；启用后仍通过配置、预算、进展、风险门禁和工具边界保护。
- `review-log.md` 仅在实现与评审阶段追加记录；本任务拆解阶段不写入评审结论。
