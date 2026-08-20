---
inclusion: always
---

# DDD 战术建模规范（Tactical Modeling）

本规范补齐 [ddd-architecture.md](ddd-architecture.md) 未覆盖的**战术设计维度**：值对象、实体、领域服务、聚合根与聚合边界、仓储（Repository）语义、限界上下文与通用语言。两者分工明确——**分层依赖方向、Port/Adapter 归属、明确禁止/允许的依赖**以 [ddd-architecture.md](ddd-architecture.md) 为准；**业务规则该以什么战术构件表达、住在 `domain/` 的哪个文件**以本文件为准。修改任一份时须同步核对另一份，避免口径分裂（见 [doc-sync.md](doc-sync.md)）。

## 1. 概述与适用范围

- 本规范只规定「**今后如何建模**」，是一道**护栏而非一次性改造令**。它不要求立即把既有代码全部重写为充血模型。
- 既有贫血模型（如仅承载 `__post_init__` 校验的数据载体）的**渐进纠偏**属于 spec `ddd-implementation-review` **需求 2 的单子域试点**范畴，按 [change-discipline.md](change-discipline.md) 最小改动、逐子域推进；本规范不代替需求 2 强制全域改造。
- 本规范以仓库既有的**正向样板**为范例基准，它们已把业务规则正确收敛进领域层，可直接对照：
  - `domain/run/state_machine.py::RunStateMachine`
  - `domain/run/workflow.py::WorkflowExecutionPolicy`（含 `.validate()`）
  - `domain/health/aggregator.py::ReadinessAggregator`
  - `domain/workspace/policy.py::WorkspacePolicy`

## 2. 值对象（Value Object）

- **建模规则**：领域值对象用 `@dataclass(frozen=True)`。判据为「**相等即同值、不可变、无独立标识**」——两个字段全等的值对象即视为同一个值，不追踪生命周期。
- **构造期校验**：不变量校验放在 `__post_init__`，或提供专用 `validate()` 方法在构造后显式调用。例如 `WorkflowExecutionPolicy.validate()` 校验策略字段的合法组合，`WorkspacePolicy`（`@dataclass(frozen=True)`）以纯函数式方法承载路径策略。
- **放置规则**：`domain/<子域>/value_objects.py`，与既有组织一致。
- **值对象不用 Pydantic**：领域值对象一律使用 Python 原生类型与 `@dataclass(frozen=True)`，**不引入 Pydantic**。此口径与 [ddd-architecture.md](ddd-architecture.md)「明确禁止的依赖」（Pydantic 列入领域层禁用项）、[pydantic-model.md](pydantic-model.md)（Pydantic 仅用于 API/DTO 与配置边界）三处必须字面一致。依据：`domain/` 下 19 个文件用 `@dataclass`、0 个用 Pydantic `BaseModel`。

## 3. 实体（Entity）

- **建模规则**：具备**稳定标识（identity）+ 生命周期内可变状态**的对象建模为实体；维护自身不变量的**行为应内聚在实体上**，而非散落到 `application/` 或 `infrastructure/` 层。
- **放置规则**：`domain/<子域>/entities.py`（本仓库当前尚无该文件）——规范据此约定「**今后有实体时置于此**」。
- **现状说明**：当前 `domain/task/value_objects.py::Task`、`domain/agent/value_objects.py::AgentConfig` 是「行为仅限 `__post_init__` 校验」的数据载体，属需求 2 待评估的充血化候选。本规范**不强制立即改**，其纠偏归属需求 2 的单子域试点（回链第 1 节与 change-discipline 最小改动）。

## 4. 领域服务（Domain Service）与放置规则

- **建模规则**：**无自然归属某个实体/值对象的跨对象业务规则**才建模为领域服务。领域服务只承载业务判定，**零 `application/`/`infrastructure/` 依赖、不引入框架 API**。
- **放置规则**：`domain/<子域>/domain_service.py`，**或与既有样板一致的具名模块**（如 `state_machine.py` / `aggregator.py` / `policy.py` / `workflow.py`）。规范**承认既有具名组织合法，不强制统一改名**为 `domain_service.py`。
- **逐一举例**（均为真实存在、可直接对照的正确形态）：
  - `RunStateMachine`（`domain/run/state_machine.py`）：以 `assert_transition()` / `is_terminal()` / `can_cancel()` 等方法承载运行状态迁移合法性判定。
  - `WorkflowExecutionPolicy.validate()`（`domain/run/workflow.py`）：策略字段的合法性校验，规则内聚在策略对象上。
  - `ReadinessAggregator.check_readiness()`（`domain/health/aggregator.py`）：聚合多个 `HealthCheckPort` 结果得出就绪判定。
  - `WorkspacePolicy.resolve()`（`domain/workspace/policy.py`）：路径归一化与边界判定的纯函数式策略。
