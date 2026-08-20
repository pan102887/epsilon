---
status: Accepted
date: 2026-07-07
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0012：上提 Agent Loop 循环编排主体与工具执行控制流至领域层（P2 第二片，引入 AgentLoopOrchestrator 领域服务与 AgentLoopEffects 端口回调）

## 背景与问题（Context）

[ADR-0010](0010-relocate-agent-loop-to-domain-direction.md) 确立方向：`ReActAgentAdapter` 中的「轮次循环控制 + 终止判定 + 控制流决策」编排逻辑属领域关注点，应经 `P2_Relocation` 上提领域层。[ADR-0011](0011-relocate-agent-loop-leaf-orchestration-to-domain.md) 首片已搬出 4 个纯编排叶子函数（`compute_total_tokens` / `is_token_budget_exceeded` / `detect_handoff` / `outcome_to_agent_result`）与 `RoundOutcome` / `RoundOutcomeKind` 值对象至 `domain/agent/agent_loop_policy.py`，建立委托范式与领域模块样板。

首片有意只搬零 I/O 的纯叶子——ADR-0010 后果节明确警示 `_iter_rounds` 循环控制主体与技术记账（guardrail 运行时累加、OTel trace、`_execute_tool_call` 的控制流与副作用、checkpoint、流式累加）**高度交织**，把编排从技术记账中剥离需引入领域服务 + 端口回调等更重结构。本片正是承接该挑战：以「领域服务 + 端口回调」解耦形态，把 `_iter_rounds` 的 `Round_Loop_Control`（轮次循环推进骨架）与 `Termination_Decision`（终止判定状态机）上提领域层，同时把 `_execute_tool_call` / `_prepare_tool_calls_for_execution` / `_collect_pending_actions` 中的纯控制流判定以首片委托范式上提为领域纯函数。

核心难题：OTel `start_as_current_span` 的 `with` 块内不能出现 `yield`（contextvars 冲突），而源 `_iter_rounds` 在 span 内 yield `RoundOutcome`；需要一种解耦形态让领域编排零 OTel 依赖、又能保持 trace 正确闭合。

## 决策（Decision）

我们将引入以下一等抽象与委托改造：

1. **`AgentLoopOrchestrator`（领域服务，`src/domain/agent/agent_loop_orchestration.py`）**：承载 `Round_Loop_Control`（轮次推进骨架、terminal 边界、budget 跨轮状态机、`Terminal_Round_Boundary_Assert`、`RoundOutcome` 五态产出协议）与 `Termination_Decision`（text/handoff/token_budget_exceeded/max_rounds 终止原因决策）。以异步生成器 `iter_rounds(...) -> AsyncIterator[RoundOutcome]` 形态产出轮次结果，保持源协作式生成器协议字面等价。全部运行时副作用经 `AgentLoopEffects` 端口回调，本服务可脱离运行时以 fake effects 单测。

2. **`AgentLoopEffects`（领域 `Protocol` 端口，`src/domain/agent/ports.py`）**：承载 `Round_Loop_Control` 编排所需的全部运行时 I/O / 副作用回调（`prepare_runtime` / `perform_model_round` / `record_assistant_with_tool_calls` / `resolve_approval_policies` / `save_interrupt` / `prepare_tool_calls_for_execution` / `checkpoint_model_completed` / `checkpoint_approval_interrupt` / `record_terminated`）；方法签名只引用领域类型。`perform_model_round` 封装「context 构建 + stream 累加 + merge_usage + guardrail model_completed + `react_agent.round` span 开闭」，span 在方法内闭合后返回 `ModelRoundResult`，orchestrator 在 span 外 yield——解决 OTel contextvars 冲突，领域编排零 OTel 依赖。

3. **纯叶子判定扩充（`agent_loop_policy.py`）**：以首片委托范式新增 `interpret_tool_guardrail_decision`（guardrail 决策→控制流分支映射）、`classify_tool_execution`（工具执行异常分类）、`collect_pending_actions`（审批动作筛选），配套值对象 `ToolGuardrailBranch`（`Literal`）、`ToolExecutionClassification`（frozen dataclass）。三者均「给定输入即定输出」纯判定，与首片 `compute_total_tokens` 等同质。

4. **`ReActAgentAdapter` 委托改造**：实现 `AgentLoopEffects` 端口（副作用实现从源 `_iter_rounds` 对应片段平移、行为字面等价）；`_iter_rounds` 降为「构造 orchestrator、以 self 作 effects、透传生成器」的薄驱动；`_execute_tool_call` / `_prepare_tool_calls_for_execution` 调用点直调领域判定（副作用位置与时机不变）。

