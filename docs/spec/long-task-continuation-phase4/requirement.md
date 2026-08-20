# 需求文档：长任务持久化检查点阶段四

## 简介

阶段四在已完成的阶段三后台 `Run_Runtime` 基础上补齐 `Durable_Checkpoint_Recovery`，让后台 `Chat_Run` 与 `Task_Run` 在服务重启、worker 中断或租约过期后，可以从最近安全边界继续，而不是默认进入 `lost` 或从头重跑。网络断开和前端刷新不属于执行恢复问题，本阶段只要求其继续通过 `Run_Snapshot`、`Run_Event_Stream` replay 与 polling fallback 完成观察恢复。

本阶段采用现有 DDD + 六边形架构继续演进：领域层定义检查点、工具结果账本与恢复端口，基础设施层分别实现本地文件与 Redis 适配器，应用层和 worker 只编排恢复流程。阶段四不引入 Celery、Temporal、LangGraph、Dapr Workflow 或其他 durable workflow runtime，也不把当前系统改造成通用工作流引擎。

行业调研结论作为本阶段约束：持久化恢复应在关键边界保存状态；恢复时应跳过已完成的确定性步骤或已完成副作用步骤；副作用工具必须先完成持久化 pending 记录再执行；工具调用、模型调用、审批中断等应具有可审计 trace；高风险或无法证明幂等的副作用工具不能自动重放。阶段四优先解决 `Run_Runtime` 可靠恢复与副作用防重放，但不承诺外部副作用 exactly-once。成本护栏、动态任务分类、更细粒度风险分级和复杂反循环策略仍属于阶段五。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 后台运行 | `Run_Runtime` | 阶段三已实现的后台运行时，以 `run_id` 管理 `Chat_Run` 与 `Task_Run` 的 queued/running/paused/awaiting_approval/cancelled/succeeded/failed/lost 状态、事件流、取消、继续和审批恢复。 |
| 运行快照 | `Run_Snapshot` | `Run_Runtime` 对外查询的最新状态视图，包含状态、结果摘要、错误摘要、审批信息、分段元数据、事件 cursor、租约和版本。 |
| 持久化检查点 | `Durable_Checkpoint` | 在模型调用、工具调用、审批中断、执行段边界等关键位置保存的 JSON-safe 恢复状态，支持服务重启或 worker 中断后继续执行。 |
| 检查点存储 | `Checkpoint_Store` | 保存、读取和列出 `Durable_Checkpoint` 的领域 Port 及其本地文件/Redis Adapter。 |
| 工具结果账本 | `Tool_Result_Ledger` | 记录工具调用 pending/completed/error 状态、幂等键、参数摘要、结果内容和错误标记的持久化账本，用于恢复时避免重复执行已成功工具调用。 |
| 工具执行键 | `Tool_Execution_Key` | 由 `run_id`、执行段、轮次、`tool_call_id`、工具名和规范化参数摘要组成的稳定键，用于识别同一次逻辑工具调用。 |
| 可恢复运行 | `Recoverable_Run` | 处于 `running` 或 `cancel_requested` 且租约过期时，存在兼容 `Durable_Checkpoint` 并满足恢复前置条件的 `Run_Runtime` 实例。 |
| 恢复前置条件 | `Recovery_Precondition` | 判断一个中断运行能否自动恢复的条件集合，包括检查点 schema 兼容、上下文可反序列化、工具边界可重建、无未决高风险副作用等。 |
| 副作用工具 | `Side_Effect_Tool` | 可能写文件、执行命令、发请求、发消息或修改外部状态的工具。 |
| 工具重放策略 | `Tool_Replay_Policy` | 恢复时对工具调用的处理策略，包括复用已完成结果、要求外部幂等键、进入人工确认或拒绝自动恢复。 |
| 观察恢复 | `Observation_Reattach` | 客户端网络断开、SSE replay 过期或前端刷新后，通过 `Run_Snapshot` 查询、事件 replay 或 polling fallback 重新观察后台运行状态；不代表 worker 从检查点恢复执行。 |
| 敏感检查点数据 | `Sensitive_Checkpoint_Data` | 可能包含用户输入、系统提示词、模型输出、工具参数、文件内容、命令输出、HTTP payload、trace 明细或其他敏感信息的检查点/账本字段。 |
| 检查点保留策略 | `Checkpoint_Retention_Policy` | 控制 `Durable_Checkpoint` 与 `Tool_Result_Ledger` 的保留数量、保留时间、单条大小上限和裁剪行为的策略。 |
| 非 Exactly-Once 边界 | `Non_Exactly_Once_Boundary` | 阶段四提供已完成工具结果复用和未知 pending 不自动重放，但不保证外部系统中的副作用只发生一次。 |
| 执行段元数据 | `Segment_Metadata` | 阶段二/三已有的分段次数、停止原因、usage、预算摘要和进展信息等可观测数据。 |
| 审批中断 | `Approval_Interrupt` | HITL 工具审批保存的中断状态，包含会话上下文、待审批动作、轮次、模型和累计 usage。 |
| 事件流 | `Run_Event_Stream` | 阶段三已实现的后台运行事件历史和 SSE/polling 观察机制。 |

