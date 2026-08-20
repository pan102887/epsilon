# 需求文档：DDD Infrastructure Logic Remediation

## 简介

本需求承接既有 DDD 调研与落地评估：`docs/spec/ddd-gap-analysis/report.md` 已指出本项目 DDD 骨架与治理能力较强，主要差距集中在贫血模型与核心逻辑下沉 `infrastructure`；`docs/spec/ddd-impl-research/report.md` 已确认 Agent Loop P2 两片落地，工具并发骨架经 ADR-0013 判定继续留在 `infrastructure`，后续主线应回归贫血子域与跨层边界治理。

本特性目标是在不推翻 ADR-0010/0011/0012/0013/0015 的前提下，治理当前 `infrastructure` 承载过多业务/用例逻辑与跨层依赖方向不清的问题。当前代码证据包括：

- `src/domain` 当前没有导入 `infrastructure` 的命中，这是需要保持的正向基线。
- `epsilon-boot/src/infrastructure/run/run_worker.py:15-18` 与 `run_worker_manager.py:10-12` 直接导入 `application.run.*`，形成 `infrastructure -> application` 反向依赖；这是最高优先级、首个实现切片。
- `application/api/routers/health.py:18` 与 `application/api/routers/task.py:24` 直接导入 `infrastructure.*_serialization`；仓库内还存在若干 `application/run/*` 到 `infrastructure.*_serialization` 的导入。后续设计必须明确 presenter/serializer 边界或登记受控迁移例外。
- `application/container_config.py` 是组合根例外，用于集中完成 Port -> Adapter 装配，不作为违规。
- `infrastructure/run/run_worker.py::_persist_outcome` 根据 `RunExecutionOutcome.status` 选择 `RunStorePort` mutation 与 `RunEventType`，包含可脱离 worker runtime 的业务结果落库/事件类型判定；worker 应保留 claim、lease、heartbeat、取消检查、进度事件与异步任务生命周期等技术职责。
- `infrastructure/chat/chat_service_adapter.py` 同时承担会话加载、系统 prompt 注入、模型解析、分段 agent 执行、continue/resume、上下文保存与事件流包装，需作为第二优先切片诊断并迁移可上移的用例编排。
- `infrastructure/agent/handoff_to_agent_tool.py` 同时包含 delegation depth、workflow handoff count、collaboration summary 更新与工具适配；需作为第三优先切片只抽取纯领域判定，ContextVar、`ToolExecutionResult`、事件记录与工具适配留在 `infrastructure`。

本特性范围包括：

1. Run worker 依赖反转与职责重划，作为首个实现切片。
2. Run outcome 持久化/事件类型判定的边界收敛。
3. Chat service adapter 编排职责的诊断与后续迁移要求。
4. Handoff tool 纯领域判定抽取要求。
5. API presenter/serializer 边界收敛或受控例外登记。
6. 静态 import guard、聚焦测试、文档同步与 ADR 评估要求。

明确不在本特性范围内：

