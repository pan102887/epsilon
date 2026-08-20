# 需求文档：贫血领域模型充血化后续片（domain/agent 三候选：委派深度规范化 / 审批查表 / 分段续跑）

## 简介

### 背景与动机

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构。整合报告 `docs/spec/ddd-gap-analysis/report.md` 的 **P1（贫血模型单子域充血化试点）** 已在两个子域落地并建立可复制范式：

- **`domain/task` 子域**（ADR-0009，spec `docs/spec/ddd-anemic-domain-pilot`）：新增 `domain/task/policy.py` 的 4 个零基础设施依赖领域服务（`DelegationDepthPolicy` / `TaskContinuationPolicy` / `TaskStatusMapping` / `ApprovalResumePrecondition`）。
- **`domain/agent` 子域**（ADR-0014，spec `docs/spec/ddd-anemic-domain-pilot-agent`）：把 `StaticAgentGuardrailPolicy` 的判定逻辑上提到 `domain/agent/guardrail_policy.py`，`infrastructure/agent/static_guardrail_policy.py` 降为 re-export 垫片（参照 ADR-0011 round_outcome 垫片范式）。

范式核心是**行为等价纯重构（Behavior_Equivalent_Refactor）**：判据零改动、Port 契约不变、不引领域事件（尊重 ADR-0001）、领域层零 `infrastructure`/`framework`/`pydantic`/`logging`/`OTel` 依赖、脱离运行时单测锁定、新增 ADR、同步文档。

前序两个 spec 在「范围外边界」中把三处散落在 `infrastructure/agent/` 的领域判定**显式列为后续片**（见 `ddd-anemic-domain-pilot-agent/requirement.md` 需求 1 与需求 6）。本 spec 即承接这三个后续候选，据既有范式按 `change-discipline.md` **逐候选推进**，一次处理三者但相互独立、可分别验收。

### 范围内行为（In Scope）

本 spec 处理以下三个候选，每个候选各设一组需求，均须满足「行为等价 / 契约与消费方不变 / 领域纯净度 / 单测锁定 / 垫片保护既有 import」：

1. **委派深度上限规范化上提（候选 A）**：把 `infrastructure/agent/agent_config.py` 中「`max_delegation_depth <= 0` 回退默认值 3」这条**规范化领域规则**与 `_DEFAULT_MAX_DELEGATION_DEPTH = 3` 默认值上提为 `domain/agent/` 下的领域构件（策略/值对象，落点由 designer 定）；pydantic-settings 配置类 `AgentRuntimeConfig` 因依赖 pydantic **必须留在 infrastructure**，改为委托该领域规则完成归一。须与 `domain/task/policy.py::DelegationDepthPolicy`（深度比较判定）显式厘清边界。
2. **审批默认查表上提（候选 B）**：把 `infrastructure/agent/approval_policy_provider.py` 中的**纯查表领域规则**（`_DEFAULT_POLICIES` 工具名→(允许决策集, 风险标签) 默认查表、`_LOW_RISK_TOOLS`、`_APPROVE_REJECT` / `_APPROVE_EDIT_REJECT` 常量，以及 `policy_for` 的默认查表判定）上提到 `domain/agent/`；JSON 配置解析（`_parse_interrupt_on` / `_policy_from_value` / `_validate_decisions`，依赖 `json`、面向配置字符串）依 ADR-0008 判定为**配置边界技术关注点**保留在 infrastructure。
3. **分段续跑判定平移（候选 C）**：把 `infrastructure/agent/segmented_orchestration.py` 的 `decide_next_segment` 与 `SegmentContinuationDecision`（**已经是纯领域判定**，仅 import `domain.agent.segmented_execution` 值对象、零 infra 依赖，只是物理放错层）**平移**到 `domain/agent/`（与 `segmented_execution.py` 同子域同层），判据逐字面不变。须与 `domain/task/policy.py::TaskContinuationPolicy` 显式厘清边界。

三候选公共约束：

