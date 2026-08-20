---
status: Accepted
date: 2026-07-06
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0011：Agent Loop 纯编排叶子与 RoundOutcome 上提领域层（P2 落地首片）

## 背景与问题（Context）

[ADR-0010](0010-relocate-agent-loop-to-domain-direction.md) 已确立方向：`ReActAgentAdapter` 中承载「轮次循环控制 + 终止判定 + 控制流决策 + 结果形态翻译」的编排逻辑属**领域关注点**，应经后续 `P2_Relocation` 上提到领域层；并给出 `Orchestration_Infrastructure_Split_Line` 切分线判据、`Domain_Orchestration_Candidates` / `Infrastructure_Encapsulation_Candidates` 据实候选清单，以及六条 `P2_Invariants`。但 ADR-0010 **只定方向、未搬任何一行**（前置降风险轮，零生产代码改动）。

整合报告（`ddd-implementation-review`）识别的 `Domain_Logic_In_Infrastructure` 依然存在：约 3313 行的 `src/infrastructure/agent/react_agent_adapter.py` 承载自研「推理→行动→观察」编排算法（顶部未 `import openai` / `agents` / `litellm`，模型调用经 `ModelAccessPort` 间接进行），并非外部技术封装适配器，落在基础设施层与其真实关注点错位，需以最低风险起步纠偏。

**首片为何选纯编排叶子（4 纯函数 + `RoundOutcome` 值对象）**：ADR-0010 后果节已明确警示，`_iter_rounds` 循环控制主体与技术记账（guardrail 运行时累加、OTel trace、`_execute_tool_call` 的控制流与副作用、checkpoint、流式累加）**高度交织**，把纯编排从技术记账中剥离需引入领域服务 + 端口回调等结构，风险与改动面大。据此，首片只搬 `Domain_Orchestration_Candidates` 中零 I/O、给定输入即定输出、可脱离运行时单测的**纯叶子构件**——4 个纯判定函数（`_compute_total_tokens` / `_is_token_budget_exceeded` / `_detect_handoff` / `_outcome_to_agent_result`）与刻画轮次终止形态的 `RoundOutcome` / `RoundOutcomeKind` 值对象——作为分片增量的第一步，把与循环主体深度交织的部分留后续片，避免首片即触发 ADR-0010 方案 C 被否的「一次性大爆炸」风险。

## 决策（Decision）

我们将引入 `Domain_Agent_Loop_Module`——新建领域模块 `src/domain/agent/agent_loop_policy.py`，承载 `First_Slice_Scope` 五项构件的**真身**：

- 4 个**模块级纯编排函数**（去前导下划线、去 `@staticmethod`）：`compute_total_tokens` / `is_token_budget_exceeded` / `detect_handoff` / `outcome_to_agent_result`；
- `RoundOutcome` / `RoundOutcomeKind` 值对象，从 `src/infrastructure/agent/round_outcome.py` **迁入**该领域模块。

配套改动：

- `ReActAgentAdapter` **去薄封装**——删除类内 4 个 `@staticmethod` 定义，import 领域函数，调用点（`_iter_rounds`、执行入口、`_log_token_budget_exceeded`）**直接委托**领域实现（不留空壳），领域构件唯一权威落点在领域层；`_log_token_budget_exceeded`（含 `logger`，ADR-0010 判据 4）本体留基础设施，内部改调领域 `compute_total_tokens` 复用计算。
- `src/infrastructure/agent/round_outcome.py` 降级为 **re-export 兼容垫片**（`from domain.agent.agent_loop_policy import RoundOutcome, RoundOutcomeKind` + `__all__`），使既有 `from infrastructure.agent.round_outcome import RoundOutcome` 引用仍可解析。
- **ADR-0010 疑点 2 不修正**：`outcome_to_agent_result` 的 `handoff` 分支 `model` 照搬 `outcome.response.model if outcome.response else ""`（保持当前实际行为，另开 spec 决策，不借上提之名修正）。