- 不重开 Agent Loop P2 第三片，不移动 `asyncio.gather` 工具并发骨架。
- 不把 OTel、ContextVar、Redis/file persistence、OpenAI compatible adapter、模型 SDK/HTTP 适配等技术关注点迁入 `domain`。
- 不修复 handoff model discrepancy；该行为语义变化需另开独立 spec。
- 不引入领域事件或事件总线，继续遵守 ADR-0001。
- 不做全仓大爆炸搬迁，不借本特性重排无关模块、重命名无关符号或批量格式化。
- 当前阶段只创建 `requirement.md`；不生成 `design.md`/`tasks.md`，不修改生产源码。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 本治理特性 | DDD_Infrastructure_Logic_Remediation | 本 spec 所描述的 infrastructure 逻辑过重、跨层依赖与职责边界治理 initiative。 |
| 领域层 | Domain_Layer | `epsilon-boot/src/domain`，承载领域值对象、领域服务、状态机、策略与 Port，禁止依赖 application/infrastructure。 |
| 应用层 | Application_Layer | `epsilon-boot/src/application`，承载用例编排、HTTP 路由、lifespan、异常映射与组合根。 |
| 基础设施层 | Infrastructure_Layer | `epsilon-boot/src/infrastructure`，承载 Port 实现、外部系统接入、序列化/持久化/运行时技术转换。 |
| 组合根例外 | Application_Composition_Root | `application/container_config.py`、启动装配与资源生命周期代码中允许同时引用 domain Port 与 infrastructure Adapter 的例外位置。 |
| 受控迁移例外 | Controlled_Migration_Exception | 过渡期必须显式记录原因、范围、清理计划和验证约束的分层例外。 |
| 下游设计阶段 | Downstream_Design | 本 requirement 获批后的 `design.md` 阶段，负责技术切分、ADR 判断和迁移方案。 |
| 实现切片 | Implementation_Slice | 后续 `tasks.md` 中可独立实现、验证和评审的最小行为等价改动单元。 |
| 行为等价 | Behavior_Equivalence | 重构前后外部可观测行为、状态迁移、事件类型、错误语义、API 响应和测试断言保持一致。 |
| Run worker 依赖反转切片 | Run_Worker_Dependence_Inversion_Slice | 首个实现切片，目标是消除 `infrastructure/run` 对 `application/run` 的生产代码直接导入。 |
| Run worker 模块 | Infrastructure_Run_Worker_Module | `infrastructure/run/run_worker.py`，当前负责 claim、heartbeat、执行段推进、结果持久化与事件追加。 |
| Run worker manager 模块 | Infrastructure_Run_Worker_Manager_Module | `infrastructure/run/run_worker_manager.py`，当前负责 worker 任务管理、wake up、lost sweep 与 checkpoint recovery 分支。 |
| Run 应用模块 | Application_Run_Module | `application/run/*` 中的 Run 应用服务、执行协调器、checkpoint recovery 与 workflow orchestrator 等模块。 |
| Run worker runtime | Run_Worker_Runtime | 后台 worker 的 claim、lease、heartbeat、asyncio task lifecycle、poll/wake、segment progress 与 lost sweep 运行时能力。 |
| Run 执行协调器 | Run_Execution_Coordinator | `application/run/run_execution_coordinator.py::RunExecutionCoordinator`，将 RunSnapshot 转换为 Chat/Task 调用并产出 RunExecutionOutcome。 |
| Run 执行结果 | Run_Execution_Outcome | `RunExecutionOutcome`，单个 Run 执行段的结果值，包含 status、result、error、approval_id、segment metadata、workflow/collaboration 摘要等。 |
| Run outcome 持久化判定 | Run_Outcome_Persistence_Decision | 从 RunExecutionOutcome 推导 RunStore mutation、RunEventType、失败 fallback 与 terminal logging 所需语义的可测试判定。 |
| Run 存储变更 | Run_Store_Mutation | 对 RunStorePort 的状态写入动作，例如 mark_succeeded、mark_paused、mark_awaiting_approval、mark_failed、mark_cancelled。 |
| Run 事件类型 | Run_Event_Type | `RunEventType` 生命周期事件，例如 run_succeeded、run_paused、approval_required、run_failed、run_cancelled。 |
| Run 存储端口 | Run_Store_Port | `domain/run/ports.py::RunStorePort`，Run 快照、状态、租约与控制操作端口。 |
| Run 事件存储端口 | Run_Event_Store_Port | `domain/run/ports.py::RunEventStorePort`，Run 事件追加、查询、等待与裁剪端口。 |
| 租约心跳技术职责 | Lease_Heartbeat_Technical_Responsibility | worker claim、refresh lease、heartbeat loop、asyncio task cancel/stop 等运行时技术职责。 |
| Run runtime 当前行为 | Current_Run_Runtime_Behavior | 现有 Run claim、segment、cancel、pause、approval、succeed/fail/lost、metrics 与事件 replay 行为。 |
| Chat service adapter 边界切片 | Chat_Service_Adapter_Boundary_Slice | 第二优先切片，诊断并迁移 `ChatServiceAdapter` 中可上移的用例编排/领域判定。 |
| Chat service adapter | Chat_Service_Adapter | `infrastructure/chat/chat_service_adapter.py`，当前实现 ChatServicePort 并承载顶层聊天编排。 |
| 聊天用例编排 | Chat_Use_Case_Orchestration | 会话加载、系统 prompt 注入、用户消息追加、执行路径选择、continue/resume、上下文保存等应用用例层职责。 |
| 会话上下文管理 | Session_Context_Management | 对 ConversationContext 与 SessionContextStorePort/SessionIndexPort 的加载、保存、恢复与索引刷新。 |
| 系统提示词注入 | System_Prompt_Injection | 确保会话上下文包含 chat-default system prompt 及 workspace path guidance 的行为。 |
| 分段续跑编排 | Segment_Continuation_Orchestration | 基于 SegmentExecutionPolicy、SegmentBudgetUsage、progress/risk gate 判断是否继续下一段的编排。 |
| 审批恢复编排 | Approval_Resume_Orchestration | 加载/校验/消费 ApprovalInterrupt 并调用 AgentPort.resume 的恢复编排。 |
| 流式事件适配 | Stream_Event_Adaptation | 将 StreamingChunk 或 AgentStreamEvent 包装为对外流式协议、补 metadata 和保存上下文的适配职责。 |
| 基础设施技术适配职责 | Infrastructure_Technical_Adapter_Responsibility | OTel、ContextVar、序列化 mapper、模型/文件/Redis/HTTP 技术接入、stream chunk 技术转换等职责。 |
| Handoff tool 边界切片 | Handoff_Tool_Boundary_Slice | 第三优先切片，抽取 handoff tool 中可脱离运行时的纯领域判定。 |
| Handoff 工具 | Handoff_To_Agent_Tool | `infrastructure/agent/handoff_to_agent_tool.py::HandoffToAgentTool`，提供 handoff_to_agent 工具适配。 |
| Handoff 领域判定 | Handoff_Domain_Decision | 与 handoff 深度、workflow handoff count、limit 命中相关且可脱离 ContextVar/I/O 的纯判定。 |
| Handoff ContextVar 上下文 | ContextVar_Handoff_Context | `infrastructure.agent.handoff_context` 中用于读取父 ConversationContext 快照的运行时上下文传递机制。 |
| ToolExecutionResult 适配 | Tool_Execution_Result_Adaptation | Handoff 工具把错误/成功路径翻译成 ToolExecutionResult 或 HandoffPerformed 信号的工具适配行为。 |
| 协作事件记录 | Collaboration_Event_Recording | `workflow_collaboration_recorder` 对 collaboration limit、step、handoff 事件和 summary 的记录。 |
| 委派端口调用 | Delegation_Port_Invocation | `DelegationPort.handoff(...)` 对目标 Agent 执行 handoff 的端口调用。 |
| Workflow 协作上下文 | Workflow_Collaboration_Context | workflow Run 内用于限制 recursion depth、handoff count 与 collaboration 状态的上下文。 |
| API presenter 边界切片 | API_Presenter_Boundary_Slice | 收敛 API router 与应用层 serializer/presenter 归属的切片。 |
| API router 序列化导入 | API_Router_Serialization_Import | `application/api/routers/*` 直接导入 `infrastructure.*_serialization` 的导入点。 |
| Application 到 infrastructure 序列化导入 | Application_To_Infrastructure_Serialization_Import | 非组合根 application 模块直接导入 infrastructure 序列化/presenter mapper 的导入点。 |
| 应用 presenter 边界 | Application_Presenter_Boundary | 后续设计可定义的 API/DTO/HTTP presenter 归属边界，用于避免路由默默依赖 infrastructure mapper。 |
| 序列化受控例外 | Serialization_Controlled_Exception | serializer/presenter 暂时保留在 infrastructure 且被 application 调用时必须登记的受控例外。 |
| Pydantic DTO 边界 | Pydantic_DTO_Boundary | Pydantic 仅用于 API/DTO/配置边界，领域层继续使用 dataclass 与原生类型。 |
| 后端静态导入守卫 | Backend_Static_Import_Guard | 后端 AST/pytest 静态测试集合，用于阻断分层 import 回归。 |
| 架构导入边界测试 | Architecture_Import_Boundary_Test | 现有 `test/static/test_architecture_import_boundaries.py` 及后续扩展的 AST import 边界测试。 |
| 领域导入基线 | Domain_Import_Baseline | `Domain_Layer` 当前无 application/infrastructure 导入的正向基线。 |
| Infrastructure 到 Application 导入规则 | Infrastructure_To_Application_Import_Rule | 生产代码 `Infrastructure_Layer` 不得直接导入 `Application_Layer` 的静态规则。 |
| Application 到 Infrastructure 导入规则 | Application_To_Infrastructure_Import_Rule | 除 Application_Composition_Root 与受控例外外，生产代码 `Application_Layer` 不得直接导入具体 infrastructure Adapter/serializer 的静态规则。 |
| 后端工作目录 | Backend_Working_Directory | `epsilon-boot/`，后端 `uv` 命令执行目录。 |
| 验证命令集合 | Verification_Command_Set | 后续实现切片完成时必须运行或记录无法运行原因的最小后端验证命令集合。 |
| 行为等价测试集 | Behavior_Equivalence_Test_Suite | 针对搬迁/委托后的 Run、Chat、Handoff 行为与静态边界的聚焦回归测试集合。 |
| 文档同步 | Documentation_Synchronization | 代码改变分层结构、Port/Adapter、Agent Loop、DI 装配、API presenter 时同步更新主题文档。 |
| ADR 判断 | ADR_Decision_Need | 后续设计对分层方向、Port/Adapter 归属或新增一等抽象是否必须新增 ADR 的判断。 |
| 新一等抽象 | New_First_Class_Abstraction | 新增领域服务、Port、应用服务、presenter 边界或其他长期架构抽象。 |
| Port/Adapter 归属 | Port_Adapter_Ownership | 领域 Port、基础设施 Adapter、应用装配职责各自所属层级的架构归属。 |
| 依赖方向 | Dependency_Direction | `application -> domain <- infrastructure` 与组合根例外等分层依赖方向约束。 |
| 受影响主题文档 | Affected_Topic_Documents | 本特性后续实现触发的 `docs/architecture.md`、`docs/domain-model.md`、`docs/agent.md`、`docs/api.md`、`docs/di-container.md` 等主题文档集合。 |
| 已接受 ADR 基线 | Accepted_ADR_Baseline | ADR-0001、ADR-0010、ADR-0011、ADR-0012、ADR-0013、ADR-0015 等已 Accepted 且本特性不得静默推翻的决策。 |
| Agent Loop P2 第三片 | Agent_Loop_P2_Third_Slice | 已由 ADR-0013 判定不开启的工具并发骨架继续上提切片。 |
| 工具并发骨架 | Concurrent_Tool_Skeleton | `_dispatch_concurrent_tool_calls`、`_stream_concurrent_tool_progress`、`_events_concurrent_tool_calls` 等 asyncio 并发/流式时序骨架。 |
| 运行时技术关注点 | Runtime_Technical_Concern | `asyncio.gather`、OTel trace、ContextVar、Redis/file persistence、OpenAI compatible adapter 等技术实现关注点。 |
| Handoff model discrepancy | Handoff_Model_Discrepancy | 既有报告登记的 handoff 分支 AgentResult.model 取值疑点，本特性不修。 |
| 领域事件总线 | Domain_Event_Bus | ADR-0001 已移除且不推荐复活的 EventBusPort/EventStorePort/DomainEvent 机制。 |
| 全仓大爆炸搬迁 | Big_Bang_Repository_Relocation | 一次性搬迁大量无关模块、重排全仓结构或批量改 import 的高风险做法。 |

