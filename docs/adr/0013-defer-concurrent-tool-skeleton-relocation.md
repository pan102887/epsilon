---
status: Accepted
date: 2026-07-07
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0013：暂缓上提工具并发骨架至领域层（P2 第三片方向收敛，工具并发编排留基础设施）

## 背景与问题（Context）

[ADR-0010](0010-relocate-agent-loop-to-domain-direction.md) 确立方向与 `Orchestration_Infrastructure_Split_Line` 四判据：`ReActAgentAdapter` 中的「轮次循环控制 + 终止判定 + 控制流决策」编排逻辑属领域关注点，应经 `P2_Relocation` 分波上提领域层，而技术记账 / 运行时缝合留基础设施。[ADR-0011](0011-relocate-agent-loop-leaf-orchestration-to-domain.md) 首片搬出纯编排叶子函数与 `RoundOutcome` / `RoundOutcomeKind` 值对象至 `domain/agent/agent_loop_policy.py`。[ADR-0012](0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md) 第二片以「`AgentLoopOrchestrator` 领域服务 + `AgentLoopEffects` 领域端口回调」上提循环编排主体（`Round_Loop_Control` + `Termination_Decision`）与工具执行控制流的纯判定（guardrail 分支映射、异常分类、审批筛选）。

ADR-0012「后续影响」节把「工具并发编排是否继续纳入领域服务 + 端口评估」显式列为待评估的 open follow-up；其「负面 / 代价」节亦将工具并发骨架 `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls` 与 guardrail / 流式累加器一并归入 `Infrastructure_Encapsulation_Candidates`，标注为后续评估范畴。本 ADR 承接并**闭合该 open 项**：对 `Concurrent_Tool_Skeleton` 是否开 P2 第三片给出方向结论。

评估对象 `Concurrent_Tool_Skeleton` 指 `src/infrastructure/agent/react_agent_adapter.py` 中的三方法（TEST 分支实测位置）：

- `_dispatch_concurrent_tool_calls`（`react_agent_adapter.py:2137`）
- `_stream_concurrent_tool_progress`（`react_agent_adapter.py:2205`）
- `_events_concurrent_tool_calls`（`react_agent_adapter.py:2273`）

三方法负责「同轮多工具如何并发执行、进度 / 事件如何配对相邻 yield」的执行时序。

## 决策（Decision）

我们将**不开 P2 第三片，工具并发骨架留基础设施**，P2 上提工作以 ADR-0011 / ADR-0012 两片为终点收敛。

依 ADR-0010 `Orchestration_Infrastructure_Split_Line` 四判据评估，`Concurrent_Tool_Skeleton` 属：

- **技术并发编排**：以 `asyncio.gather(...)`（`:2184` / `:2251` / `:2325`）调度同轮多工具并发，直接依赖 Python 运行时并发原语（命中判据 1「封装运行时并发原语 → 留基础设施」）；
- **运行时上下文传参**：`set_parent_context(context)` / `reset_parent_context(token)`（`:2153` / `:2203` 等）为 ContextVar 运行时上下文传参（handoff 子 Agent 快照），属运行时缝合胶水；
- **可观测性缝合**：`_record_tool_call_trace(...)`（定义 `:693`，调用 `:2190` / `:2259` / `:2339`）为 OTel trace 记账；
- **流式事件时序**：进度 / 事件配对相邻 yield 为流式时序缝合（命中判据 4「把技术观测 / 运行时上下文缝合进循环 → 留基础设施」）。

三方法本体不含可脱离运行时的领域纯判定残留（命中判据 2「无可再上提的纯判定」、判据 3「非领域编排语言——推进 / 终止已在 `AgentLoopOrchestrator`」）：真正的领域纯判定（终止 / 预算 / handoff 判定、异常分类、审批筛选、guardrail 分支）已由第二片 / ADR-0012 剥离为 `agent_loop_policy.py` 领域纯函数与 `AgentLoopOrchestrator` 领域服务。

详细四判据论证（判据 → 证据（真实符号）→ 归层 表格）见 [`docs/spec/ddd-impl-research/report.md`](../spec/ddd-impl-research/report.md) 第三节，本 ADR 回链不重复全文。

本 ADR **零生产源码改动**（`src/` 不动），仅作方向决策登记；**不 supersede** ADR-0012，以普通回链引用 0010 / 0011 / 0012 闭合其 open follow-up。

## 后果（Consequences）

- **正面**：
  - P2 上提工作正式收敛，方向明确，不再悬置 open follow-up。
  - 领域层保持零 `asyncio` / `ContextVar` / OTel 依赖，`Domain_Dependency_Rule`（领域禁框架 / 基础设施依赖）不被破坏，领域模块可继续脱离运行时单测。
  - 独立 ADR 的「备选方案」节作为防重开护栏，避免后续 agent 反复重开高风险的第三片上提。

- **负面 / 代价**：
  - 工具并发编排（同轮多工具并发执行、进度 / 事件配对时序）仍留在 `src/infrastructure/agent/react_agent_adapter.py`，该文件仍较大。此为技术并发编排 + 运行时缝合的**技术关注点归属**，属可接受代价（技术封装本就应留基础设施）。

- **后续影响**：
  - P2 视为收敛，主线依 `Priority_Roadmap` 回归 P1（贫血子域充血试点），P1 属独立 spec + ADR，不在本 ADR 范畴。
  - 若未来在并发编排中析出真正可脱离运行时的领域纯判定，可另开 spec 局部上提该判定；但**整段工具并发骨架不上提**这一方向已在本 ADR 定案。
  - 不引入新的 Port / Adapter / 配置 / 依赖变更。

## 备选方案（Alternatives）

- **方案 A：上提整个工具并发骨架为领域服务（+ 端口回调）** —— 未采纳原因：三方法本体依赖 `asyncio.gather`（运行时并发原语）、`set_parent_context` / `reset_parent_context`（ContextVar）、`_record_tool_call_trace`（OTel），整体上提会把这些技术依赖拖进领域层，违反 `Domain_Dependency_Rule`，使领域层无法脱离运行时单测，属过度设计。

- **方案 B：用领域事件 / 事件总线承载并发执行副作用** —— 未采纳原因：直接违反 [ADR-0001](0001-remove-domain-event-bus.md)（已 `Accepted` 移除领域事件总线）与 ADR-0010 `P2_Invariants` 第 5 条。

- **方案 C：不新增 ADR，仅在 ADR-0012「后续影响」节增量登记该收敛** —— 未采纳原因：第三片是会被后续 agent 反复提起的方向决策，需要独立 ADR 的「备选方案」节作为防重开护栏；且 ADR 遵循 `docs/steering/adr.md` **只增不改**，已 `Accepted` 的 ADR-0012 正文不得改写。按 adr.md「方向 / 边界级决策必写 ADR」判定口诀，本收敛应独立成篇。
