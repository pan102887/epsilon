# 实现计划：长任务持久化检查点阶段四

## 概述

本计划将 `requirement.md` 与 `design.md` 落地为可分批实现、可验证的任务清单。任务按依赖顺序推进：领域模型与端口 → 应用层 checkpoint/recovery 编排 → file/Redis checkpoint store → worker/container 装配 → Agent 工具防重放 hook → API/TUI/Web 观察展示 → 集成回归。每个实现任务后紧跟对应测试任务；每 10 个非检查点任务设置一次全量检查点，最终任务后再设置收尾检查点。

## Tasks

- [x] 1.1 扩展 Run 领域值对象与恢复事件
  - 在 `epsilon-boot/src/domain/run/value_objects.py` 中修改 `RunEventType`、`RunSnapshot`，新增 `CheckpointPhase`、`ToolLedgerStatus`、`ToolReplayPolicy`、`ToolSideEffectLevel`、`DurableCheckpoint`、`ToolExecutionKey`、`ToolResultLedgerEntry`、`CheckpointRetentionPolicy`、`RecoveryDecision`
  - `RunEventType` 新增 `CHECKPOINT_SAVED`、`RUN_RECOVERY_QUEUED`、`RUN_RECOVERY_FAILED`、`TOOL_RESULT_REPLAYED`
  - `RunSnapshot` 追加 `latest_checkpoint_id: str | None = None`、`recoverable: bool = False`、`recovery_attempt_count: int = 0`、`last_recovery_error: dict[str, Any] | None = None`，保持旧 JSON/旧 dataclass 调用默认兼容
  - `ToolExecutionKey.stable_key(self) -> str` 使用 `run_id`、`segment_index`、`round_num`、`tool_call_id`、`tool_name`、`arguments_digest` 计算稳定 SHA-256
  - _需求: 1, 4, 5, 7, 8_

- [x] 1.2 编写 Run checkpoint 值对象测试
  - 在 `epsilon-boot/test/domain/run/test_run_checkpoint_value_objects_unit.py` 中创建测试
  - 覆盖新增 enum 值、`RunSnapshot` 默认字段、旧快照缺失新增字段时的兼容构造、`ToolExecutionKey.stable_key()` 稳定性
  - 覆盖 Property 4、Property 8
  - **验证: 需求 1, 5, 8, 9**

- [x] 1.3 扩展 Run 领域异常
  - 在 `epsilon-boot/src/domain/run/exceptions.py` 中新增 `RunCheckpointWriteError`、`RunCheckpointSchemaError`、`RunRecoveryUnavailableError`、`RunToolReplayBlockedError`、`RunCheckpointPayloadTooLargeError`、`RunCheckpointStoreUnavailableError`
  - 错误码使用 61011-61016，错误消息只暴露 `run_id`、`checkpoint_id`、`tool_name`、`tool_execution_key` 摘要和原因，不包含完整 prompt、工具参数、工具结果或 trace
  - _需求: 1, 2, 3, 4, 5, 8_

- [x] 1.4 编写 Run checkpoint 异常测试
  - 在 `epsilon-boot/test/domain/run/test_run_checkpoint_exceptions_unit.py` 中创建测试
  - 覆盖新增异常继承 `BizException`、错误码稳定、错误消息不包含完整敏感 payload 的约束
  - **验证: 需求 2, 5, 8, 9**

- [x] 1.5 定义 checkpoint store、sink 与恢复 Port
  - 在 `epsilon-boot/src/domain/run/ports.py` 中新增 `RunCheckpointStorePort` 与 `RunCheckpointSinkPort`
  - `RunCheckpointStorePort` 定义 `save_checkpoint`、`latest_checkpoint`、`list_checkpoints`、`put_tool_pending`、`complete_tool_result`、`get_tool_result`、`list_tool_ledger`、`trim_checkpoints`
  - `RunCheckpointSinkPort` 定义 `model_completed`、`before_tool_call`、`after_tool_call`、`approval_interrupt`、`segment_done`
  - 扩展 `RunStorePort`：新增 `list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]`、`enqueue_recovery(...) -> RunSnapshot`、`mark_lost_expired_run(...) -> RunSnapshot`；保留 `mark_lost_expired_leases()` 兼容阶段三
  - _需求: 1, 2, 3, 4, 5, 6, 8_

