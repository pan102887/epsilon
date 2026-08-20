# 需求文档：贫血领域模型单子域充血化试点（domain/agent）

## 简介

### 背景与动机

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构。整合报告 `docs/spec/ddd-gap-analysis/report.md` 的 **P1（贫血模型单子域充血化试点）** 指出：本仓库 `domain/` 下的领域对象普遍是「行为仅限 `__post_init__` 校验」的贫血数据载体，本质属于领域判定的业务规则却散落在 `application/` 与 `infrastructure/` 中。姊妹 spec `docs/spec/ddd-anemic-domain-pilot`（ADR-0009）已在 **`domain/task`** 子域完成同类试点（新增 `domain/task/policy.py` 的 `DelegationDepthPolicy` / `TaskContinuationPolicy` / `TaskStatusMapping` / `ApprovalResumePrecondition`），evaluator PASS，建立了「行为等价纯重构 + 零基础设施依赖领域服务 + 单测锁定」的可复制范式。

本 spec 是对**另一个子域 `domain/agent`** 的同类试点，据 ADR-0009 建立的范式，按 `change-discipline.md` **逐子域推进**。首批范围严格锁定为 `infrastructure/agent/static_guardrail_policy.py` 的 `StaticAgentGuardrailPolicy`：该类整文件几乎全是纯业务判定（任务类型分类、预算/风险护栏决策、阈值比较、分类启发式），只 import 领域类型（`domain.agent.guardrails`、`domain.run`），无 I/O、无 ContextVar、无 OTel、无 logging、无 Pydantic，是「不封装任何外部 SDK、纯规则判定却落在 infrastructure」的典型 `Domain_Logic_In_Infrastructure`（与 ADR-0010 对 Agent Loop 的判断同源）。

> 说明：整合报告 P2 已把 ReAct Agent Loop 的循环编排/终止判定/工具控制流纯判定上提到 `domain/agent/agent_loop_policy.py` 与 `agent_loop_orchestration.py`（ADR-0010/0011/0012），**这些已在领域层，不属本 spec 范围**。

### 范围内行为（In Scope）

- 把 `StaticAgentGuardrailPolicy` 的**判定逻辑逐条字面等价**上提到领域层（建议落点 `domain/agent/guardrail_policy.py`，具体模块名由 designer 定，须对齐 `domain/task/policy.py` 命名基准）：
  - 任务类型分类：`classify_run` / `classify_payload` 及模块级 `_looks_batch` / `_segment_count` 启发式；
  - 护栏决策：`evaluate_run_start` / `evaluate_model_completed` / `evaluate_tool_before_execution` / `evaluate_tool_after_execution`；
  - 内部判定：`_budget_decision`（token / duration / context growth / repeated tool call / consecutive failure 阈值比较）与 `_risk_decision`（critical / high 风险工具决策，含 OBSERVE / ENFORCE 模式分支）；
- 基础设施保留薄适配：装配 `GuardrailPolicy`、按 ADR-0008 判定归属的序列化技术关注点等；
- 保持消费方（`react_agent_adapter.py`）经 `AgentGuardrailPolicyPort` 的调用签名与时序不变、DI 装配对外行为不变；
- 新增聚焦业务规则的单元测试，置于 `test/domain/agent/`；既有 guardrail 测试全绿，仅按需调整 import；
- 新增 ADR 记录「在 `domain/agent` 引入领域服务/策略一等抽象」这一方向决策。

### 范围外边界（Out of Scope）

