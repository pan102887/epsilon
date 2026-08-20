# 需求文档：Long Task Continuation Phase 3

## 简介

阶段一 `long-task-continuation-phase1` 已让 Chat_Flow 与 Task_Flow 能区分完成、暂停和可继续；阶段二 `long-task-continuation-phase2` 已把一次长任务拆成请求内有限的 Segmented_Run，并通过全局预算、自动续跑、人工续跑、停止原因和 adapter 事件流/客户端视图展示控制执行边界。当前剩余核心问题是：长任务仍绑定在一次 adapter 请求或事件订阅生命周期内，客户端断连、网关超时、浏览器刷新或服务进程重启都会让调用方难以确认任务到底处于何种状态。

本特性实现 `docs/plan.md` 的阶段三：长任务运行时。目标是引入后台 Run 管理，把复杂任务抽象为可查询、可观察、可取消、可继续的 Run_Runtime。业内主流实践通常不会要求长任务一直占用同步 adapter 连接：OpenAI Background mode 使用后台对象、状态轮询、取消和可恢复事件流；FastAPI 官方也提示重计算型后台任务应考虑比原生 BackgroundTasks 更完整的队列或执行体系；LangGraph 和 Temporal 则展示了更强的 checkpoint / durable execution 能力。阶段三采用对当前项目风险最低的子集：应用内后台 Run runtime + 状态/事件存储端口 + adapter-neutral 事件订阅/轮询查看，不引入 durable workflow runtime。

本期范围包括：

- 为 Chat_Flow 与 Task_Flow 新增后台 Long_Task_Run 入口，创建后立即返回 `run_id`。
- 新增 Run_Status、Run_Event、Run_Result_Snapshot、Run_Cancel_Request 和 Run_Continuation_Request 等领域语义。
- 在后台执行 Run，并复用阶段二 Segmented_Run、预算、停止原因、trace、usage、`can_continue` 语义。
- 提供 Run 查询、事件订阅、取消、继续和历史事件查看接口。
- TUI/agent 应用新增核心 Run 视图，展示运行状态、段进度、预算摘要、事件流、取消和继续动作；FastAPI/Web 仅作为薄 adapter，可在不阻碍核心质量时落地。
- 保持现有同步 Chat/Task 与阶段一/二 continue 接口兼容。

本期明确不包括：

- 持久化检查点、服务重启后从中间步骤恢复、网络断开后继续执行同一个模型/工具调用。
- 工具调用副作用去重、补偿事务、exactly-once 执行保障。
- 引入 Celery、Temporal、LangGraph、Dapr Workflow 等新运行时依赖。
- 分布式多 worker 协调、跨进程抢占式调度、死信队列和任务重试策略。
- 更细粒度的工具风险分级、金额预算、动态任务分类和智能调度策略。