## 需求

### 需求 1：定义持久化检查点模型与存储端口

**用户故事：** 作为后台运行维护者，我希望系统用领域模型表达检查点和工具结果账本，以便在不破坏 DDD 分层的前提下支持可靠恢复。

#### 验收标准

1. THE `Durable_Checkpoint` SHALL 使用 JSON-safe 字段保存 `run_id`、检查点标识、单调序号、检查点阶段、会话上下文快照、轮次、usage、trace 摘要、`Segment_Metadata`、schema 版本和创建时间。
2. THE `Tool_Result_Ledger` SHALL 使用 JSON-safe 字段保存 `Tool_Execution_Key`、工具名、规范化参数摘要、pending/completed/error 状态、工具结果、错误标记和创建/更新时间。
3. THE `Durable_Checkpoint` SHALL 标记或裁剪 `Sensitive_Checkpoint_Data`，不得无边界保存完整 trace、超大工具输出或不必要的敏感 payload。
4. THE `Checkpoint_Store` SHALL 作为领域 Port 定义在 `domain/run` 边界内，不依赖本地文件、Redis、FastAPI 或其他基础设施实现。
5. THE `Checkpoint_Store` SHALL 提供保存检查点、读取最新检查点、按 `run_id` 列出检查点、写入工具 pending、完成工具结果和查询工具结果的能力。
6. FOR ALL `Durable_Checkpoint`, THE `Checkpoint_Store` SHALL 保证同一 `run_id` 内检查点序号单调递增。
7. FOR ALL `Tool_Result_Ledger`, THE `Checkpoint_Store` SHALL 保证同一 `Tool_Execution_Key` 的 completed 结果可被后续恢复读取并复用。

### 需求 2：实现本地文件与 Redis 检查点存储

**用户故事：** 作为部署者，我希望检查点沿用阶段三的本地文件和 Redis 双存储形态，以便不同部署模式下都能获得一致恢复语义。

#### 验收标准

1. THE `Checkpoint_Store` SHALL 提供本地文件 Adapter，并复用项目现有本地持久化路径策略、锁和原子写入约定。
2. THE `Checkpoint_Store` SHALL 提供 Redis Adapter，并使用 Redis 原子操作保证检查点序号、工具 pending/completed 状态和并发恢复判定的一致性。
3. FOR ALL `Checkpoint_Store` Adapter, THE 本地文件实现与 Redis 实现 SHALL 暴露一致的领域语义、异常行为和 JSON 序列化格式。
4. WHEN `Checkpoint_Store` 检测到 schema 版本不兼容, THE `Checkpoint_Store` SHALL 拒绝自动恢复并向上层暴露可审计错误。
5. WHEN `Checkpoint_Store` 遇到存储不可用, THE `Run_Runtime` SHALL 不伪装恢复成功，并保留可观测错误信息。

### 需求 3：在关键执行边界保存检查点

**用户故事：** 作为长任务用户，我希望系统在模型调用、工具执行和执行段边界保存进度，以便中断后不用丢失已完成工作。

#### 验收标准

1. WHEN `Run_Runtime` 创建或领取一个 `Chat_Run` 或 `Task_Run`, THE `Run_Runtime` SHALL 保存初始 `Durable_Checkpoint` 或确认已有兼容检查点。
2. WHEN Agent 模型调用完成并得到 assistant 文本或 tool_calls, THE `Run_Runtime` SHALL 在执行任何工具前保存包含 assistant 消息、轮次和 usage 的 `Durable_Checkpoint`。
3. WHEN Agent 准备执行工具调用, THE `Run_Runtime` SHALL 在工具实际执行前写入 `Tool_Result_Ledger` pending 记录。
4. IF `Tool_Result_Ledger` pending 记录写入失败, THEN THE `Run_Runtime` SHALL NOT 执行对应 `Side_Effect_Tool`。
5. WHEN Agent 工具调用成功或失败并追加 `ToolMessage`, THE `Run_Runtime` SHALL 写入 `Tool_Result_Ledger` completed/error 记录并保存包含最新上下文的 `Durable_Checkpoint`。
6. WHEN Agent 进入 `Approval_Interrupt`, THE `Run_Runtime` SHALL 在对外暴露 awaiting approval 状态前保存包含审批上下文和待审批动作的 `Durable_Checkpoint`。
7. WHEN `Run_Runtime` 完成一个执行段, THE `Run_Runtime` SHALL 保存包含 `Segment_Metadata`、停止原因和累计 usage 的 `Durable_Checkpoint`。
8. THE `Run_Runtime` SHALL NOT 因保存检查点改变阶段一/二既有 continue 语义，包括不追加新的 user message、不放大单段轮次限制、不扩大 Task 工具边界。

