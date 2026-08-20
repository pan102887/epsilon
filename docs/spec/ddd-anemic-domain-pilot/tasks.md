# 实现计划：贫血领域模型单子域充血化试点（domain/task）

> 本文件由已定稿的 `design.md` 展开为可执行、可勾选的任务清单。全程为 **`Behavior_Equivalent_Refactor`（行为等价纯重构）**：把散落在委派工具/委派适配器、`TaskAgentAdapter`、`run_execution_coordinator`、`run_approval_resumer` 中的领域判定收敛进新建的 `domain/task/policy.py` 领域服务，各调用点改为委托；I/O、日志、序列化、`RunStatus` 装配等技术关注点全部留在原层。
> 每条任务标注：动作、目标文件、对应 requirement AC 与 design 组件 / Property 编号、验证命令。所有测试/lint 命令均在 `epsilon-boot/` 下执行。
> **全程硬约束**：领域服务零 `application`/`infrastructure`/框架/`domain.run` 依赖、无 Pydantic、无新第三方依赖、不引领域事件；中文 docstring（`code-documentation.md`）；全量类型标注、禁裸 `Any`、`ruff`/`pyright` 零新增错误（`python-typing-lint.md`）；`Existing_Test_Suite_Green` 每波结束保持通过（`PYTHONPATH=src uv run --frozen pytest`）。

## 概述

执行采用 **波次（Wave）+ Checkpoint 门禁** 结构，遵循 design「组件依赖图」的依赖方向（`application/infrastructure → domain`）自底向上推进：

- **Wave 1（建领域层）**：新建 `domain/task/enums.py`（`TaskOutcomeKind`）+ `domain/task/policy.py`（4 个领域服务），并在 `test/domain/task/` 新增 4 个脱离运行时的单测。此波**只新增领域构件与单测，不触碰任何调用点**，保证既有代码不断裂。→ **Checkpoint 1**：新单测全绿 + policy/enums 的 `ruff`/`pyright` 零错 + grep 验证零基础设施/反向依赖。
- **Wave 2（迁调用点）**：委派深度 5 个调用点委托 `DelegationDepthPolicy`；`task_agent_adapter` 的 `_to_task_result` 委托 `TaskContinuationPolicy`、`_load_consumed_interrupt` 委托 `ApprovalResumePrecondition`；`run_execution_coordinator._task_outcome` 与 `run_approval_resumer._task_result_to_store_result` 委托 `TaskStatusMapping` + 本层装配。→ **Checkpoint 2**：全量 pytest 全绿 + 相关调用点 lint 零新增错误。
- **Wave 3（文档）**：新增 ADR-0009 + `docs/adr/README.md` 索引。→ **Checkpoint 3（最终门禁）**：全量 pytest 全绿 + grep 门禁（`domain/task` 无 `domain.run`/`application`/`infrastructure`/框架依赖）+ `ruff`/`pyright` 零新增错误。

> **波次内并发正交**：Wave 1 的领域构件与各单测互为独立新文件，可并发；Wave 2 各调用点任务分处不同文件，除显式标注的共享文件外可并发。
> **反断裂纪律**：Wave 1 只新增不改调用点 → Wave 2 逐调用点委托并删除内联规则，删除时逐字符核对与新服务判据等价，每波结束测试可绿。

---

## Wave 1：建领域层（新增枚举 + 4 个领域服务 + 单测，并发）

> **并发正交证据**：本波任务分别创建 **互不相同的新文件**：`domain/task/enums.py`、`domain/task/policy.py`、以及 `test/domain/task/` 下 4 个新测试文件；`policy.py` import `enums.py`，故 T-1.1 需先于 T-1.2 落地（或同一次落地），其余单测任务在 `policy.py`/`enums.py` 就绪后并发。