阶段三的最低可靠性承诺是：只要当前应用进程和配置的 Run_Store 可用，调用方可以通过 `run_id` 观察 Run 状态、事件和结果，并可以请求取消或继续。若进程重启导致未完成 Run 无法确认，系统必须显式标记为 `lost` 或等价终态，而不是伪装成 `succeeded`。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 阶段三长任务运行时 | Phase_Three_Run_Runtime | `docs/plan.md` 第三阶段能力，聚焦后台 Run 管理、状态查询、事件订阅、取消和继续。 |
| 后台运行 | Long_Task_Run | 一个由系统创建并在 adapter 请求返回后继续推进的长任务运行对象。 |
| 运行标识 | Run_ID | Long_Task_Run 的唯一标识，供查询、事件订阅、取消和继续使用。 |
| 运行类型 | Run_Kind | Long_Task_Run 的业务入口类型，当前包括 Chat_Run 与 Task_Run。 |
| 聊天运行 | Chat_Run | 由 Chat_Flow 创建和推进的 Long_Task_Run。 |
| 任务运行 | Task_Run | 由 Task_Flow 创建和推进的 Long_Task_Run。 |
| 聊天流程 | Chat_Flow | 现有聊天执行入口，包括同步聊天、流式聊天和聊天继续能力。 |
| 任务流程 | Task_Flow | 现有任务执行入口，包括任务执行、任务继续、trace 与工具边界控制。 |
| 运行状态 | Run_Status | Long_Task_Run 的当前状态，例如 queued、running、paused、awaiting_approval、cancelled、succeeded、failed、lost。 |
| 运行状态机 | Run_State_Machine | 约束 Run_Status 合法迁移的规则集合。 |
| 运行事件 | Run_Event | Long_Task_Run 生命周期中的可订阅事件，例如创建、开始、段开始、段结束、暂停、取消、成功、失败。 |
| 事件游标 | Event_Cursor | Run_Event 的单调递增位置，用于事件订阅断线后追赶历史事件。 |
| 事件保留策略 | Event_Retention_Policy | Run_Event_Store 对历史 Run_Event 的保留窗口，包括 TTL、最大事件数和过期后的客户端降级行为。 |
| 运行快照 | Run_Result_Snapshot | Long_Task_Run 当前可查询的状态、预算、停止原因、错误和结果摘要。 |
| 运行存储 | Run_Store | 保存 Long_Task_Run 元数据、Run_Status、Run_Result_Snapshot 和运行控制标记的端口。 |
| 事件存储 | Run_Event_Store | 保存并按 Event_Cursor 查询 Run_Event 的端口。 |
| 后台执行器 | Run_Worker | 从 queued 状态获取 Long_Task_Run 并在后台推进执行的应用内执行组件。 |
| 运行领取 | Run_Claim | Run_Worker 原子取得 Long_Task_Run 执行权的操作。 |
| 运行租约 | Run_Lease | Run_Worker 对 Long_Task_Run 的有时限执行权，包含 owner 和 lease_until。 |
| 运行心跳 | Run_Heartbeat | Run_Worker 在执行期间周期性刷新 Run_Lease 的信号。 |
| 运行创建请求 | Run_Create_Request | 创建 Long_Task_Run 的 adapter 请求。 |
| 客户端请求标识 | Client_Request_ID | 调用方为 Run_Create_Request 提供的幂等键，用于避免客户端重试创建重复 Long_Task_Run。 |
| 运行取消请求 | Run_Cancel_Request | 请求停止 Long_Task_Run 后续推进的幂等命令。 |
| 运行继续请求 | Run_Continuation_Request | 对 paused 或可继续的 Long_Task_Run 发起后续执行的命令。 |
| 运行事件流 | Run_Event_Stream | 由应用服务暴露的只读事件订阅能力；FastAPI 可映射为 SSE，TUI 可映射为 watch 面板。 |
| 分段运行 | Segmented_Run | 阶段二已实现的请求内有限分段执行单元，阶段三 Run_Worker 应复用其预算和停止语义。 |
| 分段元数据 | Segment_Metadata | 阶段二暴露的段数、预算、停止原因和自动续跑信息。 |
| 可继续标记 | Can_Continue_Flag | 表示 Long_Task_Run 是否满足继续前置条件的布尔语义。 |
| 审批等待状态 | Approval_Wait_State | Long_Task_Run 等待 HITL 审批恢复的状态。 |
| 人工审批 | HITL | human-in-the-loop 工具审批机制，阶段三必须沿用既有审批策略。 |
| Agent 执行 | Agent_Execution | 由现有 AgentPort 推进模型调用、工具调用和上下文更新的执行过程。 |
| 取消请求态 | Cancel_Requested_State | Long_Task_Run 已收到取消请求但后台执行器尚未完成停止收敛的中间状态。 |
| 丢失状态 | Lost_State | 服务重启或执行器异常后，系统无法确认未完成 Long_Task_Run 是否仍在推进时暴露的终态。 |
| 终态运行状态 | Terminal_Run_Status | 不再接受取消或继续推进的 Run_Status，包括 cancelled、succeeded、failed、lost。 |
| 运行容量策略 | Run_Capacity_Policy | 控制后台运行队列和执行并发的配置化策略，包括最大排队数、最大运行数和队列满时的拒绝行为。 |
| 队列满状态 | Queue_Full_State | Run_Capacity_Policy 拒绝新 Run_Create_Request 时暴露给调用方的状态或错误。 |
| 兼容同步入口 | Synchronous_Compatibility_Entry | 现有 `/api/chat`、`/api/task/execute` 与 continue 接口，不因阶段三改变默认语义。 |
| 运行客户端视图 | Run_Client_View | TUI/agent 应用为核心，FastAPI/Web 为可选 adapter，用于展示 Run_Status、Run_Event、Segment_Metadata、取消和继续动作的 UI。 |