### 需求 4：恢复可恢复运行而不是默认标记 lost

**用户故事：** 作为后台运行用户，我希望服务重启或 worker 中断后，满足条件的后台任务能重新入队继续，以便减少长任务丢失和重复工作。

#### 验收标准

1. WHEN `Run_Runtime` 扫描到租约过期的 running `Run_Snapshot`, THE `Run_Runtime` SHALL 评估 `Recovery_Precondition`。
2. IF `Run_Snapshot` 满足 `Recovery_Precondition`, THEN THE `Run_Runtime` SHALL 将该 `Recoverable_Run` 重新入队并记录恢复事件。
3. IF `Run_Snapshot` 不满足 `Recovery_Precondition`, THEN THE `Run_Runtime` SHALL 保持阶段三的保守语义，将运行标记为 `lost` 或进入需要人工处理的状态。
4. WHEN `Recoverable_Run` 被 worker 再次 claim, THE `Run_Runtime` SHALL 从最新兼容 `Durable_Checkpoint` 重建会话上下文、轮次、usage 和执行段元数据。
5. WHEN `Recoverable_Run` 继续执行, THE `Run_Runtime` SHALL 跳过已经 completed 的 `Tool_Result_Ledger` 工具调用并复用其结果。
6. WHILE `Run_Runtime` IN cancel requested state, WHEN 恢复扫描运行, THE `Run_Runtime` SHALL 优先完成取消语义，不应因存在检查点而继续执行业务工具。
7. THE `Run_Runtime` SHALL 限制恢复重试次数，并在连续恢复失败时停止自动恢复并暴露可观测失败原因。

### 需求 5：防止副作用工具重复执行

**用户故事：** 作为系统安全维护者，我希望已成功的副作用工具在恢复时不会被重复执行，以便避免重复写文件、发请求、发消息或执行命令。

#### 验收标准

1. THE `Side_Effect_Tool` SHALL 能声明 `Tool_Replay_Policy`，用于表达恢复时可复用结果、要求外部幂等键、需要人工确认或禁止自动重放。
2. IF `Side_Effect_Tool` 未声明 `Tool_Replay_Policy`, THEN THE `Run_Runtime` SHALL 采用保守默认策略，不自动重复执行该工具。
3. WHEN `Tool_Result_Ledger` 已存在 completed 记录, THE `Run_Runtime` SHALL 复用已保存工具结果并追加等价 `ToolMessage`，不得再次调用工具实现。
4. WHEN `Tool_Result_Ledger` 只存在 pending 记录且工具不可证明幂等, THE `Run_Runtime` SHALL 停止自动恢复并要求人工处理。
5. WHEN `Side_Effect_Tool` 支持外部幂等键, THE `Run_Runtime` SHALL 在工具执行上下文中提供稳定 `Tool_Execution_Key` 或等价幂等标识。
6. FOR ALL `Tool_Execution_Key`, THE `Run_Runtime` SHALL 使用规范化工具参数摘要，避免 JSON key 顺序差异导致重复执行检测失效。
7. THE `Run_Runtime` SHALL 明确暴露 `Non_Exactly_Once_Boundary`，不得把工具结果账本描述为外部副作用 exactly-once 保证。

### 需求 6：保持审批恢复语义一致

**用户故事：** 作为需要人工审批的用户，我希望审批中断与恢复也能被检查点保护，以便审批前后服务重启不会丢失上下文或重复执行工具。

#### 验收标准

1. WHEN `Approval_Interrupt` 被创建, THE `Run_Runtime` SHALL 保存包含审批批次、待审批动作、上下文、轮次、模型和累计 usage 的 `Durable_Checkpoint`。
2. WHEN 用户提交审批决策, THE `Run_Runtime` SHALL 在应用 approve/edit/reject 决策前查询 `Tool_Result_Ledger`。
3. IF 审批决策对应工具调用已经 completed, THEN THE `Run_Runtime` SHALL 复用账本结果，不得重复执行 approve/edit 对应工具。
4. WHEN reject 决策被应用, THE `Run_Runtime` SHALL 以与工具结果一致的方式保存拒绝产生的 `ToolMessage` 和 `Durable_Checkpoint`。
5. WHEN 审批恢复再次进入 awaiting approval, THE `Run_Runtime` SHALL 保存新的 `Durable_Checkpoint` 并保留原有审批恢复前置条件。
6. THE `Run_Runtime` SHALL NOT 将 `Approval_Interrupt` 当作普通 paused continue 处理。