- [x] 1. 领域层判定构件与单测
  - [x] 1.1 新建中立结局枚举 `src/domain/task/enums.py`
    - 在 `src/domain/task/enums.py` 新建（当前不存在），含模块中文 docstring。
    - 定义 `class TaskOutcomeKind(Enum)`，四个成员：`SUCCEEDED = "succeeded"`、`PAUSED = "paused"`、`AWAITING_APPROVAL = "awaiting_approval"`、`FAILED = "failed"`；类 docstring 说明「任务状态 → 领域中立结局」判定输出、刻意不引用 `domain/run` 的 `RunStatus` 以避免反向依赖（对齐 design 组件 3 代码块）。
    - 顶部 `from __future__ import annotations`；仅 `from enum import Enum`，不引 `application`/`infrastructure`/框架/Pydantic/`domain.run`。
    - _需求: 4.1_ ; _design 组件 3 / Property 3、Property 5_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from domain.task.enums import TaskOutcomeKind"`（import 正常）。

  - [x] 1.2 新建领域服务 `src/domain/task/policy.py`，承载 4 个领域服务
    - 在 `src/domain/task/policy.py` 新建（当前不存在），含模块中文 docstring；顶部 `from __future__ import annotations`；全量类型标注、禁裸 `Any`。
    - 依赖仅限：`from collections.abc import Sequence`、`from domain.agent.exceptions import ApprovalDecisionCountMismatchError, ApprovalDecisionNotAllowedError, ApprovalDecisionOrderMismatchError`、`from domain.agent.value_objects import AgentTerminationReason, ApprovalDecision, PendingActionRequest`、`from domain.task.enums import TaskOutcomeKind`、`from domain.task.value_objects import TaskStatus`；**不引** `application`/`infrastructure`/框架/Pydantic/`domain.run`。
    - `class DelegationDepthPolicy`（无状态类，`@staticmethod`）：
      - `exceeds_for_next_depth(current_depth: int, max_delegation_depth: int) -> bool`，实现 `return current_depth + 1 > max_delegation_depth`（等价既有 `next_depth = current+1; next_depth > max`）。
      - `exceeds_for_current_depth(current_depth: int, max_delegation_depth: int) -> bool`，实现 `return current_depth > max_delegation_depth`（`delegate_parallel._one` 专用判据）。
      - 类 docstring 说明刻意提供两方法以保留调用点判据差异、不统一（AC2.4），且不感知 `workflow_context`、`effective_max_depth` 的 `min(...)` 归一由调用点在传入前完成。
    - `class TaskContinuationPolicy`（无状态类，`@staticmethod`）：模块级 `_PAUSE_REASONS: frozenset[str] = frozenset({"max_rounds", "token_budget_exceeded"})`；`should_pause(terminated_reason: AgentTerminationReason) -> bool` 实现 `return terminated_reason in _PAUSE_REASONS`；docstring 说明与 `_to_task_result` 现有 `terminated_reason not in ("max_rounds", "token_budget_exceeded")` 逐一等价（本方法为其取反语义），且不承载 `_can_continue_from_context` 的上下文判定（留基础设施）。
    - `class TaskStatusMapping`（无状态类，`@staticmethod`）：`outcome_of(status: TaskStatus) -> TaskOutcomeKind`，四分支 `SUCCESS→SUCCEEDED`、`PAUSED→PAUSED`、`HUMAN_INTERVENTION_REQUIRED→AWAITING_APPROVAL`、其余（含 `FAILED`）`→FAILED`；docstring 说明与 `_task_outcome` 现有分支逐一等价、不返回 `RunStatus`、装配留应用层。
    - `class ApprovalResumePrecondition`（无状态类，`@staticmethod`）：`check(actions: Sequence[PendingActionRequest], decisions: Sequence[ApprovalDecision]) -> None`，逐一校验：数量不匹配抛 `ApprovalDecisionCountMismatchError(len(actions), len(decisions))`；`zip(actions, decisions, strict=True)` 遍历中 `decision.tool_call_id != action.tool_call_id` 抛 `ApprovalDecisionOrderMismatchError(action.tool_call_id, decision.tool_call_id)`；`decision.type not in action.allowed_decisions` 抛 `ApprovalDecisionNotAllowedError(action.tool_name, decision.type, frozenset(action.allowed_decisions))`（异常类型/参数与既有内联校验逐字段等价）；docstring 说明不承载 `load`/`is_expired`/`consume` 等 I/O（留 `TaskAgentAdapter`）。
    - _需求: 2.1, 2.2, 2.4, 3.1, 3.2, 4.1, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5_ ; _design 组件 1/2/3/4 / Property 1、2、3、4、5_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from domain.task.policy import DelegationDepthPolicy, TaskContinuationPolicy, TaskStatusMapping, ApprovalResumePrecondition"`。

  - [x] 1.3 新增 `DelegationDepthPolicy` 单测 `test/domain/task/test_delegation_depth_policy_unit.py`
    - 在 `test/domain/task/test_delegation_depth_policy_unit.py` 新建，仅 import `domain.task.policy`（脱离运行时）。
    - 参数化断言 `exceeds_for_next_depth`：边界 `current+1 == max`（`current=max-1` 时 False）与 `current+1 == max+1`（`current=max` 时 True）；断言 `exceeds_for_current_depth`：`depth == max`（False）与 `depth == max+1`（True）；覆盖差异保留（两方法在同一 `(current=max, max)` 入参下结果不同：`exceeds_for_next_depth` True、`exceeds_for_current_depth` False）。
    - _需求: 2.2, 2.4, 7.1, 7.2, 7.3_ ; _design Property 1_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/task/test_delegation_depth_policy_unit.py`。

  - [x] 1.4 新增 `TaskContinuationPolicy` 单测 `test/domain/task/test_task_continuation_policy_unit.py`
    - 在 `test/domain/task/test_task_continuation_policy_unit.py` 新建，仅 import `domain.*`。
    - 断言 `should_pause` 对 `AgentTerminationReason` 各取值：`max_rounds`/`token_budget_exceeded` → True（PAUSED），其余取值（如 `completed`）→ False（SUCCESS 分支）；覆盖三个及以上终止原因取值。
    - _需求: 3.2, 7.1, 7.2, 7.3_ ; _design Property 2_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/task/test_task_continuation_policy_unit.py`。

  - [x] 1.5 新增 `TaskStatusMapping` 单测 `test/domain/task/test_task_status_mapping_unit.py`
    - 在 `test/domain/task/test_task_status_mapping_unit.py` 新建，仅 import `domain.*`。
    - 断言 4 个 `TaskStatus` → `TaskOutcomeKind` 映射：`SUCCESS→SUCCEEDED`、`PAUSED→PAUSED`、`HUMAN_INTERVENTION_REQUIRED→AWAITING_APPROVAL`、`FAILED→FAILED`（覆盖封闭枚举全部取值）。
    - _需求: 4.1, 4.2, 7.1, 7.2, 7.3_ ; _design Property 3_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/task/test_task_status_mapping_unit.py`。

  - [x] 1.6 新增 `ApprovalResumePrecondition` 单测 `test/domain/task/test_approval_resume_precondition_unit.py`
    - 在 `test/domain/task/test_approval_resume_precondition_unit.py` 新建，仅 import `domain.*`（构造 `PendingActionRequest`/`ApprovalDecision` 领域值对象作输入）。
    - 覆盖四类分支：数量不匹配 → `ApprovalDecisionCountMismatchError`（断言期望/实际计数参数）；`tool_call_id` 不对齐 → `ApprovalDecisionOrderMismatchError`（断言期望/实际 tool_call_id 参数）；决策类型不在 `allowed_decisions` → `ApprovalDecisionNotAllowedError`（断言 tool_name/type/allowed 集合参数）；全部合法 → 无异常（返回 `None`）。
    - _需求: 5.2, 5.3, 7.1, 7.2, 7.3_ ; _design Property 4_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/task/test_approval_resume_precondition_unit.py`。

