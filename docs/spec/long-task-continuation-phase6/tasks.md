# 实现计划：长任务工作流化与多 Agent 协作阶段六

## 概述

本计划基于 `requirement.md` 与 `design.md`，按项目 DDD + 六边形架构约束拆分阶段六实现。实施顺序遵循领域模型与 Port 先行、基础设施 Adapter 与配置随后、应用层 Run 编排与 worker 持久化接入、协作治理、API/CLI/Web 透传、回归验证收尾。阶段六不新增数据库表、DDL、外部 durable workflow runtime 或测试依赖。

## Tasks

- [x] 1.1 创建工作流领域模型
  - 在 `epsilon-boot/src/domain/run/workflow.py` 中创建模块
  - 实现 `StandardWorkflowName(StrEnum)`、`WorkflowPhase(StrEnum)`、`CollaborationAction(StrEnum)`、`WorkflowApplicableCondition`、`AgentRoleCapability`、`CollaborationLimit`、`WorkflowPhaseDefinition`、`WorkflowDefinition`、`WorkflowPhaseRecord`、`CollaborationStepTraceLink`、`ParentChildRunLink`、`CollaborationSummary`、`WorkflowRunState`
  - 为 `WorkflowDefinition.validate()` 增加名称非空小写 snake_case、必含 `plan/execute/evaluate/finalize`、阶段 role 引用存在、协作限制非负/正数、枚举和时间 JSON-safe 的校验
  - 为 `WorkflowRunState`、`CollaborationSummary` 提供 `to_dict()` 或等价 JSON-safe 序列化方法，输出 enum `.value`、datetime ISO-8601、tuple 为 list
  - _需求: 1.1, 1.2, 1.5, 1.6, 3.1, 3.2, 5.1, 5.4, 6.1, 6.2, 6.3_

- [x] 1.2 编写工作流领域模型测试
  - 在 `epsilon-boot/test/domain/run/test_workflow_value_objects_unit.py` 中创建测试
  - 覆盖四类标准 workflow 名称、必需 phase 校验、重复/非法名称、未知 role、非法 limit、`WorkflowRunState` 与 `CollaborationSummary` JSON-safe 序列化
  - 增加领域层静态导入断言，确保 `domain/run/workflow.py` 不导入 `application`、`infrastructure`、FastAPI、Redis 或外部 workflow runtime
  - **验证: 需求 1.1, 1.2, 1.3, 1.5, 1.6, 3.1, 3.2, 5.4, 6.1, 9.1, 9.9**

- [x] 1.3 扩展 Run 领域值对象与异常
  - 在 `epsilon-boot/src/domain/run/value_objects.py` 中给 `RunEventType` 增加 `WORKFLOW_SELECTED`、`WORKFLOW_SELECTION_SKIPPED`、`WORKFLOW_PHASE_STARTED`、`WORKFLOW_PHASE_COMPLETED`、`WORKFLOW_PHASE_FAILED`、`COLLABORATION_STEP_RECORDED`、`COLLABORATION_LIMIT_HIT`
  - 给 `RunCreateRequest` 增加 `workflow_name: str | None = None`、`workflow_run_state: dict[str, Any] | None = None`、`collaboration_summary: dict[str, Any] | None = None`
  - 给 `RunSnapshot` 增加同名字段，默认 `None` 以兼容旧快照
  - 在 `epsilon-boot/src/domain/run/exceptions.py` 中新增 `RunUnknownWorkflowError(code=61017)`、`RunWorkflowDefinitionError(code=61018)`、`RunCollaborationLimitExceededError(code=61019)`，错误消息使用 `_safe_reason()` 或安全摘要
  - _需求: 2.3, 2.6, 2.7, 3.3, 3.4, 3.5, 3.6, 5.5, 6.5, 7.1, 7.2_

- [x] 1.4 编写 Run 值对象与异常测试
  - 在 `epsilon-boot/test/domain/run/test_run_workflow_value_objects_unit.py` 中创建测试
  - 覆盖新增事件枚举值、`RunCreateRequest`/`RunSnapshot` 新字段默认兼容、`RunPayload.stable_hash()` 不包含 workflow 元数据、三个新增异常不泄露完整 payload 或工具参数
  - **验证: 需求 2.5, 2.6, 2.7, 3.6, 7.1, 7.2, 9.1**

