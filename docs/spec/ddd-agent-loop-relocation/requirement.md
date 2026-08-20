# 需求文档：P2 落地首片——Agent Loop 纯编排叶子逻辑与 RoundOutcome 值对象上提领域层

## 简介

### 背景

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构。整合评估报告识别出的最大差距（🔴 高风险）是 `Domain_Logic_In_Infrastructure`：核心业务算法 **ReAct Agent Loop** 位于 `src/infrastructure/agent/react_agent_adapter.py`（约 3313 行），模块 docstring 自称"本模块属于基础设施层"，但它并非封装外部 SDK（未 `import openai` / `agents` / `litellm`），而是**自研的"推理→行动→观察"编排算法**，本质属领域关注点。

本 spec 的方向与约束由前置轮 `docs/spec/ddd-agent-loop-relocation-prep`（已合入）备齐，其两项降风险资产是本 spec 的直接前提，均不得回退：

- **ADR-0010**（`docs/adr/0010-relocate-agent-loop-to-domain-direction.md`，`Accepted`）：确立"ReAct Agent Loop 编排逻辑应归属领域层"的方向、`Orchestration_Infrastructure_Split_Line` 可操作判据、`Domain_Orchestration_Candidates` / `Infrastructure_Encapsulation_Candidates` 据实清单、`P2_Invariants` 六条硬约束，以及两条待观测疑点。**ADR-0010 是本 spec 的最高约束源，本 spec 全部需求回链并遵守其判据与不变量。**
- **特征化测试安全网**（`test/infrastructure/agent/test_react_agent_characterization_*.py` 及既有 `test/infrastructure/agent/` 单测）：Agent Loop 对外可观测行为的回归基线，作为本 spec"行为等价"判据。

此外 P1（`docs/spec/ddd-anemic-domain-pilot`，`domain/task` 充血化试点，ADR-0009）已落地，其成果不得回退。

### 本 spec 定位：P2 的正式落地首片（分片增量策略）

本 spec 是 `P2_Relocation` 的**正式落地**，但采用**分片增量策略**——ADR-0010 后果节已明确警示：`_iter_rounds` 轮次循环控制与技术记账（guardrail / trace / checkpoint 副作用，尤其 `_execute_tool_call`）**高度交织**，一次性大爆炸搬迁 3313 行已被 ADR-0010 方案 C 否决为过高风险。

因此本 spec 作为**首片**，只搬迁**风险最低、零 I/O、给定输入即定输出的纯编排叶子函数 + `RoundOutcome` 值对象**；把 `_iter_rounds` 循环主体的深度解耦（含 `_execute_tool_call`、审批中断决策 `_collect_pending_actions`、流式累加等涉 I/O / 副作用 / 时序的部分）**明确留作后续片，不在本 spec 范围**。首片的目标是：以最低风险打通"领域层承载 Agent Loop 编排构件"的第一块落地，验证上提路径可行、建立领域层 Agent Loop 编排模块与其单测的样板，为后续片降风险。

### 范围内行为（In Scope）

将以下 4 个纯函数与 1 组值对象从 `src/infrastructure/agent/react_agent_adapter.py`（值对象源自 `src/infrastructure/agent/round_outcome.py`）上提到领域层 `src/domain/agent/`（新建合适模块，落点与命名由 design 定，requirement 只界定范围）：

1. `_compute_total_tokens(total_usage) -> int`（`Token_Budget_Computation_Rule` 纯计算，`@staticmethod`，源行 979–991）；
2. `_is_token_budget_exceeded(config, total_usage) -> bool`（纯判定，`@staticmethod`，源行 993–998）；
3. `_detect_handoff(context) -> tuple[str, str] | None`（只读 `ConversationContext` 消息列表尾部反向扫描的纯判定，`@staticmethod`，源行 1837–1865）；
4. `_outcome_to_agent_result(outcome) -> AgentResult`（`RoundOutcome → AgentResult` 纯翻译，`@staticmethod`，源行 2254–2303）；
5. `RoundOutcome` / `RoundOutcomeKind`（`src/infrastructure/agent/round_outcome.py`，已是 `@dataclass(frozen=True)` 值对象 + `Literal` 类型别名，是 Agent Loop"轮次终止形态"的领域通用语言）。

