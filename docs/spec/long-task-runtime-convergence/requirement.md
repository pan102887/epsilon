# 需求文档：长任务运行时收敛修复

## 简介

在阶段五与阶段六的现有实现上，仓库已经具备后台 `Run`、事件流、checkpoint recovery、基础 guardrail、轻量 workflow 与多 Agent 协作能力，但仍存在若干影响长任务稳定性、可观测性与治理一致性的收敛缺口：guardrail 事件未形成完整 `Run` 事件闭环，`guardrail_summary` 已透传但未形成运行时累计单一事实源，guardrail 的 `require_approval` 尚未复用既有 HITL 审批恢复路径，`risk_gate_required` 未在全部分段执行入口完成接线，协作摘要字段在 `latest_steps` / `recent_steps` 间存在 schema 漂移，阶段六的角色能力与 workflow 级交接治理仍偏弱。

本特性目标是在不脱离当前仓库 DDD/六边形架构、既有 `Run_Runtime` 与 checkpoint ledger 边界的前提下，参照业内主流经验完成运行时收敛修复。主导原则为：以事件驱动的 guardrail 可观测性作为事实来源，以 `RunSnapshot` 摘要作为统一对外视图，以既有 `HITL_Approval_Recovery` 复用代替新审批系统，以确定性运行时统计代替文本推断，以最小权限的角色能力治理多 Agent 协作，并以保守持久化语义处理 workflow 与 child run 恢复。

本特性分三层切片推进：P0 聚焦 guardrail `Run` 事件闭环、`Guardrail_Summary` 累计、`require_approval` 接入既有 HITL、`Risk_Gate_Signal` 接线与协作摘要 schema 归一；P1 聚焦 token、耗时、上下文增长、重复工具调用、连续失败与估算成本等运行时统计来源；P2 聚焦阶段六角色能力强制、workflow 级 handoff 可观测与执行策略深化、以及保守的 child run 编排语义。

本特性明确不在本期范围内的内容包括：

- 不引入 Temporal、LangGraph、Celery、Dapr Workflow 或其他外部 durable workflow engine，也不新增为其服务的运行时依赖。
- 不承诺 `Checkpoint_Ledger` 之外的 exactly-once 外部副作用语义。
- 不破坏当前 Chat、Task、Run、continue、approval recovery 的默认兼容行为。
- 不新增独立审批系统；guardrail 审批必须复用既有 `HITL_Approval_Recovery`。
- 不在 `FastAPI_Adapter`、`CLI_TUI_Adapter` 或 `Web_Run_View` 中复制 guardrail 或 workflow 策略判断逻辑。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 后台运行时 | Run_Runtime | 仓库当前既有的后台长任务运行体系，包含 `RunApplicationService`、worker、`RunSnapshot`、事件流、continue、approve、cancel 与 checkpoint recovery 等能力。 |
| 后台运行 | Run | `Run_Runtime` 中的单个后台执行实例。 |
| 运行快照 | RunSnapshot | 对外查询 `Run` 最新状态的数据对象。 |
| 运行事件流 | Run_Event_Stream | `Run` 的事件追加、轮询与 SSE 订阅能力。 |
| 运行事件类型 | RunEventType | `Run_Event_Stream` 中使用的事件类型枚举。 |
| 护栏决策 | Guardrail_Decision | guardrail 对某次运行时状态、工具调用或预算条件作出的确定性动作判断。 |
| 护栏摘要 | Guardrail_Summary | 从 `Guardrail_Decision` 与运行时累计状态派生出的统一对外 JSON-safe 摘要，是 Run 层护栏状态的单一事实源。 |
| 护栏运行时统计 | Guardrail_Runtime_Stats | 用于支持 `Guardrail_Decision` 的运行时统计集合，包括 token、耗时、上下文增长、重复工具调用、连续失败与估算成本等。 |
| ReAct 工具执行 | ReAct_Tool_Execution | `react_agent_adapter` 中在模型产生工具调用后、真实工具执行前后处理工具结果与异常的运行路径。 |
| 人工审批中断 | Approval_Interrupt | 仓库既有的人类审批中断对象，用于在工具执行前挂起当前执行并等待恢复。 |
| 人工审批恢复 | HITL_Approval_Recovery | 仓库既有的 awaiting approval、审批提交与恢复执行路径。 |
| 风险门禁信号 | Risk_Gate_Signal | 当前分段执行中的 `risk_gate_required` 停止信号，用于阻止自动续跑并要求人工介入。 |
| 分段续跑决策 | Segment_Continuation_Decision | `decide_next_segment(...)` 产出的是否进入下一段执行的决定。 |
| 协作摘要 | Collaboration_Summary | `Run` 对 workflow / 多 Agent 协作情况的对外摘要。 |
| 协作摘要模式 | Collaboration_Summary_Schema | `Collaboration_Summary` 的规范字段模型；当前 `recent_steps` / `latest_steps` 存在歧义，本特性统一以 `latest_steps` 为规范字段。 |
| 工作流运行状态 | Workflow_Run_State | 绑定在 `RunSnapshot` 上的 workflow 名称、当前 phase、阶段历史、handoff 状态与相关摘要。 |
| 角色能力声明 | Role_Capability | 对 workflow 角色可执行的工具类别、delegation、handoff 与 child run 权限的最小权限声明。 |
| 工作流执行策略 | Workflow_Execution_Policy | 控制 workflow 阶段推进、handoff 规则、review/revise 限额与 child run 使用条件的确定性执行策略。 |
| 子 Run 编排 | Child_Run_Orchestration | 当 workflow 选择使用父子 `Run` 拆分执行时，对 parent-child 链接、等待、恢复与收敛点的编排语义。 |
| 检查点账本 | Checkpoint_Ledger | 仓库既有 checkpoint 与工具结果 ledger 组合能力，用于恢复时避免无根据重放。 |
| FastAPI 适配器 | FastAPI_Adapter | `/api/runs*` 等 HTTP adapter，仅负责协议映射与字段透传。 |
| CLI/TUI 适配器 | CLI_TUI_Adapter | CLI slash command 与 TUI Run 展示路径，仅负责读取并展示 `RunSnapshot` 与事件。 |
| Web Run 视图 | Web_Run_View | 前端 Run 监控视图，仅负责读取并展示 `RunSnapshot` 与事件。 |
| 领域端口 | Domain_Port | 定义在领域层的纯模型与 Port 接口，不依赖基础设施或框架。 |
| 基础设施适配器 | Infrastructure_Adapter | 对 `Domain_Port` 的具体实现，例如文件、Redis、HTTP、前端或工具 adapter。 |
| 配置来源 | Config_Source | 本仓库新增默认配置的主来源规范，即 `epsilon-boot/config.properties`，环境变量仅用于覆盖。 |
| 公共代码接口面 | Public_Code_Surface | 本特性新增或修改的公开模块、类、公开函数与公开方法集合。 |