## 需求

### 需求 1：Run worker 依赖反转与职责重划

**用户故事：** 作为后端架构维护者，我希望优先反转 Run worker 对应用层的直接依赖，以便 `Infrastructure_Layer` 只承担后台 worker runtime 的技术职责。

#### 验收标准

1. THE Run_Worker_Dependence_Inversion_Slice SHALL be the first Implementation_Slice produced by Downstream_Design for DDD_Infrastructure_Logic_Remediation.
2. THE Infrastructure_Run_Worker_Module SHALL NOT directly import Application_Run_Module after Run_Worker_Dependence_Inversion_Slice is complete.
3. THE Infrastructure_Run_Worker_Manager_Module SHALL NOT directly import Application_Run_Module after Run_Worker_Dependence_Inversion_Slice is complete.
4. THE Application_Composition_Root SHALL remain the approved place to wire Run_Worker_Runtime dependencies to Application_Run_Module collaborators.
5. WHEN Downstream_Design identifies a temporary cross-layer dependency, THE Controlled_Migration_Exception SHALL record reason, scope, cleanup plan, and verification guard.
6. THE Run_Worker_Runtime SHALL retain Lease_Heartbeat_Technical_Responsibility in Infrastructure_Layer.
7. THE Run_Worker_Dependence_Inversion_Slice SHALL preserve Current_Run_Runtime_Behavior under Behavior_Equivalence.