---

## Checkpoint 1：领域层就绪 + 零基础设施依赖（门禁）

- [x] 2. CP1 Wave 1 门禁校验（全部通过方可进入 Wave 2）
  - 新构件可 import：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import domain.task.enums, domain.task.policy"`（无报错）。
  - 4 个新单测全绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/task`（含新增 4 个单测 + 既有 task 单测）。
  - 领域纯净度（Property 5）：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic)|from (application|infrastructure|fastapi|pydantic)" src/domain/task/policy.py src/domain/task/enums.py`（期望零命中）。
  - 无反向依赖（Property 3）：`cd epsilon-boot && grep -rnE "domain\.run|domain/run" src/domain/task/policy.py src/domain/task/enums.py`（期望零命中）。
  - 规范合规（Property 5）：`cd epsilon-boot && uv run ruff check src/domain/task/policy.py src/domain/task/enums.py` 与 `cd epsilon-boot && uv run pyright src/domain/task/policy.py src/domain/task/enums.py`（零新增错误、无裸 `Any`；中文 docstring 人工核对齐备）。
  - _需求: 2.1, 3.1, 4.1, 5.1, 6.1, 6.2, 6.3, 6.5, 7.3, 7.4_ ; _design Property 1、2、3、4、5、6_

---

## Wave 2：迁调用点（委托新领域服务，删内联规则；共享文件串行）

