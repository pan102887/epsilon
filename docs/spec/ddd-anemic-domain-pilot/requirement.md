# 需求文档：贫血领域模型单子域充血化试点（domain/task）

## 简介

### 背景与动机

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构。整合报告 `docs/spec/ddd-gap-analysis/report.md`（第二节差距 2）与前置 spec `docs/spec/ddd-implementation-review`（需求 2）指出：本仓库 `domain/` 下的领域对象普遍是「行为仅限 `__post_init__` 校验」的贫血数据载体，**本质属于领域判定的业务规则却散落在 `application/` 与 `infrastructure/` 大文件中**。这一现象的规范根源已由 ADR-0007 + `docs/steering/ddd-tactical-modeling.md` 补齐（护栏先行），但代码尚未渐进纠偏。

本 spec 落地整合报告的 **P1（贫血模型单子域充血化试点，中风险）**，在**恰好一个**子域内，以仓库既有正向样板为职责与风格基准，把散落的领域判定规则**行为等价地**收敛进领域服务/带行为的领域对象，验证充血范式可在本仓库低风险落地，为其余子域立样板。

### 正向样板基准（不改造，仅作参照）

本 spec 以下列四处既有正向样板为职责边界、命名风格与「零基础设施依赖 + 可脱离运行时单测」的验收标尺：

- `domain/run/state_machine.py::RunStateMachine`（状态迁移合法性判定）
- `domain/run/workflow.py::WorkflowExecutionPolicy.validate()`（策略字段合法性校验）
- `domain/health/aggregator.py::ReadinessAggregator`（跨对象聚合判定）
- `domain/workspace/policy.py::WorkspacePolicy`（纯函数式路径策略）

### 试点子域选择：`domain/task`

本 spec 确认试点子域为 `domain/task`，理由：

1. **证据集中**：`domain/task/value_objects.py` 的 `Task` / `TaskResult` / `TaskContinueRequest` / `TaskApprovalResumeRequest` 均为纯 `@dataclass(frozen=True)`，行为仅限 `__post_init__` 校验；task 相关的领域判定确凿散落在 `application/run/run_execution_coordinator.py`、`application/run/run_approval_resumer.py`、`infrastructure/task/task_agent_adapter.py`、`infrastructure/agent/delegation_adapter.py`、`infrastructure/agent/delegate_to_agent_tool.py` 等多处调用点，且存在**跨调用点重复实现**（委派深度上限判定在三个工具 + 适配器各写一份）。
2. **端口契约已完备**：`domain/task/ports.py::TaskAgentPort`（execute / continue_task / resume_approval）已定义，收敛判定不需改动端口签名，风险可控。
3. **规则可脱离运行时单测**：待收敛的判定均为「输入既有值 → 输出布尔/枚举/映射」的纯判定，天然适合放进领域服务并以单元测试锁定行为等价。

**不纳入其他子域的原因**（`change-discipline` 最小改动）：`domain/agent`（另一候选）牵涉 3313 行 `react_agent_adapter.py` 的 Agent Loop，属整合报告 P2 的独立高风险 spec 范畴，与本 spec 同时改动违反最小改动纪律；其余子域无同等密度的「散落 + 重复」证据，本期不动。

### 范围内行为（In Scope）

- 在 `domain/task` 内新增领域服务/带行为的领域对象，将下列散落的领域判定收敛进去（详见「需求」章各条）：
  - 委派深度上限判定（`Delegation_Depth_Policy`）；
  - 任务终止原因 → 可继续性/暂停判定（`Task_Continuation_Policy`）；
  - 任务状态 → Run 存储/执行 outcome 状态映射判定（`Task_Status_Mapping`）；
  - 审批恢复前置条件校验（`Approval_Resume_Precondition`）；
- 调用点改为委托新领域服务，删除各处重复/内联的等价规则实现；
- 新增聚焦业务规则的单元测试，置于 `test/domain/task/`；
- 新增 ADR（从 0009 起）记录「在 `domain/task` 引入领域服务」这一一等抽象决策。

