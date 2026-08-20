# DDD 落地质量评估：本项目 vs 业界主流 · 差距与改进方向

> 范围：后端 `epsilon-boot`（FastAPI + DDD 六边形架构）。本报告为**调研 + 评估 + 改进方向**的整合结论，代码事实以当前 `TEST` 分支为准（截至 2026-07-06，两批 DDD 纠偏 `ddd-implementation-review`/`ddd-tactical-remediation` 已合入）。

## 一、业界主流 DDD 实现方案（调研基线）

主流 DDD（Evans《领域驱动设计》、Vernon《实现领域驱动设计》，及 Python/FastAPI 社区实践）在工程落地上收敛为两条主线：

**1. 战略设计（宏观切分）**
- **限界上下文（Bounded Context）**：以业务能力划分独立模型边界，各上下文内术语（通用语言 Ubiquitous Language）唯一。
- **上下文映射（Context Map）**：显式表达上下文间协作关系（共享内核、防腐层 ACL、发布/订阅等）。重量级机制，仅大型多团队系统才必要。

**2. 战术设计（微观建模）**
| 构件 | 主流定义 | 典型落地 |
|---|---|---|
| 值对象 Value Object | 无标识、不可变、相等即同值 | 不可变数据类，构造期校验 |
| 实体 Entity | 有稳定标识 + 生命周期可变状态，行为内聚 | 充血对象，不变量自维护 |
| 聚合根 Aggregate Root | 聚合唯一入口，聚合边界=一致性/事务边界 | 外部只经聚合根改内部对象 |
| 领域服务 Domain Service | 无自然归属实体的跨对象业务规则 | 无状态、零基础设施依赖 |
| 仓储 Repository | 按标识存取聚合的抽象 | 领域定接口、基础设施实现 |
| 领域事件 Domain Event | 领域内发生的、有业务意义的事实 | 事件总线 / 发布订阅（可选） |

**3. 架构落地范式**
- **六边形架构 / 端口-适配器**：`domain` 零框架依赖，依赖方向 `application → domain ← infrastructure`。
- **充血模型 优先于 贫血模型**：业务规则住在实体/领域服务里，而非 Service 类堆事务脚本（Fowler 明确将 Anemic Domain Model 列为反模式）。
- **工程治理**：ADR（架构决策记录）、SRP、变更纪律、DTO 与领域模型分离。

> 主流共识里同样重要的一条**反过度设计**原则：聚合根、领域事件、上下文映射都不是"必须全用"，应按一致性需求与团队规模按需引入——这一点常被误读为"DDD=全套重武器"。

## 二、本项目落地质量评估

### 达标甚至超越主流的维度 ✅

1. **分层与六边形架构**：`domain/` 依赖方向正确，Port（Protocol）定义在 `domain/<子域>/ports.py`，Adapter 在 `infrastructure/`。子域划分清晰（`agent`/`chat`/`task`/`run`/`model_access`/`workspace`/`health`/`prompt`/`storage`），天然构成限界上下文。
2. **值对象实践**：`domain/` 普遍 `@dataclass(frozen=True)`，构造期 `__post_init__` 校验，符合值对象范式。
3. **工程治理超越多数团队**：8 条 ADR（`0001`–`0008`）持续记录方向级决策、11 份 steering 规范（含 SRP、变更纪律、文档同步、类型 lint 基线），治理成熟度高于业界平均。
4. **正向样板已存在**：`RunStateMachine`（状态机式领域服务）、`WorkflowExecutionPolicy.validate()`（策略/规则对象）、`ReadinessAggregator.check_readiness()`（跨对象聚合领域服务）、`WorkspacePolicy.resolve()`（纯函数策略）——这些是把业务规则正确收敛进领域层的范式。
5. **领域纯净度（近期已加固）**：`domain/` 下 `import logging`/`getLogger` **零命中**；对外序列化职责已外移至 `infrastructure/<子域>/*_serialization.py`，`domain/` 下 `to_dict` 仅剩 `chat/context.py` 4 处（会话消息序列化，明确保留）。
6. **规范护栏已补齐**：新增 `ddd-tactical-modeling.md` + ADR-0007，明确战术建模规则与"领域层用 dataclass、Pydantic 仅在 API/DTO/配置边界"边界，从规范根源堵住贫血模型复发。

### 与主流仍存在的差距 ⚠️

| # | 差距 | 客观证据（当前代码基复核） | 严重度 | 状态 |
|---|---|---|---|---|
| 1 | **核心业务逻辑下沉基础设施层** | ReAct Agent Loop 位于 `infrastructure/agent/react_agent_adapter.py`，单文件 **3313 行**，模块 docstring 自称"基础设施层"，但它是自研编排算法（未封装 openai/litellm 等 SDK），本质是领域关注点。三层 LOC：domain **8308** / application **9912** / infrastructure **24494**（基础设施≈领域 3 倍，业务逻辑漏进技术层的信号）。`AgentPort` 端口契约（`run`/`run_streaming`/`run_events`/`resume`）已完备。 | 高 | 已登记，待独立 spec + ADR |
| 2 | **贫血领域模型** | `grep 'class \w+(Service\|Aggregate\|Entity)\b' src/domain/` **零命中**——无任何显式实体/聚合根/领域服务类。`Task`、`AgentConfig` 等为纯数据 frozen dataclass，行为仅 `__post_init__` 校验。业务规则散落在应用/基础设施大文件。 | 中 | 规范护栏已立，待单子域充血化试点 |
| 3 | **应用层事务脚本倾向** | `container_config.py` **2004 行**、`workflow_orchestrator.py` **1384 行**、`run_application_service.py` **835 行**，疑似把本属领域的编排规则堆积成事务脚本（注：组合根装配属允许的例外，需甄别）。 | 低 | 待诊断 + 拆分方案登记 |
| 4 | **聚合根/聚合边界基本缺席** | `domain/` 无聚合根抽象。但本项目状态多为会话/流式态，强一致性事务边界影响有限——**此为按需取舍，非纯缺陷**，规范已以"判定指引"形式表达而非强制。 | 轻微 | 规范已明确"何时才需引入"，符合反过度设计 |

