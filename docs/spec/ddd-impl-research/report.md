# 业界 DDD 主流实现方案调研复用与 P2 后续任务评估

> 范围：后端 `epsilon-boot`（FastAPI + DDD 六边形架构），P2（Agent Loop 归属重划）两片落地后的**调研复用 + 后续任务评估 + 方向收敛登记**。
> 依据：`docs/spec/ddd-gap-analysis/report.md`（业界调研基线）、ADR-0010（切分判据）、ADR-0011 / ADR-0012（P2 首片 / 第二片决策）、`docs/steering/`（架构与变更纪律）。
> 截至日期：2026-07-07。代码事实以当前 `TEST` 分支为准（本报告所引符号与 LOC 均已在该分支实测核对）。
> 定位：本 feature 为**调研 + 评估 + 方向决策登记型**任务，全程**不做任何生产源码行为变更**（`src/` 零改动），交付物仅限 spec 文档 / ADR 登记 / 文档索引同步。

## 一、调研基线复用确认

本报告**不重复业界主流 DDD 实现方案调研**，直接引用既有 `docs/spec/ddd-gap-analysis/report.md`（`Industry_Research_Baseline`）作为「业界主流 DDD 实现方案调研 + 本项目差距对照」的评估基线。基线已系统沉淀：

- 业界主流 DDD 的战略/战术/架构落地范式（限界上下文、值对象/实体/聚合根/领域服务/仓储/领域事件、六边形架构、充血优于贫血、反过度设计原则）；
- 本项目对照结论：**架构骨架与工程治理达标甚至超越主流**，主要差距集中在**战术建模充血度**（贫血模型 + 核心 Agent Loop 逻辑下沉基础设施层）；
- 优先级路线 `Priority_Roadmap`：`规范先行（已完成）→ P1 单子域充血试点 → P2 Agent Loop 归属重划（先 ADR）→ P3 应用层拆分 → 治理收尾`。

**对照结论**：结合 P2 两片落地后的新事实（见第二节），基线的评估结论与优先级路线**仍成立**——P2 已按基线设想「先 ADR、后分波、行为等价」推进并落地，未推翻基线任何判断；基线所指差距 1（核心逻辑下沉）已由 P2 部分收敛，其余差距（贫血模型 P1、应用层拆分 P3）仍待按序推进。故**无需重复业界调研**，本 feature 复用而非重写基线。

> 声明：本 feature **不重写、不替换** `Industry_Research_Baseline` 的任何内容（`git diff --stat docs/spec/ddd-gap-analysis/report.md` 期望零变更）。若后续发现基线结论与代码事实不符，本 feature 仅登记差异并标注「基线待另行修订」，不在本 feature 内改基线（本次对照未发现需登记的实质性不符）。

## 二、P2 落地后新事实对照

### 2.1 三层 LOC 现状

| 层 | 当前 LOC（TEST 分支实测） | 相对基线 |
| --- | --- | --- |
| `src/domain` | **9430** | 较基线 8308 **+1122**（P2 上提生效） |
| `src/application` | **9918** | 基本持平（基线 9912） |
| `src/infrastructure` | **24242** | 较基线 24494 **略降** |

domain 增、infrastructure 降的方向，与 P2「把非技术编排从基础设施上提领域层」的意图一致，客观佐证 P2 上提已生效。

### 2.2 P2 两片落地既定事实（不可推翻的前提）

- **P2 首片**（`docs/spec/ddd-agent-loop-relocation`，ADR-0011）：tasks **全部完成**、evaluator 均裁决 **PASS**。搬出纯编排叶子函数 + `RoundOutcome` 值对象至 `domain/agent/agent_loop_policy.py`。
- **P2 第二片**（`docs/spec/ddd-agent-loop-relocation-slice2`，ADR-0012）：tasks **全部完成**、evaluator 均裁决 **PASS**。以「领域服务 + 领域端口」上提循环编排主体与工具控制流纯判定。

