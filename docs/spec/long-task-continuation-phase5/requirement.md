# 需求文档：长任务智能调度与护栏阶段五

## 简介

阶段五在已完成的后台 Run runtime、分段执行与持久化 checkpoint recovery 基础上，收敛交付 v1 子集：确定性任务分类、统一 guardrail 领域决策模型、静态策略、配置读取、工具风险分级、Run/API/TUI/Web 字段透传，以及 ReAct 工具执行前对 critical 工具的显式 `enforce` 阻断。

本阶段默认以 `observe` 模式运行，不改变既有 Chat、Task、Run、continue、approval recovery 与 checkpoint recovery 行为。显式配置 `enforce` 时，v1 只要求在真实工具执行前阻断 critical 工具；不要求完整运行时 guardrail 事件闭环，不要求 `guardrail_summary` 在模型调用后或工具执行后动态累计更新，不要求把 `require_approval` 接入既有 HITL 审批恢复流程，也不要求 checkpoint recovery 后恢复 guardrail 累计状态。

阶段五不引入外部 workflow runtime，不使用 LLM 做任务分类，不把金额估算作为硬停止条件，不新增独立审批系统，也不扩大阶段二分段执行或阶段四 checkpoint recovery 的既有语义。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 后台运行 | Run | 阶段三引入的后台长任务执行实例，具备状态、快照、事件和继续/取消等操作。 |
| 运行快照 | RunSnapshot | 对外查询 Run 最新状态的数据对象，包含状态、结果、错误、checkpoint、任务分类和 guardrail 摘要等字段。 |
| 运行事件类型 | RunEventType | Run 事件流中的事件枚举；阶段五 v1 可新增预留事件类型，但不要求所有预留事件都有完整运行时写入闭环。 |
| 任务分类 | Task_Classification | 使用确定性规则得出的任务类型，取值为 `short_qa`、`tool_task`、`long_task` 或 `batch_task`。 |
| 护栏模式 | Guardrail_Mode | Guardrail 的运行模式，取值为 `observe` 或 `enforce`。 |
| 护栏决策 | Guardrail_Decision | 静态策略根据上下文和配置返回的动作、原因、消息和元数据。 |
| 护栏摘要 | Guardrail_Summary | 对外展示的 guardrail 决策摘要字段；阶段五 v1 只要求字段可透传，运行时可为空。 |
| 静态护栏策略 | Static_Guardrail_Policy | 基于确定性规则和配置返回 Guardrail_Decision 的策略实现，不访问外部系统。 |
| 金额预算 v1 | Cost_Budget_v1 | 阶段五 v1 保留的模型单价配置和估算入口，不参与硬停止。 |
| ReAct 工具执行流程 | ReAct_Tool_Execution | ReAct Agent 在模型产生工具调用后执行单个工具并回灌 ToolMessage 的流程。 |
| 工具基类 | Tool_Base | 所有工具继承的抽象基类，提供名称、schema、执行和静态元数据入口。 |
| 工具风险等级 | Tool_Risk_Level | 工具的静态风险标签，取值为 `low`、`medium`、`high` 或 `critical`。 |
| Critical 工具 | Critical_Tool | 风险等级为 `critical` 的工具，阶段五 v1 在 `enforce` 模式下必须在真实执行前阻断。 |
| High 工具 | High_Risk_Tool | 风险等级为 `high` 的工具，阶段五 v1 默认不阻断，审批链路不属于本阶段运行时验收范围。 |
| 内置读类工具 | Builtin_Read_Tool | 只读取本地或远端信息、默认不产生写入副作用的内置工具。 |
| 委派类工具 | Delegation_Tool | 把任务委派给其他 Agent 或子流程的工具。 |
| 文件写入与 HTTP 请求类工具 | File_Write_And_Http_Tool | 会写入文件或发起 HTTP 请求的工具集合。 |
| Shell/Python 执行类工具 | Shell_Python_Tool | 可执行 shell 命令或 Python 代码的工具集合。 |
| 人工审批恢复 | HITL_Approval_Recovery | 阶段三/四已有的审批中断和恢复流程；阶段五 v1 不要求 guardrail `require_approval` 动作接入该流程。 |
| 持久化检查点恢复 | Checkpoint_Recovery | 阶段四已有的中断恢复能力；阶段五 v1 不要求额外保存或恢复 guardrail 累计状态。 |
| FastAPI 适配器 | FastAPI_Adapter | `/api/runs*` HTTP adapter，负责 DTO 转换和字段透传，不承载 guardrail 策略判断。 |
| TUI | TUI | 命令行交互界面的 Run 展示入口。 |
| Web Run 视图 | Web_Run_View | 前端 Run View 页面，用于展示 Run 快照和事件信息。 |
| 测试套件 | Test_Suite | 覆盖阶段五 v1 领域模型、策略、配置、风险等级、接入和透传行为的自动化测试集合。 |
| 验证流程 | Verification_Process | 阶段五 v1 完成后需要执行的后端全量测试和前端 lint/build 命令。 |