### 范围外边界（Out of Scope）

- 不改动 Agent Loop（`infrastructure/agent/react_agent_adapter.py`，属整合报告 P2 的独立 spec）；
- 不做应用层大文件搬迁/拆分（`container_config.py` / `workflow_orchestrator.py` / `run_application_service.py`，属整合报告 P3）；
- 不改动前端；
- 不引入任何新依赖（含 Pydantic 进入领域层）；
- 不引入领域事件总线或任何事件构件（尊重 ADR-0001，不 supersede）；
- 不改造四处既有正向样板，不把试点范式强推到其他子域；
- 不新增、删除或更改任何一条业务规则（本 spec 为**行为等价纯重构**）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 试点子域 | `Pilot_Subdomain` | 本 spec 唯一选定的充血化试点子域，取值固定为 `domain/task`。 |
| 任务值对象 | `Task` | `domain/task/value_objects.py::Task`，封装一次 Agent 执行的任务定义，含 `delegation_depth` 字段。 |
| 任务结果 | `Task_Result` | `domain/task/value_objects.py::TaskResult`，封装 Agent 执行后的结构化结果，含 `status` / `terminated_reason` / `can_continue`。 |
| 任务状态 | `Task_Status` | `domain/task/value_objects.py::TaskStatus` 枚举：SUCCESS / FAILED / PAUSED / HUMAN_INTERVENTION_REQUIRED。 |
| 任务审批恢复请求 | `Task_Approval_Resume_Request` | `domain/task/value_objects.py::TaskApprovalResumeRequest`，含 session_id / approval_id / decisions。 |
| 委派深度策略 | `Delegation_Depth_Policy` | 新增的领域服务/带行为对象，承载「下一层委派深度是否超过最大允许深度」的判定，收敛当前散落在委派工具与委派适配器中的重复实现。 |
| 任务续跑策略 | `Task_Continuation_Policy` | 新增的领域服务/带行为对象，承载「Agent 终止原因 → 任务是否暂停/可继续」的判定，收敛当前内联在 `TaskAgentAdapter._to_task_result` 的规则。 |
| 任务状态映射 | `Task_Status_Mapping` | 新增的领域服务/带行为对象，承载「`Task_Status` → Run 执行/存储 outcome 状态」的判定，收敛当前散落在 `run_execution_coordinator` 与 `run_approval_resumer` 的映射规则。 |
| 审批恢复前置条件 | `Approval_Resume_Precondition` | 新增的领域服务/带行为对象，承载审批恢复前对决策集合的合法性校验（数量匹配、顺序匹配、决策类型被允许），收敛当前内联在 `TaskAgentAdapter._load_consumed_interrupt` 的校验规则。 |
| 领域服务 | `Domain_Service` | 无自然归属某实体/值对象的跨对象业务判定构件，零 `application`/`infrastructure` 依赖、不引框架 API，见 `ddd-tactical-modeling.md` §4。 |
| 行为等价纯重构 | `Behavior_Equivalent_Refactor` | 不改变任何对外可观测行为的重构：所有被收敛规则的输入→输出判定逐一等价，不新增/删除/更改任何一条规则。 |
| 既有测试全绿 | `Existing_Test_Suite_Green` | 在 `epsilon-boot/` 下执行 `PYTHONPATH=src uv run --frozen pytest` 全部通过。 |
| 委派深度 | `Delegation_Depth` | 委派链当前深度；根 Agent 为 0，每委派一层 +1。 |
| 最大委派深度 | `Max_Delegation_Depth` | 允许的委派深度上限，默认 3，工作流上下文可下调为 `min(默认, workflow_context.limit.max_recursion_depth)`。 |
| 架构决策记录 | `ADR` | 架构级决策的只增不改记录，见 `docs/steering/adr.md` 与 `docs/adr/README.md`，本 spec 新增编号从 0009 起。 |

