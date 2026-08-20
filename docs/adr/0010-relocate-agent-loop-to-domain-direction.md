---
status: Accepted
date: 2026-07-06
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0010：将 ReAct Agent Loop 编排逻辑归属领域层的方向决策（P2 前置）

## 背景与问题（Context）

前置整合报告（`ddd-implementation-review`）识别出后端三层 LOC 严重失衡：`domain` ≈ 8.3k / `application` ≈ 9.9k / `infrastructure` ≈ 24.5k。infrastructure 膨胀的主因，是承载 ReAct「推理→行动→观察」循环的 `ReActAgentAdapter`。

- **`Domain_Logic_In_Infrastructure`（据实证据）**：ReAct Agent Loop 位于 `src/infrastructure/agent/react_agent_adapter.py`（**3313 行**），模块 docstring 自称「本模块属于基础设施层」。但循环控制、终止判定、控制流决策等编排逻辑本质上是可脱离运行时的业务判定，被误置于基础设施层。
- **无 SDK 封装证据**：该文件顶部 import 仅有 `asyncio` / `json` / `logging` / `time` / `uuid` / `contextvars` / `dataclasses` / `typing`、`opentelemetry`（可观测性）、`domain.*`、`infrastructure.*`；**未 `import openai` / `agents` / `litellm`**。模型调用经 `domain.model_access.ports.ModelAccessPort`（Port）间接进行，本文件不直接绑定任何 LLM SDK——即它**不是**「外部技术封装适配器」，而是自研的「推理→行动→观察」编排算法。一个不封装任何外部 SDK、只做业务级循环推进与终止判定的文件，落在基础设施层与其真实关注点错位。
- **问题**：P2 计划把 Agent Loop 上提到领域层以纠正分层归属，但 3313 行中「哪些是领域编排、哪些是真技术封装」缺乏成文的切分判据，若 P2 临场判断极易切错、且切分口径易随人漂移。本 ADR 为 **P2 搬迁的前置降风险决策**，只确立方向与切分线，**不搬迁任何一行业务逻辑**（本轮零生产代码改动）。

## 决策（Decision）

我们将确认：`ReActAgentAdapter` 中承载「轮次循环控制 + 终止判定 + 控制流决策 + 结果形态翻译」的编排逻辑属**领域关注点**，应经后续 `P2_Relocation` 上提到领域层；仅封装外部技术 / 运行时的部分留在基础设施层。本 ADR 不给逐行搬迁方案，只给可操作的切分线判据与据实候选清单，供 P2 spec 落地。

### `Orchestration_Infrastructure_Split_Line`（可操作判据）

对每一段逻辑逐条自问，据答案归层：

1. **是否封装外部技术 / SDK 或进程外资源？** 是 → 留**基础设施**（OTel、审批持久化 I/O、事件写入、序列化、workflow runtime）。
2. **是否为可脱离运行时、可复用的纯业务判定（给定输入即可确定输出，不触 I/O）？** 是 → 属**领域**（终止判定、预算判定、handoff 检测、循环控制、结果翻译）。
3. **是否表达 Agent Loop 的「何时停止 / 如何推进 / 产出何种形态」这一通用语言？** 是 → 属**领域**（`RoundOutcome` 五态、`_iter_rounds` 的产出契约）。
4. **是否只是把技术观测 / 记账缝合进循环的胶水？** 是 → 留**基础设施**（guardrail 运行时累加、trace 记录、abuse 检测）。

### `Domain_Orchestration_Candidates`（应上提，据实指名）

| 符号 / 位置 | 领域编排语义 |
| --- | --- |
| `_iter_rounds`（异步生成器）的**轮次循环控制** | `for round_num in range(start_round, effective_terminal + 1)` 的推进、`terminal_round` 边界、`RoundOutcome` 产出协议。 |
| `AgentTerminationReason` 四态判定 | `text`→`completed`；循环耗尽 assert + `max_rounds` final；`token_budget_exceeded` 跨轮 pending 标记 + 下一轮入口终止；`handoff` 短路。 |
| `_is_token_budget_exceeded` / `_compute_total_tokens` | `Token_Budget_Computation_Rule` 纯判定：优先 `total_tokens`，缺失回退 `prompt+completion`。 |
| `_detect_handoff` | 尾部反向扫描最近一组 `ToolMessage`、命中 `metadata["handoff_target"]` 的纯判定。 |
| `_collect_pending_actions` | tool_calls 是否命中审批策略而应中断的**审批中断决策**（读 `ApprovalPolicyPort` 后的纯筛选）。 |
| `_outcome_to_agent_result`（`@staticmethod`） | `RoundOutcome → AgentResult` 的纯翻译（按 kind 分支）。 |
| `RoundOutcome` / `RoundOutcomeKind` | `text` / `tool_calls` / `approval` / `final` / `handoff` 五态所表达的轮次终止形态本身。 |

### `Infrastructure_Encapsulation_Candidates`（留基础设施，据实指名）