- [x] 1.6 编写 Run checkpoint Port 签名测试
  - 在 `epsilon-boot/test/domain/run/test_run_checkpoint_ports_unit.py` 中创建测试
  - 使用当前项目的 Protocol 签名测试风格校验 `RunCheckpointStorePort`、`RunCheckpointSinkPort`、扩展后的 `RunStorePort` 方法名、参数名和返回类型
  - **验证: 需求 1, 2, 3, 4, 5, 6, 8**

- [x] 1.7 创建 checkpoint ContextVar 上下文
  - 在 `epsilon-boot/src/domain/run/checkpoint_context.py` 中创建 `RunCheckpointExecutionContext`
  - 实现 `set_run_checkpoint_context(value) -> contextvars.Token[...]`、`reset_run_checkpoint_context(token) -> None`、`get_run_checkpoint_context() -> RunCheckpointExecutionContext | None`
  - 同步 Chat/Task 入口默认不设置上下文，保证无 checkpoint 行为
  - _需求: 3, 5, 6, 8_

- [x] 1.8 编写 checkpoint ContextVar 测试
  - 在 `epsilon-boot/test/domain/run/test_run_checkpoint_context_unit.py` 中创建测试
  - 覆盖默认值为 `None`、set/reset 成对恢复、嵌套 token 恢复、并发 async task 上下文隔离
  - **验证: 需求 3, 5, 6, 8, 9**

- [x] 1.9 扩展 Tool 基类恢复元数据
  - 在 `epsilon-boot/src/domain/agent/tools.py` 中修改 `Tool` 基类
  - 新增 `side_effect_level(self) -> ToolSideEffectLevel` 默认返回 `ToolSideEffectLevel.EXTERNAL_WRITE`
  - 新增 `replay_policy(self) -> ToolReplayPolicy` 默认返回 `ToolReplayPolicy.MANUAL_REVIEW`
  - 新增 `idempotency_key(self, request: ToolCallRequest, execution_key: str) -> str | None` 默认返回 `None`
  - 保持现有具体工具无需改动即可运行，后续工具可按需覆盖为 `NONE + REPLAY_RESULT`
  - _需求: 5, 8_

- [x] 1.10 编写 Tool 基类恢复元数据测试
  - 在 `epsilon-boot/test/domain/agent/test_tool_replay_policy_unit.py` 中创建测试
  - 覆盖默认策略为保守人工处理、默认副作用等级为外部写、默认无幂等键、现有最小 Tool 子类无需实现新增属性仍可实例化运行
  - **验证: 需求 5, 8, 9**

- [x] 1. 检查点 — 领域模型、Port 与 Tool 元数据
  - 在 `epsilon-boot/` 下运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 确认全部测试通过；如失败，先修复本批次相关问题再继续

- [x] 2.1 实现 Run checkpoint sink
  - 在 `epsilon-boot/src/application/run/run_checkpoint_sink.py` 中创建 `RunCheckpointSink`
  - 构造函数接收 `checkpoint_store: RunCheckpointStorePort`、`event_store: RunEventStorePort`、`retention_policy: CheckpointRetentionPolicy`、`now: Callable[[], datetime] | None = None`
  - 实现 `model_completed()`、`before_tool_call()`、`after_tool_call()`、`approval_interrupt()`、`segment_done()`
  - `before_tool_call()` 先查 completed ledger；命中时返回 entry 供 replay；未命中时持久化 pending；pending 写入失败抛 `RunCheckpointWriteError`
  - `after_tool_call()` 将 pending 转 completed/error，保存包含 `ToolMessage` 后上下文的 checkpoint，并追加 `CHECKPOINT_SAVED`
  - sanitizer 对 `context_snapshot`、`trace_summary`、tool result 做大小裁剪，记录 `sanitized` 与 `truncated_fields`
  - _需求: 1, 3, 5, 6, 7, 8_

- [x] 2.2 编写 Run checkpoint sink 测试
  - 在 `epsilon-boot/test/application/run/test_run_checkpoint_sink_unit.py` 中创建测试
  - 覆盖 model/tool/approval/segment checkpoint、pending 写入失败不执行后续工具、completed ledger replay、payload 裁剪、事件追加
  - 覆盖 Property 2、Property 3
  - **验证: 需求 1, 3, 5, 6, 7, 8, 9**