> **迁移原则**：把各调用点的内联判定改为委托 Wave 1 的领域服务，删除等价内联实现；保留各调用点原有的 `effective_max_depth = min(...)` 计算位置、日志、`record_collaboration_limit_hit`、抛异常/返回失败字符串、I/O、序列化、`RunStatus` 装配等既有副作用与字段字面不变。落地时逐点 grep 核对无遗漏、无行为漂移。
> **正交与串行判定**：委派深度 5 处分处 4 个文件（`delegation_adapter.py` 承两处，归单一任务串行）；`task_agent_adapter.py` 两处委托归单一任务串行；应用层两处分处 `run_execution_coordinator.py` 与 `run_approval_resumer.py`，可并发。各任务文件集合互不相交。

- [x] 3. 委派深度判定委托 `DelegationDepthPolicy`
  - [x] 3.1 迁移 `src/infrastructure/agent/delegate_to_agent_tool.py` 深度判定
    - 修改 `src/infrastructure/agent/delegate_to_agent_tool.py`（design 定位 @161）：`next_depth > effective_max_depth` 改为 `DelegationDepthPolicy.exceeds_for_next_depth(self._current_delegation_depth, effective_max_depth)`；`next_depth`/`effective_max_depth` 计算位置不动，`logger.warning`、`record_collaboration_limit_hit`、`raise DelegationDepthExceededError(current, effective_max, agent)` 全留原处。
    - import `from domain.task.policy import DelegationDepthPolicy`。
    - _需求: 2.2, 2.3, 2.5_ ; _design 组件 1 调用点表第 1 行 / Property 1、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。

  - [x] 3.2 迁移 `src/infrastructure/agent/handoff_to_agent_tool.py` 深度判定
    - 修改 `src/infrastructure/agent/handoff_to_agent_tool.py`（design 定位 @165）：`next_depth > effective_max_depth` 改为 `DelegationDepthPolicy.exceeds_for_next_depth(self._current_delegation_depth, effective_max_depth)`；`logger.warning`、`record_collaboration_limit_hit`、返回失败字符串 `_failure(...)` 全留原处；`handoff_count` 校验（@184–201）**不动**。
    - import `from domain.task.policy import DelegationDepthPolicy`。
    - _需求: 2.2, 2.3, 2.5_ ; _design 组件 1 调用点表第 2 行 / Property 1、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。

  - [x] 3.3 迁移 `src/infrastructure/agent/delegate_parallel_tool.py` 深度判定
    - 修改 `src/infrastructure/agent/delegate_parallel_tool.py`（design 定位 @219）：`next_depth > effective_max_depth` 改为 `DelegationDepthPolicy.exceeds_for_next_depth(self._current_delegation_depth, effective_max_depth)`；`logger.warning`、`record_collaboration_limit_hit`、`raise DelegationDepthExceededError` 全留原处；并行数量超限判定（@185–208）**不动**。
    - import `from domain.task.policy import DelegationDepthPolicy`。
    - _需求: 2.2, 2.3, 2.5_ ; _design 组件 1 调用点表第 3 行 / Property 1、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。

  - [x] 3.4 迁移 `src/infrastructure/agent/delegation_adapter.py` 两处深度判定（同文件，串行）
    - 修改 `src/infrastructure/agent/delegation_adapter.py::delegate_parallel._one`（design 定位 @201）：`delegation_depth > max_delegation_depth` 改为 `DelegationDepthPolicy.exceeds_for_current_depth(delegation_depth, max_delegation_depth)`（入参 `delegation_depth` 已是 next_depth）；超限时返回 `DelegationResult(success=False, content=<既有中文文案>)` 不变，`_one` 的 try/except 隔离语义不动。
    - 修改 `src/infrastructure/agent/delegation_adapter.py::handoff`（design 定位 @283）：`delegation_depth + 1 > max_delegation_depth` 改为 `DelegationDepthPolicy.exceeds_for_next_depth(delegation_depth, max_delegation_depth)`；超限时 `raise DelegationDepthExceededError(current=delegation_depth, max=max_delegation_depth, target=agent)` 不变。
    - **差异保留（AC2.4）**：两处刻意调用不同方法（`_one` 用 `exceeds_for_current_depth`、`handoff` 用 `exceeds_for_next_depth`），不统一。import `from domain.task.policy import DelegationDepthPolicy`。
    - _需求: 2.2, 2.3, 2.4, 2.5_ ; _design 组件 1 调用点表第 4/5 行 / Property 1、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent test/domain/task/test_task_delegation_depth_properties.py`。

- [x] 4. `TaskAgentAdapter` 委托续跑与审批前置校验（同文件，串行）
  - [x] 4.1 `_to_task_result` 委托 `TaskContinuationPolicy`
    - 修改 `src/infrastructure/task/task_agent_adapter.py::_to_task_result`（design 定位 @275–323）：`approval_required` 分支（@283–297）**完全不动**；@299 `terminated_reason = getattr(agent_result, "terminated_reason", "completed")` 保留；@300 `if terminated_reason not in ("max_rounds", "token_budget_exceeded")` 改为 `if not TaskContinuationPolicy.should_pause(terminated_reason)`（等价取反）；SUCCESS 分支（@301–311）与 PAUSED 分支（@313–323）内 `content`/`terminated_reason`/`can_continue`（含 `self._can_continue_from_context(context)` 调用留原处）/`prompt_id` 透传等字段**字面不变**。
    - import `from domain.task.policy import TaskContinuationPolicy`。
    - _需求: 3.2, 3.3, 3.4, 3.5_ ; _design 组件 2 / Property 2、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/task/test_task_paused_result_unit.py`。

  - [x] 4.2 `_load_consumed_interrupt` 委托 `ApprovalResumePrecondition`
    - 修改 `src/infrastructure/task/task_agent_adapter.py::_load_consumed_interrupt`（design 定位 @432–469）：保持顺序不变——@437–438 `ApprovalNotFoundError`、@440–442 `load` + `ApprovalNotFoundError`、@443–444 `is_expired` + `ApprovalExpiredError` **留原处**；@445–461 的数量/顺序/`allowed_decisions` 内联校验**替换为** `ApprovalResumePrecondition.check(interrupt.actions, request.decisions)`；@463–468 `consume` + `ApprovalConsumedError` **留原处**。校验位置恰在 `is_expired` 之后、`consume` 之前，时机字面一致。
    - import `from domain.task.policy import ApprovalResumePrecondition`；移除对已收敛的 `ApprovalDecisionCountMismatchError`/`ApprovalDecisionOrderMismatchError`/`ApprovalDecisionNotAllowedError` 的直接 `raise` 与不再被引用的 import（以过 lint）；保留 `ApprovalNotFoundError`/`ApprovalExpiredError`/`ApprovalConsumedError` 的 import。
    - _需求: 5.2, 5.3, 5.4, 5.5, 5.6_ ; _design 组件 4 / Property 4、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/task`（含审批恢复既有测试）。

- [x] 5. 应用层委托 `TaskStatusMapping` + 本层装配（两文件可并发）
  - [x] 5.1 `run_execution_coordinator._task_outcome` 委托并本层装配 `RunStatus`
    - 修改 `src/application/run/run_execution_coordinator.py::_task_outcome`（design 定位 @497–536）：@500–507 的 4 分支 `if/elif/else` 改为先取 `kind = TaskStatusMapping.outcome_of(response.status)`，再按 design 映射表装配 `RunStatus`（`SUCCEEDED→RunStatus.SUCCEEDED`、`PAUSED→RunStatus.PAUSED`、`AWAITING_APPROVAL→RunStatus.AWAITING_APPROVAL`、`FAILED→RunStatus.FAILED`）；`result`/`error`/`terminal_reason`/`can_continue`/`approval_id`/`segment_metadata` 构造字面不变，`_json_safe`/`_extract_approval_id`/`_segment_metadata` 留原处；`FAILED` 分支的 `error = {"message": response.content, "task_status": response.status.value}` 仍由 `status is RunStatus.FAILED` 触发，等价。
    - import `from domain.task.policy import TaskStatusMapping`、`from domain.task.enums import TaskOutcomeKind`。
    - _需求: 4.2, 4.3, 4.4, 4.5_ ; _design 组件 3 应用层装配映射表 / Property 3、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/run`。

  - [x] 5.2 `run_approval_resumer._task_result_to_store_result` 委托并本层装配 `ApprovalResumeStoreResult`
    - 修改 `src/application/run/run_approval_resumer.py::_task_result_to_store_result`（design 定位 @123–176）：改为先取 `kind = TaskStatusMapping.outcome_of(response.status)`，按 design 映射表装配 `ApprovalResumeStoreResult`——`PAUSED→status="queued", result=result, ...=None`；`AWAITING_APPROVAL→status="awaiting_approval", approval_id=response.approval_id, result=result, ...=None`；`FAILED→status="failed", error={"message": response.content, "task_status": response.status.value}, terminal_reason="failed", ...=None`；`SUCCEEDED→status="succeeded", result=result, terminal_reason=str(response.terminated_reason), ...=None`（对应现 `else` 分支）；`result` dict、各 `*_summary=None` 字面不变，`_json_safe` 与 `_workflow_phase_can_continue` 留原处。
    - import `from domain.task.policy import TaskStatusMapping`、`from domain.task.enums import TaskOutcomeKind`。
    - _需求: 4.2, 4.3, 4.4, 4.5_ ; _design 组件 3 应用层装配映射表 / Property 3、6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/run`。

  - [x] 5.3 迁移既有测试的 import 路径（仅按需）
    - 若 Wave 2 的调用点改动导致既有测试文件的 import 断裂，仅调整 import 指向新领域服务/枚举，**不改动既有断言语义**（AC7.4）。逐处核查后最小改动。
    - _需求: 7.4_ ; _design 测试策略第 2 项 / Property 6_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain test/infrastructure test/application`。