- [x] 1.5 扩展 Run Port 协议
  - 在 `epsilon-boot/src/domain/run/ports.py` 中新增 `WorkflowSelection` dataclass、`WorkflowRegistryPort`、`WorkflowSelectorPort`
  - 为 `RunStorePort.mark_succeeded()`、`mark_failed()`、`mark_paused()`、`mark_awaiting_approval()`、`mark_cancelled()`、`resolve_approval_resume()`、`enqueue_recovery()` 增加 keyword-only 可选参数 `workflow_run_state: dict[str, Any] | None = None`、`collaboration_summary: dict[str, Any] | None = None`
  - 保持所有新增参数有默认值，旧调用方无需修改即可通过类型和运行时兼容
  - _需求: 1.3, 2.1, 3.6, 4.1, 4.4, 6.4, 7.1_

- [x] 1.6 编写 Run Port 签名静态测试
  - 在 `epsilon-boot/test/domain/run/test_run_workflow_ports_unit.py` 中创建测试
  - 使用 `typing.get_type_hints`/`inspect.signature` 覆盖 `WorkflowRegistryPort`、`WorkflowSelectorPort` 方法签名，以及 `RunStorePort` 新增可选 keyword-only 参数默认值
  - **验证: 需求 1.3, 2.1, 3.6, 4.4, 7.1, 9.1**

- [x] 1.7 创建工作流协作上下文
  - 在 `epsilon-boot/src/domain/run/workflow_context.py` 中创建 `WorkflowCollaborationContext` dataclass
  - 实现 `set_workflow_collaboration_context(value) -> Token`、`reset_workflow_collaboration_context(token) -> None`、`get_workflow_collaboration_context() -> WorkflowCollaborationContext | None`
  - 使用 `contextvars.ContextVar` 隔离并发 Run 执行窗口，不依赖 application 或 infrastructure
  - _需求: 5.1, 5.4, 5.5, 5.8, 6.3, 6.5_

- [x] 1.8 编写工作流协作上下文测试
  - 在 `epsilon-boot/test/domain/run/test_workflow_context_unit.py` 中创建测试
  - 覆盖 set/get/reset、嵌套 token 恢复、未设置时返回 `None`、ContextVar 并发隔离
  - **验证: 需求 5.4, 5.5, 5.8, 6.3, 9.4**

- [x] 2.1 实现工作流配置模型
  - 在 `epsilon-boot/src/infrastructure/run/workflow_config.py` 中创建 `RunWorkflowConfig(PropertiesBaseSettings)`
  - 使用 `SettingsConfigDict(env_prefix="RUN_WORKFLOW_")` 定义 `enabled`、`default_workflow`、`enabled_workflows`、`max_recursion_depth`、`max_parallel_delegations`、`max_handoff_count`、`max_revise_per_phase`、`max_child_runs`、`recent_collaboration_summary_limit`
  - 实现 `_validate_run_workflow_config()`，对负数、空 enabled workflow 名称、非法 default workflow 和 recent limit 非正值抛 `ConfigurationError`
  - 实现 `to_collaboration_limit() -> CollaborationLimit`
  - _需求: 1.4, 1.7, 5.4, 8.2, 8.5_

- [x] 2.2 增加工作流默认配置
  - 在 `epsilon-boot/config.properties` 的 Run Runtime 配置区追加 `RUN_WORKFLOW_ENABLED=true`、`RUN_WORKFLOW_DEFAULT_WORKFLOW=`、`RUN_WORKFLOW_ENABLED_WORKFLOWS=research,code_change,report,batch_processing`、`RUN_WORKFLOW_MAX_RECURSION_DEPTH=3`、`RUN_WORKFLOW_MAX_PARALLEL_DELEGATIONS=3`、`RUN_WORKFLOW_MAX_HANDOFF_COUNT=1`、`RUN_WORKFLOW_MAX_REVISE_PER_PHASE=1`、`RUN_WORKFLOW_MAX_CHILD_RUNS=0`、`RUN_WORKFLOW_RECENT_COLLABORATION_SUMMARY_LIMIT=5`
  - 不修改 `.env`，环境变量仅作为覆盖来源
  - _需求: 1.7, 5.4, 8.2, 8.5_

- [x] 2. 检查点 — 领域模型与配置默认值
  - 在 `epsilon-boot` 中运行 `PYTHONPATH=src .venv/bin/python -m pytest test/domain/run -q`，并用 `RunWorkflowConfig()` smoke check 验证默认配置可转换为 `CollaborationLimit`。
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 2.3 编写工作流配置测试
  - 在 `epsilon-boot/test/infrastructure/run/test_workflow_config_unit.py` 中创建测试
  - 覆盖默认值、环境变量覆盖、非法数值 fail-fast、`default_workflow` 不在 enabled 列表时 fail-fast、`to_collaboration_limit()` 字段映射
  - **验证: 需求 1.4, 1.7, 5.4, 8.2, 8.5, 9.1**

