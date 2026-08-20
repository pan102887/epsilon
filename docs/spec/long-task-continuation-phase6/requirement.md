# 需求文档：长任务工作流化与多 Agent 协作阶段六

## 简介

阶段六在已完成的后台 `Run_Runtime`、分段执行、`Checkpoint_Recovery`、`HITL_Approval_Recovery`、`Guardrail_v1`、`Delegation` 与 `Handoff` 能力之上，增加轻量的工作流化与多 Agent 协作治理。目标不是替代既有 `ReAct_Agent`，也不是把系统一次性迁移为通用 durable workflow engine，而是在 Run 层为常见复杂任务提供标准工作流定义、阶段化编排状态、协作关系可观测和递归边界控制。

本阶段 v1 聚焦以下范围：

- 标准工作流定义、注册与选择，例如 `research`、`code_change`、`report`、`batch_processing`。
- 在 `Run_Runtime` 中增加 `plan`、`execute`、`evaluate`、`revise`、`finalize` 等阶段化状态，供编排、事件和视图观察。
- 为多 Agent 协作建立角色、委派、handoff、父子 Run 或 step trace 关系的可观测边界，避免无限递归和不可解释的控制转移。
- 兼容阶段四 `Checkpoint_Recovery`、既有 `HITL_Approval_Recovery`、阶段五 `Guardrail_v1` 和当前 `Delegation` / `Handoff` 工具语义。
- API、TUI、Web 可以展示工作流阶段和协作状态，但 adapter 不复制编排逻辑。

本阶段明确非范围：