### 2.3 已上提构件与已删垫片（TEST 分支实测）

- `domain/agent/agent_loop_policy.py`：纯叶子函数（`detect_handoff` 等）+ 值对象 `RoundOutcome` / 判别式 `RoundOutcomeKind`（`Literal["text","tool_calls","approval","final","handoff"]`）。
- `AgentLoopOrchestrator`：领域服务，承载循环编排主体（轮次推进、终止判定）。
- `AgentLoopEffects`：领域端口，抽象循环所需的运行时副作用。
- `infrastructure/agent/round_outcome.py`：过渡垫片**已删除**（实测该路径不存在；`RoundOutcome` 已归位领域层）。

> 声明：本 feature **不提出重新实施或推翻 ADR-0011 / ADR-0012 已定结论的需求**。上述构件与决策均视为本次评估不可推翻的既定前提。

## 三、P2 第三片评估（工具并发骨架）

### 3.1 评估对象（指名三符号与位置）

`Concurrent_Tool_Skeleton` 指 `src/infrastructure/agent/react_agent_adapter.py` 中三方法（实测位置）：

- `_dispatch_concurrent_tool_calls`（`react_agent_adapter.py:2137`）
- `_stream_concurrent_tool_progress`（`react_agent_adapter.py:2205`）
- `_events_concurrent_tool_calls`（`react_agent_adapter.py:2273`）

三方法负责「同轮多工具如何并发执行、进度/事件如何配对相邻 yield」的执行时序。

### 3.2 按 ADR-0010 `Orchestration_Infrastructure_Split_Line` 四判据逐条判定

论证模板固定为「判据 → 证据（真实符号）→ 归层」，证据均引用 TEST 分支实测符号：

| 判据（ADR-0010 Split_Line） | 证据（真实符号，`react_agent_adapter.py`） | 归层判定 |
| --- | --- | --- |
| **判据 1**：是否封装外部技术 / SDK / 运行时并发原语？ | `asyncio.gather(*(...))` 并发调度（`:2184` / `:2251` / `:2325`），即 Python 运行时并发原语 | **是 → 留基础设施** |
| **判据 2**：是否为可脱离运行时、可复用的纯业务判定（给定输入即定输出、不触 I/O）？ | 三方法本体是并发编排 + I/O 副作用（`await self._execute_tool_call`、trace/observation 写入），无独立纯判定；真正的纯判定（终止 / 预算 / handoff / 审批筛选 / 异常分类）已由 slice2 剥离至 `agent_loop_policy.py` 与 `AgentLoopOrchestrator` | **否 → 无可再上提的纯判定** |
| **判据 3**：是否表达 Agent Loop「何时停止 / 如何推进 / 产出何种形态」的通用语言？ | 三方法只决定「同轮多工具如何并发、事件如何配对相邻 yield」的执行时序，不决定轮次推进或终止——推进 / 终止已在 `AgentLoopOrchestrator` | **否 → 非领域编排语言** |
| **判据 4**：是否只是把技术观测 / 记账 / 运行时上下文缝合进循环的胶水？ | `set_parent_context(context)`（`:2153` / `:2219` / `:2287`）/ `reset_parent_context(token)`（`:2203` / `:2271` / `:2358`）为 ContextVar 运行时上下文传参（handoff 子 Agent 快照）；`_record_tool_call_trace(...)`（定义 `:693`，调用 `:2190` / `:2259` / `:2339`）为 OTel trace 记账；事件配对相邻 yield 为流式时序缝合 | **是 → 留基础设施** |

### 3.3 结论：不开 P2 第三片

四判据中**判据 1、4 命中「留基础设施」**，**判据 2、3 命中「无可上提领域纯判定」**。故 `Concurrent_Tool_Skeleton` 属**技术并发编排（`asyncio.gather`）+ 运行时上下文传参（ContextVar）+ 可观测性缝合（OTel trace）+ 流式事件时序**，应**留基础设施**，**不开 P2 第三片**。