- 被移动/上提的 `infrastructure/agent/` 文件**降为 re-export 垫片**，保护既有 import 路径与测试引用（对齐 ADR-0011/0014 垫片范式）；
- 既有测试全绿，仅按需调整 import，不改断言语义；新增聚焦业务规则的单测置于 `test/domain/agent/`；
- 新增 ADR 记录本片方向决策；按 `doc-sync.md` 同步 `docs/domain-model.md` 与 `docs/architecture.md`。

### 范围外边界（Out of Scope）

- 不改动任何 Port 的方法名/签名：`ApprovalPolicyPort.policy_for`、`AgentGuardrailPolicyPort`、`DelegationPort` 均保持不变；
- 不改动已在领域层的 Agent Loop 编排（`agent_loop_policy.py` / `agent_loop_orchestration.py`，ADR-0010/0011/0012）与 `guardrail_policy.py`（ADR-0014）；
- 不改动 `domain/task/policy.py` 已有的 `DelegationDepthPolicy` / `TaskContinuationPolicy` 等领域服务（本片仅与之对比厘清边界，**不合并、不重复上提、不修改**）；
- 不改动 `domain/agent/segmented_execution.py` 已承载的值对象（`SegmentExecutionPolicy` / `SegmentBudgetUsage` / `SegmentProgressSnapshot` / `SegmentStopReason` 等）；
- 不把 `AgentRuntimeConfig` 及其 pydantic/pydantic-settings 依赖移出 infrastructure；不把 JSON 配置解析移入领域层；
- 不改动前端；
- 不引入任何新的第三方依赖（含 Pydantic / logging / OTel 进入领域层）；
- 不引入领域事件总线或任何事件构件（尊重 ADR-0001，不 supersede）；
- 不新增、删除或更改任何一条业务规则（本 spec 为 **Behavior_Equivalent_Refactor**）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 后续片 | `Followup_Slice` | 本 spec 承接前序 `ddd-anemic-domain-pilot-agent` 显式列出的三个 `domain/agent` 充血化后续候选（委派深度规范化 / 审批查表 / 分段续跑）的统称。 |
| 委派深度规范化规则 | `Delegation_Depth_Normalization` | 现居 `infrastructure/agent/agent_config.py::AgentRuntimeConfig._clamp_max_delegation_depth` 的领域规则：当 `max_delegation_depth` 取值 `<= 0`（含无法转 int 时保留原值）时回退为默认值 3；含 `_DEFAULT_MAX_DELEGATION_DEPTH = 3` 默认值常量。 |
| 委派深度规范化领域构件 | `Delegation_Depth_Normalization_Domain_Artifact` | 本片上提到 `domain/agent/` 承载 `Delegation_Depth_Normalization` 的领域构件（策略/值对象，具体名与落点由 designer 定），零 `application`/`infrastructure`/pydantic 依赖。 |
| Agent 运行时配置类 | `Agent_Runtime_Config` | `infrastructure/agent/agent_config.py::AgentRuntimeConfig`，pydantic-settings 配置类（前缀 `AGENT_`），因依赖 pydantic 须留在 infrastructure；本片改为委托 `Delegation_Depth_Normalization_Domain_Artifact`。 |
| 委派深度比较判定 | `Delegation_Depth_Policy` | `domain/task/policy.py::DelegationDepthPolicy`，做「当前/下一层深度是否超限」的比较判定（`exceeds_for_next_depth` / `exceeds_for_current_depth`）；与本片规范化语义不同，本片须与之厘清边界，不修改、不合并。 |
| 审批默认查表规则 | `Approval_Default_Lookup` | 现居 `approval_policy_provider.py` 的纯查表领域规则：`_DEFAULT_POLICIES`（工具名→(允许决策集, 风险标签)）、`_LOW_RISK_TOOLS`、`_APPROVE_REJECT`、`_APPROVE_EDIT_REJECT` 常量，以及 `policy_for` 在无 override 时的默认查表判定（含未命中工具依 `_LOW_RISK_TOOLS` 决定 `risk_label`）。 |
| 审批查表领域构件 | `Approval_Lookup_Domain_Artifact` | 本片上提到 `domain/agent/` 承载 `Approval_Default_Lookup` 的领域构件（落点由 designer 定），零 `application`/`infrastructure` 依赖、不引 `json`。 |
| 审批策略端口 | `Approval_Policy_Port` | `domain/agent/ports.py::ApprovalPolicyPort` 协议，定义 `policy_for(tool_name) -> ApprovalPolicy`，是消费方与实现之间的契约边界。 |
| 静态审批策略提供器 | `Static_Approval_Policy_Provider` | `infrastructure/agent/approval_policy_provider.py::StaticApprovalPolicyProvider`，实现 `Approval_Policy_Port`；本片保留其类身份与 JSON 解析职责，默认查表委托 `Approval_Lookup_Domain_Artifact`。 |
| 审批 JSON 配置解析 | `Approval_Json_Config_Parsing` | `_parse_interrupt_on` / `_policy_from_value` / `_validate_decisions` 三方法，依赖 `json` 且面向 `HITL_INTERRUPT_ON` 配置字符串，依 ADR-0008 判为配置边界技术关注点，保留在 infrastructure。 |
| 审批策略值对象 | `Approval_Policy` | `domain/agent/value_objects.py::ApprovalPolicy` 值对象（`tool_name` / `interrupt` / `allowed_decisions` / `risk_label`），其字段与语义不变。 |
| 分段续跑判定 | `Segment_Continuation_Decision_Logic` | 现居 `infrastructure/agent/segmented_orchestration.py::decide_next_segment` 的纯领域判定：按多阈值门（completed / approval_required / can_continue / tool_boundary / risk_gate / auto_disabled / max_continuations / token / duration / consecutive_paused / no_progress / repeated_tool_call）依序决定是否自动进入下一段。 |
| 分段续跑决策值对象 | `Segment_Continuation_Decision` | `SegmentContinuationDecision` 冻结 dataclass（`should_continue` / `stop_reason`），随 `Segment_Continuation_Decision_Logic` 平移到领域层。 |
| 分段执行值对象 | `Segmented_Execution_Value_Objects` | `domain/agent/segmented_execution.py` 已承载的 `SegmentExecutionPolicy` / `SegmentBudgetUsage` / `SegmentProgressSnapshot` / `SegmentStopReason` 等值对象，本片不改动。 |
| 任务续跑判定 | `Task_Continuation_Policy` | `domain/task/policy.py::TaskContinuationPolicy`，做「单次 Agent 终止原因 → 是否 PAUSED」映射；与 `Segment_Continuation_Decision_Logic`（分段编排多阈值续跑门）语义不重叠，本片须厘清边界，不合并、不重复上提。 |
| 续跑判定消费方 | `Segment_Continuation_Consumer` | 消费 `decide_next_segment` 的 `infrastructure/chat/chat_service_adapter.py`（2 处：约第 444、839 行）与 `infrastructure/task/task_agent_adapter.py`（1 处：约第 663 行）。 |
| 审批策略装配点 | `Approval_Policy_Wiring` | `application/container_config.py::_create_approval_policy`，`new` `Static_Approval_Policy_Provider` 并注入 `Approval_Policy_Port`。 |
| 委派配置消费方 | `Delegation_Config_Consumer` | 经全局 `agent_config` 实例读取 `max_delegation_depth` / `delegate_tool_enabled` 的 `container_config.py`（委派工具装配）与 `delegation_adapter.py` 等调用点。 |
| 向后兼容垫片 | `Backward_Compatibility_Shim` | 被上提/平移后 `infrastructure/agent/` 原文件保留的 re-export 模块，保护既有 import 路径与测试引用（参照 ADR-0011/0014）。 |
| 基础设施中的领域逻辑 | `Domain_Logic_In_Infrastructure` | 不封装任何外部 SDK、纯规则判定却落在 `infrastructure/` 的坏味道，本片待纠偏对象。 |
| 领域服务 | `Domain_Service` | 无自然归属某实体/值对象的跨对象业务判定构件，零 `application`/`infrastructure` 依赖、不引框架 API、可脱离运行时单测，见 `ddd-tactical-modeling.md` §4。 |
| 行为等价纯重构 | `Behavior_Equivalent_Refactor` | 不改变任何对外可观测行为的重构：所有被上提规则的输入→输出判定逐一等价，不新增/删除/更改任何一条规则，不改变时序。 |
| 契约不变 | `Contract_Invariance` | 相关 Port 的方法名/签名、值对象字段与语义、消费方调用时序、DI 装配对外行为均保持不变。 |
| 既有测试全绿 | `Existing_Test_Suite_Green` | 在 `epsilon-boot/` 下执行 `PYTHONPATH=src uv run --frozen pytest` 全部通过。 |
| 架构决策记录 | `ADR` | 架构级决策的只增不改记录，见 `docs/steering/adr.md` 与 `docs/adr/README.md`；本片新增编号在落地时以 `ls docs/adr/` 核验后确定（倾向 0015）。 |

