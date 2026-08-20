# 实现计划：DDD Follow-up Refinements（DDD 收尾清理）

## 概述

本计划按 `design.md` 的分片顺序，把三项非阻塞 follow-up 拆分为可独立实现、验证和评审的最小行为等价切片，全部在 `epsilon-boot/` 目录下执行验证命令（`PYTHONPATH=src uv run --frozen pytest ...`）。三项均为纯行为等价重构：不新增对外功能，不改 API 契约 / 事件类型 / 流式协议 / 错误语义，重构前后既有测试断言保持一致。

切片顺序遵循风险与依赖：

1. **切片 A（serializer 受控例外清理，最高优先，中风险）**：新增应用侧序列化 Protocol（`application/run/serialization_ports.py`）与基础设施 delegating adapter（`infrastructure/run/run_serialization_adapters.py`），随后按 `run_execution_coordinator → run_guardrail_recorder → run_checkpoint_recovery_service → run_application_service → workflow_orchestrator` 顺序逐个消费方改造，每消除一项即完成「组合根注入 + allowlist 删一项 + 静态 guard」，最终 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 收敛为空。
2. **切片 B（ChatServiceAdapter prompt 去重，低风险小面）**：新增 `infrastructure/chat/chat_default_prompt.py` 单一 helper，`ChatServiceAdapter.__init__` 与组合根 `_create_chat_service` 两处消费。
3. **切片 C（react_agent_adapter.py SRP 拆分，低风险大面）**：按 `guardrail_runtime_accumulator → react_trace_recorder → react_concurrent_tool_executor（ADR-0013 留 infra，逐字平移）→ react_approval_checkpoint` 顺序在基础设施层内部抽出协作类，`ReActAgentAdapter` 保留为门面，每抽一块跑 `test/infrastructure/agent` 守护。

约束基线：全量测试不劣于 3072 passed、2 skipped、1 warning；每切片检查点运行 `uv run ruff check src && uv run pyright src`；不重开 ADR-0013、不新增 ADR、不上提领域层、不改分层方向；改动结构后同步主题文档。本计划不含数据库 DDL、索引、Redis key schema、文件布局迁移、配置键变更或 backfill。

### 执行顺序与依赖关系

- **切片 A 内部严格有序**：任务 1（Protocol + adapter 基础设施）是任务 2–6 各消费方改造的**前置依赖**，必须先落地且**先不删任何 allowlist 条目**；任务 2→6 按 `run_execution_coordinator → run_guardrail_recorder → run_checkpoint_recovery_service → run_application_service → workflow_orchestrator` 顺序逐条消除（allowlist 5→4→3→2→1→0），每条后 `test_application_infrastructure_exception_scope_is_exact` 须精确通过；任务 7 为收尾检查点。
- **切片 B（任务 8–9）、切片 C（任务 10–14）相互独立**，与切片 A 之间无代码耦合，建议在切片 A 完成后进行，B、C 顺序可任意；切片 C 内部按 `guardrail_runtime_accumulator → react_trace_recorder → react_concurrent_tool_executor → react_approval_checkpoint` 顺序抽出。
- **文档同步与 ADR 判断（任务 15）** 在相关切片代码完成后进行；任务 16 为全特性最终检查点。

### 评审标注约定

- `【需 spec-evaluator 审查】`：含生产代码改动的实现任务，须经 spec-evaluator 审查。
- `【评审可选】`：纯文档同步 / 纯验证检查点 / ADR 判断记录，评审可选（若涉及既有 ADR 偏离则须审查）。

## Tasks

