# 长任务 Agent 执行能力演进路线

## 当前状态

阶段一至阶段六的主干能力均已完成本地验收，且 `docs/spec/long-task-runtime-convergence/` 已完成 P0/P1/P2 收敛修复。当前长任务能力已从“暂停后可人工继续”“请求内有限分段执行”“后台 Run 管理”“持久化检查点恢复”“智能调度与护栏”推进到“Run 事件事实源 + workflow 协作治理 + 保守 child run 编排”：

- 阶段一已通过独立 spec evaluator 评审，覆盖 Chat、Task、HTTP/SSE、前端 UI 与测试。
- 阶段二已实现请求内分段执行、全局预算、自动续跑、人工续跑保留、停止原因、HTTP/SSE/前端分段展示，并修复 SSE 中间段、重复工具 digest、Risk_Gate 等收敛缺口。
- 阶段三已实现 `RunApplicationService`、worker、file/Redis store、事件流、TUI/agent adapter、FastAPI `/api/runs*` 薄 adapter 和 Web Run View。
- 阶段四已实现持久化 checkpoint recovery、tool ledger、防重复副作用执行、file/Redis checkpoint store、恢复事件与 Run View 展示。
- 阶段五已实现任务分类、统一 guardrail 决策、工具风险分级、运行时统计、默认 observe 配置、critical 工具 enforce 阻断、Run/API/TUI/Web 护栏字段透传。
- 阶段六已实现轻量 workflow 选择、phase 编排、协作摘要、role capability 最小权限治理、workflow handoff 可观测、默认关闭的保守 child run 链接/等待/reconciliation。
- 最近一次后端验证结果（2026-07-10，P0 adapter 瘦身后）：`.venv/bin/ruff check src test` 通过，`.venv/bin/pyright src/domain src/application` 为 0 errors / 0 warnings，`PYTHONPATH=src .venv/bin/pytest -q` 为 `3128 passed, 2 skipped`。前端验证未在本轮重跑，历史通过记录不再作为当前最新状态表述。

当前已具备后台 `run_id`、任务队列、状态查询、事件流、取消、继续、审批恢复入口、checkpoint recovery、防重复工具结果复用、guardrail Run 事件闭环、`guardrail_summary` 单一事实源、workflow/collaboration snapshot 摘要、role capability 审批兜底和 child run 保守恢复语义。默认配置保持兼容：guardrail runtime convergence 默认开启，workflow role capability 与 child run 默认关闭。

## 背景

当前项目通过 `CHAT_MAX_TOOL_ROUNDS`、`TASK_AGENT_MAX_ROUNDS` 等配置限制单次 Agent Loop 的最大轮次。该限制可以防止失控循环和成本失控，但对复杂任务而言，单次执行段经常不足以完成完整工作。

后续演进方向不是简单放大单次轮次上限，而是将轮次限制转化为可控的执行节拍：任务可以分段推进、命中边界后继续、关键状态可恢复，并在总成本和风险边界内完成长流程。

## 阶段一：可见化与可继续

目标：先让系统明确区分“任务完成”和“到达阶段边界”。

状态：已实现，第二轮独立 evaluator 复核 PASS；阶段二实现前的前端 lint / TypeScript 准入验证已补齐。

核心方向：

- 显式暴露 Agent 终止原因，例如 `completed`、`max_rounds`、`token_budget_exceeded`。
- API、前端、任务结果不再把 `max_rounds` 当成普通完成。
- 当任务因轮次限制或 token 预算边界暂停时，提供继续执行能力。
- 保留当前单段轮次限制，避免单纯调大上限带来失控风险。

已落地能力：

- `ChatResponseVO` / `TaskResult` 暴露 `terminated_reason` 与 `can_continue`，暂停态使用 `status="paused"` / `TaskStatus.PAUSED`。
- `/api/chat/sessions/{session_id}/continue` 支持同步与流式继续；继续时加载既有 `ConversationContext`，不追加新的 user message。
- `/api/task/sessions/{session_id}/continue` 支持任务继续；继续时通过 `SystemMessage.metadata["task_allowed_tool_names"]` 保留原始工具访问边界，缺失、非法或不可重建时拒绝继续。
- 暂停时保存真实工具结果上下文，不追加 `Empty_Final_Assistant_Message`。
- SSE final payload 在 paused 时携带 `finished=true`、`status="paused"`、`terminated_reason`、`can_continue`；前端过滤非 chunk 控制事件，避免把 `prompt_id` 拼入消息。
- 前端 Chat/Task 展示暂停原因，并在 `can_continue=true` 时提供继续动作。

阶段一剩余风险：

- Task 全量工具边界以 `task_allowed_tool_names: null` 表示；若运行时工具注册表发生热变更，继续时“当前全量”可能不同于原始全量。

阶段价值：用户能明确知道任务尚未完成但可以继续，系统也具备后续自动续跑的基础信号。

## 阶段二：分段执行

目标：把一次长任务拆成多个可控执行段。