## 需求

### 需求 1：锁定后续片范围为三个 domain/agent 候选

**用户故事：** 作为架构负责人，我希望本 spec 严格锁定为前序 spec 显式列出的三个 `domain/agent` 后续候选、且逐候选独立可验收，以便遵循 `change-discipline` 的最小改动与逐候选推进纪律，避免范式蔓延。

#### 验收标准

1. THE `Followup_Slice` SHALL 仅包含 `Delegation_Depth_Normalization`、`Approval_Default_Lookup`、`Segment_Continuation_Decision_Logic` 三个候选，全部代码改动仅落在 `domain/agent/`（新增/承载三候选领域构件）、三个候选现有 infrastructure 文件（降为 `Backward_Compatibility_Shim` 或改为委托）、`Approval_Policy_Wiring` 的薄适配、以及 `test/domain/agent/`。
2. THE `Behavior_Equivalent_Refactor` SHALL NOT 改动任何 Port 方法名或签名（`ApprovalPolicyPort.policy_for`、`AgentGuardrailPolicyPort`、`DelegationPort`）。
3. THE `Behavior_Equivalent_Refactor` SHALL NOT 改动已在领域层的 Agent Loop 编排（`agent_loop_policy.py` / `agent_loop_orchestration.py`，ADR-0010/0011/0012）与 `guardrail_policy.py`（ADR-0014）。
4. THE `Behavior_Equivalent_Refactor` SHALL NOT 修改、合并或重复上提 `domain/task/policy.py` 已有的 `Delegation_Depth_Policy` 与 `Task_Continuation_Policy`。
5. THE `Behavior_Equivalent_Refactor` SHALL NOT 新增、删除或更改任何一条业务规则，SHALL NOT 引入任何新的第三方依赖，SHALL NOT 引入领域事件或事件总线构件（尊重 ADR-0001 与 `ddd-tactical-modeling.md` §8）。

