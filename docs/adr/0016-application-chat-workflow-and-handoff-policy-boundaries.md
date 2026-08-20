---
status: Accepted
date: 2026-07-08
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0016：应用层 Chat workflow/service 与 Handoff policy 边界收敛

## 背景与问题（Context）

`ddd-infrastructure-logic-remediation` 发现当前多处用例编排和纯业务判定过度集中在 `infrastructure` 层，削弱了既定 DDD 分层约束（`application -> domain <- infrastructure`）与 SRP。前置 Run worker 与 API presenter 切片已收敛部分反向依赖和 serializer 边界；Wave 3 继续处理 Chat 与 Handoff 两个剩余高风险点。

`infrastructure/chat/chat_service_adapter.py::ChatServiceAdapter` 原本同时承载会话加载、`session_id` 写入、系统 prompt 幂等注入、用户消息追加、上下文保存与会话索引刷新、`continue_chat` 前置条件、审批恢复 load / 校验 / consume / resume 顺序等应用用例编排；同一适配器还承载模型解析、direct LLM path、流式事件包装、`AgentStreamEvent` / `StreamingChunk` 适配、approval metadata 和分段 stream / metadata 等技术适配职责。职责混杂导致直接 adapter 单测需要构造大量无关前置条件，也让后续 agent 容易把技术 concerns 误迁入 application 或 domain。

`infrastructure/agent/handoff_to_agent_tool.py::HandoffToAgentTool` 中也混有两类不同职责：一类是可脱离运行时的 handoff depth / workflow handoff count 纯判定；另一类是读取 ContextVar、调用 `DelegationPort`、构造 `ToolExecutionResult`、记录 workflow collaboration 事件和抛出 `HandoffPerformed` 的工具适配职责。Wave 3 已抽取：

- `epsilon-boot/src/application/chat/session_context_workflow.py::ChatSessionContextWorkflow`
- `epsilon-boot/src/application/chat/chat_application_service.py::ChatApplicationService`
- `epsilon-boot/src/domain/agent/handoff_policy.py::HandoffDecision` / `decide_handoff`

这些抽象是长期一等边界，触发 `docs/steering/adr.md` 的 ADR gate。本 ADR 只记录行为等价边界收敛，不引入领域事件，不重开 Agent Loop P2 第三片，也不改变工具并发骨架归属。

## 决策（Decision）

我们接受 `ChatSessionContextWorkflow` 作为应用层会话上下文 workflow。它负责 chat / continue 入口共享的 session load、`session_id` 写入、系统 prompt 幂等注入、首轮用户消息追加、上下文 save + session index upsert、会话 preview 与 `prompt_id` 追踪。Prompt 文件加载、workspace guidance 追加、模型解析和流式协议包装不进入该 workflow。

我们接受 `ChatApplicationService` 作为应用层聊天用例服务。它负责 `continue_chat` 的上下文可继续性校验与用例编排，以及 approval resume 的 load、not found、expired、decision count / order / allowed、consume、`AgentPort.resume(...)` 调用顺序与异常语义。它只依赖领域 Port、领域值对象和由 adapter 提供的结构化回调，不接收具体 infrastructure adapter。

我们接受 `domain/agent/handoff_policy.py` 中的 `HandoffDecision` / `decide_handoff` 作为 handoff 前置限制的纯领域判定。该策略只根据当前 depth、配置侧 max depth、可选 workflow collaboration context 的 recursion / handoff count limit 产出 allow / reject decision；它不读取 ContextVar，不调用 `DelegationPort`，不构造 `ToolExecutionResult`，不记录 collaboration event。

`ChatServiceAdapter` 继续作为 infrastructure adapter。它保留模型解析、direct LLM path、流式包装、`AgentStreamEvent` / `StreamingChunk` 适配、approval metadata 合并、分段 stream / metadata、prompt 加载与 workspace guidance 等技术职责；它通过组合根注入和本地结构协议接收 `ChatSessionContextWorkflow` 与 `ChatApplicationService`，避免 `infrastructure -> application` 生产代码直接导入。