## 需求

### 需求 1：确认唯一试点子域并锁定范围

**用户故事：** 作为架构负责人，我希望本 spec 只在恰好一个子域内充血化并显式说明取舍，以便遵循最小改动纪律、避免范式蔓延。

#### 验收标准

1. THE `Pilot_Subdomain` SHALL 取值为 `domain/task`，且本 spec 的全部代码改动仅落在 `domain/task/`、其现有调用点，以及 `test/domain/task/`。
2. THE `Pilot_Subdomain` SHALL 在需求文档中记录选择理由与不纳入其他子域（尤其 `domain/agent` 的 Agent Loop）的原因。
3. THE `Behavior_Equivalent_Refactor` SHALL NOT 改造 `RunStateMachine` / `WorkflowExecutionPolicy` / `ReadinessAggregator` / `WorkspacePolicy` 四处既有正向样板。
4. THE `Behavior_Equivalent_Refactor` SHALL NOT 将试点范式强制推广到 `domain/task` 以外的任何子域。

### 需求 2：把委派深度上限判定收敛为领域服务

**用户故事：** 作为维护者，我希望委派深度上限判定只在领域层实现一次，以便消除三个委派工具与委派适配器中的重复规则并防止行为漂移。

#### 验收标准

1. THE `Delegation_Depth_Policy` SHALL 位于 `domain/task/` 下，为零 `application`/`infrastructure` 依赖、不引框架 API 的 `Domain_Service`。
2. THE `Delegation_Depth_Policy` SHALL 提供以「当前 `Delegation_Depth`、`Max_Delegation_Depth`」为输入、返回「下一层是否超限」布尔判定的行为方法，其判据与既有内联实现 `next_depth = current_depth + 1; next_depth > max_delegation_depth` 逐一等价。
3. WHEN 收敛 `infrastructure/agent/delegate_to_agent_tool.py`、`infrastructure/agent/handoff_to_agent_tool.py` 与 `infrastructure/agent/delegate_parallel_tool.py` 的深度判定，THE `Behavior_Equivalent_Refactor` SHALL 使各调用点委托 `Delegation_Depth_Policy`，且保留各调用点原有的 `effective_max_depth = min(max_delegation_depth, workflow_context.limit.max_recursion_depth)` 计算位置与超限后的既有副作用（日志、`record_collaboration_limit_hit`、抛 `DelegationDepthExceededError` 或返回失败字符串）不变。
4. IF `delegate_parallel_tool.py` 与 `delegation_adapter.delegate_parallel` 现存以 `delegation_depth > max_delegation_depth`（而非 `next_depth`）判定的差异语义，THEN THE `Behavior_Equivalent_Refactor` SHALL 逐调用点保持各自现有判据不变，不借收敛之名统一或修正该差异。
5. THE `Existing_Test_Suite_Green` SHALL 在收敛后保持通过。

### 需求 3：把任务终止原因到可继续性的判定收敛为领域服务

**用户故事：** 作为维护者，我希望「Agent 终止原因决定任务是否暂停/可继续」这一领域判定住在领域层，以便它可脱离基础设施被单元测试锁定。

#### 验收标准

1. THE `Task_Continuation_Policy` SHALL 位于 `domain/task/` 下，为零 `application`/`infrastructure` 依赖的 `Domain_Service`。
2. THE `Task_Continuation_Policy` SHALL 以 Agent 终止原因为输入，返回「是否应产生 `Task_Status.PAUSED`」的判定，其判据与 `TaskAgentAdapter._to_task_result` 现有 `terminated_reason not in ("max_rounds", "token_budget_exceeded")` 逻辑逐一等价。
3. WHEN `TaskAgentAdapter._to_task_result` 构造 `Task_Result`，THE `Behavior_Equivalent_Refactor` SHALL 使其委托 `Task_Continuation_Policy` 判定 SUCCESS 与 PAUSED 分支，且不改变 `approval_required` 分支、`prompt_id` 透传、`can_continue` 取值与其余字段的既有取值。
4. THE `Task_Continuation_Policy` SHALL NOT 承载 `_can_continue_from_context` 中依赖 `ConversationContext` / `ToolRegistry` 的上下文可继续性判定（该判定依赖基础设施，留在原处）。
5. THE `Existing_Test_Suite_Green` SHALL 在收敛后保持通过。

