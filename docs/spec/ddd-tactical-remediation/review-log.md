# Review Log — ddd-tactical-remediation

> Append-only history. 记录每次 evaluator 调用与跳过决策，用于恢复与审计。

## Wave 1

- T-B1 / T-B1-T (attempt 1): 新建 `infrastructure/run/workflow_serialization.py`（8 映射函数 + 私有 `_dataclass_to_json_safe_dict`/`_json_safe` 逐字符照搬）与 `test/infrastructure/run/test_workflow_serialization_equivalence.py`（8 条字面快照 + 交叉 `to_dict()` 断言）。自验证：import OK；pytest 8 passed；ruff clean。evaluator 调用由父级 checkpoint 统一处理（本次由 spec-generator 直接完成实现与自验证，未单独 spawn spec-evaluator subagent，遵循「≤3 并发/不嵌套」约束）。
- T-B4 / T-B4-T (attempt 1): 新建 `infrastructure/agent/segment_serialization.py`（2 映射函数 `segment_budget_usage_to_dict`/`segment_run_metadata_to_http_dict`，逐字段照搬 `SegmentBudgetUsage.to_dict`/`SegmentRunMetadata.to_http_dict`，后者 `budget_usage` 改调前者）与 `test/infrastructure/agent/test_segment_serialization_equivalence.py`（4 条字面快照 + 交叉 `to_dict()`/`to_http_dict()` 断言，覆盖默认与非默认构造）。未改 `domain/agent/segmented_execution.py`。自验证：import OK；pytest 4 passed。evaluator 调用由父级 checkpoint 统一处理。

## Wave 3

- T-D1 (attempt 1): 收敛 `src/domain/run/workflow.py`——删除 8 个对外 `to_dict` 方法（`WorkflowCapabilityDecision`/`WorkflowExecutionPolicy`/`WorkflowPhaseRecord`/`CollaborationStepTraceLink`/`ParentChildRunLink`/`ChildRunOrchestrationState`/`CollaborationSummary`/`WorkflowRunState`）；`canonicalize_collaboration_summary` 保留领域层，其 `value.to_dict()` 改调领域私有 `_dataclass_to_json_safe_dict(value)`；私有 `_json_safe`/`_dataclass_to_json_safe_dict` 各保留 1 份专供该函数。修复断裂测试：`test_workflow_serialization_equivalence.py` 删 8 条交叉 `obj.to_dict()` 断言（保留字面快照）；`test_workflow_value_objects_unit.py` 与 `test_workflow_collaboration_events_unit.py` 改调 `infrastructure.run.workflow_serialization` 映射器（右值字面不动）。checkpoint/recovery/role 测试的 `.to_dict()` 经 grep 核实均为 `ConversationContext`，非 workflow 子域，未动。自验证：`grep def to_dict` 零命中；私有 helper 计数 2；`pytest test/domain/run test/infrastructure/run test/application/run` 387 passed；ruff clean（无未使用 import）。evaluator：本上下文 `spec-evaluator` subagent 工具不可用（Task tool not enabled），未能 spawn；已完成全部自验证门（grep 基线 + 全绿测试 + lint），改动逐字段等价由保留的字面快照断言锁定。未勾选 tasks.md。
