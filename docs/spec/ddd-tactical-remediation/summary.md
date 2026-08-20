# ddd-tactical-remediation — 落地总结

## Feature

`ddd-tactical-remediation`：前置 spec `ddd-implementation-review`（「本项目 DDD 落地 vs 业界主流」调研）的**代码级 follow-up 第一批**。前置只落地了需求 6（补战术建模 steering 规范，纯文档）；本 spec 承接其中风险最低、行为等价的两项代码纠偏：

- **需求 A（`Domain_Purity_Blemish`）**：移除 `domain/chat/context.py` 对 `logging` 的直接依赖。
- **需求 B（`Domain_Serialization_Concern`）**：把领域对象的对外序列化职责外移到基础设施层序列化映射器。
- **需求 C**：前置需求 1/2/4 与观察项登记为后续（见 design 需求 C 表）。

全程为 `Behavior_Equivalent_Refactor`（对外线格式字面等价），既有测试全绿。

## 最终产物清单

### 新增（源码）
- `src/infrastructure/run/workflow_serialization.py` — workflow 8 映射函数 + 私有 `_dataclass_to_json_safe_dict`/`_json_safe`。
- `src/infrastructure/agent/guardrail_serialization.py` — guardrails 3 `to_dict` 映射 + `guardrail_observation_to_event_payload` + 私有 `_json_safe`/`_resolve_datetime`。
- `src/infrastructure/agent/segment_serialization.py` — segmented 2 映射函数。
- `src/infrastructure/health/health_serialization.py` — health 2 映射函数（+ `infrastructure/health/__init__.py` 补包 docstring）。

### 新增（测试）
- `test/infrastructure/run/test_workflow_serialization_equivalence.py`（8 字面快照断言）
- `test/infrastructure/agent/test_guardrail_serialization_equivalence.py`（含 to_event_payload）
- `test/infrastructure/agent/test_segment_serialization_equivalence.py`
- `test/infrastructure/health/test_health_serialization_equivalence.py`

### 新增（文档）
- `docs/adr/0008-extract-domain-serialization-to-infrastructure-mappers.md`（`Accepted`，不 supersede ADR-0001）+ `docs/adr/README.md` 索引一行。

### 修改（领域层收敛）
- `src/domain/chat/context.py` — 删 `import logging` + `logger` 定义 + 2 处 `logger.warning`。
- `src/domain/run/workflow.py` — 删 8 个对外 `to_dict`；`canonicalize_collaboration_summary` 保留并改调领域私有 helper（A 方案：保留 `_json_safe` + `_dataclass_to_json_safe_dict` 各 1）。
- `src/domain/agent/guardrails.py` — 删 3 `to_dict` + `to_event_payload`；内部编排降级为领域私有 `_runtime_stats_payload`（A 方案：保留 `_json_safe` 1）；保留领域行为 `from_raw`/`estimate_cost`/`from_model_usage`/`to_summary`。
- `src/domain/health/value_objects.py` — 删 2 `to_dict`（无残留）。
- `src/domain/agent/segmented_execution.py` — 删 `to_dict` + `to_http_dict`（无残留）。

### 修改（调用点迁移 8 文件 + 测试迁移 10 文件）
- 应用/基础设施调用点从 `obj.to_dict()` 改调映射器：`application/api/routers/{health,task}.py`、`application/run/{workflow_orchestrator,run_application_service,run_guardrail_recorder,run_checkpoint_recovery_service,run_execution_coordinator}.py`、`infrastructure/agent/{react_agent_adapter,workflow_collaboration_recorder}.py`、`infrastructure/chat/chat_service_adapter.py`。
- `run_execution_coordinator._segment_metadata` 的 `hasattr(metadata,"to_http_dict")` → `isinstance(metadata, SegmentRunMetadata)`。
- 领域侧序列化断言测试原地迁移（左值改调映射器，字面右值不动）。

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 映射器组织 | 按子域 `infrastructure/<子域>/*_serialization.py`，模块级独立函数 | 与既有 `approval_serialization.py` 范式一致，避免集中式单包反向聚合多子域导入 |
| 两套 `_json_safe` | 不合并（workflow `sorted(value)` vs guardrails `sorted(value, key=str)`+`str()` fallback） | 逐字段等价优先于消重，避免线格式漂移 |
| 私有残留 | A 方案：workflow/guardrails 各保留领域私有 helper 供内部编排 | 低风险定位；对外序列化职责已净移出；彻底零残留（选项 B）登记后续 |
| ADR | 新增 0008，不 supersede ADR-0001 | 序列化职责归属变更属架构级；领域事件决策不回退 |