## 需求

### 需求 1：创建后台 Long_Task_Run

**用户故事：** 作为 Run 调用方，我希望创建长任务时立即获得 Run_ID，以便不再依赖一次 adapter 请求等待任务完成。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL provide a Run_Create_Request entry for Chat_Run and Task_Run.
2. WHEN Run_Create_Request is accepted, THE Phase_Three_Run_Runtime SHALL create a Long_Task_Run with a unique Run_ID.
3. WHEN Long_Task_Run is created, THE Phase_Three_Run_Runtime SHALL return Run_ID and initial Run_Status without waiting for the full task result.
4. WHEN Run_Create_Request includes Client_Request_ID and an equivalent Long_Task_Run already exists, THE Phase_Three_Run_Runtime SHALL return the existing Run_ID instead of creating a duplicate Long_Task_Run.
5. WHEN Run_Create_Request includes Client_Request_ID that conflicts with a different request payload, THE Phase_Three_Run_Runtime SHALL reject the request with a client-visible conflict.
6. THE Long_Task_Run SHALL preserve the original session_id, model, prompt context, tool boundary, and request metadata needed by the selected Run_Kind.
7. FOR ALL Long_Task_Run objects, THE Run_Kind SHALL be either Chat_Run or Task_Run unless a later requirement extends the glossary.
8. THE Synchronous_Compatibility_Entry SHALL preserve existing phase two behavior and SHALL NOT be silently converted into background execution.
9. THE Phase_Three_Run_Runtime SHALL treat FastAPI as an optional thin adapter; IF FastAPI blocks core Run runtime quality, THEN implementation MAY skip FastAPI endpoints while preserving RunApplicationService and TUI/agent adapter capability.
10. THE TUI/agent adapter SHALL access Long_Task_Run through the same RunApplicationService as any other adapter and SHALL NOT call FastAPI HTTP endpoints internally.

### 需求 2：定义 Run_Status 与状态机

**用户故事：** 作为维护者，我希望后台任务拥有明确状态机，以便应用服务、TUI/agent adapter、可选 FastAPI/Web adapter 和测试能一致判断任务是否可继续、可取消或已结束。

#### 验收标准