**依据补充**：

- 若把 `asyncio` / `ContextVar` / `OTel` 拖进领域层，将使 `domain/` 反向依赖运行时并发原语、上下文变量与观测框架，直接违反 `docs/steering/ddd-architecture.md` 的 `Domain_Dependency_Rule`（领域层禁框架 / 基础设施依赖），属**过度设计**。
- 真正的领域纯判定（异常分类、审批筛选、guardrail 分支、终止 / 预算 / handoff 判定）**已由 slice2 剥离**至领域层，`Concurrent_Tool_Skeleton` 中不存在可再上提的独立纯判定。
- ADR-0012「后续影响」节曾把「工具并发编排是否继续上提领域服务 + 端口」列为待评估 open 项，本评估对其**闭合为「否」**。

> 该方向收敛决策由**新增 ADR-0013** 正式登记（本报告给论证、ADR 给决策），以完整四段式（背景 / 决策 / 后果 / 备选方案 + 未采纳原因）防止后续 agent 重开高风险第三片。

## 四、follow-up 登记：疑点 2（handoff 模型取值）

登记 `Handoff_Model_Discrepancy`（**疑点 2**）为独立 follow-up：

- **问题**：`domain/agent/agent_loop_policy.py` 的 handoff 分支构造响应时 `model=outcome.response.model`（实测 `:186` / `:194` / `:202`），取的是**父模型**而非 handoff **目标模型**。
- **性质**：**潜在语义问题**（P2 两片按「行为等价」正当保留未修），**低优先级**。
- **承载方式**：应作为**独立、需求驱动的行为变更 spec** 另行承载——因其涉及运行时响应字段语义的实际改变，须走独立需求 + 设计 + 验收，不宜混入本调研 / 评估型 feature。
- **本 feature 处置**：**只登记、不修改**对应任何生产源码（`src/` 零改动，见第六节）。

## 五、后续主线回归标注

依 `Industry_Research_Baseline` 的 `Priority_Roadmap`，P2 经本次评估收敛（第三片判定不开）后：

- **下一步主线回归 P1**：贫血子域充血试点（择一子域，以 `RunStateMachine` / `WorkflowExecutionPolicy` 为基准，把散落在应用 / 基础设施、本质属领域判定的既有规则收敛为领域服务 / 带行为对象，**行为等价、不增删规则**）。
- **P1 属独立 spec + ADR**（引入领域服务为一等抽象决策），**不在本 feature 内启动**，本报告仅作回归标注。
- **P3（应用层大文件拆分）与治理收尾项**按 `Priority_Roadmap` **排在 P1 之后**：P3 对 `container_config.py` / `workflow_orchestrator.py` / `run_application_service.py` 逐段甄别「本属领域判定」vs「组合根装配（允许的例外）」；治理收尾含序列化零残留、循环 import 治理等轻微项。

## 六、范围纪律声明

- 本 feature **不做任何生产源码行为变更**：不搬迁 `Concurrent_Tool_Skeleton`、不修疑点 2、不动 `AgentPort` 契约、不改任何 `src/` 下运行时行为（`git diff --stat -- src/` 期望零输出）。
- 本 feature 产出的改动类型**仅限**：spec 文档（本 `report.md`）、ADR 后续登记（新增 ADR-0013）、文档索引同步（`docs/adr/README.md` 追加 0013 行）。
- 遵循 `docs/steering/adr.md` **只增不改**：以新增 ADR-0013 承载第三片收敛，**不改写**任何已 `Accepted` 的 ADR（0001–0012）正文；新增 ADR 按 `docs/steering/doc-sync.md` 同步 `docs/adr/README.md` 索引。
- **不处理构建产物残留**：`SOURCES.txt` 中的 `round_outcome.py` 残留为 egg-info 打包自动再生产物，非源码事实，本 feature 明确**不处理**。
