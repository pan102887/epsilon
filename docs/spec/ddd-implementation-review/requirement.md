# 需求文档：DDD 战术建模重构（DDD Implementation Review）

## 简介

### 背景

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构，**分层骨架规范**已较好落地：`domain/` 零框架依赖、Port/Adapter 归属方向正确（`application → domain ← infrastructure`）、值对象普遍为 `@dataclass(frozen=True)`。这些结构性优点应予保留。

但一轮针对**战术建模（tactical modeling）**层面的代码勘察发现，实现偏离了主流 DDD 的若干范式。经代码核验，以下结构性偏差客观存在（本节数据均已在当前代码基上复核）：

- `Anemic_Domain_Model`（贫血领域模型）：全库 `domain/` 下各子域几乎只有 `value_objects.py` / `ports.py` / `exceptions.py`，`grep 'class \w+(Service|Aggregate|Entity)\b'` 在 `domain/` 下**零命中**——不存在任何领域服务、聚合根或实体类。`domain/task/value_objects.py::Task`、`domain/agent/value_objects.py::AgentConfig` 均为纯数据 frozen dataclass，行为仅限 `__post_init__` 校验。
- `Domain_Logic_In_Infrastructure`（核心业务逻辑下沉基础设施层）：ReAct Agent Loop（"推理→行动→观察"自研编排算法）位于 `infrastructure/agent/react_agent_adapter.py`，**单文件 3310 行**，其模块 docstring 自称"本模块属于基础设施层"。三层代码量对比 domain 8463 / application 9875 / infrastructure 24096（基础设施约为领域层 3 倍），是业务逻辑漏进技术层的信号。领域端口 `domain/agent/ports.py::AgentPort` 已定义 `run` / `run_streaming` / `run_events` / `resume` 四方法，端口边界完备。
- `Domain_Serialization_Concern`（领域对象承担序列化职责）：`domain/run/workflow.py` 含 8 处 `to_dict()`，`domain/chat/context.py` 含 4 处，`domain/health/value_objects.py`、`domain/agent/guardrails.py`、`domain/agent/segmented_execution.py` 亦有分布——序列化属基础设施关注点，与 SRP 规范冲突。
- `Application_Transaction_Script`（应用层大文件倾向）：`application/container_config.py` 2004 行、`application/run/workflow_orchestrator.py` 1367 行、`application/run/run_application_service.py` 831 行，疑似把本属领域的编排规则堆积成事务脚本。
- `Domain_Purity_Blemish`（领域纯净度瑕疵）：`domain/chat/context.py` 第 17 行 `import logging`，日志属技术关注点。

同时勘察确认了**正向样板**：`domain/run/state_machine.py::RunStateMachine`、`domain/run/workflow.py::WorkflowExecutionPolicy.validate()`、`domain/health/aggregator.py`、`domain/workspace/policy.py` 是少数把业务规则收敛进领域层的正确范式，本 spec 应以之为推广基准。

### 动机

让"若三个月后新来的 agent 来读代码，能从 `domain/` 一眼看出业务规则住在哪里"。当前贫血模型 + 逻辑下沉使领域意图散落在基础设施与应用层的大文件中，长期迭代易跑偏、难评审。本 spec 的目标是**在不改变任何外部行为的前提下**，把偏离主流 DDD 的战术建模逐步纠偏到既有正向样板的水准。

### 硬约束（贯穿所有需求）