1. THE Run_State_Machine SHALL define Run_Status values `queued`, `running`, `paused`, `awaiting_approval`, `cancel_requested`, `cancelled`, `succeeded`, `failed`, and `lost`.
2. WHEN Long_Task_Run is created, THE Run_State_Machine SHALL set Run_Status to `queued` before Run_Worker starts execution.
3. WHEN Run_Worker starts Long_Task_Run execution, THE Run_State_Machine SHALL transition Run_Status from `queued` to `running`.
4. WHEN Segmented_Run stops with a continue-capable pause, THE Run_State_Machine SHALL transition Run_Status to `paused`.
5. WHEN Long_Task_Run waits for HITL approval, THE Run_State_Machine SHALL transition Run_Status to `awaiting_approval`.
6. WHEN Run_Cancel_Request is accepted for a queued Long_Task_Run, THE Run_State_Machine SHALL transition Run_Status directly to `cancelled`; WHEN accepted for a running, paused, or awaiting_approval Long_Task_Run, THE Run_State_Machine SHALL transition Run_Status to `cancel_requested` before worker-side convergence.
7. WHEN Long_Task_Run reaches a successful terminal result, THE Run_State_Machine SHALL transition Run_Status to `succeeded`.
8. WHEN Long_Task_Run reaches an unrecoverable execution error, THE Run_State_Machine SHALL transition Run_Status to `failed`.
9. WHEN Phase_Three_Run_Runtime cannot determine the fate of an unfinished Long_Task_Run after process restart or worker loss, THE Run_State_Machine SHALL expose Run_Status `lost`.
10. FOR ALL Terminal_Run_Status values, THE Run_State_Machine SHALL reject new Run_Cancel_Request and Run_Continuation_Request as no-op or client-visible conflict according to existing API error style.

### 需求 3：持久化 Run 元数据和事件

**用户故事：** 作为 Run 调用方，我希望能通过 Run_ID 查询任务当前状态和历史事件，以便在 TUI 会话刷新、页面刷新或事件订阅断线后恢复可见性。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL define Run_Store as a domain port for Long_Task_Run metadata and Run_Result_Snapshot persistence.
2. THE Phase_Three_Run_Runtime SHALL define Run_Event_Store as a domain port for append-only Run_Event persistence.
3. WHEN Run_Status changes, THE Run_Store SHALL persist the latest Run_Result_Snapshot.
4. WHEN Run_Status changes, THE Run_Event_Store SHALL append a Run_Event with Event_Cursor.
5. FOR ALL Run_Event entries of the same Long_Task_Run, THE Event_Cursor SHALL be monotonically increasing.
6. THE Run_Result_Snapshot SHALL include Run_ID, Run_Kind, Run_Status, created_at, updated_at, Segment_Metadata, Can_Continue_Flag, error summary, latest Event_Cursor, Client_Request_ID when present, and Run_Lease metadata when active.
7. THE Run_Event_Store SHALL apply Event_Retention_Policy consistently for Run_Event lookup and Run_Event_Stream replay.
8. THE Run_Store SHALL support local-file and Redis-compatible implementation strategies consistent with existing SessionContextStorePort patterns.
9. THE Phase_Three_Run_Runtime SHALL NOT require SQL DDL unless the design phase explicitly selects the existing SQLAlchemy database stack for Run_Store.

### 需求 4：后台执行器推进 Run

**用户故事：** 作为用户，我希望长任务在请求返回后继续执行，以便复杂任务不被 HTTP 超时或浏览器刷新打断可见性。

#### 验收标准

1. THE Run_Worker SHALL perform Run_Claim atomically before executing queued Long_Task_Run objects outside the original HTTP request handler.
2. WHEN Run_Claim succeeds, THE Run_Worker SHALL persist Run_Lease with owner and lease_until before starting Agent_Execution.
3. WHILE Long_Task_Run IN Run_Status `running`, THE Run_Worker SHALL refresh Run_Lease through Run_Heartbeat.
4. IF Run_Lease expires before Terminal_Run_Status, THEN THE Phase_Three_Run_Runtime SHALL mark Long_Task_Run as Lost_State or make it safely claimable according to the design-selected policy, but SHALL NOT allow silent concurrent execution.
5. THE Run_Worker SHALL reuse the existing Chat_Flow or Task_Flow execution capability rather than reimplementing Agent loop logic.
6. THE Run_Worker SHALL reuse Segmented_Run budget, stop reason, trace, usage, and Can_Continue_Flag semantics.
7. WHEN Run_Worker finishes a segment, THE Run_Worker SHALL update Run_Result_Snapshot and append Run_Event.
8. WHEN Segmented_Run reaches a pause that allows manual continuation, THE Run_Worker SHALL stop automatic execution and expose Run_Status `paused`.
9. WHEN Segmented_Run reaches completion, THE Run_Worker SHALL expose Run_Status `succeeded`.
10. WHEN Agent_Execution raises an unrecoverable exception, THE Run_Worker SHALL expose Run_Status `failed` with a client-visible error summary.
11. THE Run_Worker SHALL NOT broaden Task_Flow Tool_Access_Boundary beyond the original request or saved SystemMessage metadata.
12. THE Run_Worker SHALL NOT increase existing single-segment round limits because execution moved into the background.