上提方式：领域层定义这些纯函数 / 值对象；`ReActAgentAdapter` 改为**委托 / import 领域层实现**（薄封装保留原 `@staticmethod` 入口，或调用点直接改用领域层实现），保持 `_iter_rounds` 等所有调用点行为字面等价。为上提构件补领域层单元测试（置于 `test/domain/agent/`），复用或对齐特征化测试断言。引入领域层 Agent Loop 编排构件属架构级决策，须新增 ADR（编号从 0011 起）。

### 范围外边界（Out of Scope）

本首片**不搬**、**不改**下列内容（留作 `P2_Relocation` 后续片）：

1. **不搬 `_iter_rounds` 循环控制主体**（`for round_num in range(...)` 推进、`terminal_round` 边界、`RoundOutcome` 产出协议）；
2. **不搬 `_execute_tool_call`**（含控制流决策与 guardrail / trace / checkpoint 副作用的高度交织逻辑）；
3. **不搬审批中断决策 `_collect_pending_actions`**（读 `ApprovalPolicyPort` 后的筛选，涉 I/O 时序）；
4. **不搬流式累加**（`_RoundStreamAccumulator` 等 SDK 分片重组）；
5. **不搬** guardrail 运行时累加、trace / abuse 检测、审批状态持久化 I/O、序列化、日志装配（属 ADR-0010 `Infrastructure_Encapsulation_Candidates`，留基础设施）；
6. 特别地，`_log_token_budget_exceeded`（含 `logger`）**留基础设施**（ADR-0010 判据 4，日志属技术关注点），本 spec 不上提；
7. **不改 `AgentPort` 四方法签名**、不改 DI 装配的对外行为、不改前端、不引入 / 替换任何第三方依赖（仍仅 `uv`）；
8. 不改动 ADR-0001 / 0007 / 0008 / 0009 / 0010 的既有结论，不复活领域事件 / 事件总线承载循环逻辑。

### 依赖归属与反向依赖核验（据实）

本 spec 已实读核验，上提这些构件**不引入反向依赖**：