### 需求 2：Run outcome 持久化判定收敛

**用户故事：** 作为 Run runtime 维护者，我希望把可脱离 worker runtime 的 outcome 状态落库与事件类型判定收敛到合适边界，以便 worker 只执行技术调度和端口调用。

#### 验收标准

1. THE Run_Outcome_Persistence_Decision SHALL be separated from Lease_Heartbeat_Technical_Responsibility.
2. THE Run_Outcome_Persistence_Decision SHALL derive Run_Store_Mutation and Run_Event_Type from Run_Execution_Outcome under Behavior_Equivalence.
3. WHEN Run_Execution_Outcome represents awaiting approval without an approval id, THE Run_Outcome_Persistence_Decision SHALL preserve the current failed fallback behavior.
4. THE Infrastructure_Run_Worker_Module SHALL execute Run_Store_Port and Run_Event_Store_Port calls according to the decision boundary selected by Downstream_Design.
5. IF Downstream_Design moves Run_Outcome_Persistence_Decision into Domain_Layer, THEN THE Run_Outcome_Persistence_Decision SHALL avoid Infrastructure_Layer, Application_Layer, Pydantic_DTO_Boundary, and Runtime_Technical_Concern dependencies.
6. IF Downstream_Design keeps Run_Outcome_Persistence_Decision in Application_Layer, THEN THE Run_Worker_Dependence_Inversion_Slice SHALL still satisfy Infrastructure_To_Application_Import_Rule.
7. THE Behavior_Equivalence_Test_Suite SHALL cover succeeded, paused, awaiting approval, missing approval id fallback, cancelled, failed, and unsupported status paths for Run_Outcome_Persistence_Decision.

