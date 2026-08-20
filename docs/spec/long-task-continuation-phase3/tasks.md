# 实现计划：长任务后台运行与续跑体验阶段三

## 概述

本计划按当前仓库 DDD 分层从内到外落地阶段三：先定义 `domain/run` 领域模型、状态机和 Port，再实现应用服务、存储适配器、worker 生命周期、TUI/agent 核心 adapter，最后补齐配置、容器装配、回归测试和文档静态约束。FastAPI/Web 仅作为可选薄 adapter，不得阻碍核心 Run runtime 质量；所有 adapter 都只能调用共享的 `RunApplicationService`，不得复制 Run 状态机、claim、cancel、continue、approval resume 或 replay 规则。

验证命令以 `epsilon-boot/` 为工作目录运行 `env PYTHONPATH=src uv run --frozen pytest`。

## Tasks

- [x] 1.1 创建 `domain.run` 领域值对象
  - 在 `epsilon-boot/src/domain/run/__init__.py`、`epsilon-boot/src/domain/run/value_objects.py` 中创建模块
  - 定义 `RunStatus(StrEnum)`、`RunKind(StrEnum)`、`RunEventType(StrEnum)`，枚举值严格为 `queued`、`running`、`paused`、`awaiting_approval`、`cancel_requested`、`cancelled`、`succeeded`、`failed`、`lost` 及设计中的事件类型
  - 定义 `RunPayload`、`RunCreateRequest`、`RunLease`、`RunSnapshot`、`RunEvent`、`RunCapacityPolicy`、`EventRetentionPolicy` dataclass，字段包含 `run_id`、`kind`、`status`、`payload`、`client_request_id`、`payload_hash`、`segment_metadata`、`latest_event_cursor`、`result`、`error`、`approval_id`、`can_continue`、`terminal_reason`、`lease`、`created_at`、`updated_at`、`version`
  - 为所有公开类添加中文 docstring，并提供 `RunPayload.stable_hash() -> str` 或等价函数用于幂等冲突检测
  - _需求: 1.2, 1.4, 1.5, 1.6, 1.7, 2.1, 3.6, 6.6, 11.1, 12.7_

- [x] 1.2 编写 Run 值对象测试
  - 在 `epsilon-boot/test/domain/run/test_run_value_objects_unit.py` 中创建测试
  - 覆盖 `RunStatus` 全部枚举值、`RunPayload.stable_hash()` 对 JSON key 顺序稳定、不同 payload hash 不同、`RunSnapshot` 默认字段可序列化
  - **验证: 需求 1.4, 1.5, 2.1, 3.6, 13.1**

- [x] 1.3 创建 Run 领域异常
  - 在 `epsilon-boot/src/domain/run/exceptions.py` 中创建 `RunNotFoundError`、`RunQueueFullError`、`RunInvalidTransitionError`、`RunContinuationUnavailableError`、`RunCancelUnavailableError`、`RunLeaseConflictError`、`RunEventReplayExpiredError`、`RunPayloadValidationError`、`RunStoreUnavailableError`、`RunIdempotencyConflictError`
  - 所有异常继承 `common.exceptions.BizException`，错误码使用 `61001` 至 `61010`，错误消息使用中文且不包含敏感 payload 全文
  - _需求: 1.5, 2.10, 5.3, 6.8, 6.9, 7.5, 8.6, 8.7, 11.2, 12.7_

- [x] 1.4 编写 Run 异常测试
  - 在 `epsilon-boot/test/domain/run/test_run_exceptions_unit.py` 中创建测试
  - 断言每个异常继承 `BizException`、错误码不重复、中文 message 包含可定位信息且不包含完整 payload
  - **验证: 需求 1.5, 2.10, 5.3, 6.8, 7.5, 8.7, 11.2, 13.1**