## 需求

### 需求 1：P0 护栏事件闭环与摘要单一事实源

**用户故事：** 作为运行观察者，我希望 guardrail 的评估、阻断与累计摘要都能在 Run 层形成统一事实来源，以便长任务的暂停、阻断、恢复与复盘都可被稳定追踪。

#### 验收标准

1. WHEN Run_Runtime 产生 Guardrail_Decision, THE Run_Event_Stream SHALL 追加与该次评估结果一一对应的 RunEventType 事件，而不是只把结果留在 `ToolMessage.metadata` 中。
2. FOR ALL 写入 Run_Event_Stream 的 Guardrail_Decision 结果, THE Guardrail_Summary SHALL 从同一运行时事实源累计更新，而不是由 FastAPI_Adapter、CLI_TUI_Adapter 或 Web_Run_View 侧二次推导。
3. THE Guardrail_Summary SHALL 以 JSON-safe 结构表达至少最近动作、最近原因、评估次数、阻断次数、审批请求次数、最近事件游标、最近更新时间与最近 Guardrail_Runtime_Stats 快照。
4. WHEN RunSnapshot 在任一护栏相关状态变化后被查询, THE RunSnapshot SHALL 暴露最新的 Guardrail_Summary。
5. WHEN Checkpoint_Ledger 恢复一个可恢复的 Run, THE Guardrail_Summary SHALL 从最近持久化的 Run 状态恢复，或被显式标记为保守过期状态，而不是被静默清空。

### 需求 2：P0 护栏审批复用既有 HITL 恢复链路

**用户故事：** 作为系统维护者，我希望 guardrail 的 `require_approval` 直接复用已有审批恢复能力，以便避免出现两套并行审批语义和恢复入口。

#### 验收标准

