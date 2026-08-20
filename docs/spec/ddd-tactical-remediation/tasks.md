# 实现计划：DDD 战术建模代码级纠偏（第一批：低风险行为等价重构）

> 本文件由 `design.md` 展开为可执行、可勾选的任务清单。**本轮范围仅需求 A（领域日志解耦）与需求 B（序列化职责外移）**；需求 C 为纯登记项，落在文档波次（Wave 4 的 ADR / TODO），零源码影响。
> 每条任务标注：改哪些文件、对应 design 组件与 AC / Property 编号、验证命令。测试/lint 命令均在 `epsilon-boot/` 下执行。
> **全程硬约束**：`Behavior_Equivalent_Refactor`（对外线格式字面等价）、`Existing_Test_Suite_Green`（基线约 2824 passed / 3 skipped / 0 failed）、change-discipline 最小改动、code-documentation 中文 docstring、python-typing-lint（`ruff`/`pyright` 零新增错误、禁裸 `Any`，`dict[str, Any]` 因是既有返回类型允许）。

## 概述

执行采用 **波次（Wave）并发 + Checkpoint 门禁** 结构：同一 Wave 内的任务相互正交（改动不同文件、无顺序依赖）可并发执行；Wave 之间由 Checkpoint 分隔，后一波依赖前一波产物时才跨波。

- **Wave 1**：并发新建 4 个基础设施序列化映射器模块（各自独立新文件）+ 领域日志解耦（改独立文件 `context.py`）。此波**只新建 / 只解耦，暂不删除领域侧旧 `to_dict`**，保证调用点不断裂、每波结束测试可绿。
- **Checkpoint 1**：新映射器 import 正常、映射器等价性测试绿；`context.py` logging grep 清零、`test/domain/chat` 全绿。
- **Wave 2**：按子域并发迁移应用/基础设施调用点，从领域 `to_dict` 改调映射器；含 `run_execution_coordinator` 的 `hasattr`→`isinstance` 改造。共享文件的任务显式串行。
- **Checkpoint 2**：全量 pytest 绿；此时领域旧方法尚未删，`grep 'def to_dict|def to_http_dict' src/domain/` 仍 = **19**。
- **Wave 3**：确认无对外调用点后，删除领域侧已外移的对外 `to_dict`/`to_http_dict`/`to_event_payload` 及不再被使用的模块级 helper（保留 A 方案登记的私有残留）。
- **Checkpoint 3（最终门禁）**：`grep 'def to_dict|def to_http_dict' src/domain/` = **4** 且全部在 `context.py`；`grep 'import logging|getLogger' src/domain/` 清零；全量 pytest ≥ 2824 passed / 0 failed；ruff/pyright 零新增错误。
- **Wave 4**：文档（ADR-0008 + 索引 + doc-sync + 需求 C 登记），与代码正交，可与 Wave 3 并发或最后单独执行。

---

## Wave 1：新建映射器 + 领域日志解耦（并发）

> **并发正交证据**：本波 5 个任务分别创建 / 修改 **5 个互不相同的文件**：
> - T-B1 → 新建 `src/infrastructure/run/workflow_serialization.py`
> - T-B2 → 新建 `src/infrastructure/agent/guardrail_serialization.py`
> - T-B3 → 新建 `src/infrastructure/health/health_serialization.py`（+ 补 `src/infrastructure/health/__init__.py` docstring，该文件当前为空，仅本任务触碰）
> - T-B4 → 新建 `src/infrastructure/agent/segment_serialization.py`
> - T-A1 → 修改 `src/domain/chat/context.py`
> 五者无共享文件、无符号依赖、无顺序依赖，可安全并发。
> **反断裂纪律**：本波新建映射器时**逐字符照搬**领域旧逻辑，**不删除**领域侧旧 `to_dict`/`to_http_dict`/`to_event_payload`（删除在 Wave 3）。测试文件的字面快照断言在各任务内**新增**（不改既有断言语义），旧断言到 Wave 2/3 才随调用点迁移。