## 需求

### 需求 1：确定性任务分类

**用户故事：** 作为 Run 使用者，我希望系统用可解释的静态规则标记任务类型，以便在不引入额外模型调用的情况下观察长任务执行形态。

#### 验收标准

1. THE Task_Classification SHALL 支持 `short_qa`、`tool_task`、`long_task` 和 `batch_task` 四类取值。
2. THE Task_Classification SHALL 只依赖 Run 入口类型、工具可用性、Run 可继续状态、分段或 checkpoint 元数据，以及 payload 中是否包含明确批量输入。
3. THE Task_Classification SHALL 不调用 LLM 或外部服务。
4. WHEN Run 创建且 guardrail 策略可用, THE RunSnapshot SHALL 暴露 `task_classification`。
5. WHEN Run 创建且 Task_Classification 成功生成, THE RunEventType SHALL 允许写入 `TASK_CLASSIFIED` 事件。

### 需求 2：统一护栏领域模型与静态策略

**用户故事：** 作为维护者，我希望 guardrail 决策先沉淀为领域值对象和静态策略，以便后续扩展预算、审批和事件闭环时有稳定边界。

#### 验收标准

1. THE Guardrail_Decision SHALL 包含动作、原因、消息、模式、估算成本和元数据。
2. THE Guardrail_Mode SHALL 支持 `observe` 和 `enforce`。
3. THE Guardrail_Decision SHALL 支持 `allow`、`observe`、`require_approval` 和 `stop` 动作值。
4. THE Guardrail_Decision SHALL 在 `require_approval` 或 `stop` 动作下提供稳定对外主终止原因 `guardrail_blocked`。
5. THE Guardrail_Summary SHALL 能从 Guardrail_Decision 转换为 JSON-safe 字段。
6. THE Static_Guardrail_Policy SHALL 能根据配置和 Guardrail_Decision 输入上下文返回确定性决策。

### 需求 3：默认 observe 不改变行为

**用户故事：** 作为现有 Chat、Task 与 Run 用户，我希望阶段五默认只观察不干预，以便升级后既有执行语义保持兼容。

#### 验收标准

1. THE Guardrail_Mode SHALL 默认配置为 `observe`。
2. WHEN Guardrail_Mode 为 `observe`, THE ReAct_Tool_Execution SHALL 不因 Tool_Risk_Level 为 `critical` 而阻断真实工具执行。
3. WHEN Guardrail_Mode 为 `observe`, THE Run SHALL 不改变既有 Chat、Task、Run、continue、HITL_Approval_Recovery 与 Checkpoint_Recovery 的状态流转。
4. IF Guardrail_Summary 无运行时决策来源, THEN THE RunSnapshot SHALL 允许 `guardrail_summary` 为空。

### 需求 4：工具风险分级与 critical 前置阻断

**用户故事：** 作为系统维护者，我希望高危工具有静态风险标签，并在显式强制模式下先阻断 critical 工具，以便降低误执行高风险命令的概率。

#### 验收标准

1. THE Tool_Risk_Level SHALL 支持 `low`、`medium`、`high` 和 `critical` 四类取值。
2. THE Tool_Base SHALL 暴露 `risk_level`，未知或未覆盖工具默认视为 `high`。
3. THE Builtin_Read_Tool SHALL 默认为 `low`。
4. THE Delegation_Tool SHALL 默认为 `medium`。
5. THE File_Write_And_Http_Tool SHALL 默认为 `high`。
6. THE Shell_Python_Tool SHALL 默认为 `critical`。
7. WHEN Guardrail_Mode 为 `enforce` 且 Critical_Tool 即将执行, THE ReAct_Tool_Execution SHALL 在真实工具执行前阻断该工具。
8. WHEN Guardrail_Mode 为 `enforce` 且 Critical_Tool 被阻断, THE ReAct_Tool_Execution SHALL 把阻断结果作为带错误元数据的工具消息回灌给上下文。
9. WHEN High_Risk_Tool 即将执行且 high-risk enforce 未显式开启, THE ReAct_Tool_Execution SHALL 不因阶段五 v1 默认策略阻断该工具。
10. IF Guardrail_Decision 动作为 `require_approval`, THEN THE HITL_Approval_Recovery SHALL 不要求在阶段五 v1 中接入 Guardrail_Decision。