- [x] 2.4 实现静态工作流注册表 Adapter
  - 在 `epsilon-boot/src/infrastructure/run/static_workflow_registry_adapter.py` 中创建 `StaticWorkflowRegistryAdapter(WorkflowRegistryPort)`
  - 内置 `research`、`code_change`、`report`、`batch_processing` 定义，分别配置设计文档列出的 phase、role、适用条件和默认策略摘要
  - 实现 `list_definitions()`、`get_definition(name)`、`require_definition(name)`，构造期校验名称唯一、必需 phase、role 引用、JSON-safe snake_case 名称
  - `RunWorkflowConfig.enabled=false` 时保留可诊断定义但 selector 不自动选择；`enabled_workflows` 控制定义启用状态
  - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 8.3_

- [x] 2.5 编写静态工作流注册表测试
  - 在 `epsilon-boot/test/infrastructure/run/test_workflow_registry_unit.py` 中创建测试
  - 覆盖四个内置 workflow、禁用 workflow、重复名称、缺少必需 phase、未知 role、非法名称、`require_definition()` 未命中抛 `RunUnknownWorkflowError` 或定义错误
  - **验证: 需求 1.1, 1.2, 1.4, 1.5, 1.6, 8.3, 9.1, 9.2**

- [x] 2.6 实现静态工作流选择器
  - 在 `epsilon-boot/src/infrastructure/run/static_workflow_selector.py` 中创建 `StaticWorkflowSelector(WorkflowSelectorPort)`
  - 实现选择顺序：显式 `RunCreateRequest.workflow_name`、配置 `default_workflow`、`task_classification` 和 payload 关键词、无匹配返回 `WorkflowSelection(workflow=None, explicit=False, reason="no_match")`
  - 显式未知或禁用 workflow 抛 `RunUnknownWorkflowError`，自动无匹配不得阻断 Run 创建
  - 选择器仅读取 request、registry、config，不调用 LLM、HTTP、Redis、文件系统或外部服务
  - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [x] 2.7 编写静态工作流选择器测试
  - 在 `epsilon-boot/test/infrastructure/run/test_static_workflow_selector_unit.py` 中创建测试
  - 覆盖显式合法、显式未知、默认 workflow、`task_classification` 映射、payload 关键词映射、禁用全局 workflow、无匹配兼容路径
  - 增加 monkeypatch 断言选择器不访问外部服务、不调用模型端口
  - **验证: 需求 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 9.2**