5. **首片垫片清理**：`infrastructure/agent/round_outcome.py` re-export 垫片在主体上提后无外部依赖，清理删除，所有引用改指 `domain.agent.agent_loop_policy`。

6. **内部分波增量**：采用特征化测试安全网 + 分波增量落地，不做一次性大爆炸。

本决策声明为 `Behavior_Equivalent_Refactor`（行为等价纯重构），严格遵守 ADR-0010 六条 `P2_Invariants`（`AgentPort` 四签名不变、`Contract_Invariance`、`V3_Decisions_Frozen`、`Existing_Test_Suite_Green`、不回退 ADR-0001、import 调整只改 import 不改断言）。端口回调为 `Protocol` 方法调用，非领域事件 / 事件总线 / 发布订阅——不违反 [ADR-0001](0001-remove-domain-event-bus.md)。

## 后果（Consequences）

- **正面**：
  - 约 3000+ 行核心编排算法的循环骨架（`Round_Loop_Control` + `Termination_Decision`）回归领域层，与其真实关注点对齐，消除 `Domain_Logic_In_Infrastructure` 错位。
  - 领域编排器可脱离运行时以 fake effects 单测，覆盖循环控制、终止判定、协作协议全路径——降低回归风险。
  - 副作用归属经端口彻底清晰：OTel trace、checkpoint、guardrail 累加、流式累加、审批持久化、日志等技术记账的**调用编排**由领域驱动，**实现本体**留基础设施。
  - `Port_Callback_Decoupling` 优雅解决 OTel span/yield contextvars 冲突：span 封装进 `perform_model_round` effect 方法内闭合，orchestrator 在 span 外 yield。
  - 首片 `round_outcome.py` re-export 垫片清理，消除临时产物。
  - 工具控制流的纯判定（guardrail 分支、异常分类、审批筛选）上提领域，adapter 调用点更薄更清晰。

- **负面 / 代价**：
  - `AgentLoopEffects` 端口面较宽（9+ 方法），adapter 实现体从 `_iter_rounds` 平移需谨慎保序，引入较大的一次性 diff。
  - 工具并发骨架（`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）、guardrail 运行时累加器（`_GuardrailRuntimeAccumulator`）、流式累加器（`_RoundStreamAccumulator`）实现本体仍留基础设施——回链 `Infrastructure_Encapsulation_Candidates`，属后续评估范畴。
  - `_execute_tool_call` 本体（793 行深度交织 checkpoint/guardrail/abuse/trace/context 变异）未整体上提，仅剥离纯判定——副作用顺序字面不变但仍在 adapter 内。

- **后续影响**：
  - 若后续片实施中发现某构件无法零风险剥离，依 `Scope_Shrink_Discipline` 缩范围并登记。
  - 后续片可继续把工具并发编排、guardrail 累加等纳入领域服务 + 端口评估。
  - `AgentLoopEffects` 端口为未来进一步细分（如拆分 model-round effects / checkpoint effects）提供稳定基线。

## 备选方案（Alternatives）

- **方案 A：一次性大爆炸整体上提 `_iter_rounds` + `_execute_tool_call`** —— 未采纳原因：即 ADR-0010 已否决的方案 C，循环主体与技术记账高度交织、改动面极大（3313 行）、牵动 `AgentPort` / DI 装配 / 大量测试 import，风险极高。本片以领域服务 + 端口 + 分波增量替代。

- **方案 B：领域编排直接 import OTel / checkpoint 具体类型** —— 未采纳原因：违反 `Domain_Dependency_Rule`（领域禁框架/基础设施导入），使领域模块无法脱离运行时单测，与 DDD 六边形架构核心约束冲突。

- **方案 C：用领域事件 / 事件总线承载循环推进副作用** —— 未采纳原因：直接违反 [ADR-0001](0001-remove-domain-event-bus.md)（已 `Accepted` 移除领域事件总线）与 `P2_Invariants` 第 5 条。端口回调是 `Protocol` 方法调用，非事件机制。

- **方案 D：把 guardrail 累加器 / 流式累加器实现本体也上提领域** —— 未采纳原因：二者属 `Infrastructure_Encapsulation_Candidates` 的技术封装（ADR-0008 序列化外移 / ADR-0010 明确留基础设施），上提会引入 OTel / streaming 技术依赖到领域层。

- **方案 E：orchestrator 用回调函数元组（`Callable` 组合）而非 `Protocol` 端口** —— 未采纳原因：`Protocol` 端口对齐仓库 `ports.py` 既有实践（`ModelAccessPort` / `HealthCheckPort` 等均为 `Protocol`），类型标注更清晰、IDE 支持更好、便于 adapter 以类实现多方法。