---

## Checkpoint 2：调用点全部委托、全量测试绿（门禁）

- [x] 6. CP2 Wave 2 门禁校验
  - 全量测试绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed）。
  - 委派深度无遗留内联判据：`cd epsilon-boot && grep -rnE "next_depth > |delegation_depth > |delegation_depth \+ 1 > " src/infrastructure/agent/delegate_to_agent_tool.py src/infrastructure/agent/handoff_to_agent_tool.py src/infrastructure/agent/delegate_parallel_tool.py src/infrastructure/agent/delegation_adapter.py`（期望仅命中已改为服务调用、无遗留内联比较；人工核对）。
  - 审批内联校验已移除：`cd epsilon-boot && grep -nE "ApprovalDecisionCountMismatchError|ApprovalDecisionOrderMismatchError|ApprovalDecisionNotAllowedError" src/infrastructure/task/task_agent_adapter.py`（期望零命中——已收敛至领域服务）。
  - 相关调用点 lint 零新增错误：`cd epsilon-boot && uv run ruff check src/infrastructure/agent src/infrastructure/task src/application/run` 与 `cd epsilon-boot && uv run pyright src/infrastructure/agent/delegate_to_agent_tool.py src/infrastructure/agent/handoff_to_agent_tool.py src/infrastructure/agent/delegate_parallel_tool.py src/infrastructure/agent/delegation_adapter.py src/infrastructure/task/task_agent_adapter.py src/application/run/run_execution_coordinator.py src/application/run/run_approval_resumer.py`。
  - _需求: 2.5, 3.5, 4.5, 5.6, 7.4_ ; _design Property 1、2、3、4、6_