- **行为等价的纯重构（`Behavior_Equivalent_Refactor`）**：本 spec 全程为结构归属 / 建模层面的重构，**不改变任何对外可观测行为**。既有测试集必须保持全绿（`Existing_Test_Suite_Green`），不得为迁就重构而删改断言语义。
- **不推翻已定行为决策**：历史 spec `agent-adapter-refactor` v1/v2/v3 已就行为层问题（全程 stream、工具 timeout、`max_total_tokens`、循环耗尽 assert、`tool_arguments_delta` 等）落定结论，本 spec **只做结构归属/建模，不触碰这些行为决策**。
- **绑定 steering 规范**：改动须同时满足 `docs/steering/ddd-architecture.md`（分层依赖方向、Port/Adapter 归属、明确禁止/例外）、`docs/steering/srp-principle.md`、`docs/steering/change-discipline.md`（最小改动、可追溯、按规模选流程门）、`docs/steering/code-documentation.md`（中文 docstring）、`docs/steering/python-typing-lint.md`（`ruff`/`pyright` 零新增错误、禁裸 `Any`）。
- **架构级决策须写 ADR**：本 spec 涉及"改变 Port/Adapter 归属 / 引入领域服务与实体等一等抽象"，按 `docs/steering/adr.md` 属**架构/方向级决策**，落地阶段（design/实现）须新增对应 ADR（当前 ADR 序号已至 0006，新增从 0007 起）。requirement 层仅登记该约束。
- **依赖管理仅 `uv`**：测试命令固定为 `PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）。

### 范围切分建议（最终由用户确认）

本 spec 是**分析产出的重构清单**，需求阶段须界定本期纳入范围。基于风险评估给出如下建议：

- 问题 1（`Domain_Logic_In_Infrastructure`，3310 行 Agent Loop 上提）**风险极高**：涉及移动/重划核心业务算法的归属，牵动 `AgentPort` 契约、DI 装配、大量既有测试的 import 路径，且与 v3 刚落定的行为决策紧邻。**建议将问题 1 拆为独立后续 spec**（例如 `ddd-agent-loop-relocation`），并**先写 ADR** 确立"ReAct Agent Loop 归属哪一层、以何种切分方式上提"的方向，再走 design/实现。
- 本期（`ddd-implementation-review`）**建议先做低风险、可增量验证的项**：问题 3（序列化职责剥离）、问题 5（领域纯净度瑕疵），以及问题 2 中"以正向样板为基准、可安全新增领域服务/规则对象而不改行为"的**局部试点**。问题 4（应用层大文件）**建议本期仅做诊断与拆分方案登记，不做大规模搬迁**，待问题 2 的领域建模方向明确后再落地，避免两处同时大改互相干扰。

> 上述为建议，最终"本期纳入哪些需求"的范围决策留给用户在审批阶段拍板。下文"本期范围界定"小节给出可勾选的分档。

### 本期范围界定（待用户确认）

| 需求 | 问题 | 优先级 | 建议本期处置 |
| --- | --- | --- | --- |
| 需求 1 | `Domain_Logic_In_Infrastructure` | 高 | **建议拆为独立后续 spec + 先写 ADR**；本期仅登记与不变量约束 |
| 需求 2 | `Anemic_Domain_Model` | 中 | 本期做**局部试点**（择一子域，以正向样板为基准新增领域服务/规则对象，行为等价） |
| 需求 3 | `Domain_Serialization_Concern` | 低 | 本期落地（序列化职责外移，纯重构） |
| 需求 4 | `Application_Transaction_Script` | 低 | 本期**仅诊断 + 登记拆分方案**，不做大搬迁 |
| 需求 5 | `Domain_Purity_Blemish` | 轻微 | 本期落地（移除 `domain/chat/context.py` 的 `logging` 依赖） |
| 需求 6 | `DDD_Tactical_Modeling_Gap` | 中 | 本期落地（新增战术建模 steering + 修订 `pydantic-model.md` + ADR-0007） |

### 不包括（Out of Scope）

1. **不改变任何对外可观测行为**：不修改 HTTP 端点契约、事件时序、流式协议、终止语义、审批语义、模型路由与工具行为。
2. 不推翻 `agent-adapter-refactor` v1/v2/v3 已落定的任一行为决策（全程 stream、工具 timeout、`max_total_tokens`、循环耗尽 assert、`tool_arguments_delta` 等）。
3. 不修改前端 `epsilon-client/` 任何代码。
4. 不新增/替换第三方依赖，不改变依赖管理方式（仍仅 `uv`）。
5. 不重写既有正向样板（`RunStateMachine` / `WorkflowExecutionPolicy` / `health/aggregator.py` / `workspace/policy.py`）——它们是基准，不是改造对象。
6. 不在需求 4 中做应用层大文件的实际搬迁（本期仅诊断与登记）。
7. 不在需求 1 中实际移动 3310 行 Agent Loop（本期仅登记方向与不变量；实际搬迁走独立 spec + ADR）。除非用户在审批阶段明确将需求 1 纳入本期并接受其高风险。
8. 不引入领域事件总线（已由 ADR-0001 否决，不得回退）；需求 6 的战术建模规范 SHALL 尊重该已定决策，不得把「领域事件」列为本仓库推荐战术构件之一。
9. 需求 6 不改任何源码——它只新增/修订 `docs/steering/` 规范文档与新增 ADR；`Existing_Test_Suite_Green` 对需求 6 平凡成立（零代码改动）。
10. 需求 6 不强制把 `domain/` 所有子域立即重建为充血模型——将贫血模型改造为带行为的领域模型属需求 2 的渐进试点范畴；需求 6 只补齐「规范护栏」，规定"今后该怎么建模"，不代替需求 2 执行改造。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 贫血领域模型 | `Anemic_Domain_Model` | 当前 `domain/` 各子域仅有 `value_objects.py` / `ports.py` / `exceptions.py`，无 `entities.py` / `aggregates.py` / `domain_service.py`；`grep 'class \w+(Service\|Aggregate\|Entity)\b'` 在 `domain/` 下零命中。`domain/task/value_objects.py::Task`、`domain/agent/value_objects.py::AgentConfig` 行为仅限 `__post_init__` 校验，业务规则未收敛进领域层。 |
| 逻辑下沉基础设施层 | `Domain_Logic_In_Infrastructure` | ReAct Agent Loop 自研编排算法位于 `infrastructure/agent/react_agent_adapter.py`（3310 行），模块 docstring 自称"属于基础设施层"，但它并非封装外部 SDK（未 import openai/agents/litellm），而是承载核心业务算法。领域端口 `domain/agent/ports.py::AgentPort` 已完备定义 `run` / `run_streaming` / `run_events` / `resume`。 |
| 领域序列化职责 | `Domain_Serialization_Concern` | `domain/` 内多个领域对象自带 `to_dict()`（`domain/run/workflow.py` 8 处、`domain/chat/context.py` 4 处，另分布于 `domain/health/value_objects.py` / `domain/agent/guardrails.py` / `domain/agent/segmented_execution.py`）；序列化属基础设施关注点，违反 SRP 规范"领域逻辑、编排逻辑与基础设施逻辑必须分离"。 |
| 应用层事务脚本倾向 | `Application_Transaction_Script` | `application/container_config.py`（2004 行）、`application/run/workflow_orchestrator.py`（1367 行）、`application/run/run_application_service.py`（831 行）等大文件，疑似把本属领域的编排规则堆积成事务脚本。 |
| 领域纯净度瑕疵 | `Domain_Purity_Blemish` | `domain/chat/context.py` 第 17 行 `import logging` 并在模块级 `logger = logging.getLogger(__name__)`；日志是技术关注点，领域层应保持对基础设施与框架 API 的零依赖（`ddd-architecture.md`「明确禁止的依赖」）。 |
| 行为等价重构 | `Behavior_Equivalent_Refactor` | 本 spec 全程为纯重构：调整代码归属与建模结构，但对任何外部消费者（HTTP 客户端、前端、测试断言、日志观测点、事件时序）的可观测行为保持字面等价。 |
| 既有测试全绿基线 | `Existing_Test_Suite_Green` | `PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）在重构前后均全部通过；测试因文件移动而需调整 import 路径时，只改 import 不改断言语义。 |
| 领域建模正向样板 | `Domain_Model_Positive_Baseline` | 已存在的、把业务规则正确收敛进领域层的范式：`domain/run/state_machine.py::RunStateMachine`、`domain/run/workflow.py::WorkflowExecutionPolicy.validate()`、`domain/health/aggregator.py`、`domain/workspace/policy.py`。作为本 spec 新增领域服务/规则对象的风格与职责基准。 |
| Agent 端口 | `AgentPort` | `domain/agent/ports.py::AgentPort`，以 Protocol 定义"接收任务、自主执行、返回结果"的能力边界，含 `run` / `run_streaming` / `run_events` / `resume` 四方法。本 spec 不修改其方法签名。 |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `infrastructure/agent/react_agent_adapter.py::ReActAgentAdapter`，`AgentPort` 的具体实现，承载 3310 行 Agent Loop。 |
| Agent Loop 归属重划 | `Agent_Loop_Relocation` | 把 `ReAct_Agent_Adapter` 中"非技术依赖"的编排逻辑（推理→行动→观察循环控制、终止判定等）上提到领域层（如领域服务），仅在基础设施层保留对外部技术的真实封装（`approval_logging` / `serialization` / `round_stream_accumulator` / `tool_abuse_detector` / `workflow_capability_runtime` / `handoff_context` 及 `chat.usage.merge_usage` 等）。本期仅登记方向与不变量，实际搬迁留待独立 spec + ADR。 |
| 序列化职责外移 | `Serialization_Extraction` | 将领域对象上的 `to_dict()` 序列化职责移出领域层，改由基础设施层的序列化适配器/映射器承担，或改用不侵入领域对象的独立序列化函数；领域对象本身不再承载"如何变成 dict"的知识。 |
| 领域日志解耦 | `Domain_Logging_Decoupling` | 从 `domain/chat/context.py` 移除对 `logging` 的直接依赖，使该领域模块回到零框架/零技术依赖；被移除的日志行为若确有必要，改由调用方（应用层/基础设施层）承担或以领域中立的方式表达。 |
| 应用层拆分方案登记 | `Application_Split_Plan_Record` | 针对 `Application_Transaction_Script` 的三大文件，产出"哪些逻辑本属领域、建议如何拆分"的诊断记录（写入本 spec design 或 `TODO.md`/独立 spec），本期不执行实际搬迁。 |
| 架构决策记录 | `Architecture_Decision_Record` | `docs/adr/` 下的 ADR。凡改变 Port/Adapter 归属或引入领域服务/实体等一等抽象的决策，须按 `docs/steering/adr.md` 新增 ADR（从 0007 起），并在 design 回链。 |
| DDD 战术建模规范缺口 | `DDD_Tactical_Modeling_Gap` | 对标业界主流 DDD 后确认的规范空白：本项目 steering 在六边形架构/分层依赖与工程治理（ADR/change-discipline/SRP）达到甚至超越主流，但**战术设计维度覆盖不完整**——`ddd-architecture.md` 全文仅一句话提及"实体、值对象、领域事件"三词、无任何建模规则；聚合根、聚合边界、实体、领域服务、仓储语义、限界上下文、通用语言在 steering 中全部缺失或未定义放置规则。这是需求 1–5 那些代码偏差的**规范根源**：现规范只要求"领域层不依赖基础设施"、从未要求"业务规则必须收敛进实体/领域服务"，故贫血模型在现规范下反而"合规"。 |
| DDD 战术建模规范 | `DDD_Tactical_Modeling_Spec` | 本期拟新增的战术建模 steering 规范（建议路径 `docs/steering/ddd-tactical-modeling.md`，最终文件名/是否并入 `ddd-architecture.md` 由 design 决定），明确聚合根/聚合边界、实体、领域服务、值对象、仓储语义在本仓库的建模规则与 `domain/*/` 下文件组织放置规则，并以 `Domain_Model_Positive_Baseline` 为范例基准。 |
| 聚合根与聚合边界 | `Aggregate_Root` | 主流 DDD 战术构件：聚合根是聚合的唯一外部入口、聚合边界即一致性/事务边界。当前 steering 完全未定义其在本仓库的建模与放置规则；`DDD_Tactical_Modeling_Spec` 须补齐（含"Agent 工作台状态多为会话/流式态、聚合一致性边界影响有限"这一上下文说明）。 |
| 领域服务放置规则 | `Domain_Service_Placement` | `DDD_Tactical_Modeling_Spec` 须规定领域服务（无自然归属实体的跨对象业务规则）在本仓库的定义方式与文件放置约定（如 `domain/<子域>/domain_service.py` 或与既有样板一致的组织），并以 `RunStateMachine` / `WorkflowExecutionPolicy` / `health/aggregator.py` / `workspace/policy.py` 为范例。 |
| Pydantic 与领域边界澄清 | `Pydantic_Domain_Boundary_Clarification` | 消解 `pydantic-model.md`（"领域数据传递优先使用 Pydantic 模型"、"领域值对象优先 `ConfigDict(frozen=True)`"）与 `ddd-architecture.md`（把 Pydantic 列入领域层"明确禁止的依赖"）之间的方向性二义。代码以脚投票：`domain/` 下 19 个文件用 dataclass、0 个用 Pydantic `BaseModel`。本期据既成实践确立"领域层用语言原生类型/dataclass、Pydantic 仅在 API/DTO 边界"，并**修订 `pydantic-model.md` 相关措辞**（属 change-discipline §4 允许的"确需调整规范本身的独立显式改动"）。 |
| 通用语言与限界上下文 | `Ubiquitous_Language` | 主流 DDD 战略构件的轻量表达。本仓库已有子域划分（`agent` / `chat` / `task` / `run` / `model_access` / `workspace` / `health` / `prompt` / `storage`）天然构成限界上下文；`DDD_Tactical_Modeling_Spec` 须补充"以子域目录为限界上下文、术语在各上下文内保持一致"的轻量约定，不引入重量级上下文映射机制。 |