- 不改动已在领域层的 Agent Loop 编排（`agent_loop_policy.py` / `agent_loop_orchestration.py`，ADR-0010/0011/0012）；
- **明确不做（列为后续片）**：`infrastructure/agent/agent_config.py` 的 `max_delegation_depth` 规范化、`approval_policy_provider.py` 的策略查表、`segmented_orchestration.py::decide_next_segment`（后者与 `domain/task` 的 `TaskContinuationPolicy` 可能语义重叠，须谨慎评估后独立处理）；
- 不改动 `domain/agent/guardrails.py` 已承载的值对象（`GuardrailDecision` / `GuardrailPolicy` / `GuardrailEvaluationContext` / `TaskExecutionClass` / `ToolRiskLevel` 等），避免重复上提；
- 不改动前端；
- 不引入任何新依赖（含 Pydantic 进入领域层）；
- 不引入领域事件总线或任何事件构件（尊重 ADR-0001，不 supersede）；
- 不新增、删除或更改任何一条业务规则（本 spec 为**行为等价纯重构**）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 试点子域 | `Pilot_Subdomain` | 本 spec 唯一选定的充血化试点子域，取值固定为 `domain/agent`。 |
| 静态护栏策略 | `Static_Agent_Guardrail_Policy` | 现居 `infrastructure/agent/static_guardrail_policy.py::StaticAgentGuardrailPolicy` 的类，基于确定性规则与静态配置做任务分类与护栏决策，是本试点唯一的首批充血候选。 |
| 护栏策略领域服务 | `Guardrail_Policy_Domain_Service` | 本 spec 上提到领域层承载 `Static_Agent_Guardrail_Policy` 全部纯判定的领域服务（建议落点 `domain/agent/guardrail_policy.py`，具体名由 designer 定），零 `application`/`infrastructure` 依赖。 |
| 护栏策略配置 | `Guardrail_Policy` | `domain/agent/guardrails.py::GuardrailPolicy` 值对象，承载 mode / enforce_critical_tools / enforce_high_risk_tools / max_total_tokens 等阈值配置。 |
| 护栏决策 | `Guardrail_Decision` | `domain/agent/guardrails.py::GuardrailDecision` 值对象，含 action / reason / message / mode / metadata。 |
| 护栏评估上下文 | `Guardrail_Evaluation_Context` | `domain/agent/guardrails.py::GuardrailEvaluationContext` 值对象，护栏评估的纯数据输入。 |
| 任务类型分类 | `Task_Execution_Class` | `domain/agent/guardrails.py::TaskExecutionClass` 枚举：SHORT_QA / TOOL_TASK / LONG_TASK / BATCH_TASK。 |
| 工具风险等级 | `Tool_Risk_Level` | `domain/agent/guardrails.py::ToolRiskLevel` 枚举：LOW / MEDIUM / HIGH / CRITICAL。 |
| 护栏策略端口 | `Agent_Guardrail_Policy_Port` | `domain/agent/ports.py::AgentGuardrailPolicyPort` 协议，定义 `classify_payload` 与四个 `evaluate_*` 方法，是消费方与策略实现之间的契约边界。 |
| 护栏消费方 | `Guardrail_Consumer` | 经 `Agent_Guardrail_Policy_Port` 消费护栏策略的 `infrastructure/agent/react_agent_adapter.py`，通过鸭子类型 `getattr` 调用 `policy` 属性、`evaluate_model_completed` / `evaluate_tool_before_execution` / `evaluate_tool_after_execution`。 |
| DI 装配点 | `Guardrail_Policy_Wiring` | `application/container_config.py::_create_guardrail_policy`，`new` 具体策略并注入 `Guardrail_Policy` 配置。 |
| JSON 安全序列化 | `Json_Safe_Serialization` | 现居 `static_guardrail_policy.py` 模块级 `_json_safe`（把 enum 转 value 用于 metadata）的技术关注点；`domain/agent/guardrails.py` 内已存在同名等价 helper。 |
| 基础设施中的领域逻辑 | `Domain_Logic_In_Infrastructure` | 不封装任何外部 SDK、纯规则判定却落在 `infrastructure/` 的坏味道，本 spec 待纠偏对象。 |
| 领域服务 | `Domain_Service` | 无自然归属某实体/值对象的跨对象业务判定构件，零 `application`/`infrastructure` 依赖、不引框架 API、可脱离运行时单测，见 `ddd-tactical-modeling.md` §4。 |
| 行为等价纯重构 | `Behavior_Equivalent_Refactor` | 不改变任何对外可观测行为的重构：所有被上提规则的输入→输出判定逐一等价，不新增/删除/更改任何一条规则，不改变时序。 |
| 契约不变 | `Contract_Invariance` | `Agent_Guardrail_Policy_Port` 的方法名/签名、`Guardrail_Decision` 与 `Guardrail_Evaluation_Context` 等值对象字段与评估时序、`Guardrail_Policy_Wiring` 的对外行为均保持不变。 |
| 既有测试全绿 | `Existing_Test_Suite_Green` | 在 `epsilon-boot/` 下执行 `PYTHONPATH=src uv run --frozen pytest` 全部通过。 |
| 架构决策记录 | `ADR` | 架构级决策的只增不改记录，见 `docs/steering/adr.md` 与 `docs/adr/README.md`，本 spec 新增编号在落地时以 `ls docs/adr/` 核验后确定（倾向 0014）。 |