- 不默认引入 Temporal、LangGraph、Dapr Workflow、Celery 或其他外部 durable workflow runtime。
- 不把 `ReAct_Agent` 改写为图执行引擎，不删除既有 Chat/Task 同步入口，不改变阶段一/二 continue 语义。
- 不扩大阶段五非范围：不要求完整 guardrail 运行时事件闭环，不要求 `guardrail_summary` 动态累计更新，不要求 guardrail `require_approval` 接入 HITL。
- 不承诺外部副作用 exactly-once；阶段四的工具账本和 checkpoint 防重放边界保持不变。
- 不要求 Web、TUI 或 FastAPI adapter 承担工作流选择、阶段推进、递归控制或恢复判定。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 后台运行时 | Run_Runtime | 阶段三至阶段五已实现的后台运行系统，负责 Run 创建、排队、执行、状态查询、事件流、取消、继续、审批恢复、checkpoint recovery 和 guardrail 字段透传。 |
| 运行快照 | Run_Snapshot | 对外查询 Run 最新状态的数据对象；阶段六可扩展工作流阶段、工作流标识和协作摘要字段。 |
| 运行事件流 | Run_Event_Stream | Run 的事件历史、SSE 和 polling fallback 观察机制；阶段六可追加工作流阶段与协作相关事件。 |
| ReAct Agent | ReAct_Agent | 现有推理、工具调用、观察循环执行器；阶段六不替代它，而是在 Run 层增加结构化编排状态。 |
| 标准工作流 | Standard_Workflow | 面向常见任务类型沉淀的可选择流程，例如调研、代码修改、报告生成和批处理。 |
| 工作流定义 | Workflow_Definition | 描述一个 Standard_Workflow 的稳定名称、适用条件、阶段序列、允许角色、协作限制和默认策略的配置或注册表记录。 |
| 工作流注册表 | Workflow_Registry | 管理 Workflow_Definition 的领域 Port 或等价注册能力，可由静态配置或代码注册提供实现。 |
| 工作流选择器 | Workflow_Selector | 根据 Run payload、任务分类、显式参数或默认策略选择 Workflow_Definition 的能力。 |
| 工作流阶段 | Workflow_Phase | Standard_Workflow 中可观察的阶段，阶段六 v1 至少支持 `plan`、`execute`、`evaluate`、`revise`、`finalize`。 |
| 工作流运行状态 | Workflow_Run_State | 某个 Run 当前绑定的 workflow 名称、当前 Workflow_Phase、阶段历史、阶段结果摘要和错误摘要。 |
| 多 Agent 协作 | Multi_Agent_Collaboration | 一个 Run 内或相关 Run 之间由多个命名 Agent 按角色、委派或 handoff 共同推进任务的行为。 |
| Agent 角色 | Agent_Role | Workflow_Definition 中声明的协作职责，例如 planner、executor、reviewer、reporter；映射到既有 NamedAgentConfig 或运行时可用 Agent。 |
| 委派 | Delegation | 既有 `DelegationPort.delegate` 或 `delegate_parallel` 能力，子 Agent 结果回灌给父 Agent 继续推理。 |
| 控制转移 | Handoff | 既有 `DelegationPort.handoff` 与 `handoff_to_agent` 能力，目标 Agent 接管上下文并产出最终回复。 |
| 父子 Run 关系 | Parent_Child_Run_Link | 当协作需要拆分为多个后台 Run 时，用于表达父 Run 与子 Run 的可观测关联。 |
| 步骤追踪关系 | Step_Trace_Link | 当协作保留在同一 Run 或同一 Agent trace 中时，用于表达某一步与 Agent 角色、委派或 handoff 的关联。 |
| 协作限制 | Collaboration_Limit | 限制协作递归深度、并行扇出数量、阶段最大重试次数、handoff 次数和子 Run 数量的策略。 |
| 持久化检查点恢复 | Checkpoint_Recovery | 阶段四已有的中断恢复能力，保存上下文、工具账本和执行段状态；阶段六必须兼容但不扩大其 exactly-once 边界。 |
| 人工审批恢复 | HITL_Approval_Recovery | 阶段三/四已有的审批中断与恢复流程；阶段六不得新增独立审批系统替代它。 |
| 护栏 v1 | Guardrail_v1 | 阶段五已实现的确定性分类、静态 guardrail 策略、工具风险分级、critical enforce 阻断和字段透传能力。 |
| FastAPI 适配器 | FastAPI_Adapter | `/api/runs*` HTTP adapter，只负责 DTO 转换和字段透传，不承载工作流编排逻辑。 |
| TUI | TUI | 命令行交互界面的 Run 和事件展示入口。 |
| Web Run 视图 | Web_Run_View | 前端 Run View 页面，用于展示 Run 快照、事件、恢复状态、guardrail 字段和阶段六新增工作流/协作状态。 |
| 配置来源 | Config_Source | 项目配置来源规范，默认写入 `epsilon-boot/config.properties`，环境变量仅用于覆盖。 |
| DDD 六边形架构 | DDD_Hexagonal_Architecture | 项目分层约束：领域层定义模型与 Port，基础设施层实现 Adapter，应用层编排，adapter 只做协议映射。 |
| Durable 工作流引擎评估 | Durable_Workflow_Engine_Evaluation | 对 Temporal、LangGraph、Dapr Workflow 等外部 durable execution 方案的可选评估活动，不代表阶段六必须引入依赖或迁移运行时。 |
| 测试套件 | Test_Suite | 覆盖阶段六领域模型、选择策略、编排状态、协作限制、恢复兼容和 adapter 透传的自动化测试集合。 |
| 验证流程 | Verification_Process | 阶段六完成后需要执行的后端全量测试和前端 lint/build 命令。 |

## 需求

### 需求 1：定义标准工作流模型与注册能力

**用户故事：** 作为长任务维护者，我希望系统能声明和注册标准工作流，以便常见复杂任务可以使用稳定、可测试的流程边界。

#### 验收标准