## 需求

### 需求 1：`Domain_Logic_In_Infrastructure` 归属登记与不变量约束（高，建议独立后续 spec）

**用户故事：** 作为后端架构维护者，我希望明确"3310 行 ReAct Agent Loop 应归属领域层"这一判断，并把它的重划方向与不可破坏的不变量登记下来，以便后续以独立 spec + ADR 的方式安全落地，而不在本期贸然搬迁核心业务算法。

#### 验收标准

1. THE `Architecture_Decision_Record` SHALL 记录"`ReAct_Agent_Adapter` 中的 Agent Loop 编排逻辑属领域关注点、应经 `Agent_Loop_Relocation` 上提，仅保留真实技术封装于基础设施层"这一方向，并列明其判定依据（未封装外部 SDK、自研业务算法、docstring 自称基础设施层、三层 LOC 失衡 domain 8463 / application 9875 / infrastructure 24096）。
2. WHEN 用户在审批阶段未将需求 1 纳入本期实施范围, THE `ReAct_Agent_Adapter` SHALL 保持文件位置与行为完全不变（本期不移动、不重写），需求 1 仅产出方向登记与 ADR 草案。
3. IF `Agent_Loop_Relocation` 在本期或后续 spec 落地, THEN THE `AgentPort` SHALL 保持 `run` / `run_streaming` / `run_events` / `resume` 四方法签名不变；重划不得改变端口契约。
4. IF `Agent_Loop_Relocation` 落地, THEN THE `Behavior_Equivalent_Refactor` SHALL 成立：搬迁后 `ReActAgentAdapter` 对外可观测行为（`AgentResult` / `StreamingChunk` / `AgentStreamEvent` 字段与时序、`terminated_reason` 取值、日志观测点）与搬迁前字面等价。
5. IF `Agent_Loop_Relocation` 落地, THEN THE `Existing_Test_Suite_Green` SHALL 成立：既有测试全部通过；因文件移动导致的 import 路径调整只改 import、不改断言语义。
6. THE 需求 1 SHALL NOT 推翻 `agent-adapter-refactor` v3 已落定的任一行为决策（全程 stream、工具 timeout、`max_total_tokens`、循环耗尽 assert、`tool_arguments_delta`）；`Agent_Loop_Relocation` 仅重划代码归属，不改这些行为语义。

