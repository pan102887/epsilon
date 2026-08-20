---
status: Accepted
date: 2026-07-06
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0009：在 domain/task 引入领域服务一等抽象（充血化试点）

## 背景与问题（Context）

前置 spec `ddd-implementation-review`（「本项目 DDD 落地 vs 业界主流」整合报告差距 2）识别出 `domain/task` 子域的贫血问题：该子域的值对象均为「行为仅限 `__post_init__` 校验」的贫血数据载体，只承载数据与构造期不变量校验，不承载任何业务判定。

与之对应的领域判定则散落在 `application` / `infrastructure` 层，且存在跨调用点的重复实现：

- **委派深度上限判定**在三个委派工具（`delegate_to_agent_tool.py` / `handoff_to_agent_tool.py` / `delegate_parallel_tool.py`）与委派适配器（`delegation_adapter.py` 的 `delegate_parallel._one` 与 `handoff`）中**各写一份**内联比较，且判据本身存在差异（多数为 `current + 1 > max`，`delegate_parallel._one` 为 `depth > max`）。
- **续跑判定**（Agent 终止原因 → 是否应产生 PAUSED）内联在 `TaskAgentAdapter._to_task_result`。
- **任务状态映射**（`TaskStatus` → 运行结局）分别内联在 `run_execution_coordinator._task_outcome` 与 `run_approval_resumer._task_result_to_store_result`。
- **审批恢复前置校验**（数量 / 顺序 / `allowed_decisions`）内联在 `TaskAgentAdapter._load_consumed_interrupt`。

护栏侧已由 [ADR-0007](0007-establish-domain-tactical-modeling-and-pydantic-boundary.md) 与 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md)（§4 领域服务放置、§8 不引入领域事件）补齐规范，但 `domain/task` 的代码尚未按护栏纠偏。散落 + 重复的判定既偏离「领域层承载业务语义」的目标，也易在多副本间发生行为漂移。

本 ADR 记录「在 `domain/task` 引入领域服务一等抽象」这一结构性 / 职责归属决策；实际落地由 spec `ddd-anemic-domain-pilot` 执行，全程为**行为等价的纯重构**（`Behavior_Equivalent_Refactor`，对外可观测行为字面不变）。

## 决策（Decision）

我们将在 `domain/task` 引入领域服务作为一等抽象，把上述散落的领域判定收敛进领域层。具体落点：

- 新建 `src/domain/task/policy.py`，承载 4 个**零基础设施依赖**的领域服务（均为无状态类）：
  - `DelegationDepthPolicy`：委派深度上限判定，提供 `exceeds_for_next_depth`（`current + 1 > max`）与 `exceeds_for_current_depth`（`depth > max`）两个方法，**逐一保留**调用点间既有的两类判据差异，不借收敛之名统一；
  - `TaskContinuationPolicy`：以 Agent 终止原因判定「是否应产生 PAUSED」；
  - `TaskStatusMapping`：把 `TaskStatus` 映射为领域内中立结局类别；
  - `ApprovalResumePrecondition`：审批决策集合的数量 / 顺序 / `allowed_decisions` 前置校验，复用现居 `domain/agent/exceptions.py` 的三个异常，异常类型、参数与触发时机不变。
- 新建 `src/domain/task/enums.py`，定义中立结局枚举 `TaskOutcomeKind`（`SUCCEEDED` / `PAUSED` / `AWAITING_APPROVAL` / `FAILED`）。`TaskStatusMapping` 返回 `TaskOutcomeKind` 而非 `domain/run` 的 `RunStatus`，以避免 `domain/task → domain/run` 的反向依赖；到 `RunStatus` / `ApprovalResumeStoreResult` 的最终装配由应用层完成。
- 各调用点改为**委托**上述领域服务；I/O（`ApprovalStateStorePort` 的 `load` / `is_expired` / `consume`）、日志（`logger.warning`）、事件写入（`record_collaboration_limit_hit`）、序列化（`_json_safe`）、`RunStatus` 装配、上下文可继续性判定（`_can_continue_from_context`，依赖 `ConversationContext` / `ToolRegistry`）等技术关注点**全部留在原层**。

命名与放置对齐既有领域样板：`domain/run/state_machine.py`（`RunStateMachine`）、`domain/workspace/policy.py`（`WorkspacePolicy`）、以及聚合器 `aggregator.py`；沿用 `policy.py` 具名模块承载纯判定服务，**不新增 `repository.py`**（本子域无仓储 Port 语义变更）。

本 ADR **不 supersede [ADR-0001](0001-remove-domain-event-bus.md)**：领域事件总线的移除是既定前提，本决策不引入领域事件 / 事件总线，与之无关、不回退。

## 后果（Consequences）

- **正面**：委派深度 / 续跑 / 状态映射 / 审批前置校验等领域判定住进领域层，可**脱离运行时单测**（零基础设施依赖）；委派深度上限的多副本内联比较被消除，行为漂移风险随之消除；领域层获得可复制的领域服务范式样板。
- **负面 / 代价**：本试点**只覆盖 `domain/task`** 一个子域，其余子域的贫血 / 散落判定仍待纠偏，暂形成「部分子域已充血、部分仍散落」的过渡态；`application` / `infrastructure` 调用点新增对 `domain/task/policy.py` 与 `domain/task/enums.py` 的 import 边界。
- **后续影响**：其余子域的充血化留待后续 spec，按 [change-discipline.md](../steering/change-discipline.md) **逐子域推进**、每步保持行为等价与测试全绿；本决策为 `Behavior_Equivalent_Refactor`，不改变任何对外可观测行为、不新增第三方依赖、不引入领域事件 / 事件总线（尊重 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) §8 与 ADR-0001）；行为等价由 4 个领域服务单测 + 各调用点既有回归测试双重锁定。

## 备选方案（Alternatives）

- **方案 A：维持判定散落 + 复制** —— 未采纳原因：即本差距本身，多副本内联比较易在演进中行为漂移，违反 SRP 与战术建模 §4 护栏。
- **方案 B：把判定收进 `Task` / `TaskResult` 值对象方法** —— 未采纳原因：这些判定跨对象 / 跨上下文（深度判定依赖 `workflow_context` 传入的 `max`，状态映射依赖 run 上下文），无自然归属的单一值对象，按 §4 应建独立领域服务而非塞入值对象。
- **方案 C：统一委派深度的两类判据** —— 未采纳原因：`delegate_parallel._one`（`depth > max`）与其余调用点（`current + 1 > max`）的判据差异属既有业务规则，统一会**修改对外行为**、违反行为等价约束，故刻意以两个方法逐一保留差异。
- **方案 D：`TaskStatusMapping` 直接返回 `RunStatus`** —— 未采纳原因：`RunStatus` 属 `domain/run`，直接返回会引入 `domain/task → domain/run` 的反向依赖、违反分层方向；改以中立枚举 `TaskOutcomeKind` 输出，跨上下文的状态语义装配留在应用层。
- **方案 E：一并充血 `domain/agent`** —— 本期未采纳原因：`domain/agent` 牵涉逾 3000 行的 Agent Loop、属高风险改动，需独立 spec 承载，一并处理会违反最小改动纪律；留待后续按子域推进。