`HandoffToAgentTool` 调用纯策略的集成留给后续 task 10；在集成前，ContextVar、`DelegationPort`、`ToolExecutionResult`、`workflow_collaboration_recorder` 和 `HandoffPerformed` 信号仍明确属于 infrastructure 工具适配边界。

本 ADR 不 supersede [ADR-0001](0001-remove-domain-event-bus.md)、[ADR-0008](0008-extract-domain-serialization-to-infrastructure-mappers.md)、[ADR-0010](0010-relocate-agent-loop-to-domain-direction.md)、[ADR-0011](0011-relocate-agent-loop-leaf-orchestration-to-domain.md)、[ADR-0012](0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md)、[ADR-0013](0013-defer-concurrent-tool-skeleton-relocation.md) 或 [ADR-0015](0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md)。本决策是 `Behavior_Equivalent_Boundary_Convergence`：不引领域事件，不重开工具并发骨架，不修复 handoff model discrepancy。

## 后果（Consequences）

- **正面**：
  - Chat 会话上下文、continue / resume 编排与 handoff depth / count 判定拥有清晰归属，可脱离 infrastructure runtime 做聚焦单元测试。
  - `ChatServiceAdapter` 的剩余职责更集中于模型、stream、metadata 和工具协议适配；`infrastructure` 通过结构协议消费应用服务，保持无 `infra -> app` 生产导入。
  - 领域 handoff policy 保持无框架、无 I/O、无 infrastructure 依赖，符合 `domain/agent` 纯策略样板。
- **负面 / 代价**：
  - 组合根需要显式装配 `ChatSessionContextWorkflow` / `ChatApplicationService`，并把它们注入 `ChatServiceAdapter`。
  - 直接构造 `ChatServiceAdapter` 的单元测试必须显式提供对应依赖或测试替身，不能再隐式依赖 adapter 内部自建用例编排。
- **后续影响**：
  - 新增抽象必须持续保持无框架 / 无具体 infrastructure 依赖；如后续需要改变 Port/Adapter 归属或依赖方向，应新增 ADR，而不是在实现中静默偏离。
  - task 10 应把 `HandoffToAgentTool` 的 depth / handoff count 分支委托给 `decide_handoff`，同时保持 ContextVar、`DelegationPort`、`ToolExecutionResult`、recorder 与成功信号仍在 infrastructure。
  - 主题文档同步继续按 `docs/spec/ddd-infrastructure-logic-remediation/tasks.md` 后续文档任务推进，确保 `docs/agent.md`、`docs/architecture.md`、`docs/di-container.md` 与 `docs/domain-model.md` 描述当前边界。

## 备选方案（Alternatives）

- **方案 A：保持 Chat 与 Handoff 逻辑继续集中在 infrastructure** —— 未采纳原因：这正是 `ddd-infrastructure-logic-remediation` 要治理的差距，会继续让用例编排和纯领域判定散落在 adapter / tool 中，违反 DDD 分层与 SRP。
- **方案 B：整体上移 `ChatServiceAdapter` 到 application** —— 未采纳原因：该类仍包含模型解析、direct LLM、stream chunk 包装、approval metadata、分段 stream / metadata 等技术适配 concern，整体搬迁会把 infrastructure 技术关注点混入 application，改动风险也远大于行为等价边界收敛。
- **方案 C：只把 handoff policy 留在 infrastructure** —— 未采纳原因：depth 与 workflow handoff count 判定是可脱离 ContextVar / I/O 的纯规则，继续留在工具适配器会让领域判定散落，并削弱 `domain/agent` 后续纯策略测试边界。
- **方案 D：把 ContextVar、`ToolExecutionResult`、recorder 等迁入 domain** —— 未采纳原因：这些是运行时上下文传递、工具协议适配和协作事件记录职责，迁入 domain 会违反领域层零 infrastructure / runtime concern 的约束，并与 [ADR-0013](0013-defer-concurrent-tool-skeleton-relocation.md) 对运行时技术边界的结论相冲突。
</content>