### 需求 2：`Anemic_Domain_Model` 局部试点纠偏（中）

**用户故事：** 作为领域模型的维护者，我希望在一个选定子域内，以 `Domain_Model_Positive_Baseline` 为基准把散落的业务规则收敛为领域服务或带行为的领域对象，以便验证"领域层承载业务规则"的范式可在本仓库低风险落地，并为后续子域提供样板。

#### 验收标准

1. THE 需求 2 SHALL 在 design 阶段选定**恰好一个**试点子域（如 `domain/task` 或 `domain/agent`），并说明选择理由与不纳入其他子域的原因（遵循 `change-discipline` 最小改动）。
2. THE 试点新增的领域服务/规则对象 SHALL 以 `Domain_Model_Positive_Baseline`（`RunStateMachine` / `WorkflowExecutionPolicy.validate()`）为职责与风格基准：只承载业务规则判定，不依赖 `application/` 或 `infrastructure/`，不引入框架 API。
3. THE 试点收敛的业务规则 SHALL 是"当前散落在 `application/` 或 `infrastructure/` 中、但本质属于领域判定"的既有规则；`Behavior_Equivalent_Refactor` SHALL 成立——收敛后规则的输入/输出判定结果与收敛前逐一等价，不新增/删除/更改任何一条规则。
4. THE 试点新增的领域类 SHALL 遵循 `code-documentation.md`（中文 docstring 说明职责）与 `python-typing-lint.md`（全量类型标注、禁裸 `Any`、`ruff`/`pyright` 零新增错误）。
5. IF 试点引入领域服务这一一等抽象, THEN THE `Architecture_Decision_Record` SHALL 记录该决策及其备选方案与未采纳原因（`adr.md` 硬要求）。
6. THE `Existing_Test_Suite_Green` SHALL 成立；THE 试点 SHALL 为新增领域服务/规则对象补充聚焦其业务规则的单元测试（置于 `test/domain/<子域>/` 下）。
7. THE 需求 2 SHALL NOT 改造既有 `Domain_Model_Positive_Baseline` 四处样板，也不在本期把试点范式强推到所有子域。

