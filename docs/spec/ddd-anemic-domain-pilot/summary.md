# ddd-anemic-domain-pilot — 落地总结

## Feature

`ddd-anemic-domain-pilot`：DDD 落地评估（`docs/spec/ddd-gap-analysis/report.md`）中 **P1（贫血领域模型单子域充血化试点，中风险）** 的落地，亦即前置 spec `ddd-implementation-review` **需求 2** 的代码级实现。在**恰好一个**子域 `domain/task` 内，以既有正向样板（`RunStateMachine`/`WorkflowExecutionPolicy`/`ReadinessAggregator`/`WorkspacePolicy`）为基准，把散落在应用/基础设施层、本质属领域判定的既有规则**行为等价地**收敛为领域服务。

全程 `Behavior_Equivalent_Refactor`：不新增/删除/更改任何一条业务规则，不改任何对外可观测行为。

## 最终产物清单

### 新增（源码）
- `epsilon-boot/src/domain/task/enums.py` — 中立结局枚举 `TaskOutcomeKind`（SUCCEEDED/PAUSED/AWAITING_APPROVAL/FAILED），刻意不引用 `domain/run` 的 `RunStatus`，避免 `domain/task → domain/run` 反向依赖。
- `epsilon-boot/src/domain/task/policy.py` — 4 个零基础设施依赖领域服务：
  - `DelegationDepthPolicy`：`exceeds_for_next_depth`（`current+1 > max`）+ `exceeds_for_current_depth`（`current > max`）两方法，刻意保留调用点判据差异。
  - `TaskContinuationPolicy.should_pause`（`reason in {max_rounds, token_budget_exceeded}`）。
  - `TaskStatusMapping.outcome_of`（TaskStatus → TaskOutcomeKind 四分支）。
  - `ApprovalResumePrecondition.check`（决策数量/顺序/allowed_decisions 校验，复用 `domain/agent/exceptions.py` 三异常）。

### 新增（测试）
- `test/domain/task/test_delegation_depth_policy_unit.py`（两方法边界 + 差异保留）
- `test/domain/task/test_task_continuation_policy_unit.py`
- `test/domain/task/test_task_status_mapping_unit.py`（封闭枚举全覆盖）
- `test/domain/task/test_approval_resume_precondition_unit.py`（三类失败分支 + 全合法）

### 修改（调用点委托，7 文件）
- 委派深度（5 处）：`infrastructure/agent/{delegate_to_agent_tool,handoff_to_agent_tool,delegate_parallel_tool}.py` 用 `exceeds_for_next_depth`；`delegation_adapter.py` 的 `delegate_parallel._one` 用 `exceeds_for_current_depth`、`handoff` 用 `exceeds_for_next_depth`（差异保留）。
- `infrastructure/task/task_agent_adapter.py`：`_to_task_result` 委托 `TaskContinuationPolicy`；`_load_consumed_interrupt` 委托 `ApprovalResumePrecondition`。
- 应用层装配：`application/run/run_execution_coordinator.py::_task_outcome`、`application/run/run_approval_resumer.py::_task_result_to_store_result` 委托 `TaskStatusMapping` 后本层装配 `RunStatus`/`ApprovalResumeStoreResult`。

### 新增（文档）
- `docs/adr/0009-introduce-domain-services-in-task-subdomain.md`（`Accepted`，`supersedes:` 留空，不 supersede ADR-0001）+ `docs/adr/README.md` 索引一行。

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 4 服务文件组织 | 统一置于 `domain/task/policy.py` | 对齐 `workspace/policy.py` 单文件样板，便于作为可复制范式 |
| 委派深度双方法 | `exceeds_for_next_depth` + `exceeds_for_current_depth` | AC2.4：`delegate_parallel._one` 入参已是 next_depth，判据与其余三处不同，保留差异不统一 |
| 状态映射不返回 RunStatus | 中立枚举 `TaskOutcomeKind`，应用层再装配 | 避免 `domain/task → domain/run` 反向依赖 |
| 审批异常 | 复用现居 `domain/agent/exceptions.py` 三异常 | 同层可依赖，类型/参数/时机不变 |
| I/O 边界 | load/is_expired/consume、序列化、上下文可继续性判定留原层 | 尊重 SRP 与 ADR-0008，领域服务只承载纯判定 |

## 执行过程中的受控偏差（如实记录）

1. **`task_agent_adapter.py` 类型标注补强**：`_to_task_result` 保留原 `getattr(agent_result, "terminated_reason", "completed")` 调用后，`getattr` 会把类型擦除为 `Any|str`，使新的 typed `should_pause(...)` 调用触发 pyright 报错（原 tuple 成员测试曾隐式收窄该类型）。为满足「零新增 pyright 错误」门禁，给该变量补 `terminated_reason: AgentTerminationReason` 显式标注 —— getattr 调用与运行时行为字面不变，仅恢复原代码等价的 Literal 类型。
2. **evaluator 调用时机**：`spec-generator` 子代理环境内无法自起 `spec-evaluator`（子代理不可嵌套），故由编排者在三波代码全部落地后统一发起独立 evaluator 复审，裁决 PASS。

## 验证结论

- **全量测试**：`PYTHONPATH=src uv run --frozen pytest` → **2869 passed, 3 skipped, 0 failed**（较前基线 2847 多 22，来自新增 4 个领域服务单测；无删任何行为断言语义）。
- **领域纯净度**：`domain/task/policy.py`+`enums.py` 的 `application`/`infrastructure`/`fastapi`/`pydantic`/`domain.run` import 语句零命中（仅 docstring 提及约束文本）。
- **规范合规**：改动/新增文件 `ruff check` 全绿；`pyright` 零新增错误（仓库残留 4 处为既存基线，与本 spec 改动无关）。
- **范围纪律**：源码改动仅落 `domain/task/`（新增 2 文件）+ 7 处调用点 + `test/domain/task/`；文档仅落 `docs/`；四处正向样板与 `react_agent_adapter.py` 均未触碰。
- **evaluator 裁决**：PASS，全维度通过（需求合规/设计一致/正确性属性 1–6/代码质量/错误处理/任务完备）。

AC1–AC8 与 Property 1–6 全覆盖（详见 `tasks.md` 追溯表与 `review-log.md`）。

## 后续事项（Follow-ups，均不在本轮范围）

- **其余子域充血化**：本试点只覆盖 `domain/task`，其余子域按 `change-discipline` 逐子域推进，可复用本 spec 的领域服务范式。
- **P2（Agent Loop 归属重划）**：`react_agent_adapter.py`（3313 行）编排逻辑上提领域层，极高风险，须独立 spec + 先写 ADR。
- **P3（应用层大文件拆分）**：`container_config.py`/`workflow_orchestrator.py`/`run_application_service.py` 诊断与拆分登记。
- **非阻塞建议（evaluator）**：续跑单测可补充更多非暂停终止原因；审批单测的 `# type: ignore[arg-type]` 可用 typed helper 替代。