- [x] 2.3 实现 Run recovery service
  - 在 `epsilon-boot/src/application/run/run_checkpoint_recovery_service.py` 中创建 `RunRecoveryService`
  - 构造函数接收 `run_store`、`checkpoint_store`、`event_store`、`retention_policy`、`max_recovery_attempts`、`auto_recovery_enabled`
  - 实现 `sweep_expired_leases(self, *, now: datetime) -> list[RunSnapshot]` 与 `evaluate_recovery(self, snapshot: RunSnapshot) -> RecoveryDecision`
  - 恢复前置条件：latest checkpoint 存在且 `schema_version == 1`、上下文可反序列化、Task 工具边界 metadata 可重建、无不安全 pending、恢复次数未超过上限、`CANCEL_REQUESTED` 优先取消
  - 可恢复时调用 `enqueue_recovery()` 并追加 `RUN_RECOVERY_QUEUED`；不可恢复时调用 `mark_lost_expired_run()` 并追加 `RUN_RECOVERY_FAILED` 或 `RUN_LOST`
  - _需求: 4, 5, 6, 7, 8_

- [x] 2.4 编写 Run recovery service 测试
  - 在 `epsilon-boot/test/application/run/test_run_checkpoint_recovery_service_unit.py` 中创建测试
  - 覆盖可恢复重新入队、无 checkpoint lost、schema 不兼容、context 反序列化失败、pending 阻塞、cancel 优先、超过恢复次数、auto recovery 关闭回退
  - 覆盖 Property 5、Property 7、Property 8
  - **验证: 需求 4, 5, 6, 7, 8, 9**

- [x] 2.5 扩展 Run execution coordinator checkpoint 编排
  - 在 `epsilon-boot/src/application/run/run_execution_coordinator.py` 中修改 `RunExecutionCoordinator`
  - 构造函数新增 `checkpoint_store: RunCheckpointStorePort | None = None`、`event_store: RunEventStorePort | None = None`、`retention_policy: CheckpointRetentionPolicy | None = None`、`checkpoint_enabled: bool = False`
  - 后台 run 执行时创建 `RunCheckpointSink` 并设置 `RunCheckpointExecutionContext`；执行结束后必须 reset token
  - `checkpoint_enabled=false` 或缺少 store 时保持阶段三语义；恢复模式下读取 latest checkpoint 的上下文/轮次/usage/segment metadata
  - _需求: 3, 4, 6, 8_

- [x] 2.6 编写 Run execution coordinator checkpoint 测试
  - 在 `epsilon-boot/test/application/run/test_run_execution_coordinator_checkpoint_unit.py` 中创建测试
  - 覆盖 checkpoint context 设置/reset、关闭 checkpoint 不写 store、恢复模式使用 latest checkpoint、同步 Chat/Task 公开入口不被 checkpoint 扩展改变
  - **验证: 需求 3, 4, 6, 8, 9**

- [x] 3.1 扩展 Run runtime 配置
  - 在 `epsilon-boot/src/infrastructure/run/run_config.py` 中修改 `RunRuntimeConfig`
  - 新增 `RUN_CHECKPOINT_ENABLED`、`RUN_CHECKPOINT_AUTO_RECOVERY_ENABLED`、`RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS`、`RUN_CHECKPOINT_MAX_COUNT`、`RUN_CHECKPOINT_TTL_SECONDS`、`RUN_CHECKPOINT_MAX_PAYLOAD_BYTES`、`RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT`
  - 增加配置校验：数量/TTL/字节上限必须为正数，最大恢复次数不得小于 0
  - 新增 `to_checkpoint_retention_policy(self) -> CheckpointRetentionPolicy`
  - 在 `epsilon-boot/config.properties` 中写入默认配置项，遵循既有 settings 与 `config.properties` 优先规则
  - _需求: 1, 8_

- [x] 3.2 编写 Run checkpoint 配置测试
  - 在 `epsilon-boot/test/infrastructure/run/test_run_checkpoint_config_unit.py` 中创建测试
  - 覆盖默认值、非法值校验、`to_checkpoint_retention_policy()` 映射、`config.properties` 键名存在
  - **验证: 需求 1, 8, 9**

