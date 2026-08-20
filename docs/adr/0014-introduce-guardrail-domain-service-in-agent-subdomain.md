---
status: Accepted
date: 2026-07-07
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0014：在 domain/agent 子域引入护栏策略领域服务一等抽象（充血化试点第二子域，StaticAgentGuardrailPolicy 上提领域层）

## 背景与问题（Context）

前置整合报告（gap report P1）识别出「贫血领域模型」差距：多个子域的纯业务判定散落在 `application` / `infrastructure` 层，领域层退化为仅承载数据与构造期校验的贫血载体。[ADR-0009](0009-introduce-domain-services-in-task-subdomain.md) 已在 `domain/task` 子域引入领域服务范式（`DelegationDepthPolicy` / `TaskContinuationPolicy` / `TaskStatusMapping` / `ApprovalResumePrecondition`），确立了「散落判定收敛进领域层、行为等价纯重构、逐子域推进」的可复制样板。本 ADR 是对 `domain/agent` 子域的**同类推进**——充血化试点的第二个子域。

评估对象 `StaticAgentGuardrailPolicy`（现居 `src/infrastructure/agent/static_guardrail_policy.py`，216 行）是**纯业务判定**：

- **任务类型分类**：`classify_run` / `classify_payload` 与启发式 `_looks_batch` / `_segment_count`（基于 payload 结构与工具可用性判定 `TaskExecutionClass`）。
- **预算护栏决策**：`_budget_decision` 按 token → duration → context_growth → repeated_tool → consecutive_failure 的固定顺序、`>=` 阈值比较、`None` 短路作确定性判定。
- **风险门决策**：`evaluate_tool_before_execution` / `_risk_decision` 按 `CRITICAL` / `HIGH` 风险等级与 `enforce_*` 开关、OBSERVE / ENFORCE 模式产出 `GuardrailDecision`。

该类**只依赖领域类型**（`domain.agent.guardrails` 的值对象 / 枚举 + `domain.run` 的 `RunKind` / `RunPayload` / `RunSnapshot`），无 SDK / I/O / `ContextVar` / OTel / logging / Pydantic 依赖——是典型的 `Domain_Logic_In_Infrastructure`（领域纯判定误落基础设施层），与 [ADR-0010](0010-relocate-agent-loop-to-domain-direction.md) 对 Agent Loop 编排「纯判定应归领域层」的方向判断**同源**。护栏侧规范早已由 [ADR-0007](0007-establish-domain-tactical-modeling-and-pydantic-boundary.md) 与 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md)（§4 领域服务放置、§8 不引领域事件）补齐，但 `StaticAgentGuardrailPolicy` 的代码尚未按护栏纠偏。

本 ADR 记录「在 `domain/agent` 子域引入护栏策略领域服务一等抽象」这一职责归属 / 结构性决策；实际落地由 spec `ddd-anemic-domain-pilot-agent` 执行，全程为**行为等价的纯重构**（`Behavior_Equivalent_Refactor`，对外可观测行为字面不变）。

## 决策（Decision）

我们将把 `StaticAgentGuardrailPolicy` 的**整类行为等价上提**到领域层新增文件 `src/domain/agent/guardrail_policy.py`，把散落于基础设施的护栏领域判定收敛进领域层。具体落点：

- 新建 `src/domain/agent/guardrail_policy.py`，**保留原类名 `StaticAgentGuardrailPolicy`**（不改名 `GuardrailEvaluationPolicy`），结构化实现领域内既有 `AgentGuardrailPolicyPort`（`domain/agent/ports.py` 的 `Protocol`，无需继承、无反向依赖、无 `import ports`）；迁入全部纯判定（`classify_run` / `classify_payload`、四个 `evaluate_*`、`_budget_decision` / `_risk_decision`）与模块级 `_looks_batch` / `_segment_count`。判据、检查顺序、比较运算符（`>=`）、`None` 短路语义、OBSERVE / ENFORCE 分支、启发式边界均与上提前**逐一字面等价**。
- `_json_safe` **复用 `domain/agent/guardrails.py` 既有等价实现**（递归版），移除基础设施本地一层副本；对 `_risk_decision` 的 metadata（`{"tool_name": str｜None, "risk_level": ToolRiskLevel}`）产出逐值等价（`ToolRiskLevel` 为 `StrEnum`，递归版与原副本均取 `.value`；`str` / `None` 原样透传）。
- `src/infrastructure/agent/static_guardrail_policy.py` **降为 re-export 垫片**，保护 7 处既有 `from infrastructure.agent.static_guardrail_policy import ...` 引用零改动通过；删除垫片并改所有引用点留待后续片。
- DI 装配点 `container_config._create_guardrail_policy` 的 import 改指领域类，`new` 语句与注入的 `agent_guardrail_config.to_policy()` 参数不变。

