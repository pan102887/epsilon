---
status: Accepted
date: 2026-07-07
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0015：在 domain/agent 上提委派深度规范化与审批默认查表、平移分段续跑判定（充血化后续片）

## 背景与问题（Context）

前置整合报告（gap report P1）识别出「贫血领域模型」差距：多个子域的纯业务判定散落在 `application` / `infrastructure` 层。[ADR-0009](0009-introduce-domain-services-in-task-subdomain.md) 已在 `domain/task` 子域引入领域服务范式（`DelegationDepthPolicy` / `TaskContinuationPolicy` / `TaskStatusMapping` / `ApprovalResumePrecondition`），[ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md) 把 `StaticAgentGuardrailPolicy` 上提至 `domain/agent/guardrail_policy.py`，两者共同确立了「散落判定收敛进领域层、行为等价纯重构、逐候选推进」的可复制样板。

[ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md) 在「后续影响」中**显式**把 `domain/agent` 子域其余三个候选列为后续片评估对象：

- **`agent_config` 规范化**：`infrastructure/agent/agent_config.py::AgentRuntimeConfig._clamp_max_delegation_depth` 中「`max_delegation_depth <= 0`（含无法转 int 时保留原值）回退为默认值 3」这条纯规范化规则，及 `_DEFAULT_MAX_DELEGATION_DEPTH = 3` 常量。
- **`approval_policy_provider` 查表**：`infrastructure/agent/approval_policy_provider.py` 中 `_DEFAULT_POLICIES`（工具名 → (允许决策集, 风险标签)）、`_LOW_RISK_TOOLS`、`_APPROVE_REJECT` / `_APPROVE_EDIT_REJECT` 常量，以及 `policy_for` 无 override 时的默认查表判定。
- **`segmented_orchestration` 续跑判定**：`infrastructure/agent/segmented_orchestration.py::decide_next_segment` 与 `SegmentContinuationDecision`——已经是纯领域判定（仅 import `domain.agent.segmented_execution` 值对象、零 infra 依赖），只是物理放错层。

三者均为 `Domain_Logic_In_Infrastructure`（纯业务判定误落 / 放错基础设施层），与 [ADR-0009](0009-introduce-domain-services-in-task-subdomain.md)（`domain/task` 范式来源）、[ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md)（`domain/agent` 首片同源判断）方向一致。本 ADR 记录这三个候选的上提 / 平移职责归属决策与两处边界厘清结论；实际落地由 spec `ddd-anemic-domain-agent-followups` 逐候选执行，全程为**行为等价的纯重构**（`Behavior_Equivalent_Refactor`，对外可观测行为字面不变）。

## 决策（Decision）

我们将按既有范式把上述三个候选按 `change-discipline` **逐候选**行为等价收敛 / 平移进 `domain/agent`，三者判据、检查顺序、比较运算符（`>=`）、`None` 阈值短路语义、吞异常语义、决策集 / 风险标签取值均与上提前**逐一字面等价**：

- **(A) 委派深度规范化上提**：新建 `src/domain/agent/config_policy.py`，承载领域服务 `DelegationDepthNormalizationPolicy`（静态方法 `normalize(raw) -> object` 完整承载「`raw is None` 不动 / 可转 int 且 `<= 0` 归一为 3 / 转 int 抛 `TypeError`/`ValueError` 保留原值 / `> 0` 保持」四分支）与模块级常量 `DEFAULT_MAX_DELEGATION_DEPTH = 3`。`AgentRuntimeConfig`（pydantic-settings）**留 infrastructure** 但其 `_clamp_max_delegation_depth` validator 改为委托该领域服务归一，配置类身份、`AGENT_` 前缀、`agent_config` 全局实例、字段与默认值不变。
- **(B) 审批默认查表上提**：新建 `src/domain/agent/approval_lookup.py`，承载模块级公开常量（`APPROVE_REJECT` / `APPROVE_EDIT_REJECT` / `DEFAULT_POLICIES` / `LOW_RISK_TOOLS`）与领域服务 `ApprovalDefaultLookup`（`policy_for(tool_name) -> ApprovalPolicy` 默认查表、`decisions_for(tool_name) -> tuple[frozenset[str], str]` 供 `value is True` 分支复用），零 `json` 依赖。`StaticApprovalPolicyProvider` **保留类身份**、构造签名 `(enabled, interrupt_on)`、`ApprovalPolicyPort` 继承与 JSON 解析三方法，默认查表与 `_policy_from_value(value is True)` 分支委托领域构件。
- **(C) 分段续跑判定平移**：把 `decide_next_segment` + `SegmentContinuationDecision` 逐行字面平移到新建 `src/domain/agent/segmented_orchestration.py`（独立模块，与依赖的 `segmented_execution.py` 同子域同层），原 `infrastructure/agent/segmented_orchestration.py` 降为 re-export 垫片（参照 [ADR-0011](0011-relocate-agent-loop-leaf-orchestration-to-domain.md) / [ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md) 垫片范式），保护既有 3 处消费方与既有 infra 单测零改动通过。经垫片与经领域路径 import 的 `SegmentContinuationDecision` 为同一类对象、`decide_next_segment` 为同一函数对象，`isinstance` / `==` 语义不破裂。

### 两处边界厘清（显式记录）