- [x] 1. Slice A 序列化抽象与基础设施实现（新增，不改消费方）【需 spec-evaluator 审查】
  - [x] 1.1 创建应用侧序列化 Protocol 模块
    - 在 `epsilon-boot/src/application/run/serialization_ports.py` 中新增模块，含模块级中文 docstring，仅 import `domain.*` 值对象类型与标准库（`typing.Protocol`、`typing.Any`），禁止 import `infrastructure`。
    - 定义 `class WorkflowSerializerPort(Protocol)`，方法 `workflow_run_state_to_dict(self, value: WorkflowRunState) -> dict[str, Any]`、`workflow_capability_decision_to_dict(self, value: WorkflowCapabilityDecision) -> dict[str, Any]`、`child_run_orchestration_state_to_dict(self, value: ChildRunOrchestrationState) -> dict[str, Any]`。
    - 定义 `class GuardrailSerializerPort(Protocol)`，方法 `guardrail_summary_to_dict(self, value: GuardrailSummary) -> dict[str, Any]`、`guardrail_observation_to_event_payload(self, value: GuardrailObservation) -> dict[str, Any]`。
    - 定义 `class SegmentSerializerPort(Protocol)`，方法 `segment_run_metadata_to_http_dict(self, value: SegmentRunMetadata) -> dict[str, object]`。
    - 类型来源：`domain.agent.guardrails`（`GuardrailObservation`、`GuardrailSummary`）、`domain.agent.segmented_execution`（`SegmentRunMetadata`）、`domain.run.workflow`（`ChildRunOrchestrationState`、`WorkflowCapabilityDecision`、`WorkflowRunState`）；每个类与公开方法均加中文 docstring。
    - _需求: 1.3, 5.4；覆盖 Property 1, Property 2_
  - [x] 1.2 创建基础设施序列化 delegating adapter 模块
    - 在 `epsilon-boot/src/infrastructure/run/run_serialization_adapters.py` 中新增模块，含模块级中文 docstring，说明各 adapter 逐一委托既有 serializer 自由函数、输出逐字节等价、序列化实现仍留基础设施（ADR-0008）。
    - 定义无状态类 `WorkflowSerializerAdapter`、`GuardrailSerializerAdapter`、`SegmentSerializerAdapter`，分别实现 1.1 的三个 Protocol；每个方法直接委托对应自由函数并原样返回：`infrastructure.run.workflow_serialization` 的 `workflow_run_state_to_dict` / `workflow_capability_decision_to_dict` / `child_run_orchestration_state_to_dict`；`infrastructure.agent.guardrail_serialization` 的 `guardrail_summary_to_dict` / `guardrail_observation_to_event_payload`；`infrastructure.agent.segment_serialization` 的 `segment_run_metadata_to_http_dict`。
    - serializer 自由函数模块保持不动；adapter 不做任何逻辑转换，仅调用同一函数；每个类与公开方法均加中文 docstring。
    - _需求: 1.4；覆盖 Property 2, Property 4_
  - [x]* 1.3 补充 serializer adapter 等价性单测（可选）
    - 在 `epsilon-boot/test/infrastructure/run/test_run_serialization_adapters_property.py` 中新增测试，对构造的 `SegmentRunMetadata` / `WorkflowRunState` / `WorkflowCapabilityDecision` / `ChildRunOrchestrationState` / `GuardrailSummary` / `GuardrailObservation` 断言 `adapter.method(v) == <对应自由函数>(v)`；若既有 Hypothesis 夹具可复用则追加，否则参数化 example 覆盖各值对象。
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_run_serialization_adapters_property.py`。
    - **验证: 需求 1.1；覆盖 Property 4**

- [x] 2. Slice A 消除 run_execution_coordinator 的 segment_serialization 例外【需 spec-evaluator 审查】
  - [x] 2.1 改造 RunExecutionCoordinator 注入 SegmentSerializerPort
    - 在 `epsilon-boot/src/application/run/run_execution_coordinator.py` 中 `from application.run.serialization_ports import SegmentSerializerPort`；`RunExecutionCoordinator.__init__` 新增 required keyword 参数 `segment_serializer: SegmentSerializerPort`（无 `None` 回退），保存为 `self._segment_serializer`。
    - 把模块级 `_chat_outcome` / `_task_outcome` / `_segment_metadata`（约 L528-539）收敛为实例方法；`_segment_metadata` 改调 `self._segment_serializer.segment_run_metadata_to_http_dict(metadata)`；删除函数体内 `from infrastructure.agent.segment_serialization import ...` 局部 import；`execute(...)` 及其它对外方法签名不变。
    - _需求: 1.1, 1.5, 1.6, 5.3；覆盖 Property 1, Property 4_
  - [x] 2.2 组合根注入 SegmentSerializerAdapter 并删 allowlist 条目
    - 在 `epsilon-boot/src/application/container_config.py` 中 `from infrastructure.run.run_serialization_adapters import SegmentSerializerAdapter`（组合根受控例外）；在 `_create_run_execution_coordinator`（约 L967）构造 `SegmentSerializerAdapter()` 并注入 `segment_serializer=`。
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 中删除 `"src/application/run/run_execution_coordinator.py"` 条目。
    - 在受影响的既有测试构造点（如 `test/application/run/*` 中直接实例化 `RunExecutionCoordinator` 的 fixture）补注入 `segment_serializer=SegmentSerializerAdapter()` 或等价 fake，断言不变。
    - _需求: 1.5, 1.6, 1.8, 4.3；覆盖 Property 1, Property 3_
  - [x] 2.3 验证 run_execution_coordinator 例外消除
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/run test/infrastructure/run`。
    - 断言 `test_application_layer_imports_infrastructure_only_through_declared_exceptions` 与 `test_application_infrastructure_exception_scope_is_exact` 通过（实际命中 == 剩余 allowlist），Run 应用/基础设施回归断言不变。
    - **验证: 需求 1.5, 1.6, 1.8, 4.1, 4.3；覆盖 Property 1, Property 3, Property 4_

- [x] 3. Slice A 消除 run_guardrail_recorder 的 guardrail_serialization 例外【需 spec-evaluator 审查】
  - [x] 3.1 改造 RunGuardrailRecorder 注入 GuardrailSerializerPort
    - 在 `epsilon-boot/src/application/run/run_guardrail_recorder.py` 中 `from application.run.serialization_ports import GuardrailSerializerPort`；`RunGuardrailRecorder.__init__` 新增 required keyword 参数 `guardrail_serializer: GuardrailSerializerPort`，保存为 `self._guardrail_serializer`。
    - `record_observation`（约 L41-78）改调 `self._guardrail_serializer.guardrail_observation_to_event_payload(...)` 与 `self._guardrail_serializer.guardrail_summary_to_dict(...)`；删除函数体内 `from infrastructure.agent.guardrail_serialization import ...` 局部 import；`RunGuardrailRecorder(RunGuardrailRecorderPort)` 契约方法签名不变。
    - _需求: 1.1, 1.5, 1.6, 5.3；覆盖 Property 1, Property 4_
  - [x] 3.2 组合根注入 GuardrailSerializerAdapter 并删 allowlist 条目
    - 在 `epsilon-boot/src/application/container_config.py` 中 `from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter`；在 `_create_run_guardrail_recorder`（约 L1077）构造并注入 `guardrail_serializer=GuardrailSerializerAdapter()`。
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 中删除 `"src/application/run/run_guardrail_recorder.py"` 条目。
    - 在既有 `RunGuardrailRecorder` 构造点与测试 fixture 补注入 `guardrail_serializer=`，断言不变。
    - _需求: 1.5, 1.6, 1.8, 4.3；覆盖 Property 1, Property 3_
  - [x] 3.3 验证 run_guardrail_recorder 例外消除
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/run test/infrastructure/run`。
    - 断言例外精确范围测试通过、Run guardrail 记账回归断言不变。
    - **验证: 需求 1.5, 1.6, 1.8, 4.1, 4.3；覆盖 Property 1, Property 3, Property 4_

- [x] 4. Slice A 消除 run_checkpoint_recovery_service 的 guardrail_serialization 例外【需 spec-evaluator 审查】
  - [x] 4.1 改造 RunRecoveryService 注入 GuardrailSerializerPort
    - 在 `epsilon-boot/src/application/run/run_checkpoint_recovery_service.py` 中 `from application.run.serialization_ports import GuardrailSerializerPort`；`RunRecoveryService.__init__` 新增 required keyword 参数 `guardrail_serializer: GuardrailSerializerPort`，保存为 `self._guardrail_serializer`。
    - 把模块级 `_recovery_guardrail_summary`（约 L156-198）改为实例方法或向其显式传入 `self._guardrail_serializer`，改调注入实现 `guardrail_summary_to_dict`；删除函数体内 `from infrastructure.agent.guardrail_serialization import ...` 局部 import；`sweep_expired_leases(...)` 签名不变，`mark_guardrail_summary_stale`（domain）保持不动。
    - _需求: 1.1, 1.5, 1.6, 5.3；覆盖 Property 1, Property 4_
  - [x] 4.2 组合根注入 GuardrailSerializerAdapter 并删 allowlist 条目
    - 在 `epsilon-boot/src/application/container_config.py` 的 `_create_run_recovery_service`（约 L992）复用/构造 `GuardrailSerializerAdapter` 实例并注入 `guardrail_serializer=`（可与 3.2 的 adapter 共用模块级单例）。
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 中删除 `"src/application/run/run_checkpoint_recovery_service.py"` 条目。
    - 在既有 `RunRecoveryService` 构造点与测试 fixture 补注入 `guardrail_serializer=`，断言不变。
    - _需求: 1.5, 1.6, 1.8, 4.3；覆盖 Property 1, Property 3_
  - [x] 4.3 验证 run_checkpoint_recovery_service 例外消除
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/run test/infrastructure/run`。
    - 断言例外精确范围测试通过、recovery sweep 回归断言不变。
    - **验证: 需求 1.5, 1.6, 1.8, 4.1, 4.3；覆盖 Property 1, Property 3, Property 4_

- [x] 5. Slice A 消除 run_application_service 的 workflow_serialization 例外【需 spec-evaluator 审查】
  - [x] 5.1 改造 RunApplicationService 注入 WorkflowSerializerPort
    - 在 `epsilon-boot/src/application/run/run_application_service.py` 中 `from application.run.serialization_ports import WorkflowSerializerPort`；`RunApplicationService.__init__` 新增 required keyword 参数 `workflow_serializer: WorkflowSerializerPort`（即使部分请求不走 `workflow_selector` 分支也不设 `None` 回退），保存为 `self._workflow_serializer`。
    - `_with_workflow_selection`（约 L313-341）改调 `self._workflow_serializer.workflow_run_state_to_dict(...)`；删除函数体内 `from infrastructure.run.workflow_serialization import ...` 局部 import；对外方法签名不变。
    - _需求: 1.1, 1.5, 1.6, 5.3；覆盖 Property 1, Property 4_
  - [x] 5.2 组合根注入 WorkflowSerializerAdapter 并删 allowlist 条目
    - 在 `epsilon-boot/src/application/container_config.py` 中 `from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter`；在 `_create_run_application_service`（约 L1057）构造并注入 `workflow_serializer=WorkflowSerializerAdapter()`。
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 中删除 `"src/application/run/run_application_service.py"` 条目。
    - 在既有 `RunApplicationService` 构造点与测试 fixture（含集成测试装配）补注入 `workflow_serializer=`，断言不变。
    - _需求: 1.5, 1.6, 1.8, 4.3；覆盖 Property 1, Property 3_
  - [x] 5.3 验证 run_application_service 例外消除
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/run test/infrastructure/run`。
    - 断言例外精确范围测试通过、workflow selection 回归断言不变。
    - **验证: 需求 1.5, 1.6, 1.8, 4.1, 4.3；覆盖 Property 1, Property 3, Property 4_

- [x] 6. Slice A 消除 workflow_orchestrator 的 workflow_serialization 例外并收敛 allowlist【需 spec-evaluator 审查】
  - [x] 6.1 改造 WorkflowRunOrchestrator 注入 WorkflowSerializerPort
    - 在 `epsilon-boot/src/application/run/workflow_orchestrator.py` 中 `from application.run.serialization_ports import WorkflowSerializerPort`；`WorkflowRunOrchestrator.__init__` 新增 required keyword 参数 `workflow_serializer: WorkflowSerializerPort`，保存为 `self._workflow_serializer`。
    - `_capability_rejection_outcome`（约 L175）、`_child_run_reconciliation_outcome`（约 L244）、`_child_run_waiting_outcome`（约 L342）三处改调 `self._workflow_serializer.workflow_capability_decision_to_dict(...)` / `self._workflow_serializer.child_run_orchestration_state_to_dict(...)`；删除三处 `from infrastructure.run.workflow_serialization import ...` 局部 import；phase routing / 字段合并语义不变。
    - _需求: 1.1, 1.5, 1.6, 5.3；覆盖 Property 1, Property 4_
  - [x] 6.2 组合根注入 WorkflowSerializerAdapter 并删最后一条 allowlist 条目
    - 在 `epsilon-boot/src/application/container_config.py` 的 `_create_workflow_run_orchestrator`（约 L911）复用/构造 `WorkflowSerializerAdapter` 实例并注入 `workflow_serializer=`（可与 5.2 共用模块级单例）。
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 中删除 `"src/application/run/workflow_orchestrator.py"` 条目，使该字典为空 `{}`；确认 `test_application_infrastructure_exception_scope_is_exact` 对空 allowlist 仍精确通过。
    - 在既有 `WorkflowRunOrchestrator` 构造点与测试 fixture 补注入 `workflow_serializer=`，断言不变。
    - _需求: 1.5, 1.6, 1.7, 1.8, 4.3；覆盖 Property 1, Property 3_
  - [x] 6.3 验证 workflow_orchestrator 例外消除且 allowlist 收敛为空
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/run test/infrastructure/run`。
    - 断言 allowlist 为空、`Application_To_Infrastructure_Import_Rule` 无 serializer 例外通过、例外精确范围测试对空集合通过。
    - **验证: 需求 1.5, 1.6, 1.7, 1.8, 1.9, 4.1, 4.3；覆盖 Property 1, Property 3, Property 4_

- [x] 7. 检查点 — Slice A（serializer 清理）完成【评审可选】
  - 在 `epsilon-boot/` 下运行聚焦回归：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/run test/infrastructure/run`。
  - 在 `epsilon-boot/` 下运行全量：`PYTHONPATH=src uv run --frozen pytest`，结果不劣于基线（3072 passed、2 skipped、1 warning）。
  - 在 `epsilon-boot/` 下运行类型与 lint：`uv run ruff check src && uv run pyright src`，改动代码通过。
  - 任一命令未能运行，记录具体原因（需求 4.5）。
  - **验证: 需求 1.1, 1.2, 1.4, 1.5, 1.6, 1.7, 1.8, 4.1, 4.2, 4.3, 4.4, 4.6, 5.1, 5.3, 5.4；覆盖 Property 1, Property 2, Property 3, Property 4, Property 8, Property 9_

- [x] 8. Slice B ChatServiceAdapter prompt 加载去重【需 spec-evaluator 审查】
  - [x] 8.1 创建 chat-default prompt 单一来源 helper
    - 在 `epsilon-boot/src/infrastructure/chat/chat_default_prompt.py` 中新增模块，含模块级中文 docstring。
    - 定义 `@dataclass(frozen=True) class ChatDefaultSystemPrompt`，字段 `system_prompt: str`、`prompt_id: str`。
    - 实现 `def resolve_chat_default_system_prompt(prompt_registry: PromptRegistryPort) -> ChatDefaultSystemPrompt`：`loaded = prompt_registry.get("chat-default")`，返回 `ChatDefaultSystemPrompt(system_prompt=append_workspace_path_guidance(loaded.content), prompt_id=loaded.prompt_id)`；与现有三行加载逻辑逐字节等价，`prompt_id` 不受 workspace guidance 影响，不新增异常捕获。
    - import 限于 `domain.prompt.ports.PromptRegistryPort` 与 `infrastructure.prompt.workspace_guidance.append_workspace_path_guidance`；类与公开函数加中文 docstring；不引入 domain 运行时关注点。
    - _需求: 2.1, 2.3, 2.4, 2.5, 2.6；覆盖 Property 5_
  - [x] 8.2 ChatServiceAdapter 与组合根改调单一 helper
    - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 的 `__init__`（约 L193-196）改为调用 `resolve_chat_default_system_prompt(prompt_registry)`，用返回值设置 `self._system_prompt` 与 `self._prompt_id`；移除构造期对 `append_workspace_path_guidance` 的直接局部 import；`prompt_registry` 构造参数与 `ChatServicePort` 方法签名保持不变（最小改动）。
    - 在 `epsilon-boot/src/application/container_config.py` 的 `_create_chat_service`（约 L1811-1813）改调同一 helper 得到 `system_prompt` / `prompt_id`，继续传给 `ChatSessionContextWorkflow` 与 `_make_agent_config`；移除组合根内 `append_workspace_path_guidance` 局部 import。
    - _需求: 2.1, 2.3, 2.4, 2.5, 2.6；覆盖 Property 5_
  - [x]* 8.3 补充 chat_default_prompt helper 单测（可选）
    - 在 `epsilon-boot/test/infrastructure/chat/test_chat_default_prompt_unit.py` 中新增测试，用 fake `PromptRegistryPort` 断言 `system_prompt == append_workspace_path_guidance(content)`、`prompt_id == loaded.prompt_id` 且不受 guidance 影响、`get("chat-default")` 恰调用一次。
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/chat/test_chat_default_prompt_unit.py`。
    - **验证: 需求 2.3, 2.4, 2.5；覆盖 Property 5_
  - [x] 8.4 验证 ChatServiceAdapter prompt 去重行为等价
    - 若既有断言未覆盖，更新 `epsilon-boot/test/infrastructure/chat/test_chat_service_adapter_refactor_property.py`、`test_chat_service_adapter_boundary_characterization.py` 守护 `_system_prompt` / `_prompt_id` 与组合根传给 `ChatSessionContextWorkflow` / `AgentConfig` 的值逐字节等价。
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/chat`。
    - **验证: 需求 2.1, 2.3, 2.4, 2.5, 2.6, 4.1；覆盖 Property 5_

- [x] 9. 检查点 — Slice B（prompt 去重）完成【评审可选】
  - 在 `epsilon-boot/` 下运行聚焦回归：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/chat`。
  - 在 `epsilon-boot/` 下运行全量：`PYTHONPATH=src uv run --frozen pytest`，结果不劣于基线。
  - 在 `epsilon-boot/` 下运行类型与 lint：`uv run ruff check src && uv run pyright src`，改动代码通过。
  - 任一命令未能运行，记录具体原因（需求 4.5）。
  - **验证: 需求 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 4.1, 4.2, 4.4, 4.6；覆盖 Property 5, Property 8_

- [x] 10. Slice C 抽出 guardrail_runtime_accumulator【需 spec-evaluator 审查】
  - [x] 10.1 迁移运行时统计累加器与 ContextVar 到新模块
    - 在 `epsilon-boot/src/infrastructure/agent/guardrail_runtime_accumulator.py` 中新增模块，含模块级中文 docstring。
    - 逐字平移 `react_agent_adapter.py` 的 `_GuardrailRuntimeAccumulator`（约 L177-359，导出为 `GuardrailRuntimeAccumulator`）、`_safe_int` / `_safe_float` / `_safe_optional_float` / `_safe_optional_str`（约 L375-406）、ContextVar `_CURRENT_GUARDRAIL_RUNTIME`（约 L362，导出为 `CURRENT_GUARDRAIL_RUNTIME`）、`_CURRENT_TOOL_ABUSE_DETECTOR`（约 L368，导出为 `CURRENT_TOOL_ABUSE_DETECTOR`）；字段、`from_summary` / `model_completed` / `tool_before` / `tool_after` / `snapshot` 等方法逻辑不变。
    - 若既有测试直接 import 了内部私有符号，门面用 `from ... import GuardrailRuntimeAccumulator as _GuardrailRuntimeAccumulator`（及 ContextVar 别名）保留原名以零改测试；门面 `_guardrail_runtime_accumulator()` / `_tool_abuse_detector()` 访问器与 `prepare_runtime` 内累加器重置 / preserve 逻辑保留在门面但引用本模块符号。
    - 模块 import 限于 `domain.agent.guardrails.GuardrailRuntimeStats`、`infrastructure.agent.tool_abuse_detector.ToolAbuseDetector` 与标准库；模块/类/公开方法加中文 docstring。
    - _需求: 3.1, 3.3, 3.5, 3.7, 3.8；覆盖 Property 6, Property 7_
  - [x] 10.2 验证 accumulator 抽出后行为等价
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。
    - 复用 `test_react_agent_adapter_property.py`、`test_react_agent_permission_properties.py`、guardrail / otel_span 系列作为行为等价网，断言运行时统计与 ContextVar 隔离语义不变。
    - **验证: 需求 3.1, 3.3, 3.5, 3.7, 3.8, 4.1；覆盖 Property 6, Property 7_

- [x] 11. Slice C 抽出 react_trace_recorder【需 spec-evaluator 审查】
  - [x] 11.1 迁移 trace / OTel 记账到 ReActTraceRecorder
    - 在 `epsilon-boot/src/infrastructure/agent/react_trace_recorder.py` 中新增模块与 `class ReActTraceRecorder`，含中文 docstring；`__init__(self, trace_store: <原类型> | None)` 持有可选 `trace_store`，为 None 或 `session_id` 为空时记账静默跳过（保持现状）。
    - 逐字平移 `_record_trace`（L583）、`_truncate`（L593）、`_build_model_call_trace`（L599）、`_build_model_call_trace_from_response`（L614）、`_build_approval_trace`（L648）、`_record_error_trace`（L661）、`_record_tool_call_trace`（L693）、`_truncate_metadata`（L750），以及工具滥用记账 `_record_tool_call_for_abuse_detection`（L793）、`_emit_tool_abuse_detected`（L804）、`_record_tool_abuse_blocked_result`（L828）；方法签名与异常/`logger.warning(exc_info=True)` 吞异常语义不变。
    - 门面持有 `self._trace_recorder = ReActTraceRecorder(trace_store)`，原调用点改为委托；模块/类/公开方法加中文 docstring。
    - _需求: 3.1, 3.3, 3.6, 3.7, 3.8；覆盖 Property 6, Property 7_
  - [x] 11.2 验证 trace recorder 抽出后行为等价
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。
    - 复用 trace / otel_span / tool abuse 相关既有测试守护 trace 结构、OTel span、工具滥用事件与错误 trace 语义不变。
    - **验证: 需求 3.1, 3.3, 3.6, 3.7, 3.8, 4.1；覆盖 Property 6, Property 7_

- [x] 12. Slice C 抽出 react_concurrent_tool_executor（ADR-0013 留 infra）【需 spec-evaluator 审查】
  - [x] 12.1 逐字平移工具并发骨架到 ConcurrentToolExecutor
    - 在 `epsilon-boot/src/infrastructure/agent/react_concurrent_tool_executor.py` 中新增模块，含中文 docstring；定义 infra 内部窄回调协议 `class ToolExecutionRuntime(Protocol)`（方法 `execute_tool_call`、`record_tool_call_trace`、`record_tool_after_observation`，签名与门面对应方法一致），门面实现该协议（或直接以 `self` 作为回调传入，二者皆不改 ADR-0013 归属结论）。
    - 定义 `class ConcurrentToolExecutor`，`__init__(self, runtime: ToolExecutionRuntime)`；逐字平移 `_dispatch_concurrent_tool_calls`（L2137→`dispatch`）、`_stream_concurrent_tool_progress`（L2205→`stream_progress`）、`_events_concurrent_tool_calls`（L2273→`events`），以及仅服务并发进度的 `_tool_progress_chunk`（L2642）、`_heartbeat_chunk`（L2633）（若仅被并发骨架使用则一并迁移，否则留门面）。
    - `asyncio.gather`、`set_parent_context` / `reset_parent_context`（ContextVar，`finally` 还原）、事件配对 yield、fast-path 单工具直 await 时序逐字不变；不重开 ADR-0013、不上提领域层；门面持有 `ConcurrentToolExecutor` 并在原调用点委托。
    - 模块 import 不引入新的跨层依赖（仅 infrastructure + domain 值对象 + 标准库）；模块/类/公开方法加中文 docstring。
    - _需求: 3.1, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 5.2；覆盖 Property 6, Property 7, Property 9_
  - [x] 12.2 验证并发骨架抽出后行为等价
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。
    - 复用 `test_react_agent_*` 中 concurrent_tool_calls / streaming / events 系列守护同轮多工具并发时序、流式 chunk / 事件类型与顺序、心跳与进度不变。
    - **验证: 需求 3.1, 3.3, 3.4, 3.6, 3.7, 3.8, 3.9, 4.1, 5.2；覆盖 Property 6, Property 7, Property 9_

- [x] 13. Slice C 抽出 react_approval_checkpoint【需 spec-evaluator 审查】
  - [x] 13.1 迁移审批 / checkpoint 缝合到 ApprovalCheckpointStitcher
    - 在 `epsilon-boot/src/infrastructure/agent/react_approval_checkpoint.py` 中新增模块与 `class ApprovalCheckpointStitcher`，`__init__(self, approval_store: ApprovalStateStorePort)`，含中文 docstring。
    - 平移 `_collect_pending_actions`（L859）、`_save_interrupt`（L888）、`_first_workflow_capability_denial`（L934）、`_save_workflow_capability_interrupt`（L950）、`checkpoint_model_completed` / `checkpoint_approval_interrupt` 的 sink 调用体（L2048 / L2071）、`_apply_approval_decisions`（L2412）、`_record_rejected_tool_call`（L2493）、`_latest_tool_calls_by_id`（L2566）；方法签名一致平移，依赖的 domain 纯判定（`collect_pending_actions` 等）不变。
    - 门面 `AgentLoopEffects.save_interrupt` / `checkpoint_model_completed` / `checkpoint_approval_interrupt` 保留为薄方法委托 stitcher；`resolve_approval_policies` / `record_terminated` / `perform_model_round` 涉及门面自身状态与 OTel span，保留门面并按需委托 trace recorder / accumulator，避免过度拆分。
    - `sink.model_completed` / `sink.approval_interrupt`、`approval_store.save`、run guardrail 观测写入的调用点与顺序不变；不新增补偿 / 幂等；模块/类/公开方法加中文 docstring。
    - _需求: 3.1, 3.3, 3.5, 3.6, 3.7, 3.8；覆盖 Property 6, Property 7_
  - [x] 13.2 确认门面瘦身后契约不变
    - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中确认 `class ReActAgentAdapter(AgentPort)` 仍实现 `AgentLoopEffects` 全部方法（`prepare_runtime` / `perform_model_round` / `record_assistant_with_tool_calls` / `resolve_approval_policies` / `save_interrupt` / `prepare_tool_calls_for_execution` / `checkpoint_model_completed` / `checkpoint_approval_interrupt` / `record_terminated`）与四入口（`run` / `run_streaming` / `run_events` / `resume`），签名与 `effects=self` 委托方式不变，方法体委托 accumulator / trace recorder / concurrent executor / stitcher。
    - _需求: 3.1, 3.6, 3.7；覆盖 Property 6_
  - [x] 13.3 验证审批 / checkpoint 缝合抽出后行为等价
    - 在 `epsilon-boot/` 下运行：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。
    - 复用 hitl / checkpoint_recovery / handoff / characterization 系列守护审批筛选、审批中断保存、checkpoint 写入、pending action 收集、终止 reason 与错误语义不变。
    - **验证: 需求 3.1, 3.3, 3.5, 3.6, 3.7, 3.8, 4.1；覆盖 Property 6, Property 7_

- [x] 14. 检查点 — Slice C（SRP 拆分）完成【评审可选】
  - 在 `epsilon-boot/` 下运行聚焦回归：`PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent`。
  - 在 `epsilon-boot/` 下运行全量：`PYTHONPATH=src uv run --frozen pytest`，结果不劣于基线。
  - 在 `epsilon-boot/` 下运行类型与 lint：`uv run ruff check src && uv run pyright src`，改动代码通过。
  - 在 `epsilon-boot/` 下运行静态边界：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py`，确认拆分产出模块未引入新的跨层依赖、并发骨架 / ContextVar / OTel 仍在 `src/infrastructure/`。
  - 任一命令未能运行，记录具体原因（需求 4.5）。
  - **验证: 需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 4.1, 4.2, 4.3, 4.4, 4.6, 5.2；覆盖 Property 6, Property 7, Property 8, Property 9_

- [x] 15. 文档同步与 ADR 判断【评审可选】
  - [x] 15.1 同步受影响主题文档
    - 在 `docs/architecture.md` 中更新：`application/run/*` 经序列化 Protocol（`application/run/serialization_ports.py`）+ 组合根注入消费 serializer、app→infra serializer 受控例外收敛为空（Slice A）；react adapter 门面 + 协作模块（accumulator / trace recorder / concurrent executor / approval-checkpoint stitcher）的运行时布局（Slice C）。
    - 在 `docs/agent.md` 中更新：ReAct Agent Loop 的模块切分与门面职责（Slice C）；chat-default prompt 单一来源说明（Slice B）。
    - 在 `docs/di-container.md` 中更新：组合根对 serializer adapter 的装配与注入点（Slice A）；`_create_chat_service` 的 prompt helper 消费（Slice B）。
    - 在 `docs/domain-model.md`（如涉及）中说明序列化能力抽象位于应用层、实现留 infrastructure 的边界（与 ADR-0008 一致）。
    - _需求: 5.6, 5.7_
  - [x] 15.2 记录 ADR 判断结论
    - 对照 `docs/steering/adr.md`、`docs/adr/README.md` 与已 Accepted 的 ADR-0008 / 0013 / 0016，在交付说明中记录三项 follow-up 均「不新增 ADR、不 supersede 任何已 Accepted ADR」的理由（序列化 Protocol 为 feature-local 依赖反转窄抽象；prompt 去重为纯内部重构；SRP 拆分为单层内模块重排，`ToolExecutionRuntime` 为 infra 内部窄协议）。
    - 若实现阶段出现改 Port 归属或依赖方向的偏离，回到 `design.md` 并按需求 5.5 记录 ADR 判断与建议；不修改现有 ADR 文件、不重开 ADR-0013。
    - _需求: 5.1, 5.2, 5.4, 5.5, 5.7；覆盖 Property 9_

- [x] 16. 检查点 — 最终验证【评审可选】
  - 在 `epsilon-boot/` 下运行静态边界：`PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py`，确认 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 为空且例外精确范围测试通过。
  - 在 `epsilon-boot/` 下运行聚焦回归：`PYTHONPATH=src uv run --frozen pytest test/application/run test/infrastructure/run test/infrastructure/chat test/infrastructure/agent`。
  - 在 `epsilon-boot/` 下运行全量：`PYTHONPATH=src uv run --frozen pytest`，结果不劣于基线（3072 passed、2 skipped、1 warning）。
  - 在 `epsilon-boot/` 下运行类型与 lint：`uv run ruff check src && uv run pyright src`。
  - 任一命令未运行或失败，记录具体原因与失败测试，不勾选最终检查点（需求 4.5）。
  - **验证: 需求 1.7, 1.8, 2.5, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.6, 5.7；覆盖 Property 1-9_

## 备注

- 本计划只定义实现与验证任务，不执行实现；需用户确认后才进入 `spec-generator` 阶段。
- 三项均为纯行为等价重构：不新增对外功能、不改 API 契约 / 事件类型 / 流式协议 / 错误语义；serializer 自由函数模块与 `workspace_guidance` 保持不动（ADR-0008）。
- Slice A 每消除一项例外必须在同一切片内同步删除 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 对应条目，并保持 `test_application_infrastructure_exception_scope_is_exact` 绿；serializer 形参一律 required keyword，不设 `None` 回退，缺失注入由 pyright 在构造点捕获。
- 不新增 ADR、不 supersede 已 Accepted ADR、不重开 ADR-0013；并发骨架、`asyncio`、ContextVar、OTel、Redis/file persistence、模型 SDK/HTTP 适配一律留基础设施层，不上提领域层。
- 不引入数据库 DDL、索引、Redis key schema、文件布局迁移、配置键变更或 backfill；不修 handoff model discrepancy。
- 所有任务保持最小改动，不做全仓重排、无关重命名或批量格式化；遇范围外问题只登记为后续事项。
- 标记 `*` 的子任务为可选（补充聚焦单测），不阻塞切片交付；每切片检查点须运行 `Verification_Command_Set`（聚焦回归 + 全量 + ruff + pyright），无法运行须记录原因（需求 4.5）。