- [x] 2.8 检查点 — 领域、配置与选择器基线
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/domain/run test/infrastructure/run/test_workflow_config_unit.py test/infrastructure/run/test_workflow_registry_unit.py test/infrastructure/run/test_static_workflow_selector_unit.py`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 3.1 扩展本地文件 Run Store 快照持久化
  - 在 `epsilon-boot/src/infrastructure/run/local_file_run_store_adapter.py` 中更新 `_create_new_snapshot()`，把 `RunCreateRequest.workflow_name`、`workflow_run_state`、`collaboration_summary` 写入新 `RunSnapshot`
  - 更新 `_worker_transition()`、`mark_succeeded()`、`mark_failed()`、`mark_paused()`、`mark_awaiting_approval()`、`mark_cancelled()`，把新增可选字段持久化到 snapshot，未传入时保留原 snapshot 值
  - 更新 `resolve_approval_resume()` 与 `enqueue_recovery()`，恢复入队时保留或按参数覆盖 workflow/collaboration 字段
  - 保持 `_read_snapshot()` 基于 dataclass fields 的旧字段缺失兼容
  - _需求: 3.6, 4.4, 4.6, 6.4, 7.1_

- [x] 3.2 编写本地文件 Run Store 工作流字段测试
  - 在 `epsilon-boot/test/infrastructure/run/test_local_file_run_store_workflow_unit.py` 中创建测试
  - 覆盖 create 写入 workflow 字段、旧 JSON 缺失字段读取为 `None`、worker mark 方法覆盖/保留字段、approval resume 和 recovery 入队保留当前 phase 状态
  - **验证: 需求 3.6, 4.4, 4.6, 6.4, 7.1, 9.5, 9.6**

- [x] 3.3 扩展 Redis Run Store 快照持久化
  - 在 `epsilon-boot/src/infrastructure/run/redis_run_store_adapter.py` 中更新 create、反序列化、worker mark、approval resume、recovery 入队路径
  - 确保 Redis hash/JSON 中缺失 `workflow_name`、`workflow_run_state`、`collaboration_summary` 时回填 `None`
  - 新增字段不得改变 queued/running claim 条件、lease owner 校验或事件 cursor 递增逻辑
  - _需求: 3.6, 4.4, 4.6, 6.4, 7.1_

- [x] 3.4 编写 Redis Run Store 工作流字段测试
  - 在 `epsilon-boot/test/infrastructure/run/test_redis_run_store_workflow_unit.py` 中创建测试
  - 覆盖 create、旧数据反序列化、mark 方法、approval resume、recovery 入队和 owner 校验不变
  - **验证: 需求 3.6, 4.4, 4.6, 6.4, 7.1, 9.5, 9.6**

- [x] 3.5 扩展 RunApplicationService 工作流选择
  - 在 `epsilon-boot/src/application/run/run_application_service.py` 构造函数增加 `workflow_selector: WorkflowSelectorPort | None = None`
  - 在 `create_run()` 中 `_with_task_classification()` 后调用 `_with_workflow_selection()`，选择成功时初始化 `workflow_name` 和 `workflow_run_state`，未匹配时保持字段为空
  - 创建后按选择结果追加 `WORKFLOW_SELECTED` 或 `WORKFLOW_SELECTION_SKIPPED`，payload 包含安全 `reason`、`workflow_name`、首个 phase，不重复追加到幂等命中 Run
  - 显式未知 workflow 直接向上抛业务异常，不创建 snapshot、不写事件
  - _需求: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 3.6_

- [x] 3. 检查点 — 选择器与 Run 创建持久化
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_workflow_config_unit.py test/infrastructure/run/test_workflow_registry_unit.py test/infrastructure/run/test_static_workflow_selector_unit.py test/infrastructure/run/test_local_file_run_store_workflow_unit.py test/infrastructure/run/test_redis_run_store_workflow_unit.py test/application/run/test_run_application_service_workflow_unit.py`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 3.6 编写 RunApplicationService 工作流选择测试
  - 在 `epsilon-boot/test/application/run/test_run_application_service_workflow_unit.py` 中创建测试
  - 使用 fake `WorkflowSelectorPort` 覆盖选择成功、选择跳过、显式未知错误、幂等命中不重复事件、task classification 先于 workflow selection
  - 增加同一 `client_request_id` payload hash 相同但显式 workflow 不同的冲突测试，确保不会复用不同编排语义
  - **验证: 需求 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 9.2**

- [x] 3.7 实现工作流阶段编排器
  - 在 `epsilon-boot/src/application/run/workflow_orchestrator.py` 中创建 `WorkflowRunOrchestrator`
  - 实现 `execute_phase(snapshot, execute_existing)`：无 workflow state 时直接调用既有路径；有 workflow state 时写 `WORKFLOW_PHASE_STARTED`，执行现有 Chat/Task 段，按 outcome 写 completed/failed/awaiting summary 并返回带 `workflow_run_state` 的 outcome
  - phase 成功且仍有后续 phase 时，把 outcome 转为 `RunStatus.PAUSED`、`can_continue=True`、`terminal_reason="workflow_phase_completed"`；final phase 成功保持 succeeded
  - 实现 revise 次数限制，超过 `CollaborationLimit.max_revise_per_phase` 时返回 failed outcome 并写 `COLLABORATION_LIMIT_HIT` 或 `WORKFLOW_PHASE_FAILED`
  - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 4.1, 4.2, 4.3, 5.4, 5.5_

- [x] 3.8 编写工作流阶段编排器测试
  - 在 `epsilon-boot/test/application/run/test_workflow_orchestrator_unit.py` 中创建测试
  - 覆盖无 workflow 直通、phase started/completed 事件顺序、非 final phase 成功转 paused、finalize 成功保持 succeeded、failed/paused/awaiting approval 保留既有语义、revise 次数限制
  - **验证: 需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 4.1, 4.2, 4.3, 5.4, 5.5, 9.3**