1. WHEN Guardrail_Decision 的动作为需要审批, THE ReAct_Tool_Execution SHALL 在真实工具执行前把该结果转换为 Approval_Interrupt。
2. WHEN Approval_Interrupt 来源于 Guardrail_Decision, THE HITL_Approval_Recovery SHALL 复用既有 awaiting approval 状态、审批提交入口、上下文保存与恢复流程，而不是创建第二套审批机制。
3. IF HITL_Approval_Recovery 对来源于 Guardrail_Decision 的审批执行通过恢复, THEN Run_Runtime SHALL 在原有 Run 上继续执行并更新 Guardrail_Summary 与 Run_Event_Stream，且不得重复追加原始 user message。
4. IF HITL_Approval_Recovery 对来源于 Guardrail_Decision 的审批执行拒绝、过期或消费失败, THEN RunSnapshot SHALL 暴露与现有审批语义一致的等待态或终态，并保留最近的 Guardrail_Summary。

### 需求 3：P0 风险门禁接线与协作摘要模式归一

**用户故事：** 作为运行治理维护者，我希望分段风险门禁在所有入口都能一致生效，并且协作摘要字段只有一个规范 schema，以便自动续跑、CLI/TUI 和前端展示不会出现分叉语义。

#### 验收标准

1. WHEN Run_Runtime 为聊天路径或任务路径计算 Segment_Continuation_Decision, THE Risk_Gate_Signal SHALL 被传入该决策输入。
2. IF Risk_Gate_Signal 为真, THEN THE Segment_Continuation_Decision SHALL 以 `risk_gate_required` 作为停止原因阻止自动续跑。
3. THE Collaboration_Summary_Schema SHALL 使用 `latest_steps` 作为规范字段表达最近协作步骤。
4. FOR ALL FastAPI_Adapter、CLI_TUI_Adapter 与 Web_Run_View 的读写路径, THE Collaboration_Summary_Schema SHALL 不再要求同时维护 `recent_steps` 与 `latest_steps` 两套字段语义。
5. IF RunSnapshot 含有历史 Collaboration_Summary 数据, THEN FastAPI_Adapter、CLI_TUI_Adapter 与 Web_Run_View SHALL 以向后兼容方式把历史数据映射到规范的 Collaboration_Summary_Schema。

### 需求 4：P1 运行时统计来源与确定性预算评估

**用户故事：** 作为护栏策略维护者，我希望 token、耗时、上下文增长、重复调用、连续失败与估算成本都来自确定性的运行时统计，以便预算与风险判断可测试、可解释且可恢复。

#### 验收标准

1. WHEN Run_Runtime 完成一次模型调用, THE Guardrail_Runtime_Stats SHALL 使用该次调用返回的 usage 与运行时钟更新累计 token、累计耗时、上下文增长与估算成本。
2. WHEN ReAct_Tool_Execution 完成一次工具调用或工具失败, THE Guardrail_Runtime_Stats SHALL 使用确定性运行数据更新重复工具调用次数、连续失败次数与工具风险观察结果。
3. FOR ALL Guardrail_Decision 的运行时评估, THE Guardrail_Runtime_Stats SHALL 来源于已持久化的 Run 状态、当前分段状态与真实模型/工具执行记录，而不是来源于 assistant 自然语言文本推断。
4. WHEN Guardrail_Runtime_Stats 命中已配置阈值, THE Run_Event_Stream SHALL 追加对应的护栏评估事件，且 THE Guardrail_Summary SHALL 累计该次命中结果。
5. IF Guardrail_Runtime_Stats 无法获得某模型的估算成本数据, THEN THE Guardrail_Runtime_Stats SHALL 把成本标记为不可用而不是默认阻断 Run_Runtime。
6. WHEN Checkpoint_Ledger 恢复一个已保存的 Run, THE Guardrail_Runtime_Stats SHALL 不重复累计已经提交的 token、工具调用与失败记录。

### 需求 5：P2 角色能力最小权限强制

**用户故事：** 作为多 Agent 工作流维护者，我希望每个角色的可执行能力都被显式声明并默认最小化，以便复杂任务不会因隐式授权而越权委派、交接或创建子运行。

#### 验收标准

1. FOR ALL Workflow_Run_State 中声明的角色, THE Role_Capability SHALL 显式定义允许的工具类别、delegation、handoff 与 child run 创建权限。
2. WHEN Run_Runtime 发现某次工具调用、delegation、handoff 或 child run 创建超出 Role_Capability, THE Run_Runtime SHALL 在真实执行前拒绝该动作并把原因写入 Run_Event_Stream。
3. IF Workflow_Run_State 切换了当前活动角色, THEN Run_Runtime SHALL 在下一次工具调用、delegation、handoff 或 child run 判定前重新应用对应的 Role_Capability。
4. THE Role_Capability SHALL 采用最小权限默认值，使未声明能力默认被拒绝而不是被隐式允许。
5. IF Workflow_Run_State 未启用角色能力治理, THEN Run_Runtime SHALL 保持当前兼容默认行为，而不是无配置地突然开启更严格限制。