### 需求 4：把任务状态到 Run outcome 的映射收敛为领域服务

**用户故事：** 作为维护者，我希望「`Task_Status` → Run 执行/存储状态」的映射只定义一次，以便 `run_execution_coordinator` 与 `run_approval_resumer` 两处不再各自复制映射规则。

#### 验收标准

1. THE `Task_Status_Mapping` SHALL 位于 `domain/task/` 下，为零 `application`/`infrastructure` 依赖、不引框架 API 的 `Domain_Service`；其输入为 `Task_Status`（及必要的 `terminated_reason`），输出为领域内的状态判定枚举/结果，不返回 `RunStatus` 以避免 `domain/task` 反向依赖 `domain/run`。
2. FOR ALL `Task_Status` 取值（SUCCESS / FAILED / PAUSED / HUMAN_INTERVENTION_REQUIRED），THE `Task_Status_Mapping` SHALL 给出与 `run_execution_coordinator._task_outcome` 现有分支（SUCCESS→SUCCEEDED、PAUSED→PAUSED、HUMAN_INTERVENTION_REQUIRED→AWAITING_APPROVAL、其余→FAILED）逐一等价的判定。
3. WHEN `application/run/run_execution_coordinator.py::_task_outcome` 与 `application/run/run_approval_resumer.py::_task_result_to_store_result` 映射任务结果，THE `Behavior_Equivalent_Refactor` SHALL 使二者委托 `Task_Status_Mapping` 得出状态判定，再在应用层完成到 `RunStatus` / `ApprovalResumeStoreResult` 的最终装配，保持 `error` 结构、`terminal_reason`、`approval_id`、`can_continue` 等既有输出字段字面等价。
4. THE `Behavior_Equivalent_Refactor` SHALL 保留应用层现有的 JSON-safe 序列化 helper（`_json_safe` 等）在应用层，不将序列化职责下沉领域层（尊重 SRP 与 ADR-0008）。
5. THE `Existing_Test_Suite_Green` SHALL 在收敛后保持通过。

### 需求 5：把审批恢复前置条件校验收敛为领域服务

**用户故事：** 作为维护者，我希望审批恢复的决策合法性校验住在领域层，以便这组前置条件规则可被独立单测且不与基础设施 I/O 混杂。

#### 验收标准

1. THE `Approval_Resume_Precondition` SHALL 位于 `domain/task/` 下，为零 `application`/`infrastructure` 依赖的 `Domain_Service`。
2. THE `Approval_Resume_Precondition` SHALL 以「待恢复审批的既有动作序列」与 `Task_Approval_Resume_Request.decisions` 为输入，逐一校验决策数量匹配、决策顺序（`tool_call_id` 对齐）、决策类型属于该动作 `allowed_decisions`，其判据与 `TaskAgentAdapter._load_consumed_interrupt` 现有校验逐一等价。
3. WHEN 任一前置条件不满足，THE `Approval_Resume_Precondition` SHALL 抛出与既有一致的领域异常（`ApprovalDecisionCountMismatchError` / `ApprovalDecisionOrderMismatchError` / `ApprovalDecisionNotAllowedError`），异常类型、参数与触发时机保持不变。
4. THE `Approval_Resume_Precondition` SHALL NOT 承载 `_load_consumed_interrupt` 中依赖 `ApprovalStateStorePort` 的加载/过期/原子消费（`load` / `is_expired` / `consume`）等基础设施步骤，这些留在 `TaskAgentAdapter`。
5. WHEN `TaskAgentAdapter._load_consumed_interrupt` 执行校验，THE `Behavior_Equivalent_Refactor` SHALL 使其委托 `Approval_Resume_Precondition`，且 `ApprovalNotFoundError` / `ApprovalExpiredError` / `ApprovalConsumedError` 的触发顺序与时机保持不变。
6. THE `Existing_Test_Suite_Green` SHALL 在收敛后保持通过。