## 需求

### 需求 1：确认唯一试点子域并锁定首批范围

**用户故事：** 作为架构负责人，我希望本 spec 只在 `domain/agent` 子域内、且仅对 `Static_Agent_Guardrail_Policy` 充血化并显式说明取舍，以便遵循最小改动纪律、避免范式蔓延与范围过大。

#### 验收标准

1. THE `Pilot_Subdomain` SHALL 取值为 `domain/agent`，且本 spec 的全部代码改动仅落在 `domain/agent/`（新增/承载 `Guardrail_Policy_Domain_Service`）、`Static_Agent_Guardrail_Policy` 现有文件、其消费方与 `Guardrail_Policy_Wiring` 的薄适配、以及 `test/domain/agent/`。
2. THE `Behavior_Equivalent_Refactor` SHALL 仅上提 `Static_Agent_Guardrail_Policy` 一个类的判定逻辑，SHALL NOT 触及 `agent_config.py` 的 `max_delegation_depth` 规范化、`approval_policy_provider.py` 的策略查表、`segmented_orchestration.py::decide_next_segment`（均列为后续片）。
3. THE `Behavior_Equivalent_Refactor` SHALL NOT 改动 `domain/agent/guardrails.py` 已承载的值对象与枚举，SHALL NOT 重复上提任何既有领域构件。
4. THE `Behavior_Equivalent_Refactor` SHALL NOT 改动已在领域层的 Agent Loop 编排（`agent_loop_policy.py` / `agent_loop_orchestration.py`，ADR-0010/0011/0012）。

### 需求 2：把静态护栏策略的判定逻辑上提为领域服务

**用户故事：** 作为维护者，我希望护栏的任务分类与决策判定住在领域层，以便这些纯规则可脱离基础设施被单元测试锁定，并消除 `Domain_Logic_In_Infrastructure` 坏味道。

#### 验收标准