### 需求 5：查询 Run 状态和结果

**用户故事：** 作为客户端，我希望按 Run_ID 查询任务状态，以便用轮询方式展示进度并在事件流不可用时降级。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL expose an adapter-neutral query capability for Run_Result_Snapshot by Run_ID through RunApplicationService; FastAPI HTTP endpoint is optional and SHALL remain a thin adapter if implemented.
2. WHEN Run_ID exists, THE Phase_Three_Run_Runtime SHALL return the latest Run_Result_Snapshot.
3. WHEN Run_ID does not exist, THE Phase_Three_Run_Runtime SHALL return a client-visible not-found error consistent with existing API error style.
4. THE Run_Result_Snapshot SHALL expose enough Segment_Metadata for callers to display segment_count, budget usage, and segment_stop_reason.
5. THE Run_Result_Snapshot SHALL expose Can_Continue_Flag when Run_Status is `paused` or `awaiting_approval`.
6. THE Run_Result_Snapshot SHALL expose terminal output summary when Run_Status is `succeeded` or `failed`.
7. THE Phase_Three_Run_Runtime SHALL support polling without mutating Long_Task_Run state.

### 需求 6：订阅 Run_Event_Stream 并支持断线追赶

**用户故事：** 作为客户端用户，我希望通过 TUI/agent 应用实时看到后台任务进展，并在 事件订阅断线后继续从上次位置追赶事件，以便长任务体验稳定。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL expose Run_Event_Stream by Run_ID.
2. THE Phase_Three_Run_Runtime SHALL define configurable Event_Retention_Policy with at least max_event_count and ttl_seconds defaults.
3. WHEN Run_Event_Stream starts without Event_Cursor, THE Phase_Three_Run_Runtime SHALL emit available Run_Event entries from the beginning or from the current retention floor.
4. WHEN Run_Event_Stream starts with Event_Cursor, THE Phase_Three_Run_Runtime SHALL emit Run_Event entries after that cursor.
5. WHEN new Run_Event entries are appended, THE Run_Event_Stream SHALL deliver them to connected subscribers.
6. FOR ALL Run_Event_Stream payloads, THE Event_Cursor SHALL be included.
7. WHEN Run_Event_Stream reaches Terminal_Run_Status, THE Run_Event_Stream SHALL emit the terminal Run_Event and then close cleanly.
8. WHEN Run_Event_Stream cannot continue because Run_Event entries have expired, THE Phase_Three_Run_Runtime SHALL return a client-visible replay-expired error and direct the client to Run_Result_Snapshot polling fallback.
9. WHEN Run_Event_Stream cannot continue because Run_ID is unknown, THE Phase_Three_Run_Runtime SHALL return a client-visible not-found error rather than silently restarting the task.
10. THE Run_Event_Stream SHALL NOT append control payload content into Chat assistant text.

### 需求 7：取消后台 Run