- [x] **T-B1** 新建 workflow 序列化映射器 `src/infrastructure/run/workflow_serialization.py`
  - 在 `src/infrastructure/run/workflow_serialization.py` 新建，参照 `src/infrastructure/agent/approval_serialization.py` 范式（`from __future__ import annotations`、模块级独立函数、模块中文 docstring 说明「基础设施层序列化 helper，依赖方向 infrastructure→domain，不向 domain 反向暴露」）。
  - 从 `src/domain/run/workflow.py` **逐字符照搬** `_dataclass_to_json_safe_dict` / `_json_safe`（workflow 版：排序 `sorted(value)`、frozenset→list、tuple→list、`StrEnum`→`.value`、`datetime`→`.isoformat()`、dict 键 `str(key)`、非 dataclass 抛 `TypeError`）为本模块**私有** helper。
  - 提供 8 个模块级映射函数，签名与 design 组件 2 一致，函数体等价 `return _dataclass_to_json_safe_dict(value)`：`workflow_capability_decision_to_dict`、`workflow_execution_policy_to_dict`、`workflow_phase_record_to_dict`、`collaboration_step_trace_link_to_dict`、`parent_child_run_link_to_dict`、`child_run_orchestration_state_to_dict`、`collaboration_summary_to_dict`、`workflow_run_state_to_dict`；入参类型从 `domain.run.workflow` 导入对应值对象，返回 `dict[str, Any]`；每个函数写中文 docstring。
  - **不改** `src/domain/run/workflow.py`（其旧 `to_dict` 与私有 helper 本波保留）。
  - _对应 design 组件「需求 B · workflow」；需求 B AC 1/2/3；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.run.workflow_serialization"`（import 正常）。

- [x]* **T-B1-T** 为 workflow 映射器补字面快照等价性测试
  - 在 `test/infrastructure/run/`（无则新建目录）下新增 `test_workflow_serialization_equivalence.py`：对 8 个映射函数各写一条**字面快照**断言 `mapper(obj) == {<逐字段展开的期望 dict>}`，覆盖 `frozenset` 排序、`enum .value`、`datetime.isoformat()`、int 键 stringify、`event_timestamps` 等边界；断言 `mapper(obj) == obj.to_dict()`（本波旧方法尚在，可交叉锁定，但**必须**同时有字面右值，避免自反型断言锁不住等价）。
  - _对应 design 测试策略第 2 项；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_workflow_serialization_equivalence.py`。

- [x] **T-B2** 新建 guardrails 序列化映射器 `src/infrastructure/agent/guardrail_serialization.py`
  - 在 `src/infrastructure/agent/guardrail_serialization.py` 新建，范式同 `approval_serialization.py`。
  - 从 `src/domain/agent/guardrails.py` **逐字符照搬** `_json_safe`（guardrails 版：排序 `sorted(value, key=str)`、set/frozenset→list、`datetime` 经 `_resolve_datetime` 补 UTC 后 `.isoformat()`、非基本类型 fallback `str(value)`）为本模块**私有** helper（与 workflow 版**不合并**，语义不同）。
  - 提供 3 个 `to_dict` 映射函数：`guardrail_model_pricing_to_dict(value) -> dict[str, float | None]`（照搬 split/total 互斥输出规则）、`guardrail_runtime_stats_to_dict(value) -> dict[str, Any]`（照搬 14 个显式字段字典，逐字段列出，非反射）、`guardrail_summary_to_dict(value) -> dict[str, Any]`（照搬 `mode.value`/`action.value`/`reason.value or None`/`_json_safe(metadata)`，其内部 `runtime_stats` 子字典改调本模块 `guardrail_runtime_stats_to_dict`）。
  - 新增 `guardrail_observation_to_event_payload(value) -> dict[str, Any]`：**外移** `GuardrailObservation.to_event_payload` 的线格式产出逻辑，内部原 `self.stats.to_dict()` 改调 `guardrail_runtime_stats_to_dict(value.stats)`，其余字段逐字段照搬。
  - 每个函数写中文 docstring。**不改** `src/domain/agent/guardrails.py`（本波保留其旧方法；领域内部私有 payload helper 的降级在 Wave 3）。
  - _对应 design 组件「需求 B · guardrails」；需求 B AC 1/2/3/7；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.agent.guardrail_serialization"`。