1. THE `Guardrail_Policy_Domain_Service` SHALL 位于 `domain/agent/` 下，为零 `application`/`infrastructure` 依赖、不引框架 API 的 `Domain_Service`，命名与放置对齐 `domain/task/policy.py`、`domain/run/state_machine.py`、`domain/workspace/policy.py` 基准。
2. THE `Guardrail_Policy_Domain_Service` SHALL 承载 `classify_run` 与 `classify_payload` 的任务分类判定，其判据与既有实现逐一等价：`classify_run` 依 `latest_checkpoint_id` / `can_continue` / `_segment_count(...) > 1` 判 `LONG_TASK`，否则委托 `classify_payload(payload, has_tools=True)`；`classify_payload` 依 `_looks_batch` 判 `BATCH_TASK`、依 `RunKind.TASK` 与 `has_tools` 分派 `TOOL_TASK` / `LONG_TASK` / `SHORT_QA`。
3. THE `Guardrail_Policy_Domain_Service` SHALL 承载 `_budget_decision` 的全部阈值比较（token / duration_seconds×1000 / context_growth_messages / repeated_tool_call_count / consecutive_failure_count），保留检查顺序、比较运算符（`>=`）、`None` 阈值短路语义，以及 OBSERVE 模式返回 `observe`、ENFORCE 模式返回 `stop` 的分支，与既有实现逐一等价。
4. THE `Guardrail_Policy_Domain_Service` SHALL 承载 `evaluate_tool_before_execution` 的风险门判定：先跑预算判定（非 ALLOW 直接返回），再按 `CRITICAL + enforce_critical_tools → STOP`、`HIGH + enforce_high_risk_tools → REQUIRE_APPROVAL`、`_risk_decision` 内 OBSERVE 模式降级为 `observe`，与既有 `evaluate_run_start` / `evaluate_model_completed` / `evaluate_tool_after_execution` 委托 `_budget_decision` 的行为逐一等价。
5. THE `Guardrail_Policy_Domain_Service` SHALL 承载模块级启发式 `_looks_batch`（`items`/`batch`/`targets`/`inputs` 为长度 > 1 的 list，或 `constraints` list 含「批量」子串）与 `_segment_count`（`segment_metadata["segment_count"]` 容错转 int），判据字面不变。
6. THE `Existing_Test_Suite_Green` SHALL 在上提后保持通过。

### 需求 3：保持端口契约与消费方时序不变

**用户故事：** 作为维护者，我希望上提不改变任何对外契约，以便 `Guardrail_Consumer` 与 DI 装配无感、护栏运行期行为字面不变。

#### 验收标准

1. THE `Agent_Guardrail_Policy_Port`（`domain/agent/ports.py::AgentGuardrailPolicyPort`）的方法名与签名 SHALL 保持不变（`classify_payload`、`evaluate_run_start`、`evaluate_model_completed`、`evaluate_tool_before_execution`、`evaluate_tool_after_execution`）。
2. THE `Contract_Invariance` SHALL 保持 `Guardrail_Consumer` 经鸭子类型 `getattr` 读取的 `policy` 属性与三个 `evaluate_*` 方法可用、返回 `Guardrail_Decision` 语义不变；`Guardrail_Decision` 与 `Guardrail_Evaluation_Context` 的字段、评估阶段与调用时序 SHALL NOT 改变。
3. WHEN designer 决定基础设施是否保留一个薄适配类（如仍名 `StaticAgentGuardrailPolicy`）委托 `Guardrail_Policy_Domain_Service`，THE `Behavior_Equivalent_Refactor` SHALL 使 `Guardrail_Policy_Wiring`（`_create_guardrail_policy`）对外注入行为与返回类型契约不变，`Guardrail_Policy` 配置的注入位置不变。
4. THE `Json_Safe_Serialization`（`_risk_decision` 内 metadata 的 enum→value 转换）归属 SHALL 在设计阶段依 ADR-0008 厘清：纯序列化关注点倾向留基础设施或复用 `domain/agent/guardrails.py` 既有等价 helper，不得因此改变 `Guardrail_Decision.metadata` 的对外产出。

### 需求 4：领域服务遵循战术建模与代码质量规范

**用户故事：** 作为规范守护者，我希望上提后的领域构件符合既有 steering 规范，以便它成为 `domain/agent` 乃至其他子域可复制的正确样板。

#### 验收标准