### 需求 2：上提委派深度规范化为领域构件（候选 A）

**用户故事：** 作为维护者，我希望「委派深度上限的归一规则」住在领域层，以便这条规则可脱离 pydantic 配置框架被单测锁定，同时保留 pydantic-settings 配置类在 infrastructure。

#### 验收标准

1. THE `Delegation_Depth_Normalization_Domain_Artifact` SHALL 位于 `domain/agent/` 下，为零 `application`/`infrastructure`/pydantic/logging/OTel 依赖的领域构件，承载「深度值 `<= 0` 时回退为默认值 3」这条规则与默认值 `3`，命名与放置对齐 `domain/task/policy.py`、`domain/agent/guardrail_policy.py` 基准。
2. THE `Delegation_Depth_Normalization_Domain_Artifact` SHALL 与 `_clamp_max_delegation_depth` 现有语义逐一等价：`raw is None` 时不改动；能转 int 且 `int(raw) <= 0` 时归一为 3；转 int 抛 `TypeError`/`ValueError` 时保留原值（吞异常语义不变）。
3. THE `Agent_Runtime_Config` SHALL 保留在 `infrastructure/agent/agent_config.py` 内、保留其 pydantic-settings 依赖与 `AGENT_` 前缀，SHALL 改为委托 `Delegation_Depth_Normalization_Domain_Artifact` 完成归一，其 `max_delegation_depth` / `delegate_tool_enabled` 字段、默认值与对外校验行为 SHALL NOT 改变。
4. THE `Delegation_Config_Consumer`（`container_config.py` 委派工具装配、`delegation_adapter.py` 等）经全局 `agent_config` 读取到的 `max_delegation_depth` 值 SHALL 与上提前逐一等价。
5. THE `Behavior_Equivalent_Refactor` SHALL 显式厘清 `Delegation_Depth_Normalization`（深度上限的规范化/归一）与 `Delegation_Depth_Policy`（深度是否超限的比较判定）的语义边界，二者 SHALL NOT 合并，`Delegation_Depth_Policy` SHALL NOT 被本片修改。