本决策声明为 `Behavior_Equivalent_Refactor`：判据零改动、`AgentGuardrailPolicyPort` 五方法签名不变、`GuardrailDecision` 对外产出逐值不变、不引领域事件（**不回退 [ADR-0001](0001-remove-domain-event-bus.md)**）。命名与放置对齐既有领域样板（`domain/task/policy.py` 承载纯判定服务、[ADR-0009](0009-introduce-domain-services-in-task-subdomain.md)）。

## 后果（Consequences）

- **正面**：
  - guardrail 护栏策略（任务分类 + 预算 / 风险决策 + 分类启发式）回归领域层，符合「领域层承载业务语义」目标。
  - 领域服务零基础设施依赖，可**脱离运行时单测**（本片新增 54 条领域单测覆盖全判定分支，含 `_risk_decision` metadata 等价专项）。
  - 为 `domain/agent` 子域其余构件的充血化立**第二块样板**（继 [ADR-0009](0009-introduce-domain-services-in-task-subdomain.md) 的 `domain/task` 之后），领域保持纯净。
- **负面 / 代价**：
  - `static_guardrail_policy.py` 降为 re-export 垫片，与领域实现**临时并存**，形成一个转发模块的过渡态。
  - 垫片清理（删除 + 改全部 7 处引用点）**待后续片**按 [change-discipline.md](../steering/change-discipline.md) 处理，本片不清理以控制改动面。
- **后续影响**：
  - `domain/agent` 子域其余候选（`agent_config` 规范化、`approval_policy_provider` 查表、`segmented_orchestration` 续跑判定）的充血化留**后续片**评估，按 `change-discipline` 逐候选推进、每步行为等价与测试全绿。
  - 垫片清理片作为独立 open follow-up。
  - 本决策为 `Behavior_Equivalent_Refactor`，不改任何对外可观测行为、不新增第三方依赖（`pyproject.toml` / `uv.lock` 不变）、不引领域事件 / 事件总线（尊重 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) §8 与 [ADR-0001](0001-remove-domain-event-bus.md)）；行为等价由领域服务单测 + 各调用点既有回归测试双重锁定。

## 备选方案（Alternatives）

- **方案 A：维持判定散落在基础设施不动** —— 未采纳原因：即本差距本身，`Domain_Logic_In_Infrastructure`（领域纯判定误落基础设施）未被纠正，违反 §4 领域服务放置护栏与 SRP。
- **方案 B：上提同时改类名为 `GuardrailEvaluationPolicy`** —— 未采纳原因：改名会引发既有测试断言与 `isinstance` 语义漂移，`Behavior_Equivalent_Refactor` 追求最小 diff，保留原类名使既有断言零变化、仅换 import 路径。
- **方案 C：直接删除基础设施文件 + 改全部 7 处引用点** —— 未采纳原因：一次性 diff 过大、扩大改动面，偏离最小改动纪律；re-export 垫片范式已由 [ADR-0011](0011-relocate-agent-loop-leaf-orchestration-to-domain.md) 首片验证更稳，垫片清理留后续片。
- **方案 D：`_json_safe` 在领域文件内保留独立一层副本** —— 未采纳原因：`_risk_decision` 内嵌的序列化步骤与领域同包 `guardrails._json_safe` 逐值等价，重复序列化 helper 违反 SRP / DRY，复用同包既有函数使领域类更自洽。
- **方案 E：让领域类显式继承 `AgentGuardrailPolicyPort`** —— 未采纳原因：`Protocol` 结构化匹配无需继承，显式继承反增耦合、偏离既有领域样板。
- **方案 F：引入领域事件 / 事件总线机制承载护栏判定副作用** —— 未采纳原因：直接违反 [ADR-0001](0001-remove-domain-event-bus.md)（已 `Accepted` 移除领域事件总线）与 §8 不引领域事件约束；护栏判定为纯函数返回值，无需事件。