状态：已实现并通过修复后本地验收。阶段二 spec、summary 与 review-log 位于 `docs/spec/long-task-continuation-phase2/`。

准入条件：阶段一独立 evaluator 已 PASS；前端静态验证已具备可信结果。

核心方向：

- 引入执行段概念，每段仍有独立轮次限制。
- 在外层增加总轮次、总 token、总耗时、最大续跑次数等全局限制。
- 支持自动续跑：系统在判断任务仍有进展时进入下一段。
- 支持人工续跑：高风险、长耗时或高成本场景由用户确认。
- 明确自动续跑只复用阶段一的 Continue_Request 语义，不追加 user message，不调大单段轮次上限。
- 为自动续跑增加停止条件：连续暂停次数、无新工具结果、重复工具调用、总 token/耗时预算。

阶段价值：任务可以自然延续，但始终运行在明确预算和边界内。

已落地能力：

- 新增 `SegmentExecutionPolicy`、`SegmentBudgetUsage`、`SegmentProgressSnapshot`、`SegmentRunMetadata` 等分段执行值对象。
- Chat/Task 同步路径支持请求内自动续跑；每段仍使用既有 `CHAT_MAX_TOOL_ROUNDS` / `TASK_AGENT_MAX_ROUNDS`，不放大单段轮次限制。
- 新增 `CHAT_SEGMENT_*`、`TASK_AGENT_SEGMENT_*` 配置，自动续跑默认关闭，支持最大续跑次数、总 token、总耗时、连续暂停、无进展、重复工具调用等停止条件。
- 自动续跑沿用阶段一 Continue_Request 语义，不追加新的 user message；Task 继续段保留原始工具访问边界。
- Chat SSE 使用 `segment_done` 作为段边界控制事件，普通 `finished=true` 仅在整个分段运行结束时发送。
- Task 重复工具调用检测使用工具名 + 规范化参数 digest，避免 JSON 参数顺序差异导致漏判。
- Risk_Gate 命中时停止自动续跑并暴露 `risk_gate_required`，在继续前置条件满足时保留人工继续语义。
- HTTP JSON、SSE payload 与前端 Chat/Task UI 透传并展示段数、停止原因、预算摘要。

阶段二剩余风险：

- 阶段二仍是请求内有限分段；长耗时任务仍可能受 HTTP 连接、进程生命周期和客户端断连影响。
- 阶段二只做基础风险门禁与反循环停止；更细粒度的工具风险分级、金额预算、动态任务分类仍属于阶段五。
- 阶段二不提供持久化检查点；服务重启、worker 中断或进程崩溃后的执行恢复仍属于阶段四，网络断开属于后续 Run 观察恢复能力覆盖范围。

## 阶段三：长任务运行时

目标：从“HTTP 请求内完成任务”演进为“后台 Run 管理”。

当前阶段三实现边界：

- 本期提供进程内后台 `RunApplicationService`、worker、store、事件流、TUI/agent adapter、FastAPI adapter 和 Web Run View 能力；checkpoint recovery 已在阶段四补齐，服务重启或 worker lease 过期后会先按 checkpoint/ledger 做 bounded recovery，仍无法确认命运时才进入 `lost` 或保守失败态。
- TUI/agent 应用通过共享应用服务接入 Run runtime，不经由 FastAPI HTTP endpoint 或 `/api/runs` 自调用；所有 adapter 只负责请求、命令和视图映射。
- FastAPI/Web 是薄 adapter，核心质量门槛仍是 `RunApplicationService`、worker、store 和 TUI/agent 应用体验。
- 阶段三不新增 Celery、Temporal、LangGraph、Dapr Workflow 等外部 workflow runtime 依赖，也不把当前实现升级为外部 durable workflow engine。现有同步 Chat/Task 与阶段一/二 continue 入口保持兼容。

核心方向：

- 将复杂任务抽象为 `run_id`。
- 请求只负责创建任务，执行在后台持续推进。
- 前端通过 SSE、轮询或事件流查看进度。
- 支持暂停、继续、取消、查看历史、查看当前状态。
- 区分聊天型任务和长任务型执行，避免所有工作都挤在同步请求链路中。

阶段价值：提升长任务稳定性，适配代码修改、资料整理、批量生成、跨工具协作等流程。

已落地能力：

- `domain/run` 定义 Run 值对象、状态机、异常、store/event/progress Port。
- `application/run` 提供 adapter-neutral `RunApplicationService` 和 `RunExecutionCoordinator`。
- `infrastructure/run` 提供本地文件与 Redis Run/Event Store、RunWorker、RunWorkerManager 和配置模型。
- FastAPI `/api/runs*` 支持创建、查询、事件轮询、SSE、取消、继续和审批恢复。
- TUI slash command 支持 `/run chat`、`/run task`、`/runs`、`/run status`、`/run watch`、`/run continue`、`/run approve`、`/run cancel`。
- Web 前端提供显式“后台运行”入口和 Run View，支持 `replay_expired` fallback。

## 阶段四：持久化检查点

目标：任务中断后可以恢复，而不是从头重跑。

核心方向：