### 需求 3：上提审批默认查表为领域构件、JSON 解析留基础设施（候选 B）

**用户故事：** 作为维护者，我希望审批的默认工具查表规则住在领域层、JSON 配置解析留在基础设施，以便纯规则可脱离配置字符串被单测锁定，并按 ADR-0008 保持配置边界技术关注点归属清晰。

#### 验收标准

1. THE `Approval_Lookup_Domain_Artifact` SHALL 位于 `domain/agent/` 下，为零 `application`/`infrastructure` 依赖、不引 `json` 的 `Domain_Service`/值对象，承载 `_DEFAULT_POLICIES`、`_LOW_RISK_TOOLS`、`_APPROVE_REJECT`、`_APPROVE_EDIT_REJECT` 及默认查表判定，判据字面不变。
2. THE `Approval_Lookup_Domain_Artifact` SHALL 与 `policy_for` 在无 override 分支下的现有语义逐一等价：命中 `_DEFAULT_POLICIES` 时返回 `interrupt=True` 且带对应 `allowed_decisions` 与 `risk_label`；未命中时返回 `interrupt=False`，`risk_label` 依「工具在 `_LOW_RISK_TOOLS` 则为『低风险工具』否则为空串」；`enabled=False` 时返回 `interrupt=False` 且 `allowed_decisions` 为空的语义保持不变。
3. THE `Approval_Json_Config_Parsing`（`_parse_interrupt_on` / `_policy_from_value` / `_validate_decisions`）SHALL 依 ADR-0008 保留在 `infrastructure/agent/approval_policy_provider.py`，其 `json` 依赖、`HitlConfigInvalidError` 抛出条件与消息、override 分支（`True`/`False`/`list`/`dict`）行为 SHALL NOT 改变；`_policy_from_value` 在 `value is True` 时对 `_DEFAULT_POLICIES.get(tool_name, ...)` 的复用 SHALL 通过 `Approval_Lookup_Domain_Artifact` 取得等价结果。
4. THE `Approval_Policy_Port`（`policy_for(tool_name) -> ApprovalPolicy`）SHALL 方法名/签名不变；THE `Approval_Policy` 值对象字段与语义 SHALL NOT 改变。
5. THE `Approval_Policy_Wiring`（`_create_approval_policy`）SHALL 对外注入 `Static_Approval_Policy_Provider` 的行为与返回类型契约不变；`Static_Approval_Policy_Provider` 的构造签名（`enabled`、`interrupt_on`）SHALL NOT 改变。

### 需求 4：平移分段续跑判定至领域层（候选 C）

**用户故事：** 作为维护者，我希望已经是纯领域判定的分段续跑逻辑物理上住在 `domain/agent/`（与其依赖的分段值对象同层），以便消除物理放错层的坏味道并与 `TaskContinuationPolicy` 划清语义边界。