### 需求 6：领域服务遵循战术建模与代码质量规范

**用户故事：** 作为规范守护者，我希望新增的领域构件符合既有 steering 规范，以便它们成为其他子域可复制的正确样板。

#### 验收标准

1. THE `Delegation_Depth_Policy`、`Task_Continuation_Policy`、`Task_Status_Mapping` 与 `Approval_Resume_Precondition` SHALL 使用 Python 原生类型或 `@dataclass`（无独立标识、承载判定的领域服务），不引入 Pydantic（对齐 `ddd-tactical-modeling.md` §2/§4 与 `ddd-architecture.md` 领域层禁用依赖）。
2. THE 各新增领域服务 SHALL 具备中文 docstring 说明职责与不变量（对齐 `code-documentation.md`）。
3. THE 各新增领域服务 SHALL 具备全量类型标注、不使用裸 `Any`，并通过 `ruff`/`pyright` 基线（零新增错误，对齐 `python-typing-lint.md`）。
4. THE 各新增领域服务 SHALL 满足 SRP：每个构件只承载单一类别的领域判定（对齐 `srp-principle.md`），置于 `domain/task/` 下与既有样板一致的具名模块（如 `policy.py` / `domain_service.py`），不新增 `repository.py` 命名。
5. THE `Behavior_Equivalent_Refactor` SHALL NOT 引入任何新的第三方依赖，SHALL NOT 引入领域事件或事件总线构件。

### 需求 7：为新增领域服务补充聚焦业务规则的单元测试

**用户故事：** 作为维护者，我希望每个新增领域服务都有独立单测锁定其判定，以便重构后的行为等价性可被回归验证。

#### 验收标准

1. THE 新增单元测试 SHALL 置于 `test/domain/task/` 下。
2. FOR ALL 新增领域服务（`Delegation_Depth_Policy` / `Task_Continuation_Policy` / `Task_Status_Mapping` / `Approval_Resume_Precondition`），THE 单元测试 SHALL 覆盖其正例与边界/异常判定分支（如深度恰好等于上限 vs 超限、各 `Task_Status` 分支、各审批校验失败分支）。
3. THE 单元测试 SHALL 不依赖 `application`/`infrastructure` 或框架运行时即可执行（脱离运行时单测，对齐正向样板特征）。
4. THE `Existing_Test_Suite_Green` SHALL 在新增测试后仍全部通过；WHEN 因文件移动导致既有测试的 import 路径变化，THE `Behavior_Equivalent_Refactor` SHALL 仅调整 import，不改动既有断言语义。

### 需求 8：新增 ADR 记录引入领域服务的架构级决策

**用户故事：** 作为架构负责人，我希望在 `domain/task` 引入领域服务这一一等抽象被 ADR 记录，以便决策可追溯且不与既有 ADR 冲突。

#### 验收标准

1. THE `ADR` SHALL 新增一条编号从 0009 起的记录，记录「在 `Pilot_Subdomain` 引入 `Domain_Service` 一等抽象」的决策、备选方案与未采纳原因，并在 `docs/adr/README.md` 索引表登记。
2. THE `ADR` SHALL 声明本决策为 `Behavior_Equivalent_Refactor`，不改变任何对外可观测行为。
3. THE `ADR` SHALL NOT supersede `ADR-0001`，SHALL NOT 复活领域事件总线（尊重 `ddd-tactical-modeling.md` §8 与 ADR-0001）。
4. THE `ADR` SHALL 说明本试点只覆盖 `domain/task`，其余子域的充血化留待后续按 `change-discipline` 逐子域推进。