- [x] 3.9 接入 RunExecutionCoordinator 工作流编排与上下文
  - 在 `epsilon-boot/src/application/run/run_execution_coordinator.py` 中给 `RunExecutionOutcome` 增加 `workflow_run_state`、`collaboration_summary`
  - 构造函数增加 `workflow_orchestrator: WorkflowRunOrchestrator | None = None`、`workflow_registry: WorkflowRegistryPort | None = None` 或等价只读定义来源
  - 在 `execute()` 中将现有 `_execute_chat()`/`_execute_task()` 包装为 `execute_existing` 传给 orchestrator；无 workflow 时保持旧路径
  - 在设置 checkpoint context 的同一执行窗口设置 `WorkflowCollaborationContext`，phase、source_role、limit 来自当前 workflow definition/state，并在 finally reset
  - _需求: 3.7, 4.1, 4.2, 4.3, 4.4, 5.1, 5.4, 5.8, 6.3_

- [x] 3.10 编写 RunExecutionCoordinator 工作流接入测试
  - 在 `epsilon-boot/test/application/run/test_run_execution_coordinator_workflow_unit.py` 中创建测试
  - 覆盖 orchestrator 被调用、无 workflow 保持旧 Chat/Task 路径、checkpoint context 与 collaboration context 同窗口生效、finally reset、continue 语义不追加原始 user message
  - **验证: 需求 2.5, 3.7, 4.1, 4.2, 4.3, 4.4, 5.8, 9.3, 9.5**

- [x] 3.11 扩展 RunWorker outcome 持久化
  - 在 `epsilon-boot/src/infrastructure/run/run_worker.py` 中更新 `_persist_outcome()`，调用 `mark_succeeded()`、`mark_failed()`、`mark_paused()`、`mark_awaiting_approval()`、`mark_cancelled()` 时透传 `outcome.workflow_run_state` 与 `outcome.collaboration_summary`
  - 更新 `_append_terminal_event()`，在 terminal event payload 中加入 workflow/collaboration 摘要字段
  - 保持取消前/取消后检查优先级不被 workflow phase 覆盖
  - _需求: 3.4, 3.5, 3.6, 4.3, 6.4, 7.2_

- [x] 3.12 编写 RunWorker 工作流持久化测试
  - 在 `epsilon-boot/test/infrastructure/run/test_run_worker_workflow_unit.py` 中创建测试
  - 覆盖 succeeded/paused/awaiting_approval/failed outcome 透传 workflow state 和 collaboration summary，缺少 approval_id 的失败降级保留 workflow state，取消优先级不变
  - **验证: 需求 3.4, 3.5, 3.6, 4.3, 6.4, 7.2, 9.3**

- [x] 3.13 扩展 checkpoint workflow 摘要保存与恢复
  - 在 `epsilon-boot/src/application/run/run_checkpoint_sink.py` 中把当前 `WorkflowCollaborationContext` 或 outcome 中的 `workflow_run_state`、`collaboration_summary` 合并进 `DurableCheckpoint.segment_metadata`
  - 在 `epsilon-boot/src/application/run/run_checkpoint_recovery_service.py` 中恢复时优先使用 `RunSnapshot.workflow_run_state`，snapshot 缺失但 checkpoint 存在时读取 checkpoint 摘要；非法 phase/schema 按阶段四保守策略 failed/lost
  - 不改变工具账本 exactly-once 边界或 replay policy
  - _需求: 4.4, 4.5, 4.6, 6.4, 9.5_

- [x] 3.14 编写 checkpoint 工作流兼容测试
  - 在 `epsilon-boot/test/application/run/test_workflow_checkpoint_recovery_unit.py` 中创建测试
  - 覆盖 checkpoint segment_metadata 带 workflow 摘要、snapshot 优先、checkpoint fallback、非法 phase/schema 保守失败或 lost、工具账本 replay 行为不变
  - **验证: 需求 4.4, 4.5, 4.6, 9.5**