- [x] 3.3 实现本地文件 checkpoint store
  - 在 `epsilon-boot/src/infrastructure/run/local_file_run_checkpoint_store_adapter.py` 中创建 `LocalFileRunCheckpointStoreAdapter`
  - 实现 `RunCheckpointStorePort` 全部方法，复用现有 `runs/` 路径策略、bucket、`CrossPlatformFileLock`、`TempFileAtomicWriter`、JSON-safe 编码约定
  - 文件布局为 `runs/checkpoints/<bucket>/<run_id>.jsonl` 与 `runs/tool_ledgers/<bucket>/<run_id>.json`
  - `save_checkpoint()` 在同一 run lock 内分配单调 sequence 并 append JSONL；`put_tool_pending()` 保证同一 `tool_execution_key` 不重复创建；`complete_tool_result()` 原子更新 pending/completed/error；`trim_checkpoints()` 按数量、TTL、payload 和 ledger 上限 best-effort 裁剪
  - _需求: 1, 2, 3, 5, 8_

- [x] 3.4 编写本地文件 checkpoint store 测试
  - 在 `epsilon-boot/test/infrastructure/run/test_local_file_run_checkpoint_store_adapter_unit.py` 中扩展或创建 checkpoint 专用测试
  - 覆盖 append/latest/list、sequence 单调、ledger pending/completed、completed 结果可读、schema 不兼容、trim 数量/TTL/大小裁剪、旧数据兼容
  - 覆盖 Property 1、Property 3、Property 8
  - **验证: 需求 1, 2, 3, 5, 8, 9**

- [x] 2. 检查点 — 应用编排、配置与本地 checkpoint store
  - 在 `epsilon-boot/` 下运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 确认全部测试通过；如失败，先修复本批次相关问题再继续

- [x] 3.5 实现 Redis checkpoint store
  - 在 `epsilon-boot/src/infrastructure/run/redis_run_checkpoint_store_adapter.py` 中创建 `RedisRunCheckpointStoreAdapter`
  - 实现 `RunCheckpointStorePort` 全部方法，使用 `run:{run_id}:checkpoints`、`run:{run_id}:checkpoint_seq`、`run:{run_id}:tool_ledger`
  - `save_checkpoint()` 使用 WATCH/MULTI 原子递增 sequence；`put_tool_pending()` 使用 HSETNX 语义；`complete_tool_result()` 在事务中校验现有状态；WATCH 冲突沿用现有 `conflict_retry_max`
  - `trim_checkpoints()` 裁剪 list 与 hash，裁剪失败只记录 warning，不回滚业务结果
  - _需求: 1, 2, 3, 5, 8_

- [x] 3.6 编写 Redis checkpoint store 测试
  - 在 `epsilon-boot/test/infrastructure/run/test_redis_run_checkpoint_store_adapter_unit.py` 中扩展或创建 checkpoint 专用测试
  - 覆盖 WATCH/MULTI sequence、HSETNX pending、completed replay、并发冲突重试、schema 不兼容、trim、Redis 不可用错误
  - 覆盖 Property 1、Property 3
  - **验证: 需求 1, 2, 3, 5, 8, 9**

- [x] 4.1 扩展 Run store 恢复方法的本地文件与 Redis 实现
  - 在 `epsilon-boot/src/infrastructure/run/local_file_run_store_adapter.py` 与 `epsilon-boot/src/infrastructure/run/redis_run_store_adapter.py` 中实现 `list_expired_leased_runs()`、`enqueue_recovery()`、`mark_lost_expired_run()`
  - `enqueue_recovery()` 必须原子校验 run 仍是过期 `RUNNING` 或 `CANCEL_REQUESTED`，更新为 queued/recovery metadata，清除旧 lease，递增 `recovery_attempt_count`
  - `mark_lost_expired_run()` 写入 `last_recovery_error`，保持阶段三 `mark_lost_expired_leases()` 行为兼容
  - _需求: 4, 7, 8_

- [x] 4.2 编写 Run store 恢复方法测试
  - 在 `epsilon-boot/test/infrastructure/run/test_run_store_recovery_methods_unit.py` 中创建测试，或分别扩展 file/Redis store 测试
  - 覆盖过期 running 列出、非过期/状态变化跳过、恢复入队原子条件、恢复次数递增、lost 错误摘要、阶段三 `mark_lost_expired_leases()` 兼容
  - **验证: 需求 4, 7, 8, 9**