1. THE `Guardrail_Policy_Domain_Service` SHALL 使用 Python 原生类型或 `@dataclass`，SHALL NOT 引入 Pydantic、logging、OTel、ContextVar 或任何 `application`/`infrastructure`/框架依赖（对齐 `ddd-architecture.md` 领域层禁用依赖与 `ddd-tactical-modeling.md` §4「零基础设施依赖 + 可脱离运行时单测」标尺）。
2. THE `Guardrail_Policy_Domain_Service` SHALL 仅依赖 `domain/agent/guardrails` 与 `domain/run` 的领域类型，与上提前 `Static_Agent_Guardrail_Policy` 的 import 集合等价（不新增反向或跨层依赖）。
3. THE `Guardrail_Policy_Domain_Service` SHALL 具备中文 docstring 说明职责与不变量（对齐 `code-documentation.md`），具备全量类型标注、不使用裸 `Any`（分类启发式对 `dict[str, Any]` 的既有用法可保留），并通过 `ruff`/`pyright` 基线（零新增错误，对齐 `python-typing-lint.md`）。
4. THE `Guardrail_Policy_Domain_Service` SHALL 满足 SRP：只承载护栏领域判定一类职责（对齐 `srp-principle.md`）。
5. THE `Behavior_Equivalent_Refactor` SHALL NOT 引入任何新的第三方依赖，SHALL NOT 引入领域事件或事件总线构件（尊重 ADR-0001 与 `ddd-tactical-modeling.md` §8）。
6. WHEN 代码改动落地，THE `Behavior_Equivalent_Refactor` SHALL 按 `doc-sync.md` 同步受影响的主题文档（如 `docs/domain-model.md` / `docs/architecture.md`）与索引。

### 需求 5：为上提的领域服务补充聚焦业务规则的单元测试

**用户故事：** 作为维护者，我希望上提后的领域服务有独立单测锁定其判定，以便行为等价性可被回归验证。

#### 验收标准

1. THE 新增单元测试 SHALL 置于 `test/domain/agent/` 下，SHALL 不依赖 `application`/`infrastructure` 或框架运行时即可执行（脱离运行时单测，对齐正向样板特征）。
2. FOR ALL 上提的判定分支，THE 单元测试 SHALL 覆盖：`classify_run` 的 checkpoint / can_continue / segment_count 与 payload 委托分支、`classify_payload` 的 batch / task / chat × has_tools 分支、`_budget_decision` 的每条阈值命中与 None 短路、OBSERVE vs ENFORCE 模式、`evaluate_tool_before_execution` 的 CRITICAL / HIGH × enforce 开关分支、`_looks_batch` 与 `_segment_count` 边界。
3. WHEN 既有 guardrail 测试（如 `test/infrastructure/agent/test_static_guardrail_policy_unit.py`、`test_react_agent_guardrail_unit.py`、`test_react_agent_guardrail_runtime.py`、`test_workflow_hitl_guardrail_regression_unit.py`）因文件移动导致 import 路径变化，THE `Behavior_Equivalent_Refactor` SHALL 仅调整 import，不改动既有断言语义。
4. THE `Existing_Test_Suite_Green` SHALL 在新增测试后仍全部通过。

### 需求 6：新增 ADR 记录引入领域服务的架构级决策

**用户故事：** 作为架构负责人，我希望在 `domain/agent` 引入护栏策略领域服务这一一等抽象被 ADR 记录，以便决策可追溯且不与既有 ADR 冲突。

#### 验收标准

1. THE `ADR` SHALL 新增一条记录（编号落地时以 `ls docs/adr/` 核验，倾向 0014），记录「在 `Pilot_Subdomain`（`domain/agent`）引入 `Guardrail_Policy_Domain_Service` 一等抽象」的决策、备选方案与未采纳原因，并在 `docs/adr/README.md` 索引表登记。
2. THE `ADR` SHALL 声明本决策为 `Behavior_Equivalent_Refactor`，不改变任何对外可观测行为，并援引 ADR-0009（`domain/task` 同类决策）作为范式来源、ADR-0010（Agent Loop 归属领域层方向）作为同源判断。
3. THE `ADR` SHALL NOT supersede `ADR-0001`，SHALL NOT 复活领域事件总线（尊重 `ddd-tactical-modeling.md` §8 与 ADR-0001）。
4. THE `ADR` SHALL 说明本试点只覆盖 `Static_Agent_Guardrail_Policy`，`agent_config` / `approval_policy_provider` / `segmented_orchestration` 等留待后续按 `change-discipline` 逐子域、逐候选推进。