**用户故事：** 作为用户，我希望能取消仍在运行的长任务，以便控制成本和避免无意义的后台执行。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL expose Run_Cancel_Request by Run_ID.
2. WHEN Run_Cancel_Request targets `queued` Long_Task_Run, THE Run_State_Machine SHALL transition Run_Status to `cancelled` before execution starts.
3. WHEN Run_Cancel_Request targets `running` Long_Task_Run, THE Run_State_Machine SHALL record Cancel_Requested_State.
4. WHEN Run_Worker observes Cancel_Requested_State before starting a next segment, THE Run_Worker SHALL stop execution and transition Run_Status to `cancelled`.
5. WHEN Run_Cancel_Request is repeated for the same Long_Task_Run, THE Phase_Three_Run_Runtime SHALL handle it idempotently.
6. THE Phase_Three_Run_Runtime SHALL NOT guarantee interruption of an already in-flight model call or tool call during phase three.
7. WHEN cancellation completes, THE Run_Event_Store SHALL append a terminal cancellation Run_Event.

### 需求 8：继续 paused Run

**用户故事：** 作为用户，我希望对暂停的后台任务发起继续，以便在人工确认成本或风险后继续推进下一段。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL expose Run_Continuation_Request by Run_ID.
2. WHEN Long_Task_Run IN Run_Status `paused` and Can_Continue_Flag is true, THE Run_Continuation_Request SHALL enqueue or start further execution for the same Run_ID.
3. WHEN Run_Continuation_Request is accepted, THE Phase_Three_Run_Runtime SHALL preserve Continue_Request semantics from phase one and phase two.
4. THE Run_Continuation_Request SHALL NOT append a duplicate user message to Chat_Flow or Task_Flow context.
5. THE Run_Continuation_Request SHALL NOT broaden Task_Flow Tool_Access_Boundary.
6. IF Long_Task_Run IN Run_Status `paused` and Can_Continue_Flag is false, THEN THE Phase_Three_Run_Runtime SHALL reject Run_Continuation_Request with a client-visible error.
7. IF Long_Task_Run IN Terminal_Run_Status, THEN THE Phase_Three_Run_Runtime SHALL reject Run_Continuation_Request with a client-visible conflict.

### 需求 9：处理审批等待状态

**用户故事：** 作为维护者，我希望后台 Run 正确暴露 HITL 审批等待，以便阶段三不会绕过现有人工审批策略。

#### 验收标准

1. WHEN Agent_Execution returns Approval_Wait_State, THE Run_State_Machine SHALL transition Long_Task_Run to `awaiting_approval`.
2. WHEN Long_Task_Run IN Run_Status `awaiting_approval`, THE Run_Result_Snapshot SHALL include approval metadata needed by existing HITL resume flow.
3. THE Phase_Three_Run_Runtime SHALL NOT treat Approval_Wait_State as `paused` caused by max rounds.
4. THE Run_Worker SHALL stop automatic execution while Long_Task_Run IN Run_Status `awaiting_approval`.
5. THE Phase_Three_Run_Runtime SHALL preserve existing HITL approval policy and SHALL NOT auto-approve actions.
6. WHEN approval resumes successfully through existing approval flow or a phase-three-specific continuation path selected in design, THE Long_Task_Run SHALL continue with the same Run_ID and SHALL transition from `awaiting_approval` to `queued` through RunApplicationService, not through adapter-local state mutation.

### 需求 10：Run 客户端视图

**用户故事：** 作为客户端用户，我希望在 TUI/agent 应用看到后台任务的运行状态、事件和操作按钮，以便理解任务是在排队、运行、暂停、等待审批、取消还是完成。

#### 验收标准

1. THE Run_Client_View SHALL display Run_ID, Run_Status, Segment_Metadata, and latest Run_Event summary.
2. WHEN Run_Status is `queued` or `running`, THE Run_Client_View SHALL show active progress state.
3. WHEN Run_Status is `paused` and Can_Continue_Flag is true, THE Run_Client_View SHALL expose a continue action.
4. WHEN Run_Status is `queued` or `running`, THE Run_Client_View SHALL expose a cancel action.
5. WHEN Run_Status is `awaiting_approval`, THE Run_Client_View SHALL preserve existing HITL approval user experience.
6. WHEN Run_Status is Terminal_Run_Status, THE Run_Client_View SHALL display final output summary or error summary.
7. THE Run_Client_View SHALL support Run_Event_Stream and polling fallback.
8. THE Run_Client_View SHALL NOT remove existing Chat_Flow and Task_Flow synchronous UI behavior.