- [x]* **T-B2-T** 为 guardrails 映射器补字面快照等价性测试
  - 在 `test/infrastructure/agent/`（无则新建）下新增 `test_guardrail_serialization_equivalence.py`：对 3 个 `to_dict` 与 `to_event_payload` 映射各写字面快照断言，覆盖 `GuardrailModelPricing` split/total 互斥、`reason=None`、`metadata` 空/非空、`datetime` 补 UTC。
  - _对应 design 测试策略第 2 项；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_guardrail_serialization_equivalence.py`。

- [x] **T-B3** 新建 health 序列化映射器 `src/infrastructure/health/health_serialization.py`
  - 先给 `src/infrastructure/health/__init__.py`（当前为空）补一行模块中文 docstring（说明该包为 health 基础设施适配器与序列化落点）。
  - 在 `src/infrastructure/health/health_serialization.py` 新建，范式同 `approval_serialization.py`。
  - 提供 2 个映射函数：`health_check_result_to_dict(value) -> dict[str, object]`（照搬 `{"status": value.status.value}`，仅当 `value.reason is not None` 追加 `"reason"`）、`readiness_result_to_dict(value) -> dict[str, object]`（`{"status": value.status.value, "checks": {c.name: health_check_result_to_dict(c) for c in value.checks}}`，内部复用同模块函数替代原 `check.to_dict()`）。返回类型收紧为 `dict[str, object]`（类型改进，不改运行时）；写中文 docstring。
  - **不改** `src/domain/health/value_objects.py`（本波保留旧 `to_dict`）。
  - _对应 design 组件「需求 B · health」；需求 B AC 1/2/3；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.health.health_serialization"`。

- [x]* **T-B3-T** 为 health 映射器补字面快照等价性测试
  - 在 `test/infrastructure/health/`（无则新建）下新增 `test_health_serialization_equivalence.py`：对 2 个映射函数写字面快照断言，覆盖 `reason=None` / 有 `reason`、多 check 嵌套。
  - _对应 design 测试策略第 2 项；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/health/test_health_serialization_equivalence.py`。

- [x] **T-B4** 新建 segmented 序列化映射器 `src/infrastructure/agent/segment_serialization.py`
  - 在 `src/infrastructure/agent/segment_serialization.py` 新建，范式同 `approval_serialization.py`。
  - 提供 2 个映射函数：`segment_budget_usage_to_dict(value) -> dict[str, int | float]`（逐字段照搬旧实现）、`segment_run_metadata_to_http_dict(value) -> dict[str, object]`（逐字段照搬，内部 `"budget_usage"` 改调 `segment_budget_usage_to_dict(value.budget_usage)` 替代原 `value.budget_usage.to_dict()`）；写中文 docstring。
  - **不改** `src/domain/agent/segmented_execution.py`（本波保留旧方法）。
  - > 与 T-B2 同处 `infrastructure/agent/` 目录但**文件不同**（`segment_serialization.py` vs `guardrail_serialization.py`），互不写同一文件，正交。
  - _对应 design 组件「需求 B · segmented」；需求 B AC 1/2/3；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.agent.segment_serialization"`。

- [x]* **T-B4-T** 为 segmented 映射器补字面快照等价性测试
  - 在 `test/infrastructure/agent/` 下新增 `test_segment_serialization_equivalence.py`：对 2 个映射函数写字面快照断言。
  - _对应 design 测试策略第 2 项；Property 3_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_segment_serialization_equivalence.py`。

- [x] **T-A1** 领域日志解耦 `src/domain/chat/context.py`
  - 修改 `src/domain/chat/context.py`：删除第 17 行 `import logging`、第 25 行 `logger = logging.getLogger(__name__)`。
  - 删除 `raise` 分支（第 166–169 行）的 `logger.warning(...)`：**不动**随后的 `raise InvalidToolCallIdError(source=..., raw_id_value=..., tool_name=..., tool_call_index=..., extra={"skipped_count": ..., "session_id": ...})`（第 170–179 行控制流/构造参数逐字段不变，信号完整保留在异常 `details`）。
  - 删除 `filter` 分支（`else`，第 181–185 行）的 `logger.warning(...)`：删除后 `else` 体为空，须以 `pass` 或直接移除 `else` 分支使控制流不变（`skipped` 项已在循环中 `continue` 跳过，`tool_calls` 仅含合法项，最终 `return AssistantMessage(..., tool_calls=tool_calls)` 不变）；按 design 决策，`filter` 分支「移除后无可观测信息损失」（AC 4 允许直接删除），不引入返回通道、不侵入 `from_dict` 签名。
  - docstring：模块与 `from_dict` docstring 均不涉及 logging，保持不变。
  - > 与 Wave 1 各 T-B* 完全正交（独立文件、无符号交叉）。
  - _对应 design 组件「需求 A」；需求 A AC 1/2/3/4/5/6；Property 1、Property 2_
  - 验证：`cd epsilon-boot && grep -nE 'import logging|getLogger|logging\.' src/domain/chat/context.py`（期望零输出）。

---

## Checkpoint 1：映射器就绪 + 领域日志清零（门禁）

- [x] **CP1** Wave 1 门禁校验（全部通过方可进入 Wave 2）
  - 4 个新映射器可 import：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.run.workflow_serialization, infrastructure.agent.guardrail_serialization, infrastructure.health.health_serialization, infrastructure.agent.segment_serialization"`（无报错）。
  - 映射器等价性测试绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/run test/infrastructure/agent test/infrastructure/health`。
  - 领域日志清零：`cd epsilon-boot && grep -nE 'import logging|getLogger|logging\.' src/domain/chat/context.py`（期望空）。
  - chat 子域回归：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/chat`（含 `test_context_serialization_roundtrip_property.py`、`test_base_message_from_dict_raise_strategy_unit.py`、`test_base_message_from_dict_id_validation_unit.py`、`test_message_hierarchy_unit.py`，全绿）。
  - _对应 需求 A AC 1/7；Property 1、Property 2、Property 3、Property 6_