- [x] 1.5 实现 Run 状态机
  - 在 `epsilon-boot/src/domain/run/state_machine.py` 中创建 `RunStateMachine`
  - 实现 `assert_transition(self, current: RunStatus, target: RunStatus) -> None`、`is_terminal(self, status: RunStatus) -> bool`、`can_cancel(self, status: RunStatus) -> bool`、`can_continue(self, status: RunStatus) -> bool`、`can_claim(self, status: RunStatus) -> bool`
  - 合法迁移覆盖 `queued -> running/cancelled`、`running -> paused/awaiting_approval/cancel_requested/succeeded/failed/lost`、`paused -> queued/cancel_requested`、`awaiting_approval -> queued/cancel_requested`、`cancel_requested -> cancelled/lost`，终态拒绝后续迁移
  - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 7.2, 7.3, 8.2, 8.7, 9.1_

- [x] 1.6 编写 Run 状态机测试
  - 在 `epsilon-boot/test/domain/run/test_run_state_machine_unit.py` 中创建测试
  - 覆盖所有合法迁移、终态拒绝 cancel/continue/claim、非法迁移抛 `RunInvalidTransitionError`，幂等 payload 冲突抛 `RunIdempotencyConflictError`
  - 覆盖正确性属性一的状态机前置条件：只有 `queued` 可 claim，`lost` 终态不可 claim
  - **验证: 需求 2.1-2.10, 7.2, 7.3, 8.2, 8.7, 9.1, 13.1**

- [x] 1.7 定义 Run 存储与事件 Port
  - 在 `epsilon-boot/src/domain/run/ports.py` 中创建 `RunStorePort(Protocol)`、`RunEventStorePort(Protocol)`、`RunProgressSink(Protocol)`
  - `RunStorePort` 方法签名按设计实现：`create_run`、`get_run`、`get_by_client_request_id`、`count_by_status`、`claim_next`、`refresh_lease`、`request_cancel`、`mark_succeeded`、`mark_failed`、`mark_paused`、`mark_awaiting_approval`、`mark_cancelled`、`resolve_approval_resume`、`enqueue_continue`、`mark_lost_expired_leases`
  - `RunEventStorePort` 方法签名按设计实现：`append_event`、`list_events`、`wait_events`、`trim_events`、`first_cursor`
  - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 4.1, 4.2, 4.3, 4.4, 6.1, 6.4, 6.5, 7.7, 13.1_

- [x] 1.8 编写 Run Port 静态签名测试
  - 在 `epsilon-boot/test/domain/run/test_run_ports_unit.py` 中创建测试
  - 使用 `inspect.signature` 校验 `RunStorePort`、`RunEventStorePort`、`RunProgressSink` 方法名和关键参数与设计一致
  - **验证: 需求 3.1, 3.2, 4.1, 4.3, 4.4, 6.1, 13.1**

- [x] 2.1 实现 Run 应用服务
  - 在 `epsilon-boot/src/application/run/__init__.py`、`epsilon-boot/src/application/run/run_application_service.py` 中创建应用服务
  - 实现 `RunApplicationService.create_run(self, request: RunCreateRequest) -> RunSnapshot`、`get_run(self, run_id: str) -> RunSnapshot`、`request_cancel(self, run_id: str) -> RunSnapshot`、`continue_run(self, run_id: str, model: str | None = None) -> RunSnapshot`、`resume_approval_run(self, run_id: str, decisions: list[ApprovalDecision], model: str | None = None) -> RunSnapshot`，并在领域端口定义 `ApprovalResumeStoreResult` 与 `resolve_approval_resume`、`list_events(self, run_id: str, after_cursor: int | None, limit: int) -> list[RunEvent]`、`stream_events(self, run_id: str, after_cursor: int | None) -> AsyncIterator[RunEvent]`
  - 注入 `RunStorePort`、`RunEventStorePort`、`RunCapacityPolicy`、`EventRetentionPolicy`、可选 `RunWorkerManager` 唤醒回调，不依赖 FastAPI 或 TUI
  - `create_run` 检查容量、处理 `client_request_id` + `payload_hash` 幂等冲突、写 `run_created`/`run_queued` 事件并立即返回快照
  - _需求: 1.1-1.8, 3.3, 3.4, 5.1-5.7, 6.1-6.9, 7.1-7.7, 8.1-8.7, 11.1-11.5, 12.6_