- `RoundOutcome`（`round_outcome.py`）的全部 import 指向 `domain.agent.value_objects`（`AgentTerminationReason` / `ApprovalRequiredPayload`）与 `domain.model_access.value_objects`（`LLMResponse` / `ToolCallRequest`），**零 `infrastructure` 符号依赖**——上提后不产生 `domain → infrastructure` 反向依赖。
- 4 个纯函数的入参 / 出参类型 `AgentConfig` / `AgentResult` / `ApprovalRequiredPayload`（`domain.agent.value_objects`）、`ConversationContext` / `ToolMessage`（`domain.chat.context`）**均已在领域层**；`domain.agent.value_objects` 仅依赖 `domain.agent.exceptions`，`RoundOutcome` 依赖亦全在领域层。故上提是"领域内向领域内"引用，方向合规。
- **既有测试直接引用现址的风险（据实）**：`test/domain/agent/test_value_objects_terminated_reason_unit.py` 与 `test/infrastructure/agent/test_react_agent_token_budget_unit.py` 现以 `from infrastructure.agent.round_outcome import RoundOutcome` 引用值对象、并直接调用 `ReActAgentAdapter._outcome_to_agent_result`。上提后须保证这些引用仍可解析（re-export 兼容垫片，或仅调整其 import 路径），且**只改 import、不改断言语义**（ADR-0010 `P2_Invariants` 第 6 条）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| P2 搬迁 | `P2_Relocation` | 把 `ReAct_Agent_Adapter` 中「领域编排逻辑」上提到领域层的多片增量重构工程；本 spec 是其**首片**。 |
| 首片搬迁范围 | `First_Slice_Scope` | 本 spec 唯一纳入搬迁的集合：4 个纯函数（`_compute_total_tokens` / `_is_token_budget_exceeded` / `_detect_handoff` / `_outcome_to_agent_result`）+ `RoundOutcome` / `RoundOutcomeKind` 值对象。`_iter_rounds` 主体、`_execute_tool_call`、`_collect_pending_actions`、流式累加、guardrail / trace / abuse / 序列化 / 日志明确**不在**其中。 |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `src/infrastructure/agent/react_agent_adapter.py::ReActAgentAdapter`，`AgentPort` 的具体实现，承载 Agent Loop。首片上提后改为委托领域层实现，位置不变。 |
| Agent 端口 | `AgentPort` | `src/domain/agent/ports.py::AgentPort`，含 `run` / `run_streaming` / `run_events` / `resume` 四方法。本 spec 不改其任何方法签名。 |
| 领域层 Agent Loop 编排模块 | `Domain_Agent_Loop_Module` | 本 spec 在 `src/domain/agent/` 下新建、承载 `First_Slice_Scope` 上提构件的领域模块（具体文件名 / 落点由 design 定，如 `agent_loop_policy.py` 或按子域惯例）；零 `application` / `infrastructure` / 框架 / Pydantic 依赖。 |
| 轮次终止形态值对象 | `RoundOutcome` | `src/infrastructure/agent/round_outcome.py::RoundOutcome` 及 `RoundOutcomeKind`，`@dataclass(frozen=True)` + `Literal["text","tool_calls","approval","final","handoff"]`，刻画 Agent Loop 单轮终止形态的领域通用语言。本 spec 将其上提到 `Domain_Agent_Loop_Module`。 |
| token 预算计算规则 | `Token_Budget_Computation_Rule` | `_compute_total_tokens` 纯计算：优先取 `total_usage["total_tokens"]`，该键缺失或为 0 时回退 `prompt_tokens + completion_tokens`。 |
| token 预算超限判定 | `Token_Budget_Exceeded_Predicate` | `_is_token_budget_exceeded` 纯判定：`config.max_total_tokens is None` 时恒 `False`，否则 `Token_Budget_Computation_Rule(total_usage) > config.max_total_tokens`。 |
| handoff 检测 | `Handoff_Detection` | `_detect_handoff` 纯判定：从 `ConversationContext` 消息列表尾部反向扫描最近一组连续 `ToolMessage`，遇非 `ToolMessage` 停止，命中 `metadata["handoff_target"]` 则返回 `(target, content)`，否则 `None`。 |
| 结果翻译 | `Outcome_To_Result_Translation` | `_outcome_to_agent_result` 纯翻译：按 `RoundOutcome.kind` 分支构造 `AgentResult`（`handoff`→取 `handoff_content` + `terminated_reason="completed"`；`text`/`final`→取 `response.content` + 透传 `terminated_reason`；`approval`→空 content + `status="approval_required"` + 携 `approval`）。含 ADR-0010 疑点 2：`handoff` 分支 `model` 取 `outcome.response.model`（当前实际行为，不修正）。 |
| 行为等价纯重构 | `Behavior_Equivalent_Refactor` | 上提不改变任何对外可观测行为；被搬迁函数 / 值对象的输入→输出、字段与时序逐一等价，所有调用点行为字面等价。 |
| 方向决策 ADR | `Direction_ADR` | 前置轮已落地的 **ADR-0010**，本 spec 的最高约束源。本 spec **落地其方向**，不 supersede 它。 |
| 首片落地 ADR | `First_Slice_ADR` | 本 spec 新增的 ADR（编号从 **0011** 起），记录"引入 `Domain_Agent_Loop_Module` 承载首片编排构件"这一架构级决策、分片增量策略、首片范围、以及为何 `_iter_rounds` 主体留后续片；不 supersede ADR-0001 / 0010，而是落地 ADR-0010 方向。 |
| P2 不变量清单 | `P2_Invariants` | ADR-0010 锁定的、`P2_Relocation` 落地不可破坏的六条约束（见需求 2）。本 spec 全部遵守。 |
| 契约不变性 | `Contract_Invariance` | 对任何外部消费者（HTTP / 前端 / CLI/TUI / 既有测试断言 / trace 观测点 / 事件时序）而言，`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 字段与时序、`AgentTerminationReason` 取值、审批中断 / 恢复协议、流式协议保持字面等价。 |
| v3 行为决策锁定 | `V3_Decisions_Frozen` | `agent-adapter-refactor` v3 已落定、本 spec 不得推翻的行为决策：全程 stream、工具 `timeout`（`AgentConfig.tool_timeout_seconds`）、`max_total_tokens` 预算终止、循环耗尽 assert、`tool_arguments_delta`。 |
| 既有测试全绿基线 | `Existing_Test_Suite_Green` | `PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）在本 spec 落地前后均全部通过，含特征化测试。 |
| 特征化测试安全网 | `Characterization_Tests` | 前置轮新增的 `test/infrastructure/agent/test_react_agent_characterization_*.py` 及既有 `test/infrastructure/agent/` 单测，锁定 Agent Loop 对外可观测行为，作为首片"行为等价"回归基线。 |
| 领域层依赖禁则 | `Domain_Dependency_Rule` | 领域层模块零 `application` / `infrastructure` / 框架 / Pydantic 依赖，见 `docs/steering/ddd-architecture.md`。`Domain_Agent_Loop_Module` 须满足。 |
| 反向依赖风险 | `Reverse_Dependency_Risk` | 上提构件若引用任何 `infrastructure` 符号将产生 `domain → infrastructure` 反向依赖。经据实核验，`First_Slice_Scope` 的全部依赖已在领域层，无此风险；design 须再次核验并处理既有测试对现址 import 的引用。 |
| 终止原因值对象 | `AgentTerminationReason` | `src/domain/agent/value_objects.py::AgentTerminationReason`，`Literal["completed","max_rounds","token_budget_exceeded"]`。 |
| 会话上下文 | `ConversationContext` | `src/domain/chat/context.py::ConversationContext`，`Handoff_Detection` 的只读输入源。 |
| 架构决策记录 | `Architecture_Decision_Record` | `docs/adr/` 下的 ADR，写作规则见 `docs/steering/adr.md`，`Accepted` 后只增不改。 |