---

## Wave 2：调用点迁移（按子域并发；共享文件串行）

> **迁移原则**：把 `application/`、`infrastructure/` 中对领域 `to_dict`/`to_http_dict`/`to_event_payload` 的调用改为调 Wave 1 的映射器；import 从 `domain.*` 改为对应 `infrastructure.*_serialization`。**领域旧方法本波仍保留**（Wave 3 才删），故迁移期新旧并存、测试始终可绿。落地时须以 `grep -rnE '\.to_dict\(\)|\.to_http_dict\(\)|\.to_event_payload\(\)' src/application src/infrastructure` 对每个对象类型逐点核对无遗漏。
> **正交与串行判定（基于 design 调用点迁移表）**：
> - `react_agent_adapter.py` 同时含 guardrails 相关调用点（`runtime_stats.to_dict()` @1341/1420/2044），归入 **T-M2**，其它任务不得触碰该文件。
> - `run_guardrail_recorder.py`（`to_event_payload` @56、`summary_after.to_dict()` @71）与 `run_checkpoint_recovery_service.py`（`mark_guardrail_summary_stale(...).to_dict()` @194）均属 guardrails 子域，归入 **T-M2**，避免与 workflow 任务写冲突。
> - `run_checkpoint_recovery_service.py` 若同时命中 `WorkflowRunState` 构造调用点，则该文件整体归 **T-M2 串行处理**（同一文件不并发写）；workflow 任务 T-M1 只处理其**独占**的文件（`workflow_orchestrator.py`、`run_application_service.py`、`workflow_collaboration_recorder.py`、`run_checkpoint_sink.py`）。落地时 grep 核对：若某文件跨子域，归入其中一个任务串行，禁止两任务并发写同文件。
> - T-M1（workflow）/ T-M2（guardrails 及跨子域共享文件）/ T-M3（health）/ T-M4（segmented）各自的文件集合在核对后须互不相交，方可并发；相交文件一律并入单一任务串行。

- [x] **T-M1** 迁移 workflow 对象调用点（workflow 独占文件）
  - 修改 `src/application/run/workflow_orchestrator.py`（@202/208/359/365 `decision.to_dict()`/`capability_decision.to_dict()` → `workflow_capability_decision_to_dict(...)`；@289/402 `ChildRunOrchestrationState(...).to_dict()` → `child_run_orchestration_state_to_dict(...)`）。
  - 修改 `src/application/run/run_application_service.py`（@329 `WorkflowRunState(...).to_dict()` → `workflow_run_state_to_dict(...)`）。
  - 修改 `src/infrastructure/agent/workflow_collaboration_recorder.py`（@61 `step.to_dict()` → `collaboration_step_trace_link_to_dict(step)`）。
  - 以 grep 逐点核对并替换 `WorkflowExecutionPolicy`/`WorkflowPhaseRecord`/`ParentChildRunLink`/`CollaborationSummary` 在 workflow **独占**文件（含 `run_checkpoint_sink.py` 若非跨子域）中的调用点为对应 `*_to_dict(obj)`；import 改为 `from infrastructure.run.workflow_serialization import ...`。
  - > `workflow_orchestrator.py` 等文件中的本地私有 `_json_safe` 是各文件自有 helper，与 domain 无关，**不改**。
  - _对应 design「调用点迁移表 · workflow 对象」；需求 B AC 4/7；Property 4_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/run test/infrastructure/agent`。

- [x] **T-M2** 迁移 guardrails 对象调用点 +（跨子域）共享文件（串行，独占 `react_agent_adapter.py` / `run_guardrail_recorder.py` / `run_checkpoint_recovery_service.py`）
  - 修改 `src/application/run/run_guardrail_recorder.py`（@56 `observation.to_event_payload()` → `guardrail_observation_to_event_payload(observation)`；@71 `summary_after.to_dict()` → `guardrail_summary_to_dict(summary_after)`）。
  - 修改 `src/application/run/run_checkpoint_recovery_service.py`（@194 `mark_guardrail_summary_stale(...).to_dict()` → `guardrail_summary_to_dict(mark_guardrail_summary_stale(...))`；**若该文件同时命中 `WorkflowRunState(...).to_dict()`，一并在本任务替换为 `workflow_run_state_to_dict(...)`**——因该文件跨 workflow/guardrails 子域，整体归本任务串行，不与 T-M1 并发）。
  - 修改 `src/infrastructure/agent/react_agent_adapter.py`（@1341/1420/2044 `runtime_stats.to_dict()` 等作为 `metadata=` 传入 trace → `guardrail_runtime_stats_to_dict(runtime_stats)`；逐点核对该文件是否含其它 guardrails/workflow 调用点，一并在本任务处理）。
  - import 改为对应 `infrastructure.agent.guardrail_serialization` / `infrastructure.run.workflow_serialization`。
  - _对应 design「调用点迁移表 · guardrails 对象」；需求 B AC 4/7；Property 4_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/run test/infrastructure/agent`。