### 需求 3：`Domain_Serialization_Concern` 序列化职责外移（低）

**用户故事：** 作为领域模型的维护者，我希望领域对象不再自带 `to_dict()` 序列化职责，以便领域层专注业务语义、序列化关注点回归基础设施层，符合 SRP 与 DDD 分层规范。

#### 验收标准

1. THE 需求 3 SHALL 在 design 阶段清点 `domain/` 下全部 `to_dict()`（至少含 `domain/run/workflow.py` 8 处、`domain/chat/context.py` 4 处、`domain/health/value_objects.py`、`domain/agent/guardrails.py`、`domain/agent/segmented_execution.py`），并逐一给出外移目标（基础设施序列化适配器 / 独立映射函数）。
2. THE `Serialization_Extraction` SHALL 把领域对象的 `to_dict()` 序列化逻辑移出领域对象自身；重构后领域对象不再承载"如何变成 dict"的知识。
3. THE `Serialization_Extraction` SHALL 保持 `Behavior_Equivalent_Refactor`：对任一被外移对象，外移后产出的 dict 结构（键集合、取值、嵌套形态）与外移前 `to_dict()` 的输出逐字段等价。
4. WHILE 某个 `to_dict()` 被现有序列化往返测试覆盖（如 `test/domain/chat/test_context_serialization_roundtrip_property.py`）, WHEN 执行 `Serialization_Extraction`, THE `Existing_Test_Suite_Green` SHALL 成立：往返序列化的语义不变，测试只按新调用位置调整而不改断言语义。
5. IF 某处 `to_dict()` 的外移会改变 `domain/` 与 `infrastructure/` 的依赖归属（引入新的序列化适配器抽象）, THEN THE `Architecture_Decision_Record` SHALL 记录该结构性决策。
6. THE 需求 3 SHALL NOT 改变任何序列化产物的对外线格式（wire format）；上游消费方（持久化、HTTP、事件）看到的 JSON/字典结构保持不变。