---

## Wave 3：文档（ADR-0009 + 索引，与代码正交，可最后执行）

> **正交证据**：本波只改 `docs/` 下文件，与 `epsilon-boot/` 源码零交集。

- [x] 7. ADR-0009 及索引登记
  - [x] 7.1 新增 ADR-0009
    - 在 `docs/adr/` 新建 `0009-introduce-domain-services-in-task-subdomain.md`（编号紧接现有 0008），遵循 `docs/adr/0000-template.md` 四段式。front matter：`status: Accepted`、`date: 2026-07-06`、`supersedes:` **留空**（**不 supersede ADR-0001**）。
    - 标题：`在 domain/task 引入领域服务一等抽象（充血化试点）`；四段按 design「ADR-0009 草案要点」写：背景（`domain/task` 值对象贫血、委派深度/续跑/状态映射/审批前置校验散落且跨调用点重复）、决策（`domain/task/policy.py` 引入 4 个零基础设施依赖领域服务 + 中立枚举 `TaskOutcomeKind` 避免 `domain/task→domain/run` 反向依赖，调用点委托，I/O/序列化/`RunStatus` 装配/上下文判定留原层，命名对齐 `state_machine.py`/`policy.py`/`aggregator.py`、不新增 `repository.py`）、后果（判定住进领域层、可脱离运行时单测、消除重复；本试点只覆盖 `domain/task`，其余子域按 `change-discipline` 逐子域推进；声明 `Behavior_Equivalent_Refactor`、不改对外行为、不引第三方依赖、不引领域事件/事件总线、不 supersede ADR-0001）、备选方案与未采纳原因（维持散落 / 收进值对象方法 / 统一委派两类判据 / 直接返回 `RunStatus` / 一并充血 `domain/agent` 均被否）。
    - _需求: 8.1, 8.2, 8.3, 8.4_ ; _design ADR-0009 草案要点_
    - 验证：`test -f docs/adr/0009-introduce-domain-services-in-task-subdomain.md`；`grep -nE "supersedes:" docs/adr/0009-*.md`（字段存在且值为空）。

  - [x] 7.2 更新 `docs/adr/README.md` 索引
    - 在 `docs/adr/README.md` 索引表 0008 行之后追加 0009 索引行（编号 / 标题 / `Accepted` / `2026-07-06`）。
    - _需求: 8.1_ ; _design ADR-0009 草案要点_
    - 验证：`grep -n "0009" docs/adr/README.md`（有命中）。