### 需求 7：对外暴露恢复状态与观察恢复

**用户故事：** 作为前端、TUI 或 API 调用方，我希望能看到后台运行是否可恢复、是否正在恢复以及为何恢复失败，并能在断线或刷新后重新观察运行状态，以便正确展示状态和引导用户操作。

#### 验收标准

1. THE `Run_Snapshot` SHALL 暴露最新检查点标识、是否可恢复、恢复尝试次数和最近恢复失败原因。
2. THE `Run_Event_Stream` SHALL 暴露检查点保存、恢复入队、工具结果复用和恢复失败等事件。
3. WHEN 事件历史 replay 过期, THE `Run_Event_Stream` SHALL 保持阶段三既有 `replay_expired` 降级语义。
4. WHEN 客户端网络断开或前端刷新, THE `Observation_Reattach` SHALL 通过 `Run_Snapshot` 查询、事件 replay 或 polling fallback 恢复观察状态，不得触发新的执行恢复。
5. THE FastAPI Run adapter SHALL 只映射 `Run_Runtime` 的共享应用服务结果，不得复制 checkpoint、恢复、claim 或工具重放规则。
6. THE TUI/agent adapter SHALL 直接调用共享应用服务，不得通过 FastAPI endpoint 自调用。
7. THE Web Run View SHALL 在已有 Run View 基础上展示恢复相关状态，不改变同步 Chat/Task 默认入口语义。

### 需求 8：配置、兼容性与迁移边界

**用户故事：** 作为部署和维护人员，我希望阶段四具备清晰配置、兼容旧运行数据，并能安全关闭，以便平滑升级和回滚。

#### 验收标准

1. THE `Run_Runtime` SHALL 提供阶段四检查点功能开关，并默认按项目配置规范写入 `epsilon-boot/config.properties`。
2. THE `Run_Runtime` SHALL 支持配置 `Checkpoint_Retention_Policy`、恢复最大尝试次数和是否自动恢复租约过期运行。
3. THE `Checkpoint_Retention_Policy` SHALL 支持检查点保留数量、保留时间、单条大小上限和工具结果账本裁剪规则。
4. WHEN 检查点功能关闭, THE `Run_Runtime` SHALL 保持阶段三行为：运行状态、事件流、取消、继续和审批恢复仍可用，租约过期运行仍按既有规则处理。
5. WHEN 读取阶段三遗留 `Run_Snapshot`, THE `Run_Runtime` SHALL 将其视为无检查点运行，不得尝试从不存在的 `Durable_Checkpoint` 自动恢复。
6. THE `Run_Runtime` SHALL 不要求新增 SQL/DDL 或外部 workflow runtime 部署。
7. FOR ALL 新增配置项, THE 配置读取 SHALL 遵循项目既有 settings 与 `config.properties` 优先规则。

### 需求 9：测试与回归覆盖

**用户故事：** 作为维护者，我希望阶段四有覆盖核心恢复和防重放风险的测试，以便后续阶段演进不会破坏长任务可靠性。

#### 验收标准

1. THE 测试套件 SHALL 覆盖 `Durable_Checkpoint`、`Tool_Result_Ledger` 和 `Tool_Execution_Key` 的序列化、稳定摘要和 schema 兼容行为。
2. THE 测试套件 SHALL 覆盖本地文件与 Redis `Checkpoint_Store` 的一致语义、并发写入和 completed 结果复用。
3. THE 测试套件 SHALL 覆盖模型调用后崩溃、工具调用后崩溃、执行段结束后崩溃和审批中断前后崩溃的恢复路径。
4. THE 测试套件 SHALL 覆盖已 completed 工具调用恢复时不重复执行，pending 且不可证明幂等的副作用工具进入人工处理。
5. THE 测试套件 SHALL 覆盖 `Tool_Result_Ledger` pending 写入失败时 `Side_Effect_Tool` 不会执行。
6. THE 测试套件 SHALL 覆盖 `Observation_Reattach` 不触发执行恢复。
7. THE 测试套件 SHALL 覆盖 `Checkpoint_Retention_Policy` 对数量、大小和过期数据的裁剪行为。
8. THE 测试套件 SHALL 回归阶段二 Chat SSE final payload 只有整个 segmented run 结束时才 `finished=true`。
9. THE 测试套件 SHALL 回归 Task paused `can_continue` 与 `continue_task` 前置条件一致，尤其是旧会话或复用 system message 时的工具边界 metadata。
10. THE 验证流程 SHALL 包含后端全量 `env PYTHONPATH=src uv run --frozen pytest`，以及前端 `npm run lint` 和 `npm run build`。