### 需求 4：`Application_Transaction_Script` 诊断与拆分方案登记（低，本期不搬迁）

**用户故事：** 作为应用层维护者，我希望先摸清三大应用层文件里"哪些逻辑本属领域"，产出可评审的拆分方案，以便在领域建模方向明确后再安全搬迁，而不在本期与需求 2 同时大改互相干扰。

#### 验收标准

1. THE `Application_Split_Plan_Record` SHALL 对 `application/container_config.py`（2004 行）、`application/run/workflow_orchestrator.py`（1367 行）、`application/run/run_application_service.py`（831 行）逐文件诊断，标注其中"本属领域判定/规则"与"确属应用编排/组合根装配"的部分。
2. THE `Application_Split_Plan_Record` SHALL 对判定为"本属领域"的逻辑给出建议去向（迁往哪个领域服务/子域），并按 `change-discipline` 标注该搬迁的风险档与建议流程门（是否需 ADR、是否需独立 spec）。
3. THE 需求 4 SHALL NOT 在本期执行任何实际代码搬迁或文件拆分；产出物仅为诊断与登记（写入本 spec design 或 `TODO.md`/独立 spec 条目），以保证 `Existing_Test_Suite_Green` 与 `Behavior_Equivalent_Refactor` 平凡成立（零代码改动）。
4. IF 诊断认定 `application/container_config.py` 的组合根装配职责应保留在应用层, THEN THE `Application_Split_Plan_Record` SHALL 明确记录"组合根引用 `domain/` Port 与 `infrastructure/` Adapter 属 `ddd-architecture.md` 允许的例外"，不将其误判为待拆分坏味道。