- [x] 2.2 编写 Run 应用服务测试
  - 在 `epsilon-boot/test/application/run/test_run_application_service_unit.py` 中创建测试
  - 使用内存 fake store/event store 覆盖 create 成功、相同幂等键返回既有 run、不同 payload hash 返回 409、队列满抛 `RunQueueFullError`、查询不存在抛 `RunNotFoundError`
  - 覆盖 cancel 幂等、queued cancel 直接 cancelled、终态 cancel 冲突、paused continue 入队、awaiting_approval resume approval 通过 `resolve_approval_resume` 入队或终态，不伪造 worker owner、非 paused continue 冲突、事件 replay 过期返回 `RunEventReplayExpiredError` 或 replay 事件
  - **验证: 需求 1.1-1.8, 5.1-5.7, 6.1-6.9, 7.1-7.7, 8.1-8.7, 11.1-11.5, 13.1**

- [x] 2. 检查点 — 领域与应用服务基础完成
  - 在 `epsilon-boot/` 中运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 运行项目中的全部测试用例，并要求全部通过

- [x] 3.1 实现 Run 执行协调器
  - 在 `epsilon-boot/src/application/run/run_execution_coordinator.py` 中创建 `RunExecutionCoordinator`、`RunExecutionOutcome`
  - 实现 `execute(self, snapshot: RunSnapshot, progress: RunProgressSink) -> RunExecutionOutcome`，根据 `RunKind.CHAT` 转换为 `ChatRequestVO` 或 `ChatContinueRequestVO`，根据 `RunKind.TASK` 转换为 `Task` 或 `TaskContinueRequest`
  - 首次执行只使用 create payload；paused run 继续只调用 `ChatServicePort.continue_chat` 或 `TaskAgentPort.continue_task`，不得重复追加原始用户消息
  - 将 `ChatResponseVO` / `TaskResult` 的 `status`、`terminated_reason`、`can_continue`、`approval_id`、`segment_metadata`、usage/trace/result 转换为 JSON-safe `RunExecutionOutcome`
  - _需求: 1.6, 4.5, 4.6, 4.7, 4.8, 4.9, 4.11, 4.12, 8.3, 8.4, 8.5, 9.1-9.6, 12.6_

- [x] 3.2 编写 Run 执行协调器测试
  - 在 `epsilon-boot/test/application/run/test_run_execution_coordinator_unit.py` 中创建测试
  - 使用 fake `ChatServicePort`、fake `TaskAgentPort` 验证 chat 首次执行、chat paused 继续、task 首次执行、task paused 继续、approval 等待、approval resume 后继续同一 run、failed 异常映射
  - 验证正确性属性三：continue 路径不调用 create payload，不重复用户消息，调用的是 `continue_chat` 或 `continue_task`
  - **验证: 需求 4.5-4.12, 8.3-8.5, 9.1-9.6, 13.1**

- [x] 4.1 实现 Run 配置模型
  - 在 `epsilon-boot/src/infrastructure/run/__init__.py`、`epsilon-boot/src/infrastructure/run/run_config.py` 中创建 `RunRuntimeConfig`
  - 配置键包括 `RUN_WORKER_ENABLED`、`RUN_WORKER_COUNT`、`RUN_LEASE_SECONDS`、`RUN_HEARTBEAT_INTERVAL_SECONDS`、`RUN_MAX_QUEUED_RUNS`、`RUN_MAX_RUNNING_RUNS`、`RUN_EVENT_MAX_COUNT`、`RUN_EVENT_TTL_SECONDS`、`RUN_EVENT_STREAM_WAIT_SECONDS`、`RUN_LOST_SWEEP_INTERVAL_SECONDS`
  - 使用项目现有配置工厂读取 `epsilon-boot/config.properties` 为主源，校验数值必须为正且 `heartbeat_interval < lease_seconds`
  - _需求: 6.2, 11.1, 11.4, 12.8, 13.4_

- [x] 4.2 编写 Run 配置测试
  - 在 `epsilon-boot/test/infrastructure/run/test_run_config_unit.py` 中创建测试
  - 覆盖默认值、`config.properties` 覆盖、非法数值 fail-fast、`heartbeat_interval >= lease_seconds` fail-fast
  - **验证: 需求 6.2, 11.1, 11.4, 12.8, 13.4**