- [x] 3.15 检查点 — Run 创建、阶段编排与恢复
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/application/run test/infrastructure/run test/domain/run`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 4.1 实现协作治理事件记录 helper
  - 在 `epsilon-boot/src/infrastructure/agent/workflow_collaboration_recorder.py` 中创建轻量 helper
  - 实现 `record_collaboration_step()`、`record_collaboration_limit_hit()`，从 `WorkflowCollaborationContext` 构造 `CollaborationStepTraceLink` payload 并通过可选 `RunEventStorePort` 追加事件
  - 实现最近 N 条 `CollaborationSummary` 裁剪逻辑，N 来自 `RunWorkflowConfig.recent_collaboration_summary_limit`
  - event_store 不可用或 context 不存在时返回原有行为所需的空结果，不阻断非 Run Agent loop
  - _需求: 5.2, 5.3, 5.5, 6.1, 6.3, 6.5, 7.2_

- [x] 4. 检查点 — 阶段编排与协作记录基础
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/application/run test/infrastructure/run test/domain/run test/infrastructure/agent/test_workflow_collaboration_governance_unit.py`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 4.2 接入 DelegateToAgentTool 协作治理
  - 在 `epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py` 中读取 `get_workflow_collaboration_context()`
  - 在原有 `_current_delegation_depth` / `_max_delegation_depth` 校验基础上，与 workflow `max_recursion_depth` 取更严格值
  - 命中限制时不调用 `DelegationPort.delegate()`，写 `COLLABORATION_LIMIT_HIT`，返回失败文本或抛既有 `DelegationDepthExceededError`，保持调用方错误处理语义
  - 成功或失败后写 `COLLABORATION_STEP_RECORDED`，payload 包含 source role、target agent、action=`delegation`、task/result summary、depth
  - _需求: 5.1, 5.2, 5.4, 5.5, 5.6, 5.8, 6.3, 6.5_

- [x] 4.3 接入 DelegateParallelTool 协作治理
  - 在 `epsilon-boot/src/infrastructure/agent/delegate_parallel_tool.py` 中读取 workflow context
  - 在执行前检查请求数量不超过 `CollaborationLimit.max_parallel_delegations`，超过时不调用 `DelegationPort.delegate_parallel()` 并记录 limit hit
  - 保持原有 `_MAX_REQUESTS` schema 校验、深度校验、错误隔离和按输入顺序聚合结果
  - 对每条请求的成功/失败记录 `COLLABORATION_STEP_RECORDED`，action=`delegation`
  - _需求: 5.2, 5.4, 5.5, 5.6, 5.8, 6.3, 6.5_

- [x] 4.4 接入 HandoffToAgentTool 协作治理
  - 在 `epsilon-boot/src/infrastructure/agent/handoff_to_agent_tool.py` 中读取 workflow context
  - 检查 `max_recursion_depth` 和 `max_handoff_count`，命中时不调用 `DelegationPort.handoff()`，返回既有工具错误字符串并记录 `COLLABORATION_LIMIT_HIT`
  - 成功 handoff 抛 `HandoffPerformed` 前记录 `COLLABORATION_STEP_RECORDED`，payload 包含 action=`handoff`、target agent、原因摘要和结果摘要
  - 保持目标 Agent 接管上下文、父 Agent loop 终止的既有语义
  - _需求: 5.1, 5.3, 5.4, 5.5, 5.7, 5.8, 6.3, 6.5_

- [x] 4.5 编写协作治理工具测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_workflow_collaboration_governance_unit.py` 中创建测试
  - 覆盖无 context 时 delegate/parallel/handoff 旧行为不变、深度限制取更严格值、并行扇出限制、handoff 次数限制、limit hit 不调用真实 port、成功/失败事件 payload JSON-safe
  - **验证: 需求 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.1, 6.3, 6.5, 9.4**

- [x] 4.6 编写协作事件与摘要测试
  - 在 `epsilon-boot/test/application/run/test_workflow_collaboration_events_unit.py` 中创建测试
  - 覆盖 `StepTraceLink` 事件顺序、latest summary 裁剪、父 Run 可通过事件观察协作步骤、future `ParentChildRunLink` 模型序列化但 v1 不强制创建子 Run
  - **验证: 需求 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 9.4**

- [x] 4.7 回归 HITL 与 Guardrail 边界
  - 在现有 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_unit.py`、`test_react_agent_hitl_checkpoint_recovery_unit.py`、`test_react_agent_guardrail_unit.py` 或新增 `test_workflow_hitl_guardrail_regression_unit.py` 中补充 workflow context 场景
  - 验证 awaiting approval 与 `resume_approval_run()` 入口不变，guardrail observe/critical enforce 阻断字段透传不变，workflow 不新增 guardrail 运行时闭环
  - **验证: 需求 4.3, 4.6, 4.7, 9.6, 9.7**