## 需求

### 需求 1：将首片纯编排叶子函数与 RoundOutcome 值对象上提领域层（`First_Slice_Scope`）

**用户故事：** 作为准备 `P2_Relocation` 的后端架构维护者，我希望先把风险最低的纯编排叶子函数与轮次终止形态值对象上提到领域层，以便以最低风险打通"领域层承载 Agent Loop 编排构件"的第一块落地，并为后续片建立样板。

#### 验收标准

1. THE `Domain_Agent_Loop_Module` SHALL 位于 `src/domain/agent/` 下，承载 `First_Slice_Scope` 全部上提构件，且仅纳入 `First_Slice_Scope` 所列 5 项，不夹带任何 Out of Scope 内容。
2. THE `RoundOutcome` 与 `RoundOutcomeKind` SHALL 上提到 `Domain_Agent_Loop_Module`，保持 `@dataclass(frozen=True)` 与全部字段（`kind` / `round_num` / `response` / `total_usage` / `tool_calls` / `approval` / `assistant_message_index` / `terminated_reason` / `handoff_target` / `handoff_content`）的名称、类型、默认值、`Literal` 取值逐一等价。
3. THE `Token_Budget_Computation_Rule`（`_compute_total_tokens`）SHALL 上提到 `Domain_Agent_Loop_Module`，其判据与源实现（优先 `total_tokens`，缺失或为 0 回退 `prompt_tokens + completion_tokens`）逐一等价。
4. THE `Token_Budget_Exceeded_Predicate`（`_is_token_budget_exceeded`）SHALL 上提到 `Domain_Agent_Loop_Module`，`config.max_total_tokens is None` 时返回 `False`，否则以 `Token_Budget_Computation_Rule` 与 `config.max_total_tokens` 比较，与源实现逐一等价。
5. THE `Handoff_Detection`（`_detect_handoff`）SHALL 上提到 `Domain_Agent_Loop_Module`，其尾部反向扫描最近一组连续 `ToolMessage`、命中 `metadata["handoff_target"]` 返回 `(target, content)` 否则 `None` 的判定，与源实现逐一等价。
6. THE `Outcome_To_Result_Translation`（`_outcome_to_agent_result`）SHALL 上提到 `Domain_Agent_Loop_Module`，其按 `kind` 分支构造 `AgentResult` 的全部字段取值（含 `handoff` 分支 `model` 取 `outcome.response.model` 的 ADR-0010 疑点 2 当前实际行为）与源实现逐一等价，SHALL NOT 借上提之名修正该疑点。
7. WHEN `ReAct_Agent_Adapter` 的 `_iter_rounds` 及其它调用点使用上述构件, THE `ReAct_Agent_Adapter` SHALL 改为委托 / import `Domain_Agent_Loop_Module` 的实现（薄封装保留原 `@staticmethod` 入口或调用点直接改用领域层实现），且 `Behavior_Equivalent_Refactor` 使所有调用点行为字面等价。
8. THE 本 spec SHALL NOT 上提 `_iter_rounds` 循环主体、`_execute_tool_call`、`_collect_pending_actions`、流式累加、guardrail / trace / abuse / 序列化，以及 `_log_token_budget_exceeded`（后者含 `logger`，按 ADR-0010 判据 4 留基础设施）。

### 需求 2：全程遵守 ADR-0010 的 P2_Invariants 六条硬约束

**用户故事：** 作为对 Agent Loop 契约敏感的维护者，我希望首片搬迁严格遵守 ADR-0010 锁定的不变量，以便对外可观测行为零变化、既有测试与前端不受影响。

#### 验收标准