- [x] 5.1 实现本地文件 Run Store 和 Event Store
  - 在 `epsilon-boot/src/infrastructure/run/local_file_run_store_adapter.py` 中创建 `LocalFileRunStoreAdapter`
  - 复用 `infrastructure.persistence.local_file.path_policy.CrossPlatformPathPolicy`、`file_lock.LockFactory`、`atomic_writer.TempFileAtomicWriter`，存储布局为 `runs/snapshots/<bucket>/<run_id>.json`、`runs/events/<bucket>/<run_id>.jsonl`、`runs/indexes/client_request/<hash>.json`
  - 同一 run 的状态、lease、事件 cursor 修改必须在同一文件锁内完成；`claim_next` 原子完成 `queued -> running` 和 lease 写入；`mark_lost_expired_leases` 将过期 running/cancel_requested run 标记 `lost`
  - `create_run` 在索引锁内比较 `payload_hash`：相同返回既有 run，不同抛幂等冲突；事件 `append_event` 保证同一 run cursor 单调递增
  - _需求: 1.4, 1.5, 3.1-3.8, 4.1-4.4, 6.2-6.8, 7.2-7.7, 8.2, 11.3, 12.1-12.3_

- [x] 5.2 编写本地文件 Run Store 契约测试
  - 在 `epsilon-boot/test/infrastructure/run/test_local_file_run_store_adapter_unit.py` 中创建测试
  - 覆盖原子创建、相同幂等键相同 payload 返回既有 run、相同幂等键不同 payload 冲突、并发 `claim_next` 只有一个成功、owner 不匹配 mark 失败、lease 过期标记 lost、事件 cursor 单调、`max_event_count` trim 后 replay 过期
  - 验证正确性属性一、属性二、属性四
  - **验证: 需求 1.4, 1.5, 3.1-3.8, 4.1-4.4, 6.2-6.8, 7.7, 11.3, 12.1-12.3, 13.1**

- [x] 6.1 实现 Redis Run Store 和 Event Store
  - 在 `epsilon-boot/src/infrastructure/run/redis_run_store_adapter.py` 中创建 `RedisRunStoreAdapter`
  - 使用现有 Redis 资源类型和序列化风格，key 布局为 `run:{run_id}:snapshot`、`run:{run_id}:events`、`run:index:client_request:{hash}`、`run:queue`、`run:running`
  - `create_run` 使用 SETNX 或 WATCH/MULTI 保证 `client_request_id` 幂等，并比较 `payload_hash`；`claim_next` 事务化领取 queued run；`refresh_lease` 和 `mark_*` 校验 owner_id；事件 list 用 `LTRIM` 与 TTL 执行保留策略
  - _需求: 1.4, 1.5, 3.8, 4.1-4.4, 6.2-6.8, 7.7, 8.2, 11.3, 12.1-12.3_

- [x] 6.2 编写 Redis Run Store 测试
  - 在 `epsilon-boot/test/infrastructure/run/test_redis_run_store_adapter_unit.py` 中创建测试
  - 参考现有 Redis 测试跳过策略，在 Redis 可用时覆盖幂等、payload 冲突、claim 原子性、owner 校验、事件 TTL/trim、lease 过期 lost
  - 验证正确性属性一、属性二、属性四与本地文件适配器一致
  - **验证: 需求 1.4, 1.5, 3.8, 4.1-4.4, 6.2-6.8, 11.3, 12.1-12.3, 13.1**