---

## Checkpoint 3：最终门禁（Property 全量验收）

- [x] 8. CP3 最终门禁校验（必须全部通过）
  - Property 6（测试全绿）：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed）。
  - Property 3/5（无反向依赖 + 领域纯净度）：`cd epsilon-boot && grep -rnE "domain\.run|domain/run|import (application|infrastructure|fastapi|pydantic)|from (application|infrastructure|fastapi|pydantic)" src/domain/task/`（期望零命中）。
  - Property 5（规范合规）：`cd epsilon-boot && uv run ruff check src/domain/task src/infrastructure/agent src/infrastructure/task src/application/run` 与 `cd epsilon-boot && uv run pyright src/domain/task`（零新增错误、无裸 `Any`；中文 docstring 齐备）。
  - 范围锁定（AC1.1）：`cd epsilon-boot && git diff --name-only` 中源码改动仅落 `src/domain/task/`（新增 `policy.py`/`enums.py`）+ design 列出的现有调用点 + `test/domain/task/`；文档改动仅落 `docs/`；未改 `RunStateMachine`/`WorkflowExecutionPolicy`/`ReadinessAggregator`/`WorkspacePolicy`。
  - _需求: 1.1, 1.3, 1.4, 2.5, 3.5, 4.5, 5.6, 6.1, 6.2, 6.3, 6.5, 7.4, 8.1_ ; _design Property 1、2、3、4、5、6_