| 符号 / 位置 | 技术关注点 |
| --- | --- |
| `_GuardrailRuntimeAccumulator` + `_CURRENT_GUARDRAIL_RUNTIME` | guardrail 运行时累加器（有状态、ContextVar 绑定）。 |
| `ToolAbuseDetector` + `_CURRENT_TOOL_ABUSE_DETECTOR` | 工具滥用运行时检测。 |
| OTel `tracer` + `_record_trace` / `_record_error_trace` / `_record_tool_call_trace` / `_build_*_trace` | trace 记录（外部可观测性技术）。 |
| `ApprovalStateStorePort` 的 `save` / `load` / `consume` 调用（`_save_interrupt`、`resume` 前置 I/O） | 审批状态持久化 I/O。 |
| `approval_serialization`（`approval_payload_to_metadata`）/ `guardrail_serialization`（`guardrail_runtime_stats_to_dict`） | 序列化（ADR-0008 已定归属基础设施）。 |
| `approval_logging`（`approval_log_extra`） | 审批日志装配。 |
| `_RoundStreamAccumulator` | 流式分片累加器（SDK 分片重组技术）。 |
| `handoff_context`（`set_parent_context` / `reset_parent_context`） | handoff 上下文栈（ContextVar 传参技术）。 |
| `workflow_capability_runtime`（`enforce_workflow_capability_before_action`） | workflow 能力运行时（依赖 `RunEventStorePort`）。 |
| `merge_usage`（`infrastructure.chat.usage`） | usage 合并工具。 |

## 后果（Consequences）

- **正面**：P2 搬迁「哪些上提、哪些留下」有据可依，无需临场判断；本前置轮已通过特征化测试固化 5 个对外可观测行为面（终止四态 / 流式事件时序 / 审批中断恢复 / handoff / token budget），为 P2 提供「行为等价」回归判据；本轮零行为风险（不动生产代码），`Existing_Test_Suite_Green` 与 `Contract_Invariance` 天然成立。
- **负面 / 代价**：切分线是**方向性**判据而非逐行方案。P2 落地时仍需处理领域编排与技术记账**高度交织**的解耦细节——典型如 `_execute_tool_call` 同时含控制流决策与 guardrail / trace / checkpoint 副作用，把纯编排从技术记账中剥离可能需引入领域服务 + 端口回调等结构。这些细节留待独立的 P2 spec 承载。
- **后续影响**：下述 `P2_Invariants` 成为 P2 spec 的**硬约束**；本 ADR 不改变任何现有代码，也不引入任何新依赖。

### `P2_Invariants` 清单（P2 落地不可破坏）

1. **`AgentPort` 四方法签名不变**——`run(context, config, model_access) -> AgentResult`、`run_streaming(...) -> AsyncIterator[StreamingChunk]`、`run_events(...) -> AsyncIterator[AgentStreamEvent]`、`resume(context, config, model_access, interrupt, decisions) -> AgentResult`。
2. **`Contract_Invariance`**——`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 字段与时序、`AgentTerminationReason` 取值、审批中断 / 恢复协议、流式协议对外字面等价。
3. **`V3_Decisions_Frozen`**——全程 stream、工具 `timeout`（`AgentConfig.tool_timeout_seconds`）、`max_total_tokens` 预算终止、循环耗尽 assert、`tool_arguments_delta` 决策不动。
4. **`Existing_Test_Suite_Green`**——`PYTHONPATH=src uv run --frozen pytest` 前后全绿。
5. **不回退 [ADR-0001](0001-remove-domain-event-bus.md)**——不引入领域事件 / 事件总线承载循环逻辑。
6. 因文件移动导致的 **import 路径调整只改 import、不改断言语义**。

### P2 搬迁待观测疑点（前置特征化暴露、本轮只登记不修复）

以下两条为前置轮特征化测试暴露的当前实际行为，本轮**只登记、不修复**，供 P2 spec 决策：

1. **`resume` 入口的 handoff 终止未被独立测试锁定**：`resume` 经 `_iter_rounds` 与 `run` 共享 handoff 短路逻辑（`_detect_handoff`），理论可触发，但无既有测试；是否 / 如何在恢复路径触发 `HandoffPerformed` 属边界。P2 搬迁循环控制时须留意此路径，本轮不补 resume+handoff 测试以免断言未验证的「理想行为」。
2. **`AgentResult.model` 在 handoff 分支取 `outcome.response.model`（上一轮父模型），而非 `HandoffPerformed.model`（目标 Agent 模型）**：`_outcome_to_agent_result` 用 `outcome.response.model`，未采纳 `HandoffPerformed.model`。这是当前实际行为，特征化不改；若 P2 认为应透传目标模型，另开 spec 决策。

## 备选方案（Alternatives）

- **方案 A：就地把 docstring 改为「领域层」、不搬迁** —— 未采纳原因：文件物理仍在 `infrastructure/`，依赖方向与分层归属未真正纠正，只是文字自欺；且改 docstring 也属对生产代码的改动，违背本前置轮零改动倾向。
- **方案 B：引入领域事件 / 事件总线承载循环推进** —— 未采纳原因：直接违反 [ADR-0001](0001-remove-domain-event-bus.md)（已 `Accepted` 移除领域事件总线）。本 ADR **不**把领域事件列为 P2 推荐落地形态。
- **方案 C：本轮一次性大爆炸搬迁 3313 行** —— 未采纳原因：牵动 `AgentPort`、DI 装配、大量测试 import，紧邻 v3 行为决策，风险极高；本 spec 定位为前置降风险轮，搬迁留待独立 P2 spec。
- **方案 D：不写 ADR、P2 时临场判断切分** —— 未采纳原因：切分判据是高价值、易漂移的方向决策，按 [adr.md](../steering/adr.md) 判定口诀「三个月后新 agent 会问『为什么这么切』」即应写 ADR。
- **方案 E：把整个适配器（含 guardrail / trace / abuse / 序列化）整体上提** —— 未采纳原因：违反分层，技术关注点（外部可观测性、持久化 I/O、序列化，[ADR-0008](0008-extract-domain-serialization-to-infrastructure-mappers.md)）应留基础设施。