- `Delegation_Depth_Normalization`（配置取值一元归一，`object -> int`，`config_policy.py::DelegationDepthNormalizationPolicy`）vs `DelegationDepthPolicy`（运行期深度二元比较 `current vs max`，`domain/task/policy.py`）——语义不同、**不合并**、不修改后者。
- `Segment_Continuation_Decision_Logic`（分段编排 12 门多阈值续跑门，决定是否自动进入下一段，`segmented_orchestration.py::decide_next_segment`）vs `TaskContinuationPolicy`（单次 Agent 终止原因 → 是否 PAUSED 映射，`domain/task/policy.py`）——语义不重叠、**不合并**、不重复上提、不修改后者。

### 留 infrastructure 取舍理由

- `AgentRuntimeConfig` 依赖 pydantic-settings（框架），须留 infrastructure，仅委托领域服务归一。
- `Approval_Json_Config_Parsing`（`_parse_interrupt_on` / `_policy_from_value` / `_validate_decisions`）依赖 `json`、面向 `HITL_INTERRUPT_ON` 配置字符串，按 [ADR-0008](0008-extract-domain-serialization-to-infrastructure-mappers.md) 属**配置边界技术关注点**，不进领域层（[config-source.md](../steering/config-source.md)）；`HitlConfigInvalidError` 抛出条件、消息与 override 分支（`True`/`False`/`list`/`dict`/非法）行为字面不变。

本决策声明为 `Behavior_Equivalent_Refactor`：判据零改动、`ApprovalPolicyPort` / `AgentGuardrailPolicyPort` / `DelegationPort` 方法名与签名不变、`ApprovalPolicy` / `ApprovalDecisionType` / `SegmentContinuationDecision` / `SegmentStopReason` 字段与语义不变、不引领域事件（**不 supersede [ADR-0001](0001-remove-domain-event-bus.md)**）。命名与放置对齐既有领域样板（`domain/task/policy.py` 以 `Policy` 结尾的领域服务惯例，[ADR-0009](0009-introduce-domain-services-in-task-subdomain.md) / [ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md)）。

## 后果（Consequences）

- **正面**：
  - 三候选领域判定（委派深度归一 / 审批默认查表 / 分段续跑门）回归领域层，可**脱离运行时单测**（本片新增三个 `test/domain/agent/*_unit.py` 覆盖各候选全分支）。
  - 为 `domain/agent` 子域续立样板（继 [ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md) 之后），领域保持纯净——三领域文件零 `application`/`infrastructure`/框架/Pydantic/`json`/logging/OTel/ContextVar 依赖。
  - 两处易混淆边界（规范化 vs 比较判定、分段续跑门 vs 单次终止映射）被显式记录，避免后续借收敛之名错误耦合。
- **负面 / 代价**：
  - `infrastructure/agent/segmented_orchestration.py` 降为 re-export 垫片，与领域实现**临时并存**，形成一个转发模块的过渡态。
  - 候选 C 垫片清理（删除 + 改全部消费方引用点）**待后续片**按 [change-discipline.md](../steering/change-discipline.md) 处理，本片不清理以控制改动面。
- **后续影响**：
  - 候选 A（`AgentRuntimeConfig`）、候选 B（`StaticApprovalPolicyProvider`）的对外符号本就留原位、无移动，无需垫片；仅候选 C 的符号物理迁走，故设垫片。
  - 本决策为 `Behavior_Equivalent_Refactor`，不改任何对外可观测行为、不新增第三方依赖（`pyproject.toml` / `uv.lock` 不变）、不引领域事件 / 事件总线（尊重 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) §8 与 [ADR-0001](0001-remove-domain-event-bus.md)）；行为等价由三个领域服务单测 + 各调用点既有回归测试双重锁定。
  - 依据回链：[ADR-0009](0009-introduce-domain-services-in-task-subdomain.md)（`domain/task` 范式来源）、[ADR-0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md)（`domain/agent` 首片同源方向）、[ADR-0008](0008-extract-domain-serialization-to-infrastructure-mappers.md)（配置 / 序列化解析归属基础设施）、[ADR-0001](0001-remove-domain-event-bus.md)（不复活事件总线）。

## 备选方案（Alternatives）

- **方案 A：维持三处判定散落在基础设施不动** —— 未采纳原因：即本差距本身，`Domain_Logic_In_Infrastructure` 未被纠正，违反 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) §4 领域服务放置护栏与 SRP。
- **方案 B：把 `AgentRuntimeConfig` / JSON 解析整体移入领域层** —— 未采纳原因：会把 pydantic-settings 框架依赖与 `json` 解析引入领域层，违反 §4 领域服务零框架依赖与 [ADR-0008](0008-extract-domain-serialization-to-infrastructure-mappers.md) 配置边界归属；仅上提纯规则、委托边界薄适配才符合分层。
- **方案 C：候选 C 并入 `segmented_execution.py`** —— 未采纳原因：混淆「分段值对象定义」与「续跑编排判定」两类职责（违 SRP）；独立模块 `segmented_orchestration.py` 与原 infra 文件同名，使 re-export 垫片更直观、diff 最小。
- **方案 D：合并候选 A 与 `DelegationDepthPolicy` / 候选 C 与 `TaskContinuationPolicy`** —— 未采纳原因：两两语义不同（规范化 vs 比较判定；分段多阈值续跑门 vs 单次终止原因映射），借收敛之名统一会引入错误耦合；显式厘清边界、各自独立更正确。
- **方案 E：引入领域事件 / 事件总线承载三候选判定** —— 未采纳原因：直接违反 [ADR-0001](0001-remove-domain-event-bus.md)（已 `Accepted` 移除领域事件总线）与 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) §8；三候选均为纯函数返回值判定，无需事件。