1. THE Workflow_Definition SHALL 包含稳定名称、描述、适用条件、Workflow_Phase 序列、允许 Agent_Role、Collaboration_Limit 和默认策略摘要。
2. THE Standard_Workflow SHALL 至少支持 `research`、`code_change`、`report` 和 `batch_processing` 四类可注册名称。
3. THE Workflow_Registry SHALL 作为 DDD_Hexagonal_Architecture 下的领域 Port 或等价领域能力定义，不依赖 FastAPI_Adapter、TUI、Web_Run_View、Redis、本地文件或外部 workflow runtime。
4. THE Workflow_Registry SHALL 支持从静态配置或代码注册读取 Workflow_Definition。
5. WHEN Workflow_Definition 缺少必需 Workflow_Phase 或名称重复, THE Workflow_Registry SHALL fail-fast 并暴露可测试错误。
6. FOR ALL Workflow_Definition, THE Workflow_Registry SHALL 保证 Workflow_Phase 名称和 Agent_Role 引用可被稳定序列化为 JSON-safe 字段。
7. THE Config_Source SHALL 用于新增工作流配置默认值，环境变量仅作为覆盖来源。

### 需求 2：选择工作流而不破坏既有 Run 创建语义

**用户故事：** 作为 Run 使用者，我希望系统能根据任务输入或显式参数选择合适工作流，以便后台任务获得阶段化编排而不影响已有同步入口。

#### 验收标准

1. WHEN Run_Runtime 创建后台 Run, THE Workflow_Selector SHALL 能根据 Run payload、Guardrail_v1 的任务分类、显式 workflow 参数或默认策略选择 Workflow_Definition。
2. IF Workflow_Selector 无法匹配 Workflow_Definition, THEN Run_Runtime SHALL 使用兼容默认路径，不得阻止既有 Chat/Task Run 创建。
3. IF 调用方显式指定未知 Standard_Workflow, THEN Run_Runtime SHALL 返回可观测的业务错误，不得静默降级为其他 Workflow_Definition。
4. THE Workflow_Selector SHALL 不调用 LLM 或外部服务完成选择。
5. THE Run_Runtime SHALL 不因 Workflow_Selector 启用而改变阶段一/二 continue 语义，包括不追加新的 user message、不放大单段轮次限制、不扩大 Task 工具边界。
6. THE Run_Snapshot SHALL 能暴露已选择的 Standard_Workflow 名称；未选择时该字段可为空。
7. THE Run_Event_Stream SHALL 能记录工作流选择结果或选择失败摘要。

### 需求 3：在 Run 层暴露工作流阶段状态

**用户故事：** 作为复杂任务用户，我希望看到任务处于计划、执行、评估、修正还是收尾阶段，以便理解长任务进展而不是只看到底层 Agent loop 状态。

#### 验收标准

1. THE Workflow_Phase SHALL 至少支持 `plan`、`execute`、`evaluate`、`revise` 和 `finalize`。
2. THE Workflow_Run_State SHALL 包含 Standard_Workflow 名称、当前 Workflow_Phase、阶段开始时间、阶段历史、阶段结果摘要和阶段错误摘要。
3. WHEN Run_Runtime 进入新的 Workflow_Phase, THE Run_Event_Stream SHALL 追加阶段切换事件。
4. WHEN Run_Runtime 完成一个 Workflow_Phase, THE Run_Event_Stream SHALL 追加阶段完成事件，并包含 JSON-safe 阶段摘要。
5. IF Workflow_Phase 执行失败, THEN Workflow_Run_State SHALL 保存失败阶段和错误摘要，不得伪装为任务成功。
6. THE Run_Snapshot SHALL 暴露 Workflow_Run_State 的当前可观察字段。
7. THE ReAct_Agent SHALL 继续负责具体模型与工具循环，Workflow_Run_State SHALL 不替代 ReAct_Agent 的工具执行、审批中断或 checkpoint hook。

### 需求 4：编排阶段化执行但保持 ReAct Agent 兼容

**用户故事：** 作为系统维护者，我希望工作流编排只组织阶段和状态，以便复用现有 Chat、Task、Agent、checkpoint 和 guardrail 能力。

#### 验收标准