- [x] 7.1 实现 Run Worker 与 Worker Manager
  - 在 `epsilon-boot/src/infrastructure/run/run_worker.py`、`epsilon-boot/src/infrastructure/run/run_worker_manager.py` 中创建 `RunWorker`、`RunWorkerManager`
  - `RunWorker.run_once() -> bool` 调用 `claim_next(owner_id, lease_seconds)`，claim 成功后写 `run_claimed`、`segment_started`，启动 `heartbeat_loop`，调用 `RunExecutionCoordinator.execute`，根据 outcome 标记 `succeeded`、`paused`、`awaiting_approval`、`failed` 或 `cancelled`
  - `heartbeat_loop(self, run_id: str, owner_id: str) -> None` 周期刷新 lease；`RunWorkerManager.start()` 创建 `RUN_WORKER_COUNT` 个任务并启动 lost sweep；`stop()` 优雅取消后台任务；`wake_up()` 唤醒轮询等待
  - worker 在段开始前、段完成后检查 `cancel_requested`；不尝试中断正在 await 的模型或工具调用
  - _需求: 2.2-2.10, 4.1-4.12, 6.7, 7.1-7.7, 8.2-8.5, 9.1-9.6, 11.3, 12.1-12.5, 13.1, 13.4_

- [x] 7.2 编写 Run Worker 测试
  - 在 `epsilon-boot/test/infrastructure/run/test_run_worker_unit.py` 中创建测试
  - 使用 fake store/event store/coordinator 覆盖 queued claim 到 running、heartbeat 刷新、成功终态、paused 终态、awaiting_approval、执行异常 failed、running cancel 在段边界 cancelled、lease 过期 lost
  - 验证正确性属性一：多 worker 竞争同一 run 只有一个执行，其他 worker 不调用 coordinator
  - **验证: 需求 2.2-2.10, 4.1-4.12, 7.1-7.7, 8.2-8.5, 9.1-9.6, 11.3, 12.1-12.5, 13.1**

- [x] 7. 检查点 — 存储与 worker 核心完成
  - 在 `epsilon-boot/` 中运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 运行项目中的全部测试用例，并要求全部通过

- [x] 8.1 接入容器配置和资源生命周期
  - 修改 `epsilon-boot/src/application/container_config.py`
  - 注册 `RunStorePort`、`RunEventStorePort`、`RunApplicationService`、`RunExecutionCoordinator`、`RunWorkerManager`，按现有 session store backend 选择本地文件或 Redis 兼容适配器
  - 在容器 async resource 生命周期中启动和停止 `RunWorkerManager`；当 `RUN_WORKER_ENABLED=false` 时仅注册服务和 store，不启动 worker
  - 暴露队列饱和和 worker 运行状态给 readiness 或测试可观测入口，不改变现有 Chat/Task 同步入口
  - _需求: 1.8, 3.8, 4.1, 4.5, 11.1-11.5, 12.4, 12.6, 12.8, 13.2, 13.4_

- [x] 8.2 编写容器装配测试
  - 在 `epsilon-boot/test/application/test_run_container_wiring_unit.py` 中创建测试
  - 覆盖默认本地文件 store 注册、Redis backend 下 Redis store 注册、`RUN_WORKER_ENABLED=false` 不启动 worker、容器 stop 会调用 manager stop、现有 `ChatServicePort` 和 `TaskAgentPort` 解析不受影响
  - **验证: 需求 1.8, 3.8, 11.4, 12.4, 12.6, 12.8, 13.2, 13.4**

- [x]* 9.1 实现可选 FastAPI Run router
  - 在 `epsilon-boot/src/application/api/routers/runs.py` 中创建 Run HTTP adapter
  - 定义 `RunCreateRequestBody`、`ChatRunCreateBody`、`TaskRunCreateBody`、`RunSnapshotBody`、`RunEventBody`、`RunEventsResponseBody`、`RunContinueRequestBody`，并实现 `POST /api/runs`、`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/events`、`GET /api/runs/{run_id}/events/stream`、`POST /api/runs/{run_id}/cancel`、`POST /api/runs/{run_id}/continue`
  - router 只做 DTO 转换、输入校验和 `BizException` 到 JSONResponse 映射；不得直接调用 store、worker、chat service 或 task agent
  - SSE 事件 data 使用 JSON，包含 `cursor`；遇到 replay 过期发送 `replay_expired` 并提示 polling fallback
  - _需求: 1.1-1.8, 5.1-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 9.2, 11.2, 12.6, 12.7_