### 需求 5：`Domain_Purity_Blemish` 领域日志解耦（轻微）

**用户故事：** 作为领域模型的维护者，我希望 `domain/chat/context.py` 不再直接依赖 `logging`，以便会话上下文领域模块回到零技术依赖，符合 `ddd-architecture.md`「明确禁止的依赖」。

#### 验收标准

1. THE `Domain_Logging_Decoupling` SHALL 移除 `domain/chat/context.py` 第 17 行的 `import logging` 及其模块级 `logger = logging.getLogger(__name__)` 依赖；重构后 `grep -n 'import logging' domain/chat/context.py` 零命中。
2. WHEN `domain/chat/context.py` 原本通过 `logger` 输出的信息在业务上确有保留必要, THE `Domain_Logging_Decoupling` SHALL 把该日志职责移交调用方（应用层/基础设施层）承担，或以领域中立的返回值/异常表达，不在领域层直接调用 `logging`。
3. WHEN `domain/chat/context.py` 原本的 `logger` 调用仅为调试且移除后无信息损失, THE `Domain_Logging_Decoupling` MAY 直接删除该调用，前提是不改变任何控制流与返回值。
4. THE `Domain_Logging_Decoupling` SHALL 保持 `Behavior_Equivalent_Refactor`：`ConversationContext` 及同模块内消息类型的行为（校验、序列化、历史恢复策略 `filter`/`raise` 分支）与解耦前逐一等价。
5. THE `Existing_Test_Suite_Green` SHALL 成立；覆盖 `domain/chat/context.py` 的既有测试（如 `test/domain/chat/` 下的历史恢复、序列化往返、消息层次测试）在解耦后全部通过。
6. THE 需求 5 SHALL 复核 `domain/` 其余模块是否还有同类框架/技术 import；若发现同类瑕疵，SHALL 在 design 或 `TODO.md` 中登记，但按 `change-discipline` 本期只处理已确认的 `domain/chat/context.py` 一处，其余登记待议。

### 需求 6：补齐本项目 DDD 战术建模规范约束（中）

**用户故事：** 作为规范维护者与后续长期迭代该仓库的 agent，我希望 steering 里补齐主流 DDD 的战术设计规范（聚合根、实体、领域服务、值对象、仓储语义、限界上下文、通用语言的建模与放置规则），并消解 Pydantic 与领域层的规范二义，以便后续 agent 不会再"合规地"写出贫血模型——即从规范根源上堵住需求 1–5 那些偏差的复发。

> **需求 6 与需求 1–5 的关系**：需求 1–5 是"修既有代码偏差"，需求 6 是"补规范护栏"。经对标确认：现规范只约束"领域层不依赖基础设施"，从未要求"业务规则必须收敛进实体/领域服务"，故贫血模型在现规范下反而合规——这正是偏差的规范根源（`DDD_Tactical_Modeling_Gap`）。补齐规范后，需求 2 的建模范式才有据可依、后续 agent 才不会重新跑偏。需求 6 只写规范文档、不改源码。

#### 验收标准