### 需求 3：Chat service adapter 编排边界诊断与迁移

**用户故事：** 作为聊天子域维护者，我希望诊断并迁移 `ChatServiceAdapter` 中的应用用例编排，以便聊天流程边界与 DDD 分层职责一致。

#### 验收标准

1. THE Chat_Service_Adapter_Boundary_Slice SHALL be planned after Run_Worker_Dependence_Inversion_Slice.
2. THE Downstream_Design SHALL classify Chat_Service_Adapter responsibilities into Chat_Use_Case_Orchestration and Infrastructure_Technical_Adapter_Responsibility.
3. FOR ALL Session_Context_Management responsibilities, THE Downstream_Design SHALL decide whether they belong to Application_Layer or require Controlled_Migration_Exception.
4. FOR ALL System_Prompt_Injection responsibilities, THE Downstream_Design SHALL decide whether they belong to Application_Layer, Domain_Layer, or Infrastructure_Technical_Adapter_Responsibility.
5. FOR ALL Segment_Continuation_Orchestration responsibilities, THE Downstream_Design SHALL preserve the existing domain-level segmented decision usage and avoid duplicating that logic in Infrastructure_Layer.
6. FOR ALL Approval_Resume_Orchestration responsibilities, THE Chat_Service_Adapter_Boundary_Slice SHALL preserve existing approval validation, consume, resume, and error semantics under Behavior_Equivalence.
7. FOR ALL Stream_Event_Adaptation responsibilities, THE Downstream_Design SHALL keep protocol wrapping and Runtime_Technical_Concern out of Domain_Layer.
8. THE Chat_Service_Adapter_Boundary_Slice SHALL keep Runtime_Technical_Concern and Infrastructure_Technical_Adapter_Responsibility out of Domain_Layer.