## 三、差距根因分析

差距 1–3 的**共同规范根源**是：原有 steering 只约束"领域层不依赖基础设施"，从未要求"业务规则必须收敛进实体/领域服务"——于是贫血模型 + 逻辑下沉在旧规范下反而"合规"。`ddd-architecture.md` 全文仅一句提及"实体/值对象/领域事件"，无任何战术建模规则。这已由 ADR-0007 + `ddd-tactical-modeling.md` 补齐（护栏先行）。

差距是**成因可解释的、而非失控的**：项目起于 Agent 编排这一"算法密集"场景，自研 Loop 天然庞大；早期规范重分层、轻战术，导致规则外溢。当前项目已进入"规范先行 → 代码渐进纠偏"的健康治理节奏。

## 四、改进方向（按优先级 + 风险分档）

### 已完成 ✅
- **规范护栏**：`ddd-tactical-modeling.md`、ADR-0007、Pydantic 边界澄清（`ddd-implementation-review` 需求 6）。
- **领域纯净度**：移除 `domain/chat/context.py` 的 `logging` 依赖；对外序列化职责外移至基础设施映射器 + ADR-0008（`ddd-tactical-remediation` 需求 A/B）。测试 2847 passed。

### P1 — 贫血模型单子域充血化试点（中风险，推荐下一步）
- **做法**：择一子域（建议 `domain/task` 或 `domain/agent`），以 `RunStateMachine`/`WorkflowExecutionPolicy` 为基准，把"当前散落在应用/基础设施、本质属领域判定"的既有规则收敛为领域服务/带行为对象，**行为等价、不新增/删除规则**。
- **流程门**：独立 spec + ADR（引入领域服务属一等抽象决策）；补 `test/domain/<子域>/` 单测。
- **价值**：验证充血范式可在本仓库低风险落地，为其余子域立样板。

### P2 — Agent Loop 归属重划（高风险，需最谨慎）
- **做法**：把 `react_agent_adapter.py` 中"非技术依赖"的编排逻辑（推理→行动→观察循环控制、终止判定）上提到领域层（领域服务），基础设施层仅保留真实技术封装（序列化、stream 累加、工具滥用检测、handoff 上下文等）。
- **硬约束**：`AgentPort` 四方法签名不变；`AgentResult`/`StreamingChunk`/`AgentStreamEvent` 字段与时序字面等价；不推翻 `agent-adapter-refactor` v3 已定行为决策（全程 stream、工具 timeout、`max_total_tokens`、循环耗尽 assert、`tool_arguments_delta`）。
- **流程门**：**先写 ADR 定方向**（归属哪层、以何切分上提），再走独立 spec + 分波实施。3313 行搬迁牵动大量测试 import，切忌与 P1 同时大改。

### P3 — 应用层大文件诊断与拆分登记（低风险）
- **做法**：对三大文件逐段标注"本属领域判定"vs"确属应用编排/组合根装配"，前者给出迁往哪个领域服务的建议，后者（如 `container_config.py` 组合根）明确保留为**允许的例外**。
- **流程门**：本期仅诊断登记（写入 spec/TODO），零代码改动；待 P1 领域建模方向明确后再搬迁。

### 治理层收尾项（轻微）
- **序列化彻底零残留（选项 B）**：`canonicalize_collaboration_summary` 外移、guardrails 汇总编排上提，消除领域私有序列化 helper。
- **循环 import 治理**：`infrastructure/run/__init__.py` eager-import 迫使 `application/run` 用函数内局部 import；后续调整装配使映射器 import 回归模块级。

## 五、总体结论

| 维度 | 评级 | 说明 |
|---|---|---|
| 战略设计（限界上下文/通用语言） | **优** | 子域划分清晰、术语一致，轻量表达恰当，未过度设计上下文映射 |
| 六边形架构/分层 | **优** | 依赖方向、Port/Adapter 归属正确 |
| 工程治理（ADR/SRP/变更纪律） | **优（超越主流平均）** | 8 ADR + 11 steering，护栏完善 |
| 战术建模（实体/聚合/领域服务） | **中** | 贫血模型为主，正向样板存在但未推广；规范已补齐、代码待渐进纠偏 |
| 领域纯净度 | **良（近期加固）** | logging/序列化已收敛，仅存明确保留项 |

**一句话**：本项目 DDD 落地在**架构骨架与工程治理**上达到甚至超越业界主流，主要差距集中在**战术建模的充血度**（贫血模型 + 核心 Agent Loop 逻辑下沉）。差距成因清晰、已有正向样板和规范护栏，改进路径明确：**规范先行（已完成）→ 单子域充血试点（P1）→ Agent Loop 归属重划（P2，先 ADR）→ 应用层拆分（P3）**，全程坚持行为等价与最小改动，风险可控。