1. THE Run_Runtime SHALL 在应用层编排 Workflow_Phase，不得在 FastAPI_Adapter、TUI 或 Web_Run_View 中推进阶段状态。
2. THE Run_Runtime SHALL 复用既有 ReAct_Agent 执行阶段内模型调用、工具调用和上下文推进。
3. WHEN Workflow_Phase 命中 max rounds、token budget、approval required 或 guardrail blocked 等既有停止原因, THE Run_Runtime SHALL 保留既有暂停、审批或失败语义。
4. IF Checkpoint_Recovery 启用, THEN Workflow_Run_State SHALL 能随 Run 恢复读取最近可用阶段状态或保守进入可观察失败状态。
5. THE Checkpoint_Recovery SHALL 不因阶段六要求承诺外部副作用 exactly-once。
6. THE HITL_Approval_Recovery SHALL 继续使用既有审批恢复入口，Workflow_Phase SHALL 不新增独立审批系统。
7. THE Guardrail_v1 SHALL 保持阶段五边界，Workflow_Phase SHALL 不要求模型完成后或工具执行后新增 guardrail 运行时闭环。

### 需求 5：治理多 Agent 角色、委派和控制转移

**用户故事：** 作为多 Agent 工作流使用者，我希望每个 Agent 的职责、交接方式和边界清晰可见，以便复杂协作不会退化为不可控递归。

#### 验收标准

1. THE Workflow_Definition SHALL 能声明允许参与的 Agent_Role 以及每个 Agent_Role 可使用的 Delegation 或 Handoff 能力。
2. WHEN Multi_Agent_Collaboration 触发 Delegation, THE Run_Runtime SHALL 记录发起角色、目标角色或目标 Agent、子任务摘要和结果摘要。
3. WHEN Multi_Agent_Collaboration 触发 Handoff, THE Run_Runtime SHALL 记录发起角色、目标 Agent、控制转移原因和最终结果摘要。
4. THE Collaboration_Limit SHALL 至少支持最大递归深度、最大并行委派数量、最大 handoff 次数和每阶段最大 revise 次数。
5. IF Collaboration_Limit 被命中, THEN Run_Runtime SHALL 停止对应协作动作并暴露可观测原因。
6. THE Delegation SHALL 保持既有“子 Agent 结果回灌给父 Agent 继续推理”语义。
7. THE Handoff SHALL 保持既有“目标 Agent 接管上下文并产出最终回复”语义。
8. THE Multi_Agent_Collaboration SHALL 不允许通过重复 Delegation 或 Handoff 绕过既有 `AGENT_MAX_DELEGATION_DEPTH` 约束。

### 需求 6：建立父子 Run 或步骤追踪可观测关系

**用户故事：** 作为运行观察者，我希望能看清一个工作流中的父子任务、委派步骤和 handoff 步骤，以便排查失败和复盘协作过程。

#### 验收标准

1. THE Multi_Agent_Collaboration SHALL 支持 Parent_Child_Run_Link 或 Step_Trace_Link 至少一种可观测关系表达。
2. WHEN Multi_Agent_Collaboration 创建子 Run, THE Parent_Child_Run_Link SHALL 记录 parent run id、child run id、Agent_Role、触发 Workflow_Phase 和创建原因。
3. WHEN Multi_Agent_Collaboration 不创建子 Run, THE Step_Trace_Link SHALL 记录 run id、Workflow_Phase、Agent_Role、Delegation 或 Handoff 类型和步骤摘要。
4. FOR ALL Parent_Child_Run_Link, THE Run_Snapshot 或 Run_Event_Stream SHALL 能让调用方从父 Run 找到子 Run 关系。
5. FOR ALL Step_Trace_Link, THE Run_Event_Stream SHALL 能让调用方按事件顺序观察协作步骤。
6. THE Parent_Child_Run_Link SHALL 不要求阶段六必须把所有 Delegation 都改造成独立子 Run。
7. THE Step_Trace_Link SHALL 不要求阶段六必须替换既有结构化 trace 存储的全部数据模型。

### 需求 7：对外展示工作流与协作状态

**用户故事：** 作为 API、TUI 和 Web 使用者，我希望能看到工作流阶段和协作关系，以便正确判断任务进度、等待点和后续操作。

#### 验收标准