### 需求 6：P2 工作流级交接可观测与执行策略深化

**用户故事：** 作为长任务用户，我希望控制转移不仅出现在工具层元数据里，还能在 workflow 层变成可观察、可执行的状态，以便我能理解任务为何转交、谁在负责以及何时需要 revise 或 review。

#### 验收标准

1. WHEN Run_Runtime 在角色或 Agent 之间发生控制转移, THE Workflow_Run_State SHALL 记录独立于工具消息文本的 workflow 级交接状态。
2. WHEN Workflow_Execution_Policy 声明了 phase 级 handoff、review 或 revise 约束, THE Run_Runtime SHALL 在执行顺序中强制应用该策略，而不是只把它作为展示元数据。
3. FOR ALL workflow 级交接转换, THE Run_Event_Stream SHALL 记录来源角色、目标角色或目标 Agent、触发原因与结果性的 Workflow_Run_State。
4. IF Workflow_Execution_Policy 命中阶段重试或 revise 次数上限, THEN Run_Runtime SHALL 停止继续推进并在 RunSnapshot 与 Run_Event_Stream 中暴露停止原因。
5. THE FastAPI_Adapter、CLI_TUI_Adapter 与 Web_Run_View SHALL 展示 Workflow_Run_State 与交接事件，但不得复制 Workflow_Execution_Policy 的判断逻辑。

### 需求 7：P2 保守的子 Run 编排与恢复语义

**用户故事：** 作为运行时架构维护者，我希望 child run 仅在有明确策略时才被启用，并且其恢复与对账语义保持保守，以便在不引入外部工作流引擎的前提下逐步提升多阶段协作可靠性。

#### 验收标准

1. IF Workflow_Execution_Policy 未要求 Child_Run_Orchestration, THEN Run_Runtime SHALL 保持当前 delegation 与 handoff 的既有 in-run 路径。
2. WHEN Child_Run_Orchestration 被启用, THE Run_Event_Stream SHALL 持久化 parent-child 链接、当前所有权状态与终态对账节点。
3. WHEN Child_Run_Orchestration 需要等待子 Run 结果, THE Checkpoint_Ledger SHALL 在父 Run 进入等待态前保存恢复所需的 Workflow_Run_State。
4. IF 父 Run 或子 Run 发生恢复, THEN Child_Run_Orchestration SHALL 从最近持久化的对账节点继续，或进入保守的可恢复失败状态，而不是假定子流程已经成功完成。
5. THE Child_Run_Orchestration SHALL 在现有 Run_Runtime 内运行，而不是要求引入外部 durable workflow engine 才能满足本需求。
6. THE Child_Run_Orchestration SHALL 不宣称超出 Checkpoint_Ledger 边界的 exactly-once 外部副作用保证。

### 需求 8：架构边界、配置来源与默认兼容性

**用户故事：** 作为仓库维护者，我希望这次收敛修复继续遵守现有 DDD/六边形边界、配置约束与默认兼容行为，以便实现可以渐进上线且不破坏既有运行路径。

#### 验收标准

1. FOR ALL 本特性新增的默认开关、阈值与策略配置, THE Config_Source SHALL 使用 `epsilon-boot/config.properties` 作为主配置来源。
2. FOR ALL 本特性的实现变更, THE Domain_Port SHALL 保持不直接依赖 Infrastructure_Adapter、FastAPI、Redis 或外部 workflow engine。
3. IF 未开启新的收敛修复配置或策略, THEN Run_Runtime SHALL 保持当前 Chat、Task、Run、continue 与 approval 默认语义不变。
4. THE FastAPI_Adapter、CLI_TUI_Adapter 与 Web_Run_View SHALL 只从 RunSnapshot 与 Run_Event_Stream 读取并展示 Guardrail_Summary、Collaboration_Summary_Schema 与 Workflow_Run_State，而不是在 adapter 或前端重复执行 Guardrail_Decision 或 Workflow_Execution_Policy 判断。
5. FOR ALL 本特性涉及的 Public_Code_Surface, THE Public_Code_Surface SHALL 提供符合仓库约定的中文 docstring。