### 需求 5：预算、进展与金额估算边界

**用户故事：** 作为后续阶段维护者，我希望 v1 保留预算、进展和失败原因的模型入口，但不把尚未接入的运行时累计能力承诺为已完成范围。

#### 验收标准

1. THE Guardrail_Decision 输入上下文 SHALL 能表达 token、耗时、上下文增长、重复工具调用次数和连续失败次数。
2. THE Static_Guardrail_Policy SHALL 能在输入上下文命中配置阈值时返回 `observe` 或 `stop` 决策。
3. WHEN Guardrail_Mode 为 `observe` 且 Static_Guardrail_Policy 命中阈值, THE Static_Guardrail_Policy SHALL 返回不阻断执行的 `observe` 决策。
4. WHEN Guardrail_Mode 为 `enforce` 且 Static_Guardrail_Policy 命中阈值, THE Static_Guardrail_Policy SHALL 返回 `stop` 决策。
5. THE ReAct_Tool_Execution SHALL 不要求在阶段五 v1 中接入模型调用完成后的 Guardrail_Decision 运行时评估。
6. THE ReAct_Tool_Execution SHALL 不要求在阶段五 v1 中接入工具执行后的 Guardrail_Decision 运行时评估。
7. THE Cost_Budget_v1 SHALL 只保留模型单价配置和估算字段入口，不作为硬停止条件。

### 需求 6：Run 字段透传与事件预留

**用户故事：** 作为 API、TUI 和 Web 使用者，我希望新增分类和护栏字段可以稳定透传，以便前端先展示可观测信息而不复制策略判断。

#### 验收标准

1. THE RunSnapshot SHALL 暴露 `task_classification` 字段。
2. THE RunSnapshot SHALL 暴露 `guardrail_summary` 字段。
3. THE RunEventType SHALL 新增 `TASK_CLASSIFIED`、`GUARDRAIL_EVALUATED` 和 `GUARDRAIL_BLOCKED` 枚举值。
4. THE RunEventType SHALL 把 `GUARDRAIL_EVALUATED` 和 `GUARDRAIL_BLOCKED` 作为阶段五 v1 预留事件类型，不要求完整运行时事件写入闭环。
5. THE FastAPI_Adapter SHALL 透传 RunSnapshot 的 `task_classification` 与 `guardrail_summary` 字段。
6. THE TUI SHALL 透传展示 RunSnapshot 的 `task_classification` 与 `guardrail_summary` 字段。
7. THE Web_Run_View SHALL 透传展示 RunSnapshot 的 `task_classification` 与 `guardrail_summary` 字段。
8. THE FastAPI_Adapter、TUI 与 Web_Run_View SHALL 不复制 Guardrail_Decision 的策略判断逻辑。
9. THE Checkpoint_Recovery SHALL 保持阶段四既有恢复语义，不要求保存、恢复或累计 Guardrail_Summary 状态。

### 需求 7：测试与回归

**用户故事：** 作为维护者，我希望阶段五 v1 有聚焦回归覆盖，以便证明新增模型、策略、配置、风险标签和透传字段不会破坏既有能力。

#### 验收标准

1. THE Test_Suite SHALL 覆盖 Task_Classification 的确定性分类规则。
2. THE Test_Suite SHALL 覆盖 Guardrail_Decision、Guardrail_Summary、Guardrail_Mode、Tool_Risk_Level 和 `guardrail_blocked` 主终止原因。
3. THE Test_Suite SHALL 覆盖 Guardrail_Mode 默认 `observe`、非法配置 fail-fast、阈值配置转换和模型单价格式校验。
4. THE Test_Suite SHALL 覆盖 Tool_Risk_Level 默认值和内置工具风险等级。
5. THE Test_Suite SHALL 覆盖 `observe` 不阻断 Critical_Tool。
6. THE Test_Suite SHALL 覆盖 `enforce` 在真实执行前阻断 Critical_Tool。
7. THE Test_Suite SHALL 覆盖 High_Risk_Tool 默认不阻断。
8. THE Test_Suite SHALL 覆盖 RunSnapshot、FastAPI_Adapter、TUI 和 Web_Run_View 对新增字段的透传。
9. THE Verification_Process SHALL 包含后端全量 `env PYTHONPATH=src uv run --frozen pytest`。
10. THE Verification_Process SHALL 包含前端 `npm run lint` 与 `npm run build`。
