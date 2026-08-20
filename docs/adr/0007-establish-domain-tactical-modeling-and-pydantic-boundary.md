---
status: Accepted
date: 2026-07-06
deciders: [架构评审]
supersedes:
superseded-by:
---

# ADR-0007：确立领域层战术建模范式与 Pydantic 边界

## 背景与问题（Context）

本项目的 steering 规范在**六边形架构/分层依赖**与**工程治理**（ADR / change-discipline / SRP）维度达到甚至超越主流，但存在一处规范空白（`DDD_Tactical_Modeling_Gap`）：

- **战术设计维度覆盖不完整**：[ddd-architecture.md](../steering/ddd-architecture.md) 全文仅一句提及「实体、值对象、领域事件」三词，无任何建模规则；聚合根与聚合边界、实体、领域服务、仓储语义、限界上下文、通用语言在 steering 中全部缺失或未定义放置规则。
- **这是既有代码偏差的规范根源**：现规范只要求「领域层不依赖基础设施」，从未要求「业务规则必须收敛进实体/领域服务」。因此贫血领域模型（各子域仅有 `value_objects.py` / `ports.py` / `exceptions.py`，业务规则散落在 `application/` 与 `infrastructure/` 大文件中）在现规范下反而「合规」——后续 agent 会持续「合规地」写出贫血模型。这正是 spec `ddd-implementation-review` 需求 1–5 所诊断偏差的规范根源。
- **Pydantic 与领域边界的方向性二义（`Pydantic_Domain_Boundary_Clarification`）**：[pydantic-model.md](../steering/pydantic-model.md) 原措辞（「API 边界与领域数据传递优先使用 Pydantic 模型」「领域值对象优先使用不可变模型 `ConfigDict(frozen=True)`」）与 [ddd-architecture.md](../steering/ddd-architecture.md)（把 Pydantic 列入领域层「明确禁止的依赖」）存在方向性冲突。代码以脚投票：`domain/` 下 19 个文件用 `@dataclass(frozen=True)`、0 个用 Pydantic `BaseModel`。二义不消解，后续 agent 会被旧措辞误导把 Pydantic 下沉领域层。

## 决策（Decision）

- 我们将新增 [docs/steering/ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md)，确立领域层**战术建模范式**（值对象 / 实体 / 领域服务 / 聚合边界判定 / 仓储 Port 语义 / 限界上下文=子域目录 / 通用语言），以正向样板 `RunStateMachine` / `WorkflowExecutionPolicy.validate()` / `ReadinessAggregator` / `WorkspacePolicy` 为范例基准。
- 我们将确立「**领域层用 Python 原生类型 / `@dataclass(frozen=True)`，Pydantic 仅用于 API/DTO 与配置边界**」这一既成实践，并据此**修订 [pydantic-model.md](../steering/pydantic-model.md)** 的冲突措辞。**该修订是一次显式规范修订（change-discipline §4「确需调整规范本身的独立显式改动」），非顺手改动**。
- 我们将以「**轻量约束 + 何时才需引入聚合边界的判定指引**」表达聚合——鉴于 Agent 工作台状态多为会话/流式态、强一致性事务边界影响有限，**不强制所有子域引入聚合根**。
- 我们**尊重 [ADR-0001](0001-remove-domain-event-bus.md)**：领域事件不列为推荐战术构件，本 ADR **不 supersede ADR-0001**，不回退领域事件总线的移除决策。

## 后果（Consequences）

- **正面**：后续 agent 有据可依，不再「合规地」写贫血模型；消解 Pydantic 二义，领域纯净度规则单一收敛到「领域层不用 Pydantic」；`ddd-implementation-review` 需求 2 的建模范式获得规范背书。
- **负面 / 代价**：新增一份必读 steering（`inclusion: always`），带来增量阅读成本；既有贫血模型（如 `Task` / `AgentConfig`）与新规范存在暂时落差，须靠需求 2 单子域试点渐进纠偏，本 ADR 不强制立即改。
- **后续影响**：今后引入实体 / 聚合 / 领域服务等一等抽象仍属架构级决策，须按 [change-discipline.md](../steering/change-discipline.md) §2 先写新 ADR；[pydantic-model.md](../steering/pydantic-model.md)、[ddd-architecture.md](../steering/ddd-architecture.md) 与 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) 三者对「领域层可否用 Pydantic」的表述须**永久保持一致**。

## 备选方案（Alternatives）

- **方案 A：把战术规范并入 `ddd-architecture.md`** —— 未采纳原因：违反 SRP，使分层规范膨胀、冲淡「分层依赖/Port-Adapter 归属」主旨（`ddd-architecture.md` 管分层，战术建模管构件形态，属两类关注点）。
- **方案 B：修订 `ddd-architecture.md` 改为允许领域层用 Pydantic** —— 未采纳原因：与既成实践（`domain/` 0 Pydantic）及领域纯净度目标相悖，会诱导框架耦合下沉领域层。
- **方案 C：强制所有子域立即引入聚合根 / 充血实体** —— 未采纳原因：Agent 工作台状态多为会话/流式态、一致性边界影响有限，全面强推属过度设计，且与 change-discipline 最小改动冲突。
- **方案 D：引入领域事件补齐「事件」这一战术构件** —— 未采纳原因：[ADR-0001](0001-remove-domain-event-bus.md) 已 `Accepted` 移除事件总线，复活须走 supersede 流程且当前无稳定订阅方；跨模块副作用走 Port/Adapter + trace。