1. THE `DDD_Tactical_Modeling_Spec` SHALL 作为一份 steering 规范落地（建议路径 `docs/steering/ddd-tactical-modeling.md`，最终文件名或是否并入 `docs/steering/ddd-architecture.md` 由 design 决定），并在 `docs/steering/README.md` 与仓库根 `CLAUDE.md` 的 steering 索引中登记（遵循 `doc-sync.md`）。
2. THE `DDD_Tactical_Modeling_Spec` SHALL 明确定义 `Aggregate_Root`（聚合根与聚合/一致性边界）、实体（Entity）、`Domain_Service_Placement`（领域服务）、值对象（Value Object）、仓储（Repository）语义在本仓库的**建模规则**与 `domain/*/` 下的**文件组织放置规则**（如 `entities.py` / `aggregates.py` / `domain_service.py` 的约定或与既有样板一致的组织方式）。
3. THE `DDD_Tactical_Modeling_Spec` SHALL 以 `Domain_Model_Positive_Baseline`（`RunStateMachine` / `WorkflowExecutionPolicy.validate()` / `health/aggregator.py` / `workspace/policy.py`）为范例基准逐一举例说明各战术构件的正确形态，使规范可被后续 agent 直接对照。
4. THE `DDD_Tactical_Modeling_Spec` SHALL NOT 把「领域事件」列为本仓库推荐战术构件——领域事件总线已由 ADR-0001（`Accepted`）主动移除，规范须显式尊重该决策并回链 ADR-0001，不得诱导后续 agent 复活事件总线。
5. THE `Pydantic_Domain_Boundary_Clarification` SHALL 消解 `pydantic-model.md` 与 `ddd-architecture.md` 的方向性二义，明确"领域层用语言原生类型 / `@dataclass(frozen=True)`、Pydantic 仅用于 API/DTO 与配置边界"这一既成实践（依据：`domain/` 下 19 个文件用 dataclass、0 个用 Pydantic `BaseModel`）。
6. THE `Pydantic_Domain_Boundary_Clarification` SHALL **修订 `docs/steering/pydantic-model.md`** 的冲突措辞（至少调整其"API 边界与领域数据传递优先使用 Pydantic 模型""领域值对象优先使用 `ConfigDict(frozen=True)`"两处，使之与"领域层不用 Pydantic"一致）；该修订属 `change-discipline` §4 允许的"确需调整规范本身的独立显式改动"，SHALL 在变更说明中标注这是一次显式规范修订而非顺手改动。
7. THE `DDD_Tactical_Modeling_Spec` SHALL 补充 `Ubiquitous_Language` 与限界上下文的轻量表达：明确以既有子域目录（`agent` / `chat` / `task` / `run` / `model_access` / `workspace` / `health` / `prompt` / `storage`）为天然限界上下文、术语在各上下文内保持一致，不引入重量级上下文映射机制。
8. THE `DDD_Tactical_Modeling_Spec` SHALL 就"聚合一致性/事务边界"给出与本项目实际匹配的约束：说明 Agent 工作台状态多为会话/流式态、强一致性事务边界影响有限，规范以"轻量约束 + 何时才需要引入聚合边界"的判定指引形式表达，而非强制所有子域引入聚合根。
9. WHEN `DDD_Tactical_Modeling_Spec` 确立领域层建模范式（属方向级决策）, THE `Architecture_Decision_Record` SHALL 新增 **ADR-0007**（当前 ADR 至 0006），记录"确立领域层战术建模范式与 Pydantic 边界"的决策、背景、后果与备选方案（含未采纳原因，`adr.md` 硬要求），并在 `docs/adr/README.md` 索引登记；ADR-0007 SHALL 尊重且不 supersede ADR-0001（领域事件决策不回退）。
10. THE 需求 6 SHALL NOT 改动 `epsilon-boot/` 下任何源码——仅新增/修订 `docs/steering/` 规范文档与新增 `docs/adr/` ADR；因此 `Existing_Test_Suite_Green` 对需求 6 平凡成立（零代码改动、零测试影响）。
11. THE 需求 6 SHALL NOT 代替需求 2 执行贫血模型改造——本期不因新规范而强制把 `domain/` 所有子域立即重建为充血模型；规范只规定"今后如何建模"，既有代码的渐进纠偏仍归需求 2 的单子域试点。