#### 验收标准

1. THE `Segment_Continuation_Decision_Logic`（`decide_next_segment`）与 `Segment_Continuation_Decision` SHALL 平移到 `domain/agent/` 下（与 `segmented_execution.py` 同子域同层），函数签名、keyword-only 参数、默认值与全部多阈值门的判定顺序、比较运算符（`>=`）、`None` 阈值短路语义、每条 `stop_reason` 返回值 SHALL 与现有实现逐一等价。
2. THE 平移后的 `Segment_Continuation_Decision_Logic` SHALL 仅依赖 `domain/agent/segmented_execution` 的值对象（`SegmentExecutionPolicy` / `SegmentBudgetUsage` / `SegmentProgressSnapshot` / `SegmentStopReason`），SHALL NOT 引入任何 `application`/`infrastructure`/框架依赖，SHALL NOT 改动 `Segmented_Execution_Value_Objects`。
3. THE `Segment_Continuation_Consumer`（`chat_service_adapter.py` 2 处、`task_agent_adapter.py` 1 处）SHALL 仅调整 `decide_next_segment` 的 import 路径，其调用参数、返回值消费与时序 SHALL NOT 改变。
4. THE `Behavior_Equivalent_Refactor` SHALL 显式厘清 `Segment_Continuation_Decision_Logic`（分段编排的多阈值续跑门）与 `Task_Continuation_Policy`（单次 Agent 终止原因→是否 PAUSED 映射）的语义边界，说明二者不重叠、不合并、不重复上提，`Task_Continuation_Policy` SHALL NOT 被本片修改。

### 需求 5：以垫片保护既有 import、既有测试仅调 import

**用户故事：** 作为维护者，我希望上提/平移不破坏既有 import 路径与测试引用，以便消费方与测试无感切换，降低回归风险。

#### 验收标准

1. FOR ALL 被上提/平移的 infrastructure 文件（`agent_config.py` 除外，因其配置类留原位），THE `Backward_Compatibility_Shim` SHALL 在原路径 re-export 领域实现，使既有 `from infrastructure.agent.segmented_orchestration import decide_next_segment`、`from infrastructure.agent.approval_policy_provider import StaticApprovalPolicyProvider` 等 import 保持可用（对齐 ADR-0011/0014 垫片范式）。
2. WHEN 既有测试因文件移动导致 import 路径变化（`test/infrastructure/agent/test_approval_policy_provider_unit.py`、`test_approval_policy_provider_property.py`、`test_segmented_orchestration_unit.py`、`test/domain/agent/test_agent_config_validation_unit.py`、`test_named_agent_config_properties.py` 等），THE `Behavior_Equivalent_Refactor` SHALL 仅调整 import，SHALL NOT 改动既有断言语义。
3. THE re-export 的 `SegmentContinuationDecision`、`StaticApprovalPolicyProvider` SHALL 与领域/基础设施实现为同一类对象，`isinstance`/`==` 语义 SHALL NOT 破裂。
4. THE `Existing_Test_Suite_Green` SHALL 在三候选上提/平移与 import 调整后保持通过。

### 需求 6：为上提的领域构件补充聚焦业务规则的单元测试

**用户故事：** 作为维护者，我希望三候选上提后的领域构件有独立单测锁定其判定，以便行为等价性可被回归验证。

#### 验收标准

1. THE 新增单元测试 SHALL 置于 `test/domain/agent/` 下，SHALL 不依赖 `application`/`infrastructure` 或框架运行时即可执行（脱离运行时单测，对齐正向样板特征）。
2. FOR ALL `Delegation_Depth_Normalization` 分支，THE 单元测试 SHALL 覆盖：`raw is None` 不改动、`<= 0` 归一为 3、正数保持、无法转 int 时保留原值三类分支。
3. FOR ALL `Approval_Default_Lookup` 分支，THE 单元测试 SHALL 覆盖：命中 `_DEFAULT_POLICIES` 各工具、`_APPROVE_REJECT` 与 `_APPROVE_EDIT_REJECT` 决策集、未命中且属 `_LOW_RISK_TOOLS`、未命中且非低风险三类查表结果。
4. FOR ALL `Segment_Continuation_Decision_Logic` 的 `stop_reason` 门，THE 单元测试 SHALL 覆盖每条阈值门命中与 `None` 阈值短路，以及全部门未触发时 `should_continue=True` 的分支。
5. THE `Existing_Test_Suite_Green` SHALL 在新增测试后仍全部通过。