- [x] 4.8 检查点 — 协作治理与 HITL/Guardrail 回归
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent test/application/run/test_workflow_collaboration_events_unit.py`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 5.1 接入容器装配
  - 在 `epsilon-boot/src/application/container_config.py` 中创建并注册 `RunWorkflowConfig`、`StaticWorkflowRegistryAdapter`、`StaticWorkflowSelector`、`WorkflowRunOrchestrator`
  - 将 `WorkflowSelectorPort` 注入 `RunApplicationService`，将 `WorkflowRunOrchestrator` 和 registry/definition 来源注入 `RunExecutionCoordinator`
  - 确保配置非法、定义非法在启动期 fail-fast，且未启用 workflow 时旧 Run runtime 可正常创建和执行
  - _需求: 1.4, 1.5, 1.7, 2.1, 2.2, 4.1, 8.3_

- [x] 5.2 编写容器装配测试
  - 在 `epsilon-boot/test/application/test_run_workflow_container_wiring_unit.py` 中创建测试
  - 覆盖默认装配包含 registry/selector/orchestrator，非法配置 fail-fast，禁用 workflow 时 RunApplicationService 仍可用，领域层未反向依赖基础设施
  - **验证: 需求 1.3, 1.4, 1.5, 1.7, 2.2, 4.1, 8.3, 9.9**

- [x] 5.3 扩展 FastAPI Run DTO 透传
  - 在 `epsilon-boot/src/application/api/routers/runs.py` 和兼容旧路径 `epsilon-boot/src/application/routers/runs.py` 中为 `RunSnapshotBody` 增加 `workflow_name`、`workflow_run_state`、`collaboration_summary`
  - 为创建 Run 请求 DTO 增加可选 `workflow_name`，转换到 `RunCreateRequest.workflow_name`
  - 确保 `RunEventBody` 对新增 `RunEventType` 字符串可序列化，不在 router 中调用 selector、registry、orchestrator 或 limit 判断
  - _需求: 2.3, 2.6, 7.1, 7.2, 7.5_

- [x] 5.4 编写 FastAPI Run DTO 透传测试
  - 在 `epsilon-boot/test/application/routers/test_runs_router_workflow_unit.py` 中创建测试
  - 覆盖 create 请求 workflow_name 传入、snapshot 新字段响应、事件新类型响应、显式未知 workflow 映射 400、router 不导入 workflow selector/orchestrator 的静态断言
  - **验证: 需求 2.3, 2.6, 7.1, 7.2, 7.5, 9.8, 9.9**

- [x] 5. 检查点 — 协作治理、容器与 API 透传
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent test/application/run/test_workflow_collaboration_events_unit.py test/application/test_run_workflow_container_wiring_unit.py test/application/routers/test_runs_router_workflow_unit.py`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 5.5 扩展 CLI/TUI Run 展示
  - 在 `epsilon-boot/src/application/cli/commands.py` 中展示 `workflow_name`、当前 `workflow_phase`、最近协作摘要
  - 在 `epsilon-boot/src/application/cli/tui.py` 中为 Run 视图增加 workflow、phase、recent collaboration summary 显示，事件日志识别新增 workflow/collaboration 事件
  - CLI/TUI 只读取 `RunSnapshot` 和 `RunEvent` 字段，不调用 selector、phase 推进或 collaboration limit 判断
  - _需求: 7.3, 7.5, 7.6, 7.7_

- [x] 5.6 编写 CLI/TUI 工作流展示测试
  - 在 `epsilon-boot/test/application/cli/test_tui_run_workflow.py` 和 `test/application/cli/test_commands.py` 中补充测试
  - 覆盖 workflow/phase/协作摘要文本展示、字段为空时兼容显示、replay expired 后仍通过 snapshot 展示 workflow 字段
  - **验证: 需求 7.3, 7.5, 7.6, 7.7, 9.8**

- [x] 5.7 扩展前端 API 类型与 Run View 展示
  - 在 `epsilon-client/src/lib/chat-api.ts` 中新增 `WorkflowRunState`、`CollaborationSummary` 或使用 `Record<string, unknown>` 类型，并给 `RunSnapshot` 增加 `workflow_name`、`workflow_run_state`、`collaboration_summary`
  - 在 `epsilon-client/src/components/run/run-view.tsx` 中展示当前 workflow、phase、阶段历史摘要、最近协作摘要
  - 在 `epsilon-client/src/components/run/run-event-list.tsx` 中确保新增 workflow/collaboration 事件以已有事件 payload 渲染路径展示
  - 保持 replay expired 后 polling fallback 使用 snapshot 字段；前端不实现 workflow selection、phase 推进或 limit 判断
  - _需求: 7.4, 7.5, 7.6, 7.7_