1. THE 本 spec SHALL NOT 改动 `AgentPort` 的 `run(context, config, model_access) -> AgentResult`、`run_streaming(...) -> AsyncIterator[StreamingChunk]`、`run_events(...) -> AsyncIterator[AgentStreamEvent]`、`resume(context, config, model_access, interrupt, decisions) -> AgentResult` 四方法签名（`P2_Invariants` 第 1 条）。
2. THE `Contract_Invariance` SHALL 成立：`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 的字段与时序、`AgentTerminationReason` 取值、审批中断 / 恢复协议、流式协议对外字面等价（`P2_Invariants` 第 2 条）。
3. THE `V3_Decisions_Frozen` SHALL 成立：全程 stream、工具 `timeout`、`max_total_tokens` 预算终止、循环耗尽 assert、`tool_arguments_delta` 决策不因首片搬迁而改变（`P2_Invariants` 第 3 条）。
4. THE `Existing_Test_Suite_Green` SHALL 在本 spec 落地前后均成立（`PYTHONPATH=src uv run --frozen pytest`，含 `Characterization_Tests`）（`P2_Invariants` 第 4 条）。
5. THE 本 spec SHALL NOT 回退 ADR-0001，SHALL NOT 引入领域事件 / 事件总线承载循环逻辑（`P2_Invariants` 第 5 条）。
6. WHEN 因构件移动导致既有生产代码或测试的 import 路径变化, THE `Behavior_Equivalent_Refactor` SHALL 仅调整 import，不改任何断言语义（`P2_Invariants` 第 6 条）。

### 需求 3：领域层新模块满足依赖禁则与代码质量规范

**用户故事：** 作为规范守护者，我希望 `Domain_Agent_Loop_Module` 符合领域层依赖禁则与既有代码质量规范，以便它成为后续片可复制的正确样板且不引入反向依赖。

#### 验收标准

1. THE `Domain_Agent_Loop_Module` SHALL 满足 `Domain_Dependency_Rule`：零 `application` / `infrastructure` / 框架 / Pydantic 依赖，仅引用领域层内符号（对齐 `docs/steering/ddd-architecture.md` 与 `docs/steering/ddd-tactical-modeling.md`）。
2. THE design 阶段 SHALL 复核 `Reverse_Dependency_Risk`：确认上提构件依赖的 `AgentConfig` / `AgentResult` / `ApprovalRequiredPayload` / `RoundOutcome` / `ConversationContext` / `ToolMessage` / `LLMResponse` / `ToolCallRequest` 均在领域层；IF 复核发现 `RoundOutcome` 或任一上提函数实际引用任何 `infrastructure` 符号, THEN THE design SHALL 将其标注为风险并给出不产生 `domain → infrastructure` 反向依赖的处理方案。
3. THE `Domain_Agent_Loop_Module` SHALL 具备中文 docstring 说明各构件职责与不变量（对齐 `docs/steering/code-documentation.md`）。
4. THE `Domain_Agent_Loop_Module` SHALL 具备全量类型标注、不使用裸 `Any`，并通过 `ruff` / `pyright` 基线（零新增错误，对齐 `docs/steering/python-typing-lint.md`）。
5. THE `Domain_Agent_Loop_Module` SHALL 满足 SRP：只承载 Agent Loop 纯编排判定与轮次终止形态值对象，不夹带序列化、日志、I/O（对齐 `docs/steering/srp-principle.md`）。

### 需求 4：为上提构件补齐领域层单元测试

**用户故事：** 作为维护者，我希望每个上提构件都有可脱离运行时的领域层单测，以便首片搬迁的行为等价性可被独立回归验证，且不与既有特征化测试重复。

#### 验收标准

1. THE 新增单元测试 SHALL 置于 `test/domain/agent/` 下，命名清晰标识其锁定的构件。
2. FOR ALL `First_Slice_Scope` 的可测构件（`Token_Budget_Computation_Rule` / `Token_Budget_Exceeded_Predicate` / `Handoff_Detection` / `Outcome_To_Result_Translation` / `RoundOutcome`）, THE 单元测试 SHALL 覆盖其正例与边界 / 分支：`total_tokens` 命中与回退、`max_total_tokens` 为 `None` 与恰好等于 / 超限、handoff 命中 / 未命中 / 尾部非 `ToolMessage` 停止、`RoundOutcome` 各 `kind`（`handoff` / `text` / `final` / `approval`）的翻译分支。
3. THE 新增单元测试 SHALL 不依赖 `application` / `infrastructure` 或框架运行时即可执行（脱离运行时单测）。
4. THE 新增单元测试 SHALL 复用或对齐 `Characterization_Tests` 的既有断言，SHALL NOT 与已充分覆盖处添加等价重复断言（对齐 `docs/steering/change-discipline.md`）。
5. WHEN `test/domain/agent/test_value_objects_terminated_reason_unit.py` 与 `test/infrastructure/agent/test_react_agent_token_budget_unit.py` 现以 `from infrastructure.agent.round_outcome import RoundOutcome` 或 `ReActAgentAdapter._outcome_to_agent_result` 等引用被移动的构件, THE `Behavior_Equivalent_Refactor` SHALL 保证这些引用仍可解析（re-export 兼容或仅调整 import 路径），且只改 import、不改断言语义。

### 需求 5：新增 ADR 记录首片落地与分片增量策略（`First_Slice_ADR`）

**用户故事：** 作为架构负责人，我希望"引入领域层 Agent Loop 编排模块并只搬首片"这一架构级决策被 ADR 记录，以便决策可追溯、分片策略成文、且不与既有 ADR 冲突。

#### 验收标准

1. THE `First_Slice_ADR` SHALL 新增一条编号从 **0011** 起的记录（当前 ADR 已至 0010），采用 `docs/steering/adr.md` 规定的四段式（背景 / 决策 / 后果 / 备选方案含未采纳原因），状态为 `Accepted`。
2. THE `First_Slice_ADR` SHALL 记录"引入 `Domain_Agent_Loop_Module` 承载 `First_Slice_Scope` 编排构件"的决策、分片增量策略、本首片范围，以及**为何 `_iter_rounds` 主体 / `_execute_tool_call` / 审批中断 / 流式累加留后续片**（回链 ADR-0010 后果节"高度交织"警示与方案 C 否决）。
3. THE `First_Slice_ADR` SHALL 声明本决策为 `Behavior_Equivalent_Refactor`，不改变任何对外可观测行为，并声明遵守 `P2_Invariants` 六条。
4. THE `First_Slice_ADR` SHALL NOT supersede ADR-0001 与 ADR-0010——而是**落地 ADR-0010 方向**；SHALL NOT 复活领域事件 / 事件总线。
5. WHEN `First_Slice_ADR` 落地, THE `Architecture_Decision_Record` SHALL 在 `docs/adr/README.md` 索引表新增该条目（遵循 `docs/steering/doc-sync.md`）；WHERE 上提使领域模型文档（`docs/domain-model.md`）或架构文档（`docs/architecture.md`）与代码脱节, THE 相应主题文档 SHALL 同步更新。

### 需求 6：严格限定首片范围，不越界搬迁后续片内容

**用户故事：** 作为对 3313 行核心算法风险敏感的维护者，我希望首片严格只搬纯叶子构件、不触碰高度交织的循环主体与技术记账，以便把大爆炸搬迁的风险隔离到后续独立分片。

#### 验收标准

1. THE 本 spec SHALL NOT 移动、重写或深度解耦 `_iter_rounds` 循环控制主体、`_execute_tool_call`、`_collect_pending_actions`、`_RoundStreamAccumulator` 及流式累加逻辑。
2. THE 本 spec SHALL NOT 上提或改动 ADR-0010 `Infrastructure_Encapsulation_Candidates` 所列的 guardrail 运行时累加、`ToolAbuseDetector`、OTel trace 记录、`ApprovalStateStorePort` 持久化调用、`approval_serialization` / `guardrail_serialization` 序列化、`approval_logging`、`handoff_context`、`workflow_capability_runtime`、`merge_usage`。
3. THE 本 spec SHALL NOT 改动 DI 装配的对外行为、SHALL NOT 改动前端 `epsilon-client/`、SHALL NOT 新增 / 替换任何第三方依赖或改变依赖管理方式（仍仅 `uv`）。
4. THE `ReAct_Agent_Adapter` 文件 SHALL 保持位于 `src/infrastructure/agent/react_agent_adapter.py`（首片只上提叶子构件，不移动适配器本体）。
5. IF 首片实施中发现某上提构件与循环主体 / 技术记账存在未预期的耦合而无法零风险剥离, THEN THE 处置 SHALL 为"缩小该构件的首片范围并登记于 design / `First_Slice_ADR` 后果节，留后续片处理"，SHALL NOT 借首片之名扩张搬迁到 Out of Scope。