### 需求 7：领域构件遵循战术建模与代码质量规范

**用户故事：** 作为规范守护者，我希望上提后的领域构件符合既有 steering 规范，以便它们成为 `domain/agent` 可复制的正确样板。

#### 验收标准

1. THE `Delegation_Depth_Normalization_Domain_Artifact` 与 `Approval_Lookup_Domain_Artifact` 与平移后的 `Segment_Continuation_Decision_Logic` SHALL 使用 Python 原生类型或 `@dataclass`，SHALL NOT 引入 Pydantic、logging、OTel、ContextVar 或任何 `application`/`infrastructure`/框架依赖（对齐 `ddd-architecture.md` 与 `ddd-tactical-modeling.md` §4）。
2. THE 三候选领域构件 SHALL 具备中文 docstring 说明职责与不变量（对齐 `code-documentation.md`），具备全量类型标注、不使用裸 `Any`（`approval_policy_provider` 中面向配置值的既有 `Any` 用法留在 infrastructure 解析侧），并通过 `ruff`/`pyright` 基线（零新增错误，对齐 `python-typing-lint.md`）。
3. THE 三候选领域构件 SHALL 各自满足 SRP：分别只承载深度规范化 / 审批默认查表 / 分段续跑判定一类职责（对齐 `srp-principle.md`）。
4. THE `Approval_Json_Config_Parsing` 保留在 infrastructure SHALL 符合 `config-source.md` 与 ADR-0008：JSON 解析、异常抛出等配置/技术关注点不进入领域层。
5. WHEN 代码改动落地，THE `Behavior_Equivalent_Refactor` SHALL 按 `doc-sync.md` 同步 `docs/domain-model.md`、`docs/architecture.md` 与相关索引。

### 需求 8：新增 ADR 记录本后续片的方向决策

**用户故事：** 作为架构负责人，我希望三候选上提/平移与两处边界厘清被 ADR 记录，以便决策可追溯且不与既有 ADR 冲突。

#### 验收标准

1. THE `ADR` SHALL 新增一条记录（编号落地时以 `ls docs/adr/` 核验，倾向 0015），记录「在 `domain/agent` 上提 `Delegation_Depth_Normalization` / `Approval_Default_Lookup` / 平移 `Segment_Continuation_Decision_Logic`」的决策、备选方案与未采纳原因，并在 `docs/adr/README.md` 索引表登记。
2. THE `ADR` SHALL 声明本决策为 `Behavior_Equivalent_Refactor`，不改变任何对外可观测行为，并援引 ADR-0009（`domain/task` 范式来源）、ADR-0014（`domain/agent` 首片同源判断）、ADR-0008（序列化/配置解析归属基础设施）作为依据。
3. THE `ADR` SHALL 显式记录两处边界厘清结论：`Delegation_Depth_Normalization` vs `Delegation_Depth_Policy`（规范化 vs 比较判定，不合并）、`Segment_Continuation_Decision_Logic` vs `Task_Continuation_Policy`（分段多阈值续跑门 vs 单次终止原因映射，不合并）。
4. THE `ADR` SHALL NOT supersede `ADR-0001`，SHALL NOT 复活领域事件总线（尊重 `ddd-tactical-modeling.md` §8 与 ADR-0001）。
5. THE `ADR` SHALL 说明 `Agent_Runtime_Config`（pydantic-settings）与 `Approval_Json_Config_Parsing` 因依赖边界保留在 infrastructure 的取舍理由。