- [x] 4.3 接入 Run worker manager 恢复扫描
  - 在 `epsilon-boot/src/infrastructure/run/run_worker_manager.py` 中修改 lost sweep
  - 构造函数新增可选 `recovery_service: RunRecoveryService | None`
  - `RUN_CHECKPOINT_ENABLED=true` 且 `RUN_CHECKPOINT_AUTO_RECOVERY_ENABLED=true` 时调用 `recovery_service.sweep_expired_leases(now=...)`
  - checkpoint 关闭或未装配 recovery service 时保留阶段三 `mark_lost_expired_leases()` + `RUN_LOST` 事件路径
  - Recovery sweep 内异常不得杀死 manager loop，必须记录可观测 failure event
  - _需求: 4, 7, 8_

- [x] 4.4 编写 Run worker manager 恢复扫描测试
  - 在 `epsilon-boot/test/infrastructure/run/test_run_worker_manager_checkpoint_recovery_unit.py` 中创建测试
  - 覆盖 checkpoint 开启走 recovery service、关闭走阶段三 lost、recovery service 抛错不杀 loop、`CANCEL_REQUESTED` 优先取消
  - **验证: 需求 4, 7, 8, 9**

- [x] 4.5 接入容器配置与资源装配
  - 在 `epsilon-boot/src/application/container_config.py` 中装配 `RunCheckpointStorePort`、`RunRecoveryService`、扩展后的 `RunExecutionCoordinator` 与 `RunWorkerManager`
  - 按 `SESSION_STORE_BACKEND` 选择 `LocalFileRunCheckpointStoreAdapter` 或 `RedisRunCheckpointStoreAdapter`
  - checkpoint 关闭时不得创建不必要的后台恢复服务；同步 Chat/Task 入口不注入 checkpoint context
  - 更新 `epsilon-boot/src/infrastructure/run/__init__.py` 与 `epsilon-boot/src/application/run/__init__.py` 的必要导出
  - _需求: 2, 3, 4, 7, 8_

- [x] 4.6 编写容器装配测试
  - 在 `epsilon-boot/test/application/test_run_checkpoint_container_wiring_unit.py` 中创建测试
  - 覆盖 file/Redis 后端选择、checkpoint 开关、RunRecoveryService 注入、RunExecutionCoordinator 参数注入、关闭 checkpoint 时阶段三路径可用
  - **验证: 需求 2, 3, 4, 7, 8, 9**

- [x] 5.1 在 ReActAgentAdapter 接入 checkpoint hook
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中修改 `_iter_rounds()` 与 `_execute_tool_call()`
  - `_iter_rounds()` 完成模型调用并构造 response 后，如存在 `RunCheckpointExecutionContext`，调用 `sink.model_completed(...)`
  - 记录 assistant tool_calls 后、审批保存前，调用 `sink.approval_interrupt(...)`
  - `_execute_tool_call()` 在 `_tool_registry.execute()` 前调用 `sink.before_tool_call(...)`
  - `before_tool_call()` 返回 completed entry 时直接 `context.add_tool_result(...)`，追加 `TOOL_RESULT_REPLAYED` 事件，不调用工具
  - pending 写入失败时异常向上冒泡，不执行工具；工具完成后调用 `sink.after_tool_call(...)`
  - 多工具并发保持现有 `asyncio.gather`，每个工具独立 pending/completed/replay
  - _需求: 3, 5, 6, 7, 8_