- [x] **T-M3** 迁移 health 对象调用点
  - 修改 `src/application/api/routers/health.py`（@52 `result.to_dict()` → `readiness_result_to_dict(result)`），import 改为 `from infrastructure.health.health_serialization import readiness_result_to_dict`。
  - > 独占 `routers/health.py`，与其它迁移任务无文件交集，正交。
  - _对应 design「调用点迁移表 · health 对象」；需求 B AC 4/7；Property 4_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/api`（或既有 health router 测试子集）。

- [x] **T-M4** 迁移 segmented 对象调用点 + `hasattr`→`isinstance` 改造
  - 修改 `src/application/api/routers/task.py`（@118 `metadata.budget_usage.to_dict()` → `segment_budget_usage_to_dict(metadata.budget_usage)`），import 对应映射器。
  - 修改 `src/application/run/run_execution_coordinator.py`（`_segment_metadata`，@542-543）：把 `hasattr(metadata, "to_http_dict")` + `metadata.to_http_dict()` 改为 `isinstance(metadata, SegmentRunMetadata)` 判定后调 `segment_run_metadata_to_http_dict(metadata)`；import `SegmentRunMetadata`（domain）与 `segment_run_metadata_to_http_dict`（infrastructure）。此为唯一需改**判定形态**的点：外移后领域对象不再有 `to_http_dict` 属性，`hasattr` 恒 False，故须改 `isinstance` 保持等价行为。
  - > 独占 `routers/task.py`、`run_execution_coordinator.py`，与 T-M1/T-M2/T-M3 文件集合不相交，正交。
  - _对应 design「调用点迁移表 · segmented 对象」及其 `hasattr`→`isinstance` 说明；需求 B AC 4/7；Property 4_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application`。

---

## Checkpoint 2：调用点全部迁移、全量测试绿（门禁）

- [x] **CP2** Wave 2 门禁校验
  - 全量测试绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（期望 ≥ 2824 passed / 0 failed）。
  - 无遗漏调用点核对：`cd epsilon-boot && grep -rnE '\.to_event_payload\(\)' src/application src/infrastructure`（对 `GuardrailObservation` 应无遗留领域方法调用；本地非领域对象除外，需人工确认）。
  - 领域旧方法**尚未删**，期望 `cd epsilon-boot && grep -rcE 'def to_dict|def to_http_dict' src/domain/`（各文件命中合计 = **19**：context.py 4 + workflow.py 8 + guardrails.py 3 + health/value_objects.py 2 + segmented_execution.py 2）。
  - _对应 需求 B AC 5；Property 4、Property 6_

---

## Wave 3：删除领域侧对外旧序列化方法 + 收敛（按子域并发）

> **前置**：CP2 通过（对外调用点已全部改调映射器）。本波删除领域各文件中**已外移的对外方法**，保留 A 方案登记的私有残留。
> **并发正交证据**：4 个任务分别改 **4 个互不相同的领域文件**（`workflow.py` / `guardrails.py` / `health/value_objects.py` / `segmented_execution.py`），无共享文件、无符号依赖，可并发。