本决策采用**分片增量策略**，本首片只搬零 I/O 的纯叶子构件；声明为 `Behavior_Equivalent_Refactor`（行为等价纯重构），不改任何对外可观测行为，遵守 ADR-0010 六条 `P2_Invariants`（`AgentPort` 四签名不变、`Contract_Invariance`、`V3_Decisions_Frozen`、`Existing_Test_Suite_Green`、不回退 ADR-0001、import 调整只改 import 不改断言）。

## 后果（Consequences）

- **正面**：领域层首次承载 Agent Loop 编排构件（4 个纯判定 + `RoundOutcome` 通用语言），依赖方向严格 `infrastructure → domain`、零 `application` / `infrastructure` / 框架 / Pydantic 依赖，可脱离运行时单测；建立领域模块 + 单测样板（对齐 P1 `domain/task/policy.py`、既有 `domain/workspace/policy.py`），为后续片降风险。
- **负面 / 临时性**：
  - `round_outcome.py` re-export 垫片是**首片临时产物**——它仅为让 `react_agent_adapter.py` 与既有测试的 `from infrastructure.agent.round_outcome import RoundOutcome` 引用平滑过渡；待后续片 `_iter_rounds` 主体上提完成、所有引用改指领域模块后，此垫片应清理。
  - **为何 `_iter_rounds` 循环控制主体、`_execute_tool_call`、审批中断决策 `_collect_pending_actions`、流式累加明确留后续片**：ADR-0010 后果节已警示这些构件与技术记账（guardrail 运行时累加、OTel trace、checkpoint 副作用、`_RoundStreamAccumulator` 流式分片重组、`ApprovalStateStorePort` 持久化 I/O）**高度交织**，零风险剥离需引入领域服务 + 端口回调等更重结构；ADR-0010 方案 C「本轮一次性大爆炸搬迁 3313 行」已被否（牵动 `AgentPort`、DI 装配、大量测试 import，紧邻 v3 行为决策，风险极高）。首片先搬无交织的纯叶子，把这些部分留后续片以维持分片增量的低风险节奏。
- **后续影响**：若后续片实施中发现某构件与循环主体 / 技术记账存在未预期耦合而无法零风险剥离，处置为「缩小该构件首片范围并登记于本 ADR 后果节，留后续片」，不借首片之名扩张至 Out of Scope。本 ADR **不 supersede** [ADR-0001](0001-remove-domain-event-bus.md) 与 [ADR-0010](0010-relocate-agent-loop-to-domain-direction.md)（落地 ADR-0010 方向），并 SHALL NOT 复活领域事件 / 事件总线承载循环逻辑（`P2_Invariants` 第 5 条）。

## 备选方案（Alternatives）

- **方案 A：本片连 `_iter_rounds` 主体一起搬（一次性大爆炸）** —— 未采纳原因：即 ADR-0010 已否决的方案 C，牵动 `AgentPort`、DI 装配、大量测试 import 且循环主体与技术记账高度交织，风险极高；与本片「分片增量、首片只搬纯叶子」定位冲突。
- **方案 B：保留 4 个空壳 `@staticmethod` 薄封装再委托领域** —— 未采纳原因：会造成「适配器与领域模块两处都像入口」的认知负担，并在 infrastructure 侧遗留冗余定义；requirement AC1.7 二选一允许「调用点直接委托」，去薄封装使领域构件成为唯一权威落点。
- **方案 C：全量改所有 import 路径、不留 re-export 垫片** —— 未采纳原因：改动面更大、漏改风险高，违背最小改动纪律（`change-discipline.md`）；垫片为首片临时产物，后续片清理即可，改动面最小。
- **方案 D：引入领域事件 / 事件总线承载循环推进** —— 未采纳原因：直接违反 [ADR-0001](0001-remove-domain-event-bus.md)（已 `Accepted` 移除领域事件总线）与 `P2_Invariants` 第 5 条；本 ADR 不把领域事件列为 P2 落地形态。
- **方案 E：把 `RoundOutcome` 拆入 `domain/agent/value_objects.py` 而非新模块** —— 未采纳原因：值对象与消费它的翻译函数 `outcome_to_agent_result` 强内聚，同处 `agent_loop_policy.py` 更利首片样板边界清晰，且避免与 `value_objects.py` 的循环引用风险。