- [x] 5.2 编写 ReActAgentAdapter checkpoint hook 测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_checkpoint_recovery_unit.py` 中创建测试
  - 覆盖模型完成 checkpoint、工具 pending 先于 execute、pending 失败 execute 调用次数为 0、completed ledger replay 不执行工具、多工具并发独立账本、审批中断 checkpoint
  - 覆盖 Property 2、Property 3
  - **验证: 需求 3, 5, 6, 7, 8, 9**

- [x] 3. 检查点 — Redis store、worker 装配与 Agent checkpoint hook
  - 在 `epsilon-boot/` 下运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 确认全部测试通过；如失败，先修复本批次相关问题再继续

- [x] 5.3 扩展审批恢复防重放路径
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 的 HITL approve/edit/reject 恢复路径中查询 `Tool_Result_Ledger`
  - approve/edit 对应工具已 completed 时复用账本结果，不再次执行工具；reject 决策产生的 `ToolMessage` 写入 checkpoint 与 ledger
  - 再次进入 awaiting approval 时保存新的 `DurableCheckpoint`，不得把 `Approval_Interrupt` 当普通 paused continue 处理
  - _需求: 5, 6_

- [x] 5.4 编写审批恢复防重放测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_checkpoint_recovery_unit.py` 中创建测试
  - 覆盖 approve/edit completed replay、reject 写入 checkpoint、服务重启后 awaiting approval 上下文保留、不走普通 paused continue 路径
  - **验证: 需求 5, 6, 9**

- [x] 5.5 扩展 Run application service 对外快照与事件
  - 在 `epsilon-boot/src/application/run/run_application_service.py` 中保持所有 Run 查询/事件写入通过共享应用服务
  - `get_run()` 返回扩展后的 `RunSnapshot` 字段：`latest_checkpoint_id`、`recoverable`、`recovery_attempt_count`、`last_recovery_error`
  - 事件读取路径暴露 `CHECKPOINT_SAVED`、`RUN_RECOVERY_QUEUED`、`RUN_RECOVERY_FAILED`、`TOOL_RESULT_REPLAYED`
  - 客户端 replay/polling 路径只读 snapshot/events，不调用 `RunRecoveryService`
  - _需求: 7, 8_

- [x] 5.6 编写 Run application service 观察恢复测试
  - 在 `epsilon-boot/test/application/run/test_run_application_service_checkpoint_unit.py` 中创建测试
  - 覆盖扩展字段返回、恢复事件 replay、`replay_expired` 降级保持阶段三语义、Observation reattach 不触发 `sweep_expired_leases()` 或 `enqueue_recovery()`
  - 覆盖 Property 6
  - **验证: 需求 7, 8, 9**

- [x] 6.1 扩展 FastAPI Run adapter 与异常映射
  - 在 `epsilon-boot/src/application/routers/runs.py` 与 `epsilon-boot/src/application/api/routers/runs.py` 中扩展响应模型/映射，展示 checkpoint/recovery 字段和事件类型
  - 在 `epsilon-boot/src/application/api/exception_handlers.py` 中映射新增 checkpoint/recovery BizException：不可恢复/重放阻塞为 409，存储不可用为 503
  - Adapter 只映射 `RunApplicationService` 结果，不复制 checkpoint、恢复、claim 或工具重放规则
  - _需求: 7, 8_

- [x] 6.2 编写 FastAPI Run adapter 测试
  - 在 `epsilon-boot/test/application/routers/test_runs_checkpoint_router_unit.py` 中创建测试
  - 覆盖响应模型新字段、事件类型序列化、异常状态码映射、router 不直接调用 store/recovery service
  - **验证: 需求 7, 8, 9**

- [x] 6.3 扩展 TUI Run view 与 runtime 展示
  - 在 `epsilon-boot/src/application/cli/runtime.py` 与 TUI Run View 相关模块中展示 `latest_checkpoint_id`、`recoverable`、`recovery_attempt_count`、`last_recovery_error`
  - 观察恢复沿用 `get_run`、事件 replay 与 polling fallback，不通过 FastAPI endpoint 自调用，不触发执行恢复
  - _需求: 7, 8_

- [x] 6.4 编写 TUI Run view 测试
  - 在 `epsilon-boot/test/application/cli/test_tui_run_checkpoint_view.py` 中创建测试
  - 覆盖恢复字段展示、恢复失败摘要隐藏敏感 payload、事件 replay 过期后 polling fallback 仍只读
  - **验证: 需求 7, 8, 9**

- [x] 6.5 扩展 Web Run View 展示
  - 在 `epsilon-client/src/lib/chat-api.ts`、`epsilon-client/src/hooks/use-run.ts`、`epsilon-client/src/components/run/run-view.tsx`、`epsilon-client/src/components/run/run-event-list.tsx` 中扩展类型与展示
  - 展示恢复状态、恢复尝试次数、最近失败摘要、checkpoint/recovery/replay 事件；保持现有 Run View 布局，不改变同步 Chat/Task 默认入口
  - SSE replay 过期或刷新后继续使用 snapshot 查询/polling fallback，不触发执行恢复
  - _需求: 7, 8_