## 执行过程中的受控偏差（如实记录）

1. **需求 A 告警信号 —— Checkpoint 1 复核修订**：design 初判「filter 分支告警无测试断言、可安全删」不成立——实测有 **4 个既有测试**（`test_t8`/`test_t10`/`test_session_id_propagated`/`test_id_validation_log_extra_alignment` 的 history_restore 用例）断言该领域 WARN 日志。经用户批准，认定该日志为**纯领域内部诊断 telemetry**（不属对外可观测面），随需求 A 一并移除，属**有意的受控可观测面变更**；4 个测试**保留全部行为/异常断言、仅删日志断言**（`test_session_id_...` 改为断言 filter 分支 metadata 保留行为）。raise 分支信号完整保留于 `InvalidToolCallIdError.details`。已更新 requirement 需求 A AC 3 记录此受控例外。
2. **循环 import —— 函数内局部 import**：`application/run/*` 在模块级 import `infrastructure.run.*` 会触发既有 `infrastructure/run/__init__.py` eager-import（`run_worker`）造成的循环依赖。采用**函数内局部 import** 最小规避，未重构 `__init__.py`。登记为后续观察项（见下）。

## 验证结论（Checkpoint 3 最终门禁全绿）

- **Property 1（领域纯净度）**：`grep -rnE '^import logging|getLogger|logging\.' src/domain/` → 零命中。
- **Property 5（领域对外序列化零残留 + 目标基线）**：`grep -rnE 'def to_dict|def to_http_dict' src/domain/` → **命中 4，全部在 `domain/chat/context.py`**（chat 消息序列化，属需求 A 回归对象、明确不外移）；workflow/guardrails/health/segmented 对外序列化全部清零。私有残留符合 A 方案基线（workflow `_json_safe`+`_dataclass_to_json_safe_dict` 各 1、guardrails `_json_safe` 1、health/segmented 0）。
- **Property 6（测试全绿）**：`PYTHONPATH=src uv run --frozen pytest` → **2847 passed / 3 skipped / 0 failed**（较基线 2824 多 23，来自新增等价性测试；无删任何行为断言语义）。
- **Property 7（规范合规）**：本 spec 改动/新增文件 `ruff check` 全绿；4 个新增映射器 `pyright` 0 errors。仓库残留的 3 处 pyright 错误（`context.py:147`、`guardrails.py` isoformat）经与 HEAD 逐行核对为**既存基线**，非本 spec 引入。

## 后续事项（Follow-ups，均不在本轮范围，见 design 需求 C 表）

- **前置需求 1**（`Domain_Logic_In_Infrastructure`，3310 行 Agent Loop 上提，极高风险）：独立后续 spec，先写 ADR。
- **前置需求 2**（`Anemic_Domain_Model` 单子域充血化试点，中）：独立 spec + ADR。
- **前置需求 4**（`Application_Transaction_Script` 应用层大文件拆分，低）：独立 spec，可不必 ADR。
- **序列化彻底零残留（选项 B）**：`canonicalize_collaboration_summary` 外移、guardrails 汇总编排上提，消除领域私有序列化 helper。
- **循环 import 治理**：`infrastructure/run/__init__.py` eager-import 导致 `application/run → infrastructure.run` 需函数内局部 import；后续可通过延迟 `__init__` 聚合或调整装配消除，使映射器 import 可回归模块级。
- **观察项 `to_schema`**：`domain/agent/tools.py::to_schema` 属工具契约自描述（非数据序列化），本期及后续均不外移，仅登记。

本 spec 按流水线规则以「校验命令 + 全绿测试 + lint」为验收依据；波次并发（Wave 1 建映射器 → CP1 → Wave 2 迁调用点 → CP2 → Wave 3 删领域旧方法 → CP3 → Wave 4 文档）每个 Checkpoint 均通过。