- 在关键边界保存检查点：模型调用后、工具执行后、审批中断前后、执行段结束时。
- 检查点保存上下文、轮次、usage、trace、工具调用结果和当前任务状态。
- 支持服务重启、worker 中断或租约过期后的执行恢复；网络断开和前端刷新属于观察恢复，继续沿用 Run 状态查询、事件 replay 与 polling fallback。
- 避免重复执行已经成功的工具调用，尤其是写文件、发请求、发消息等有副作用操作；阶段四不承诺外部副作用 exactly-once。

阶段价值：让长任务从“能跑”走向“可靠跑”。

## 阶段五：智能调度与护栏

目标：让系统知道什么时候该继续，什么时候该停。

状态：已实现并通过本地验收。阶段五 spec、summary 与 review-log 位于 `docs/spec/long-task-continuation-phase5/`；后续运行时收敛修复位于 `docs/spec/long-task-runtime-convergence/`。当前 guardrail 已形成 Run 事件闭环与 `guardrail_summary` 单一事实源，P1 统计基于真实模型 usage、工具结果、上下文增长、耗时与价格配置；缺失价格时仅标记 `cost_available=false`，不改变 allow/observe/stop/require_approval 语义。

核心方向：

- 增加进展判断：是否产生新信息、新文件、新决策或有效工具结果。
- 增加反循环检测：重复工具调用、重复失败、无效重试、上下文膨胀。
- 增加成本护栏：按 token、金额、耗时、工具风险等级控制执行。
- 对高风险工具、长时间执行、连续失败场景引入人工确认。
- 根据任务类型动态选择执行策略，例如短问答、普通工具任务、长任务、批处理任务。

阶段价值：避免机械续跑，将长任务执行控制在安全、成本和质量边界内。

## 阶段六：工作流化与多 Agent 协作

目标：把复杂任务从单一 ReAct 循环升级为更结构化的执行体系。

状态：轻量 workflow 运行时已实现并通过本地验收；不引入 Celery、Temporal、LangGraph、Dapr Workflow 等外部 durable workflow engine。

已落地能力：

- 静态 workflow 定义、创建 Run 时 workflow selection、phase started/completed/failed 事件与 `WorkflowRunState`。
- 常见任务 workflow（research、code_change、report、batch_processing）的阶段化结构。
- collaboration recorder 与 `collaboration_summary.latest_steps` 规范 schema，旧 `recent_steps` 仅兼容读取。
- workflow 级 handoff state 与 `WORKFLOW_HANDOFF_RECORDED` 事件；真实 `handoff_to_agent` 成功转交也会写入 workflow handoff 状态。
- role capability 最小权限治理，默认关闭；开启后真实 ReAct 工具、delegation/handoff、child-run 创建前均按当前 active role 判断能力，越权时写 `ROLE_CAPABILITY_REJECTED` 并复用 HITL 审批兜底。
- child run 保守编排，默认关闭；显式启用时创建/链接真实 child Run、父 Run 等待前保存 checkpoint、恢复时从 `CHILD_RUN_RECONCILED` 或保守失败态继续，不扩大 exactly-once 承诺。

阶段价值：提升复杂任务成功率，并降低长期维护成本，同时保持默认兼容和可灰度治理。

## 推荐推进顺序

1. 持续回归后台 Run、checkpoint recovery、guardrail 事件闭环和 Web/TUI 展示。
2. 在 observe 模式下观察 guardrail runtime stats 与 `guardrail_summary`，再按需灰度 enforce / require_approval。
3. 按 workflow 逐步开启 `RUN_WORKFLOW_ROLE_CAPABILITY_ENABLED`，补齐角色能力声明与人工审批兜底流程。
4. 仅对明确需要父子运行隔离的 workflow 灰度 `RUN_WORKFLOW_CHILD_RUN_ENABLED`，重点验证 waiting/reconciliation/recovery 语义。

## 当前优先级

短期最高优先级从“补齐能力”转为“生产化硬化”：保持 P0/P1/P2 回归稳定，补充更接近生产的 workflow integration 场景，并完善观测/告警/发布手册。

- P0：持续回归阶段二 SSE final、阶段四 checkpoint recovery、guardrail observe 兼容性和 Run 事件 replay，防止长任务基础语义回退。
- P0：跟踪 `RunObservationStorePort` file/Redis 原子写入、summary cursor 同步、owner/lease 冲突处理。
- P1：扩展 guardrail pricing 与成本展示覆盖更多模型，完善运行时统计告警阈值。
- P1：补充生产式 workflow 集成测试：在 `WorkflowRunOrchestrator` 启用的真实 phase 内触发 `handoff_to_agent`，验证事件/快照合并语义。
- P2：按业务 workflow 梳理 role capability 声明，灰度 child run 并记录 reconciliation 失败处理经验。

## 总体原则

保留单段轮次限制，把它从“任务失败原因”转化为“任务执行节拍”。真正要解决的问题不是取消限制，而是让任务可以在可控边界内持续推进，并在必要时恢复、暂停、继续或终止。