### 需求 11：运行容量和背压

**用户故事：** 作为运维开发者，我希望后台 Run 有明确容量边界，以便应用内 runtime 不会因无限排队或无限并发而拖垮服务。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL define Run_Capacity_Policy with configurable max_queued_runs and max_running_runs.
2. WHEN Run_Create_Request would exceed max_queued_runs, THE Phase_Three_Run_Runtime SHALL reject the request with Queue_Full_State using a client-visible error.
3. WHEN Run_Worker observes running count at max_running_runs, THE Run_Worker SHALL leave additional Long_Task_Run objects in Run_Status `queued`.
4. FOR ALL Run_Capacity_Policy configuration keys, THE Phase_Three_Run_Runtime SHALL use `epsilon-boot/config.properties` as the primary source.
5. THE Phase_Three_Run_Runtime SHALL expose enough status or health data for tests and operators to distinguish queue saturation from execution failure.

### 需求 12：阶段边界和兼容性

**用户故事：** 作为项目维护者，我希望阶段三边界清晰，以便后台 Run 能稳定落地而不提前引入 durable execution 的复杂度。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL NOT introduce persistent checkpoint recovery for in-flight Agent calls.
2. THE Phase_Three_Run_Runtime SHALL NOT guarantee service-restart recovery for unfinished Long_Task_Run.
3. WHEN unfinished Long_Task_Run cannot be safely resumed after restart, THE Phase_Three_Run_Runtime SHALL expose Lost_State.
4. THE Phase_Three_Run_Runtime SHALL NOT introduce Celery, Temporal, LangGraph, Dapr Workflow, or another workflow runtime dependency.
5. THE Phase_Three_Run_Runtime SHALL NOT implement tool side-effect deduplication or compensation.
6. THE Phase_Three_Run_Runtime SHALL preserve Synchronous_Compatibility_Entry behavior for existing API callers.
7. FOR ALL new public modules, classes, functions, methods, and API models, THE Phase_Three_Run_Runtime SHALL include Chinese docstrings consistent with repository documentation rules.
8. FOR ALL new configuration keys, THE Phase_Three_Run_Runtime SHALL use `epsilon-boot/config.properties` as the primary source.

### 需求 13：可测试性和可观测性

**用户故事：** 作为维护者，我希望阶段三有明确测试和观测信号，以便验证后台 Run 管理不会破坏阶段二能力。

#### 验收标准

1. THE Phase_Three_Run_Runtime SHALL be verifiable with focused backend tests for Run_State_Machine, Run_Store, Run_Event_Store, Run_Worker, Run_Claim, Run_Lease, Run_Heartbeat, Run_Capacity_Policy, Run_Cancel_Request, and Run_Continuation_Request.
2. THE Phase_Three_Run_Runtime SHALL be verifiable with integration tests covering Chat_Run and Task_Run creation, status query, event stream, cancellation, pause, and continuation.
3. THE Phase_Three_Run_Runtime SHALL include regression tests proving Synchronous_Compatibility_Entry still follows phase two behavior.
4. THE Phase_Three_Run_Runtime SHALL include TUI adapter verification for Run_Client_View contracts; optional FastAPI/Web static verification only when those adapters are implemented.
5. THE Run_Worker SHALL log Run_ID, Run_Kind, Run_Status transition, segment count, and terminal reason in a structured form consistent with existing logging practices.
6. THE Run_Event_Store SHALL expose enough data for tests to assert Event_Cursor order, Event_Retention_Policy behavior, replay-expired handling, and terminal event delivery.
7. THE Phase_Three_Run_Runtime SHALL pass existing backend full test suite and frontend lint / TypeScript verification before downstream implementation is considered complete.