### 需求 4：Handoff tool 纯领域判定抽取

**用户故事：** 作为 Agent 协作能力维护者，我希望只抽取 Handoff 工具中可脱离运行时的领域判定，以便降低基础设施逻辑混杂而不破坏工具适配语义。

#### 验收标准

1. THE Handoff_Tool_Boundary_Slice SHALL be planned after Chat_Service_Adapter_Boundary_Slice unless Downstream_Design records a Controlled_Migration_Exception for priority inversion.
2. THE Handoff_Tool_Boundary_Slice SHALL extract only Handoff_Domain_Decision that can be tested without ContextVar_Handoff_Context, Collaboration_Event_Recording, Delegation_Port_Invocation, or Tool_Execution_Result_Adaptation.
3. IF Workflow_Collaboration_Context is present, THEN THE Handoff_Domain_Decision SHALL preserve current recursion depth and handoff count limit semantics under Behavior_Equivalence.
4. WHEN Handoff_Domain_Decision rejects handoff, THE Handoff_To_Agent_Tool SHALL preserve current Tool_Execution_Result_Adaptation content and metadata semantics.
5. THE Handoff_To_Agent_Tool SHALL keep ContextVar_Handoff_Context in Infrastructure_Layer.
6. THE Handoff_To_Agent_Tool SHALL keep Collaboration_Event_Recording in Infrastructure_Layer.
7. THE Handoff_To_Agent_Tool SHALL keep Delegation_Port_Invocation in Infrastructure_Layer.
8. THE Handoff_Tool_Boundary_Slice SHALL NOT change Handoff_Model_Discrepancy.

### 需求 5：API presenter/serializer 边界收敛或受控例外

**用户故事：** 作为 API 边界维护者，我希望明确 router/application 与 serializer/presenter 的归属，以便后续代码不再默默扩大 `Application_Layer -> Infrastructure_Layer` 依赖。

#### 验收标准

1. THE API_Presenter_Boundary_Slice SHALL inventory existing API_Router_Serialization_Import instances.
2. THE API_Presenter_Boundary_Slice SHALL inventory existing Application_To_Infrastructure_Serialization_Import instances outside Application_Composition_Root.
3. FOR ALL API_Router_Serialization_Import instances, THE Downstream_Design SHALL choose Application_Presenter_Boundary relocation or Serialization_Controlled_Exception.
4. FOR ALL Application_To_Infrastructure_Serialization_Import instances, THE Downstream_Design SHALL choose Application_Presenter_Boundary relocation or Serialization_Controlled_Exception.
5. THE Application_To_Infrastructure_Import_Rule SHALL explicitly exclude Application_Composition_Root from ordinary violation reporting.
6. IF Serialization_Controlled_Exception remains active, THEN THE Controlled_Migration_Exception SHALL record reason, exact import scope, cleanup plan, and static guard coverage.
7. THE API_Presenter_Boundary_Slice SHALL preserve Pydantic_DTO_Boundary and SHALL keep Pydantic_DTO_Boundary outside Domain_Layer.