---

## 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 |
|---|---|---|---|
| 1.1 | 4.1 | 组件 3（`TaskOutcomeKind`） | Property 3、5 |
| 1.2 | 2.1/2.2/2.4/3.1/3.2/4.1/5.1/5.2/5.3/6.1–6.5 | 组件 1/2/3/4 | Property 1、2、3、4、5 |
| 1.3 | 2.2/2.4/7.1/7.2/7.3 | 组件 1 | Property 1 |
| 1.4 | 3.2/7.1/7.2/7.3 | 组件 2 | Property 2 |
| 1.5 | 4.1/4.2/7.1/7.2/7.3 | 组件 3 | Property 3 |
| 1.6 | 5.2/5.3/7.1/7.2/7.3 | 组件 4 | Property 4 |
| 3.1–3.3 | 2.2/2.3/2.5 | 组件 1 调用点表 | Property 1、6 |
| 3.4 | 2.2/2.3/2.4/2.5 | 组件 1 调用点表 4/5 | Property 1、6 |
| 4.1 | 3.2/3.3/3.4/3.5 | 组件 2 | Property 2、6 |
| 4.2 | 5.2/5.3/5.4/5.5/5.6 | 组件 4 | Property 4、6 |
| 5.1/5.2 | 4.2/4.3/4.4/4.5 | 组件 3 装配映射表 | Property 3、6 |
| 5.3 | 7.4 | 测试策略第 2 项 | Property 6 |
| 7.1/7.2 | 8.1/8.2/8.3/8.4 | ADR-0009 草案要点 | —（可追溯性） |
| CP1/CP2/CP3 | 全交付物门禁 | 全组件 | Property 1–6 |

---

## 备注

- **范围纪律（change-discipline）**：仅列达成需求所必需的改动；四处既有正向样板（`RunStateMachine`/`WorkflowExecutionPolicy`/`ReadinessAggregator`/`WorkspacePolicy`）与 Agent Loop（`react_agent_adapter.py`）明确**不改**。
- **反断裂顺序**：Wave 1 先建领域构件与单测、不碰调用点 → Wave 2 逐调用点委托并删内联规则 → Wave 3 文档，保证每个 Checkpoint 处测试可绿。
- **差异保留（AC2.4）**：`DelegationDepthPolicy` 刻意提供两个方法，`delegate_parallel._one` 用 `exceeds_for_current_depth`、其余四个调用点用 `exceeds_for_next_depth`，两类判据差异不统一。
- **不下沉技术关注点**：`effective_max_depth` 的 `min(...)`、`logger.warning`、`record_collaboration_limit_hit`、`load`/`is_expired`/`consume` I/O、`_json_safe`/`_can_continue_from_context`、`RunStatus`/`ApprovalResumeStoreResult` 装配全部留在原层。
- **异常复用**：审批前置校验复用现居 `domain/agent/exceptions.py` 的三异常，不新建、不迁移；I/O 异常（`ApprovalNotFound/Expired/Consumed`）仍由 `TaskAgentAdapter` 抛出，时机不变。
- **不返回 `RunStatus`**：`TaskStatusMapping` 返回中立枚举 `TaskOutcomeKind`，应用层再装配为 `RunStatus`/`ApprovalResumeStoreResult`，避免 `domain/task→domain/run` 反向依赖。
- **回滚**：领域构件为独立新文件、调用点为局部委托替换，可按波次 `git revert`；因行为等价，回滚不影响既有测试基线。
- **行号说明**：本文中 `@行号` 引自 design.md 定位，落地前以 grep 逐点核对实际位置，防止上游文件已微调导致偏移。