- [x]* 9.2 注册可选 Run router 兼容导出
  - 修改 `epsilon-boot/src/application/api/routers/__init__.py`、`epsilon-boot/src/application/api/server_app.py`、`epsilon-boot/src/application/routers/__init__.py`
  - 新增 `epsilon-boot/src/application/routers/runs.py` 作为 backward-compatible import
  - 确保新增 router 不影响现有 `/api/chat`、`/api/task/execute`、模型和 health routes
  - _需求: 1.8, 5.1, 6.1, 7.1, 8.1, 12.6, 12.7_

- [x]* 9.3 编写可选 FastAPI Run router 测试
  - 在 `epsilon-boot/test/application/routers/test_runs_router_unit.py` 中创建测试
  - 覆盖 create chat/task、payload 校验 400、幂等冲突 409、query 404、events cursor、SSE replay_expired、cancel/continue 409/404/429 映射
  - 使用 fake `RunApplicationService` 注入，断言 router 不直接依赖基础设施 adapter
  - **验证: 需求 1.1-1.8, 5.1-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 11.2, 12.6, 13.2**

- [x] 10.1 扩展 TUI runtime 的 Run 方法
  - 修改 `epsilon-boot/src/application/cli/runtime.py`
  - 在 `CliRuntime.start()` 中解析 `RunApplicationService`，新增 `_require_run_service(self) -> RunApplicationService`
  - 实现 `create_chat_run(self, message: str, state: TuiSessionState) -> RunSnapshot`、`create_task_run(self, goal: str, state: TuiSessionState) -> RunSnapshot`、`get_run(self, run_id: str) -> RunSnapshot`、`watch_run_events(self, run_id: str, after_cursor: int | None) -> AsyncIterator[RunEvent]`、`continue_run(self, run_id: str, model: str | None = None) -> RunSnapshot`、`resume_approval_run(self, run_id: str, decisions: list[ApprovalDecision], model: str | None = None) -> RunSnapshot`、`cancel_run(self, run_id: str) -> RunSnapshot`
  - 所有方法只调用 `RunApplicationService`，不得发 HTTP 请求；`client_request_id` 可由 TUI session + message hash 生成
  - _需求: 1.1-1.8, 5.2, 6.1-6.8, 7.1-7.7, 8.1-8.7, 10.1-10.8, 12.6, 12.7_

- [x] 10.2 扩展 TUI slash command router
  - 修改 `epsilon-boot/src/application/cli/commands.py`
  - 增加 `/run chat <message>`、`/run task <goal>`、`/runs`、`/run status <run_id>`、`/run watch <run_id>`、`/run continue <run_id>`、`/run approve <run_id>`、`/run cancel <run_id>` 命令解析
  - `/run approve` 通过 `RunApplicationService.resume_approval_run` 恢复 awaiting_approval run；命令输出包含 run_id、status、can_continue、latest cursor、错误摘要；命令错误使用 `domain.run.exceptions` 映射为可读中文提示
  - _需求: 1.1-1.8, 5.2-5.7, 6.1-6.8, 7.1-7.7, 8.1-8.7, 10.1-10.8, 12.6, 12.7_

- [x] 10.3 扩展 TUI Run View 与取消行为
  - 修改 `epsilon-boot/src/application/cli/tui.py`、`epsilon-boot/src/application/cli/tui.css`
  - 增加 run 面板渲染：展示 `queued`、`running`、`paused`、`awaiting_approval`、`cancel_requested`、`cancelled`、`succeeded`、`failed`、`lost` 状态、事件日志、段进度、错误摘要
  - `/run watch` 或界面 watch 任务使用 cursor 订阅事件；`replay_expired` 时调用 `get_run` 补快照并提示事件历史已过期
  - Ctrl+C 在 run watch/active run 场景只调用 `cancel_run`，不得直接取消 worker task；普通同步聊天流保留现有取消语义
  - _需求: 6.1-6.10, 7.1-7.7, 8.1-8.7, 9.2-9.6, 10.1-10.8, 12.6, 12.7_