### 需求 6：静态 import guard 与验证闭环

**用户故事：** 作为架构治理负责人，我希望通过静态测试和聚焦回归测试锁定分层边界，以便后续迭代不会重新引入跨层反向依赖。

#### 验收标准

1. THE Backend_Static_Import_Guard SHALL keep Domain_Import_Baseline enforced for Domain_Layer.
2. THE Architecture_Import_Boundary_Test SHALL continue to parse source by AST without importing production modules.
3. THE Backend_Static_Import_Guard SHALL enforce Infrastructure_To_Application_Import_Rule after Run_Worker_Dependence_Inversion_Slice.
4. THE Backend_Static_Import_Guard SHALL enforce Application_To_Infrastructure_Import_Rule with Application_Composition_Root and Controlled_Migration_Exception handling.
5. FOR ALL Controlled_Migration_Exception entries, THE Backend_Static_Import_Guard SHALL fail when the exception scope silently expands.
6. THE Verification_Command_Set SHALL be executable from Backend_Working_Directory through `uv`.
7. THE Verification_Command_Set SHALL include focused Architecture_Import_Boundary_Test execution for Backend_Static_Import_Guard.
8. THE Verification_Command_Set SHALL include Behavior_Equivalence_Test_Suite execution for the Implementation_Slice being completed.

### 需求 7：文档同步、ADR 判断与最小改动纪律

**用户故事：** 作为长期维护者，我希望每个跨层治理切片都有文档和 ADR 判断闭环，以便后续 agent 不会基于过时上下文继续跑偏。

#### 验收标准

1. THE Downstream_Design SHALL evaluate ADR_Decision_Need before introducing New_First_Class_Abstraction.
2. IF Downstream_Design changes Port_Adapter_Ownership or Dependency_Direction, THEN THE ADR_Decision_Need SHALL be recorded with a recommendation.
3. IF Downstream_Design introduces New_First_Class_Abstraction, THEN THE ADR_Decision_Need SHALL be recorded with a recommendation.
4. THE Documentation_Synchronization SHALL cover Affected_Topic_Documents when the corresponding behavior or structure changes.
5. FOR ALL Implementation_Slice plans, THE Behavior_Equivalence SHALL be stated as an explicit constraint.
6. FOR ALL Implementation_Slice plans, THE Downstream_Design SHALL avoid Big_Bang_Repository_Relocation.
7. THE DDD_Infrastructure_Logic_Remediation SHALL preserve Accepted_ADR_Baseline unless a new ADR explicitly supersedes an existing decision.

### 需求 8：既定非目标与技术边界保护

**用户故事：** 作为架构评审者，我希望本特性显式保护已收敛的非目标，以便后续阶段不把范围扩张成新的 Agent Loop 或全仓搬迁工程。

#### 验收标准

1. THE DDD_Infrastructure_Logic_Remediation SHALL NOT reopen Agent_Loop_P2_Third_Slice.
2. THE DDD_Infrastructure_Logic_Remediation SHALL NOT move Concurrent_Tool_Skeleton into Domain_Layer.
3. THE DDD_Infrastructure_Logic_Remediation SHALL NOT move Runtime_Technical_Concern into Domain_Layer.
4. THE DDD_Infrastructure_Logic_Remediation SHALL NOT repair Handoff_Model_Discrepancy.
5. THE DDD_Infrastructure_Logic_Remediation SHALL NOT introduce Domain_Event_Bus.
6. THE DDD_Infrastructure_Logic_Remediation SHALL NOT perform Big_Bang_Repository_Relocation.
7. THE DDD_Infrastructure_Logic_Remediation SHALL NOT weaken Accepted_ADR_Baseline through implementation-only changes.