- [x] **T-D1** 收敛 `src/domain/run/workflow.py`
  - 删除 8 个对外 `to_dict` 方法（`WorkflowCapabilityDecision`/`WorkflowExecutionPolicy`/`WorkflowPhaseRecord`/`CollaborationStepTraceLink`/`ParentChildRunLink`/`ChildRunOrchestrationState`/`CollaborationSummary`/`WorkflowRunState`）。
  - 处理 `canonicalize_collaboration_summary`（领域归一逻辑，**保留领域层**）：其内部第 352 行 `value.to_dict()` 改为调领域私有 `_dataclass_to_json_safe_dict(value)`；据 A 方案在本文件**保留私有** `_json_safe` **1 份** + `_dataclass_to_json_safe_dict` **1 份**，专供 `canonicalize_collaboration_summary` 使用（不再作为对外 `to_dict` 的实现）。
  - 更新受影响处中文 docstring；确认删除后无 domain 内部残留调用被断裂。
  - _对应 design「需求 B · workflow」`canonicalize` 处置 + Property 5 目标基线；需求 B AC 1/2；Property 5_
  - 验证：`cd epsilon-boot && grep -nE 'def to_dict' src/domain/run/workflow.py`（期望零命中）；`grep -cE 'def _json_safe|def _dataclass_to_json_safe_dict' src/domain/run/workflow.py`（期望各 1）。

- [x] **T-D2** 收敛 `src/domain/agent/guardrails.py`
  - 删除 3 个对外 `to_dict`（`GuardrailModelPricing`/`GuardrailRuntimeStats`/`GuardrailSummary`）；删除 `GuardrailObservation.to_event_payload`（已外移到 `guardrail_serialization.py`）。
  - 保留领域行为方法：`GuardrailModelPricing.from_raw`/`estimate_cost`、`GuardrailRuntimeStats.from_model_usage`、`GuardrailDecision.to_summary`。
  - 领域内部编排（`merge_guardrail_summary`/`mark_guardrail_summary_stale`/`_coerce_runtime_stats_payload`）原先调 `to_dict` 生成 `runtime_stats` 子字典处：将 `to_dict` 逻辑降级为领域**私有内联** helper（如模块级 `_runtime_stats_payload(value)` / `_summary_payload(value)`），供上述内部编排使用；据 A 方案保留 `_json_safe` **1 份** + 上述 payload helper（属领域内部规范表示，非对外序列化）。
  - 更新受影响处中文 docstring。
  - _对应 design「需求 B · guardrails」各方法去留判定 + Property 5 目标基线；需求 B AC 1/2；Property 5_
  - 验证：`cd epsilon-boot && grep -nE 'def to_dict|def to_event_payload' src/domain/agent/guardrails.py`（期望零命中）；确认 `from_raw`/`from_model_usage`/`estimate_cost`/`to_summary` 仍在。

- [x] **T-D3** 收敛 `src/domain/health/value_objects.py`
  - 删除 `HealthCheckResult.to_dict`、`ReadinessResult.to_dict`（已外移到 `health_serialization.py`；本文件本无私有序列化 helper，无残留）。
  - 更新受影响处中文 docstring。
  - _对应 design「需求 B · health」+ Property 5（health 私有 helper = 0）；需求 B AC 1/2；Property 5_
  - 验证：`cd epsilon-boot && grep -nE 'def to_dict' src/domain/health/value_objects.py`（期望零命中）。

- [x] **T-D4** 收敛 `src/domain/agent/segmented_execution.py`
  - 删除 `SegmentBudgetUsage.to_dict`、`SegmentRunMetadata.to_http_dict`（已外移到 `segment_serialization.py`；本文件本无私有序列化 helper，无残留）。
  - 更新受影响处中文 docstring。
  - _对应 design「需求 B · segmented」+ Property 5（segmented 私有 helper = 0）；需求 B AC 1/2；Property 5_
  - 验证：`cd epsilon-boot && grep -nE 'def to_dict|def to_http_dict' src/domain/agent/segmented_execution.py`（期望零命中）。