- **共有的正确特征**：零基础设施依赖、业务规则内聚、可脱离运行时环境单元测试。新增领域服务时以此为验收标尺。

## 5. 聚合根与聚合边界

- **建模规则**：聚合根是聚合的**唯一外部入口**、聚合边界即**一致性/事务边界**——聚合内的对象只能经聚合根变更，以保证在同一次变更内维持不变量。
- **本仓库上下文约束**：本项目为 Agent 工作台，状态多为**会话态/流式态**，强一致性事务边界的实际影响有限。因此**不强制每个子域引入聚合根**——盲目为每个子域套聚合根属于过度设计。
- **「何时才需要引入聚合边界」判定指引**（满足以下条件时才引入聚合根，否则用值对象/实体组合表达即可）：
  1. 存在**一组对象必须在同一次变更内共同保持某个不变量**（跨对象一致性约束）；
  2. **且**该组对象存在**并发写竞争**或需要明确的事务边界来避免部分更新；
  3. 若仅是数据分组、无跨对象一致性约束、无并发写竞争，**不引入聚合根**，用值对象/实体的组合表达，避免过度设计。
- 引入聚合根属**一等抽象的架构级决策**，须按 [change-discipline.md](change-discipline.md) §2 先写 ADR 再落地。

## 6. 仓储（Repository）语义与本项目 Port 命名的关系

- **建模规则**：本仓库以 `domain/<子域>/ports.py` 中的 **Port（Python `Protocol`）** 承载经典 Repository 语义——即「按标识取/存领域对象」的能力边界；基础设施层在 `infrastructure/` 提供 Adapter 实现（举例既有 `domain/agent/ports.py::TraceStorePort`、`domain/agent/ports.py::ArtifactStorePort` 等持久化 Port）。
- **放置规则**：领域侧接口在 `ports.py`，实现在 `infrastructure/`。**不新增独立的 `repository.py` 命名**，避免与既有 Port 实践分裂（见本规范决策依据与 [adr.md](adr.md)）。
- 该实践与 [ddd-architecture.md](ddd-architecture.md)「组件级声明」（领域 Port 定义在 `src/domain/*/ports.py`、Adapter 位于 `src/infrastructure/`）完全一致。

## 7. 限界上下文与通用语言

- **限界上下文**：以既有子域目录 `agent` / `chat` / `task` / `run` / `model_access` / `workspace` / `health` / `prompt` / `storage` 为**天然限界上下文**，每个子域即一个上下文边界。
- **通用语言（Ubiquitous Language）**：术语在**各上下文内保持一致**——同一名词在同一子域内含义唯一；**跨上下文同名不同义**须显式区分（如命名加限定前缀或在 docstring 说明其上下文含义）。
- 本规范**不引入**重量级的上下文映射（Context Map）机制或映射矩阵；限界上下文以子域目录轻量表达即可。

## 8. 不推荐的构件：领域事件

- **领域事件 / 事件总线不是本仓库推荐的战术构件**。相关基础设施（`EventBusPort` / `EventStorePort` / `DomainEvent`）已由 [ADR-0001](../adr/0001-remove-domain-event-bus.md)（`Accepted`）**主动移除**。
- 跨模块副作用编排走 **Port/Adapter 与 trace 抽象**（`SessionTrace` / `ModelCallTrace` / `ToolCallTrace` / `ApprovalTrace` 等），不以「补回事件总线」为默认方案。
- **禁止**后续 agent 以「补齐战术构件」为由复活领域事件总线；若未来确有真正的多订阅方异步解耦需求，须新增 ADR 走 supersede 流程（见 [ADR-0001](../adr/0001-remove-domain-event-bus.md) 与 [adr.md](adr.md)），而非直接引入。

## 9. 与其它规范的衔接

- [srp-principle.md](srp-principle.md)：序列化、日志等**技术关注点不入领域对象**——领域对象只承载业务语义（呼应 `ddd-implementation-review` 需求 3/5）。
- [code-documentation.md](code-documentation.md)：新增领域类/领域服务须有**中文 docstring** 说明职责与不变量。
- [python-typing-lint.md](python-typing-lint.md)：领域代码**全量类型标注、禁裸 `Any`**，通过 `ruff`/`pyright` 基线。
- [change-discipline.md](change-discipline.md)：引入领域服务、实体、聚合等**一等抽象属架构级决策**，须按 §2 先写 ADR 再落地。