1. THE FastAPI_Adapter SHALL 透传 Run_Snapshot 中的 Standard_Workflow、Workflow_Run_State 和协作摘要字段。
2. THE FastAPI_Adapter SHALL 透传 Run_Event_Stream 中的工作流阶段和协作事件。
3. THE TUI SHALL 展示当前 Standard_Workflow、Workflow_Phase 和最近协作摘要。
4. THE Web_Run_View SHALL 展示当前 Standard_Workflow、Workflow_Phase、阶段历史摘要和最近协作摘要。
5. THE FastAPI_Adapter、TUI 和 Web_Run_View SHALL 不复制 Workflow_Selector、Workflow_Phase 推进、Collaboration_Limit 或 Checkpoint_Recovery 判定逻辑。
6. WHEN Run_Event_Stream replay 过期, THE Web_Run_View SHALL 保持既有 polling fallback 观察语义。
7. THE Web_Run_View SHALL 不因新增工作流字段破坏既有 checkpoint、recoverable、task_classification 和 guardrail_summary 展示。

### 需求 8：保留 durable workflow engine 为评估/可选项

**用户故事：** 作为技术负责人，我希望阶段六能评估外部 durable workflow engine 的取舍，但不把它作为 v1 必然依赖，以便控制迁移风险。

#### 验收标准

1. THE Durable_Workflow_Engine_Evaluation SHALL 可以记录 Temporal、LangGraph、Dapr Workflow 或其他方案的适配性、收益、风险和迁移成本。
2. THE Durable_Workflow_Engine_Evaluation SHALL 不要求阶段六新增运行时依赖、部署组件或锁文件变更。
3. THE Workflow_Definition SHALL 能在现有 Run_Runtime 内运行，不依赖外部 durable workflow runtime 才能通过验收。
4. IF 后续决定引入外部 durable workflow runtime, THEN 该决策 SHALL 进入新的 requirement/design/tasks 流程，不得在阶段六 v1 实现中隐式引入。
5. THE Verification_Process SHALL 能在不安装 Temporal、LangGraph 或 Dapr Workflow 的情况下通过。

### 需求 9：测试、回归与架构边界

**用户故事：** 作为维护者，我希望阶段六有覆盖工作流、协作、恢复和 adapter 透传的测试，以便证明新增编排不会破坏前五阶段能力。

#### 验收标准

1. THE Test_Suite SHALL 覆盖 Workflow_Definition、Workflow_Registry、Workflow_Selector 和 Workflow_Run_State 的领域行为。
2. THE Test_Suite SHALL 覆盖 `research`、`code_change`、`report` 和 `batch_processing` 的注册与选择路径。
3. THE Test_Suite SHALL 覆盖 Workflow_Phase 切换、完成、失败和 revise 次数限制。
4. THE Test_Suite SHALL 覆盖 Delegation、Handoff、Collaboration_Limit、Parent_Child_Run_Link 或 Step_Trace_Link 的可观测输出。
5. THE Test_Suite SHALL 覆盖 Checkpoint_Recovery 启用时 Workflow_Run_State 恢复或保守失败行为。
6. THE Test_Suite SHALL 回归 HITL_Approval_Recovery，不允许阶段六新增审批编排破坏既有 awaiting approval 与 resume 语义。
7. THE Test_Suite SHALL 回归 Guardrail_v1 默认 observe、critical enforce 阻断和字段透传边界。
8. THE Test_Suite SHALL 覆盖 FastAPI_Adapter、TUI 和 Web_Run_View 对新增字段的透传展示，并验证 adapter 不复制编排逻辑。
9. THE Test_Suite SHALL 覆盖 DDD_Hexagonal_Architecture 静态边界，确保领域层不导入 application、infrastructure、FastAPI、Redis 或外部 workflow runtime。
10. THE Verification_Process SHALL 包含后端全量 `env PYTHONPATH=src uv run --frozen pytest`。
11. THE Verification_Process SHALL 包含前端 `npm run lint` 与 `npm run build`。