- [x] 6.6 编写 Web Run View 静态验证任务
  - 在 `epsilon-client/` 下运行 `npm run lint` 与 `npm run build`
  - 验证 TypeScript 类型、组件引用和生产构建通过；必要时补充现有前端测试/类型断言
  - **验证: 需求 7, 8, 9**

- [x] 4. 检查点 — HITL、后端对外契约与 Web Run View
  - 在 `epsilon-boot/` 下运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 在 `epsilon-client/` 下运行 `npm run lint` 和 `npm run build`
  - 确认全部测试通过；如失败，先修复本批次相关问题再继续

- [x] 7.1 编写阶段四后端集成恢复测试
  - 在 `epsilon-boot/test/integration/test_run_checkpoint_recovery_integration.py` 中创建测试
  - 覆盖 file backend 下模型调用后崩溃、工具调用后崩溃、执行段结束后崩溃、审批中断前后崩溃、已 completed 工具不重复执行、pending 副作用工具不自动恢复
  - 如 Redis 集成测试已有可用 fixture，同文件或 `epsilon-boot/test/integration/test_run_checkpoint_recovery_redis_integration.py` 覆盖 Redis backend 关键路径；无 Redis fixture 时保持 Redis unit coverage，不新增外部依赖
  - **验证: 需求 2, 3, 4, 5, 6, 9**

- [x] 7.2 编写阶段二/三回归测试
  - 在 `epsilon-boot/test/integration/test_long_task_continuation_phase4_regression.py` 中创建测试，或扩展既有阶段二/三回归测试
  - 回归 Chat SSE final payload 只有整个 segmented run 结束时 `finished=true`
  - 回归 Task paused `can_continue` 与 `continue_task` 前置条件一致，特别是旧会话或复用 system message 时的工具边界 metadata
  - 回归 checkpoint 关闭时运行状态、事件流、取消、继续、审批恢复与阶段三一致
  - **验证: 需求 3, 6, 8, 9**

- [x] 7.3 编写阶段四静态边界测试
  - 在 `epsilon-boot/test/static/test_run_checkpoint_architecture_boundaries.py` 中创建测试
  - 验证 `domain/run` 不 import Redis、文件系统、FastAPI、Pydantic；FastAPI routers 不直接 import checkpoint store/recovery service；TUI 不通过 FastAPI endpoint 自调用；未引入 Celery/Temporal/LangGraph/Dapr workflow runtime
  - 验证 `docs/spec/long-task-continuation-phase4/requirement.md`、`design.md`、`tasks.md` 均声明 non-exactly-once 边界与 Observation reattach 只读语义
  - **验证: 需求 1, 2, 7, 8, 9**

- [x] 7.4 执行最终全量验证
  - 在 `epsilon-boot/` 下运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 在 `epsilon-client/` 下运行 `npm run lint` 和 `npm run build`
  - 对照 `docs/spec/long-task-continuation-phase4/requirement.md` 的需求 1-9、`design.md` 的 Property 1-8 与本文件所有任务，确认无未覆盖项、未扩大阶段四边界
  - **验证: 需求 1, 2, 3, 4, 5, 6, 7, 8, 9**

- [x] 5. 检查点 — 阶段四实现完成
  - 在 `epsilon-boot/` 下运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 在 `epsilon-client/` 下运行 `npm run lint` 和 `npm run build`
  - 通过后等待 `spec_evaluator` 评审；只有评审 PASS 后才勾选已实现任务并进入最终 `summary.md`

## 备注

- 本阶段无 SQL/DDL、数据回填或外部 workflow runtime 部署任务。
- 实现阶段必须按任务顺序推进；每完成一个实现切片后进入 evaluator review，PASS 后再勾选对应任务。
- 如果实现中发现需要改变 `Recovery_Precondition`、pending 重放策略、checkpoint 数据内容或对外契约，先回到 `design.md` 修订，再重新生成本任务清单。
- `review-log.md` 仅在实现与评审阶段追加记录；本任务拆解阶段不写入评审结论。