- [x] 5.8 编写前端静态契约测试
  - 在 `epsilon-boot/test/application/test_long_task_phase6_frontend_contract_static.py` 中创建测试
  - 读取 `epsilon-client/src/lib/chat-api.ts`、`run-view.tsx`、`run-event-list.tsx`，断言新增字段存在、Run View 展示 workflow/phase/协作摘要、未出现 selector/orchestrator/limit 推进相关实现
  - **验证: 需求 7.4, 7.5, 7.6, 7.7, 9.8, 9.9**

- [x] 5.9 编写阶段六架构与依赖静态测试
  - 在 `epsilon-boot/test/application/test_long_task_phase6_architecture_static.py` 中创建测试
  - 断言 `domain/run` 不导入 `application`、`infrastructure`、FastAPI、Redis、Temporal、LangGraph、Dapr、Celery
  - 断言 `pyproject.toml`、`uv.lock`、`epsilon-client/package.json` 不新增 Temporal/LangGraph/Dapr/Celery 等 durable workflow runtime 依赖
  - 断言 FastAPI、CLI、Web adapter 不导入 `StaticWorkflowSelector`、`WorkflowRunOrchestrator` 或调用 collaboration limit 判定
  - **验证: 需求 1.3, 7.5, 8.1, 8.2, 8.3, 8.5, 9.9, 9.10, 9.11**

- [x] 5.10 检查点 — Adapter、前端与架构边界
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest test/application/routers test/application/cli test/application/test_run_workflow_container_wiring_unit.py test/application/test_long_task_phase6_frontend_contract_static.py test/application/test_long_task_phase6_architecture_static.py`
  - 在 `epsilon-client` 中运行 `npm run lint`
  - 修复本检查点暴露的问题后再继续后续任务

- [x] 6.1 编写阶段六端到端集成测试
  - 在 `epsilon-boot/test/application/test_long_task_phase6_integration.py` 中创建测试
  - 覆盖 create -> workflow selected/skipped event -> worker execute first phase -> paused continue -> next phase 状态推进，验证 snapshot/event 中 workflow fields、phase history、terminal reason
  - 覆盖显式未知 workflow 不创建 Run，自动无匹配兼容创建，guardrail task classification 可参与选择
  - _需求: 1.2, 2.1, 2.2, 2.3, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 3.6, 4.1, 4.2, 4.3, 9.1, 9.2, 9.3_

- [x] 6.2 编写恢复、审批和协作集成测试
  - 在 `epsilon-boot/test/application/test_long_task_phase6_recovery_collaboration_integration.py` 中创建测试
  - 覆盖 checkpoint recovery 保留 workflow phase、awaiting approval resume 后继续当前 phase、delegate/handoff 协作事件进入 event stream、limit hit 可观察
  - 验证阶段四工具账本 replay 与阶段五 guardrail critical enforce 阻断语义不变
  - _需求: 4.3, 4.4, 4.5, 4.6, 4.7, 5.2, 5.3, 5.5, 5.6, 5.7, 5.8, 6.1, 6.3, 6.5, 9.4, 9.5, 9.6, 9.7_

- [x] 6.3 更新文档与最终验证说明
  - 在 `docs/spec/long-task-continuation-phase6/review-log.md` 首次实现前创建或追加任务执行审计占位，后续 generator/evaluator 按 task slice 追加真实记录
  - 如实现中出现必须偏离 `design.md` 的细节，先回到 spec_designer 修改设计，不在代码中隐式扩大范围
  - 确认 `docs/spec/long-task-continuation-phase6/summary.md` 仍不存在，待所有任务勾选且 evaluator PASS 后由 coordinator 生成
  - _需求: 8.4, 9.10, 9.11_

- [x] 6.4 最终检查点 — 全量验证
  - 在 `epsilon-boot` 中运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 在 `epsilon-client` 中运行 `npm run lint`
  - 在 `epsilon-client` 中运行 `npm run build`
  - 所有检查通过后进入 evaluator；evaluator PASS 后才可勾选完成任务并生成 `summary.md`

## 备注

- 本阶段默认审批门开启：`tasks.md` 生成后暂停，待确认后进入实现。
- 实现不得引入 Temporal、LangGraph、Dapr Workflow、Celery 或其他外部 durable workflow runtime。
- Adapter、TUI、Web 只透传和展示 workflow/collaboration 字段，不复制选择器、phase 推进、协作限制或 checkpoint recovery 判定。
- `review-log.md` 是实现评审审计日志；只有 generator/evaluator 执行具体任务后才追加对应记录。