- [x] **T-D5** 迁移既有领域侧序列化断言测试（随删除同步）
  - 对 `test/domain/run/*`、`test/domain/agent/test_guardrail_*`、`test/domain/health/test_value_objects_property.py`、`test/domain/agent/test_segmented_execution_value_objects_unit.py` 中断言旧 `obj.to_dict()`/`to_http_dict()`/`to_event_payload()` 的用例：把左值 `obj.to_dict()` 改为对应 `mapper(obj)`，**右值字面不动**（只改调用位置，不改断言语义）。
  - **迁移前逐处核查**：若某断言是「字面 expected dict / golden 右值」→ 直接换左值；若发现为**自反型断言**（右值也由 `to_dict` 派生，锁不住等价）→ **不得**直接迁移，须在 Wave 1 的 `test/infrastructure/<子域>/` 等价性测试中已有字面快照覆盖该对象，否则补写。
  - 若某测试文件因 import `to_dict` 相关符号断裂，只改 import 指向 `infrastructure.*_serialization`，不改断言语义。
  - > 与 T-D1..T-D4 是不同文件（`test/` 下），但因逻辑上依赖领域方法被删，故置于同波末尾；本任务与 T-D1..T-D4 若并发须确保各改各自子域测试文件、互不写同文件。
  - _对应 design 测试策略第 1/2 项；需求 B AC 5；Property 3、Property 6_
  - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain`。

---

## Checkpoint 3：最终门禁（Property 全量验收）

- [x] **CP3** 最终门禁校验（必须全部通过）
  - Property 5（领域对外序列化零残留 + 目标基线）：`cd epsilon-boot && grep -rnE 'def to_dict|def to_http_dict' src/domain/` → 命中数 **= 4** 且 4 处**全部**落在 `domain/chat/context.py`（第 94/254/297/477 行）；`run/workflow.py`、`agent/guardrails.py`、`health/value_objects.py`、`agent/segmented_execution.py` **均不得再命中**。
  - Property 5（私有残留基线，A 方案）：`workflow.py` 保留 `_json_safe` 1 份 + `_dataclass_to_json_safe_dict` 1 份；`guardrails.py` 保留 `_json_safe` 1 份 + payload helper；health / segmented 私有 helper = 0（人工核对 + grep）。
  - Property 1（领域纯净度）：`cd epsilon-boot && grep -rnE '^import logging|getLogger' src/domain/` → **零命中**；`grep -nE 'import logging|getLogger|logging\.' src/domain/chat/context.py` → 零命中。
  - Property 6（测试全绿）：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest` → ≥ 2824 passed / 0 failed。
  - Property 7（规范合规）：`cd epsilon-boot && uv run ruff check` 与 `cd epsilon-boot && uv run pyright` → 零新增错误、无裸 `Any`。
  - _对应 需求 A AC 1/7/9、需求 B AC 5/9；Property 1、Property 5、Property 6、Property 7_

---

## Wave 4：文档与登记（与代码正交，可最后单独执行）

> **正交证据**：本波只改 `docs/` 下文件，与 `epsilon-boot/` 源码零交集，可与 Wave 3 并发或最后执行。

- [x] **T-DOC1** 新增 ADR-0008
  - 在 `docs/adr/` 新建 `0008-extract-domain-serialization-to-infrastructure-mappers.md`（编号紧接现有 0007），遵循 `docs/adr/0000-template.md` 四段式。front matter：`status: Accepted`、`date: 2026-07-06`、`supersedes:` **留空**（**不 supersede ADR-0001**）。
  - 标题：`将领域对象序列化职责外移至基础设施层序列化映射器`；四段内容按 design「ADR 决策（ADR-0008）」写：背景（domain 值对象自带 `to_dict`/`to_http_dict`/`to_event_payload` + 私有 helper 违反 SRP 与 `ddd-tactical-modeling.md` 第 9 节）、决策（按子域 `infrastructure/<子域>/*_serialization.py` 模块级独立函数，与 `approval_serialization.py` 范式一致；领域行为方法与领域内部规范表示 helper 保留）、后果、备选方案与未采纳原因（集中式单包 / 映射器基类抽象 / 保留领域 `to_dict` 均被否）。
  - _对应 design「ADR 决策」；需求 B AC 6；需求 A/B change-discipline §2/§3_
  - 验证：`test -f docs/adr/0008-extract-domain-serialization-to-infrastructure-mappers.md`；`grep -nA6 '^---' docs/adr/0008-*.md | grep -n 'supersedes:'`（字段存在且值为空）。