- [x] 10.4 编写 TUI adapter 测试
  - 修改 `epsilon-boot/test/application/cli/test_runtime.py`、`epsilon-boot/test/application/cli/test_commands.py`，新增 `epsilon-boot/test/application/cli/test_tui_run_view.py`
  - 覆盖 runtime 不发 HTTP、slash command 解析、watch replay 过期回退、continue 仅允许 paused、cancel 终态冲突、Ctrl+C active run 调用 `request_cancel`
  - **验证: 需求 1.1-1.8, 5.2-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 10.1-10.8, 12.6, 13.2**

- [x]* 11.1 扩展可选前端 API client
  - 修改 `epsilon-client/src/lib/chat-api.ts`
  - 新增 TypeScript 类型 `RunStatus`、`RunKind`、`RunSnapshot`、`RunEvent`、`RunCreateRequest`、`RunEventsResponse`，以及函数 `createRun`、`fetchRun`、`fetchRunEvents`、`streamRunEvents`、`cancelRun`、`continueRun`
  - `streamRunEvents` 支持 cursor 重连和 `replay_expired` 回调，不把控制事件拼进 assistant text
  - _需求: 1.1-1.8, 5.1-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 10.1-10.8, 12.6_

- [x] 11. 检查点 — 第三个实现批次完成
  - 在 `epsilon-boot/` 中运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 如果本批次执行了可选 Web 前端任务，则在 `epsilon-client/` 中运行 `npm run lint` 和 `npm run build`
  - 运行项目中的全部测试用例，并要求全部通过

- [x]* 11.2 实现可选前端 Run View 组件
  - 新增 `epsilon-client/src/components/run/run-view.tsx`、`epsilon-client/src/components/run/run-event-list.tsx`、`epsilon-client/src/hooks/use-run.ts`
  - 展示 Run_ID、Run_Status、Segment_Metadata、latest Run_Event summary、预算摘要、terminal result/error；queued/running 显示 active progress，paused 且 `can_continue` 显示 continue，queued/running 显示 cancel，terminal 禁用操作
  - 支持 SSE 事件流和 polling fallback；awaiting_approval 保留既有 HITL 用户体验入口或提示
  - _需求: 5.1-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 9.2-9.6, 10.1-10.8, 12.6_

- [x]* 11.3 集成可选前端 Run View 到现有页面
  - 修改 `epsilon-client/src/app/page.tsx`、`epsilon-client/src/components/chat/chat-panel.tsx`、`epsilon-client/src/components/task/task-workspace.tsx`
  - 增加后台运行入口或运行面板区域，同时保留现有同步 Chat_Flow 和 Task_Flow 默认行为，不把同步按钮静默改为后台执行
  - 移动端和桌面端布局不得出现文本重叠，长错误摘要和长结果内容需要截断或滚动
  - _需求: 1.8, 10.1-10.8, 12.6_

- [x]* 11.4 编写可选前端静态契约测试
  - 在 `epsilon-boot/test/application/test_long_task_phase3_frontend_contract_static.py` 中创建静态测试
  - 检查 `chat-api.ts` 暴露 Run 类型和函数、Run View 处理全部 `RunStatus`、包含 `replay_expired` fallback、现有同步 chat/task API 函数仍存在
  - **验证: 需求 5.1-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 10.1-10.8, 12.6, 13.2**

- [x] 12.1 编写阶段三端到端后端集成测试
  - 在 `epsilon-boot/test/application/test_long_task_phase3_integration.py` 中创建测试
  - 使用 fake chat/task 服务或轻量模型 adapter 覆盖 create -> worker run -> query -> events -> terminal、paused -> continue -> succeeded、running cancel -> cancelled、approval -> awaiting_approval
  - 验证现有 `/api/chat`、`/api/task/execute`、chat continue、task continue 行为保持阶段二兼容
  - **验证: 需求 1.1-1.8, 2.1-2.10, 4.1-4.12, 5.1-5.7, 6.1-6.10, 7.1-7.7, 8.1-8.7, 9.1-9.6, 12.1-12.6, 13.2**

- [x] 12.2 补充可观测性和日志字段
  - 修改 `epsilon-boot/src/infrastructure/run/run_worker.py`、`epsilon-boot/src/application/run/run_application_service.py`、必要时修改 `epsilon-boot/src/domain/health/aggregator.py` 或 readiness 相关文件
  - 所有关键日志带 `run_id`、`run_kind`、`run_status`、`worker_id`、`client_request_id`，不记录完整用户消息、工具参数敏感内容或模型响应全文
  - 暴露 queued/running 数、claim 成功数、lease 过期数、lost 数、cancel 请求数、run 执行耗时、replay 过期次数，至少提供测试可断言的内部状态或 readiness 扩展
  - _需求: 11.5, 13.3, 13.4_

- [x] 12.3 编写可观测性测试
  - 在 `epsilon-boot/test/application/test_long_task_phase3_observability_unit.py` 中创建测试
  - 使用 `caplog` 或 fake metrics collector 验证日志字段存在、敏感 payload 不出现、队列饱和与执行失败可区分、lost sweep 有可见信号
  - **验证: 需求 11.5, 13.3, 13.4**

- [x] 12.4 更新配置样例和运行说明
  - 修改 `epsilon-boot/config.properties`，添加阶段三 Run runtime 配置键及安全默认值
  - 如项目已有 docs 运行说明，更新 `docs/plan.md` 或阶段三 spec 备注，说明本期不提供 checkpoint recovery、服务重启未完成 run 会进入 lost、TUI/agent 应用通过共享应用服务接入，FastAPI/Web 仅为可选薄 adapter
  - 不新增 Celery、Temporal、LangGraph、Dapr Workflow 等依赖，不修改 `pyproject.toml` 添加 workflow runtime；说明 FastAPI/Web 是可选薄 adapter，核心质量门槛是 RunApplicationService、worker、store、TUI/agent 应用体验
  - _需求: 11.1, 11.4, 12.1-12.8, 13.4_

- [x] 12.5 编写阶段三静态边界测试
  - 在 `epsilon-boot/test/application/test_long_task_phase3_architecture_static.py` 中创建测试
  - 断言 `domain/run` 不导入 `application`、`infrastructure`、FastAPI、Redis 客户端；若实现可选 FastAPI adapter，则 `application/api/routers/runs.py` 不直接导入 `infrastructure.run.*`；TUI runtime 不调用 HTTP client 或 `/api/runs`
  - 断言 `pyproject.toml` 未新增 Celery、Temporal、LangGraph、Dapr Workflow 依赖
  - **验证: 需求 3.9, 4.5, 12.1-12.7, 13.2**

- [x] 12.6 执行最终全量验证
  - 在 `epsilon-boot/` 中运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 如果本批次执行了可选 Web 前端任务，则在 `epsilon-client/` 中运行 `npm run lint` 和 `npm run build`
  - 核对 `docs/spec/long-task-continuation-phase3/requirement.md`、`design.md`、`tasks.md` 与实际实现一致，未扩大阶段三边界
  - **验证: 需求 1.1-13.4**

- [x] 12. 检查点 — 阶段三实现完成
  - 在 `epsilon-boot/` 中运行 `env PYTHONPATH=src uv run --frozen pytest`
  - 如果本批次执行了可选 Web 前端任务，则在 `epsilon-client/` 中运行 `npm run lint` 和 `npm run build`
  - 运行项目中的全部测试用例，并要求全部通过

## 备注

- 本计划不包含 SQL DDL、数据回填脚本或新 workflow runtime 依赖；Run Store 仅按设计落地本地文件与 Redis 兼容实现。
- `TUI adapter 契约` 是核心客户端契约；可选 `FastAPI adapter 契约` 若实现也必须共享 `RunApplicationService`，任何实现任务发现需要在 adapter 内复制状态机、approval resume 或直接调用 store/worker，都应先回到设计阶段修正。
- `Client_Request_ID` 幂等必须比较 `payload_hash`；相同键相同 payload 返回既有 run，相同键不同 payload 返回客户端可见冲突。
- worker 对 in-flight 模型调用或工具调用不提供抢占式中断；取消只在段边界收敛。
- 服务重启或 worker lease 过期后的未完成 run 按设计进入 `lost`，不自动重新 claim。