- [x] **T-DOC2** 更新文档索引与主题文档（doc-sync）
  - `docs/adr/README.md`：在 0007 行之后追加 0008 索引行。
  - 按 `doc-sync.md`，在涉及序列化职责归属的主题文档中同步（如 `docs/architecture.md` / `docs/domain-model.md` 中若描述领域对象自带 `to_dict`，改为「序列化由 `infrastructure/<子域>/*_serialization.py` 映射器承担」；仅在实际存在相关表述处最小改动，回链 ADR-0008）。
  - _对应 design「ADR README 索引」；doc-sync §3；需求 B AC 6_
  - 验证：`grep -n '0008' docs/adr/README.md`（有命中）。

- [x] **T-DOC3** 登记需求 C 后续事项与观察项
  - 在 `TODO.md`（或 design 需求 C 表已登记则回链之）登记：前置需求 1（`Domain_Logic_In_Infrastructure`，极高风险，独立后续 spec、先写 ADR）、需求 2（`Anemic_Domain_Model`，中，独立 spec + ADR）、需求 4（`Application_Transaction_Script`，低，独立 spec、无需 ADR）；`Serialization_Observation_Item`（`domain/agent/tools.py::to_schema` 属工具契约自描述，本期及后续均不外移，仅登记结论）；序列化彻底零残留选项 B（workflow 的 `canonicalize_collaboration_summary` 上提、guardrails 汇总编排上提）留待后续 spec。
  - **零源码影响**：本任务不改 `epsilon-boot/` 任何源码。
  - _对应 design 需求 C 表；需求 C AC 1/2/3/4_
  - 验证：人工核对登记项齐备；`git diff --name-only` 中本任务改动仅落 `docs/` 或根 `TODO.md`。

---

## 任务 → 组件 → AC → 正确性属性 追溯表

| 任务 | design 组件 | 覆盖 AC | 正确性属性 |
|---|---|---|---|
| T-A1 | 需求 A 组件 | A: 1/2/3/4/5/6 | Property 1、2 |
| T-B1 (+T) | 需求 B · workflow | B: 1/2/3/7 | Property 3 |
| T-B2 (+T) | 需求 B · guardrails | B: 1/2/3/7 | Property 3 |
| T-B3 (+T) | 需求 B · health | B: 1/2/3 | Property 3 |
| T-B4 (+T) | 需求 B · segmented | B: 1/2/3 | Property 3 |
| T-M1..T-M4 | 调用点迁移表 | B: 4/7 | Property 4 |
| T-D1..T-D5 | 需求 B 收敛 + Property 5 基线 | B: 1/2/5 | Property 3、5、6 |
| CP1/CP2/CP3 | 全交付物门禁 | A: 1/7/9；B: 5/9 | Property 1–7 |
| T-DOC1..T-DOC3 | ADR-0008 + 索引 + 需求 C | B: 6；C: 1/2/3/4 | —（可追溯性） |

---

## 备注

- **范围纪律（change-discipline §1）**：仅列达成需求 A/B 所必需的改动；各文件本地私有 `_json_safe`（`workflow_orchestrator.py` 等）与 `domain/agent/tools.py::to_schema` 明确**不改**。
- **反断裂顺序**：Wave 1 先建映射器 + 保留领域旧方法 → Wave 2 迁调用点 → Wave 3 才删领域旧方法，保证每个 Checkpoint 处测试可绿；Wave 3 之前 `grep 'def to_dict|def to_http_dict' src/domain/` 恒 = 19，Wave 3 后 = 4。
- **必须串行的共享文件（并发写冲突规避）**：
  - Wave 2 中 `react_agent_adapter.py`、`run_guardrail_recorder.py`、`run_checkpoint_recovery_service.py` 归入单一任务 **T-M2** 串行（跨 workflow/guardrails 子域或本身 guardrails 子域），不得与 T-M1 并发写。
  - Wave 3 中 T-D5（测试迁移）与 T-D1..T-D4（领域收敛）逻辑依赖领域方法删除，置于同波末尾；若并发须确保各改各自子域文件、互不写同文件。
- **私有残留（A 方案，非违约、明确登记）**：`workflow.py` 保留 `_json_safe` + `_dataclass_to_json_safe_dict` 各 1 份；`guardrails.py` 保留 `_json_safe` + payload helper。彻底零残留（选项 B）登记为需求 C 后续 spec。
- **回滚**：映射器为独立新文件、领域收敛为局部删除，可按波次 `git revert`；因线格式字面等价，回滚不影响既有测试基线。
- **前提修正说明**：design「目录/模块落点」备注称 `infrastructure/health/__init__.py` 可能需新建——经核对该文件**已存在但为空**，故 T-B3 仅为其补 docstring，不新建包目录。
