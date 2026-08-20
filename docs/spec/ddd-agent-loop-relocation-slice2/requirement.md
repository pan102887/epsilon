# 需求文档：P2 落地第二片——Agent Loop 循环编排主体上提与工具执行控制流解耦

## 简介

### 背景

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构。整合评估报告识别的最大差距（🔴 高风险）是 `Domain_Logic_In_Infrastructure`：核心业务算法 **ReAct Agent Loop** 位于 `src/infrastructure/agent/react_agent_adapter.py`（现约 3217 行，首片后减少），模块自称"基础设施层"，但它并非封装外部 SDK，而是**自研的"推理→行动→观察"编排算法**，本质属领域关注点。

本 spec 是 `P2_Relocation`（多片增量重构工程）的**第二片**，是 `docs/spec/ddd-agent-loop-relocation`（**首片**，已合入，ADR-0011）的**独立后续 spec**，直接复用首片建立的三项资产，均不得回退：

- **首片领域模块** `src/domain/agent/agent_loop_policy.py`（ADR-0011，`Accepted`）：已上提 4 个纯编排叶子函数（`compute_total_tokens` / `is_token_budget_exceeded` / `detect_handoff` / `outcome_to_agent_result`）与 `RoundOutcome` / `RoundOutcomeKind` 值对象；本片在其**同一样板**上继续扩充领域构件并直调，不重复上提已上提项。
- **首片兼容垫片** `src/infrastructure/agent/round_outcome.py`（re-export）：本片在 `_iter_rounds` 主体上提完成后**清理该垫片**（首片 ADR-0011 后果节已登记为可清理项）。
- **首片委托范式**：领域构件为唯一权威落点、`ReActAgentAdapter` 调用点直接委托、既有测试只改 import/调用形式不改断言。本片沿用。

本片的最高约束源仍是 **ADR-0010**（`docs/adr/0010-relocate-agent-loop-to-domain-direction.md`，`Accepted`）：其 `Orchestration_Infrastructure_Split_Line` 判据、`Domain_Orchestration_Candidates` / `Infrastructure_Encapsulation_Candidates` 据实清单、`P2_Invariants` 六条硬约束、两条待观测疑点，本片全部回链并遵守。

### 本 spec 定位：P2 的第二片（承接首片的深度解耦片）

ADR-0010 后果节已明确警示：`_iter_rounds` 轮次循环控制与技术记账（guardrail / trace / checkpoint 副作用，尤其 `_execute_tool_call`）**高度交织**，"把纯编排从技术记账中剥离可能需引入领域服务 + 端口回调等结构"。首片刻意只搬"零 I/O、给定输入即定输出"的纯叶子，把这块**高度交织的主体**留给了本片。

因此本片的核心工作是 **解耦**：把 `_iter_rounds` 的**领域编排骨架**（轮次循环控制、终止判定状态机、`RoundOutcome` 产出协议）与 `_execute_tool_call` / `_prepare_tool_calls_for_execution` 中的**控制流决策**（guardrail 分支判定、异常分类、审批中断筛选、handoff 信号识别）上提到领域层，同时把全部 I/O / 副作用（模型调用、流式累加、guardrail 运行时累加、trace、checkpoint、审批持久化、context 变异、日志）经**端口回调（`AgentLoopEffects`）**留在基础设施层。本片仍是 `Behavior_Equivalent_Refactor`（行为等价纯重构），以首片同款特征化测试安全网为"行为等价"判据。

因风险显著高于首片，本片采用**内部分波（Wave）增量**：先以首片同款「叶子委托」范式上提剩余纯控制流判定（低风险波），再引入领域服务 + 端口回调承载循环骨架（高风险波）；每波以 Checkpoint 门禁保障 `Existing_Test_Suite_Green`。任一构件若无法零风险剥离，按 `Scope_Shrink_Discipline` 缩小范围并登记，不借本片之名做大爆炸。

### 范围内行为（In Scope）

将以下**领域编排构件**从 `src/infrastructure/agent/react_agent_adapter.py` 上提到领域层 `src/domain/agent/`（落点与命名由 design 定，复用/扩充首片 `agent_loop_policy.py` 或新增同子域领域模块 / 领域服务）：

1. **`Round_Loop_Control`**——`_iter_rounds` 的轮次循环推进骨架：`for round_num in range(start_round, effective_terminal + 1)` 的推进、`terminal_round` 边界、`budget_exceeded_pending_after_tools` 跨轮状态机、`Terminal_Round_Boundary_Assert` 循环耗尽不变量、`RoundOutcome` 五态产出协议与顺序。
2. **`Termination_Decision`**——每轮/耗尽处的终止原因决策：`text`→`completed` 自然终止、`handoff` 短路终止、`token_budget_exceeded` 跨轮 pending 标记 + 下一轮入口终止、`max_rounds` 循环耗尽终止（决策本身，不含 OTel span 写入与日志）。
3. **`Pending_Action_Collection`**——`_collect_pending_actions` 的审批中断筛选纯判定：按模型 tool_calls 顺序、依 `allowed_tool_names` 与已解析的审批策略（`ApprovalPolicy.interrupt`）筛选出 `PendingActionRequest` 序列（审批策略的**解析**经端口注入，判定本身为纯函数）。
4. **`Tool_Guardrail_Branch_Interpretation`**——`_execute_tool_call` / `_prepare_tool_calls_for_execution` 中把 `GuardrailDecision.action` 映射为控制流分支（`PROCEED` / `REQUIRE_APPROVAL` / `STOP`）的纯判定。
5. **`Tool_Exception_Classification`**——`_execute_tool_call` 的工具执行异常分类纯判定：`HandoffPerformed`→非错误 + 记 `handoff_target`；`ToolPermissionDeniedError` / `TimeoutError` / 其它 `Exception`→`is_error=True` + `error_class` + 回灌 content 文本形态（分类语义纯函数化，`str(exc)` 与 `timeout` 值作入参）。
6. **`Agent_Loop_Effects_Port`**——在领域层定义承载全部 I/O / 副作用的**端口回调协议**（`Protocol`），由 `ReActAgentAdapter` 实现；`Round_Loop_Control` 领域编排经此端口驱动模型调用、流式累加、guardrail、trace、checkpoint、审批持久化、context 变异、日志。
7. **`Agent_Loop_Orchestrator`**——承载 `Round_Loop_Control` + `Termination_Decision` 的领域服务（域内可脱离运行时、经 `Agent_Loop_Effects_Port` 与运行时解耦）；`_iter_rounds` 改为**委托** `Agent_Loop_Orchestrator`、并作为 `Agent_Loop_Effects_Port` 的实现宿主。

上提方式：领域层定义上述构件；`ReActAgentAdapter` 改为**委托 / 实现端口**，`_iter_rounds` / `_execute_tool_call` / `_prepare_tool_calls_for_execution` / `_collect_pending_actions` 保持四入口（`run` / `run_streaming` / `run_events` / `resume`）对外行为字面等价。为上提构件补领域层单元测试（置于 `test/domain/agent/`），复用/对齐首片同款特征化测试断言。引入 `Agent_Loop_Orchestrator` 领域服务与 `Agent_Loop_Effects_Port` 端口属架构级决策，须新增 ADR（编号从 **0012** 起）。首片 `round_outcome.py` re-export 垫片在本片 `_iter_rounds` 主体上提、无外部依赖后清理。

### 范围外边界（Out of Scope）

本第二片**不搬**、**不改**下列内容（留作 `P2_Relocation` 后续片或永留基础设施）：

1. **不改 `AgentPort` 四方法签名**（`run` / `run_streaming` / `run_events` / `resume`）、不改 DI 装配的对外行为、不改前端 `epsilon-client/`、不新增 / 替换任何第三方依赖（仍仅 `uv`）。
2. **不把技术封装下沉领域层**——ADR-0010 `Infrastructure_Encapsulation_Candidates` 所列构件的**实现本体**（`_GuardrailRuntimeAccumulator` + `_CURRENT_GUARDRAIL_RUNTIME`、`ToolAbuseDetector` + `_CURRENT_TOOL_ABUSE_DETECTOR`、OTel `tracer` + `_record_*_trace` / `_build_*_trace`、`ApprovalStateStorePort` 的 `save`/`load`/`consume` I/O、`approval_serialization` / `guardrail_serialization`、`approval_logging`、`_RoundStreamAccumulator`、`handoff_context` ContextVar、`workflow_capability_runtime`、`merge_usage`）**留基础设施**；本片只把对它们的**调用编排**经端口上提，不搬其实现。
3. **不改工具并发执行编排**——`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls` 的并发（`asyncio.gather`）、事件配对相邻语义、`set_parent_context` / `reset_parent_context` 属运行时并发技术，本片不上提其并发实现（可复用本片上提的 `Tool_Exception_Classification` / `Tool_Guardrail_Branch_Interpretation` 领域判定，但并发骨架留基础设施）。
4. **不改流式 / 事件协议**——`StreamingChunk` / `AgentStreamEvent` 字段与时序、`tool_arguments_delta`、全程 stream 决策（`V3_Decisions_Frozen`）不动。
5. **不修正 ADR-0010 两条疑点的既有行为**：疑点 2（`handoff` 分支 `model` 取 `outcome.response.model`）由首片 `outcome_to_agent_result` 承载，本片不改；疑点 1（`resume` + handoff 未独立锁定）本片**触及共享的循环控制骨架**，须在 design 决策"是否补 `resume`+handoff 特征化测试"，但**不改既有 handoff 短路行为语义**。
6. 不改动 ADR-0001 / 0007 / 0008 / 0009 / 0010 / 0011 的既有结论，不复活领域事件 / 事件总线承载循环逻辑。

### 依赖归属与反向依赖核验（据实）

本片上提构件**不得引入反向依赖**（`domain → infrastructure`）：

- `Round_Loop_Control` / `Termination_Decision` / `Pending_Action_Collection` / `Tool_Guardrail_Branch_Interpretation` / `Tool_Exception_Classification` 的入参 / 出参类型（`AgentConfig` / `AgentResult` / `ApprovalPolicy` / `ApprovalRequiredPayload` / `PendingActionRequest` / `RoundOutcome` / `ConversationContext` / `ToolMessage` / `LLMResponse` / `ToolCallRequest` / `AgentTerminationReason` / `GuardrailDecision` / `GuardrailAction`）经据实核验须**全部在领域层**（`domain.agent.*` / `domain.chat.*` / `domain.model_access.*`）；design 阶段须逐一复核。
- `Agent_Loop_Effects_Port` 作为领域层 `Protocol` 定义，其方法签名只引用领域层类型（对齐既有 `domain/agent/ports.py` 端口风格，如 `RunGuardrailRecorderPort` / `TraceStorePort`）；`_GuardrailRuntimeAccumulator` / OTel / checkpoint 等**具体技术类型不出现在端口签名**，仅在基础设施实现内部使用。
- IF 复核发现某上提构件实际引用任何 `infrastructure` 符号（如 `_GuardrailRuntimeAccumulator` / `_RoundStreamAccumulator` / `tracer`），THEN design SHALL 将该依赖收敛进 `Agent_Loop_Effects_Port` 的端口方法（以领域类型表达输入/输出），或将该构件标注为不可零风险剥离并按 `Scope_Shrink_Discipline` 留后续片。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| P2 搬迁 | `P2_Relocation` | 把 `ReAct_Agent_Adapter` 中「领域编排逻辑」上提领域层的多片增量重构工程；本 spec 是其**第二片**。 |
| 首片 | `First_Slice` | `docs/spec/ddd-agent-loop-relocation`（ADR-0011），已上提 4 纯叶子函数 + `RoundOutcome`，建立 `agent_loop_policy.py` 领域模块、`round_outcome.py` 垫片、委托范式。 |
| 第二片搬迁范围 | `Second_Slice_Scope` | 本 spec 纳入的集合：`Round_Loop_Control` + `Termination_Decision` + `Pending_Action_Collection` + `Tool_Guardrail_Branch_Interpretation` + `Tool_Exception_Classification` + `Agent_Loop_Effects_Port` + `Agent_Loop_Orchestrator`。 |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `src/infrastructure/agent/react_agent_adapter.py::ReActAgentAdapter`，`AgentPort` 实现，承载 Agent Loop。本片上提后改为委托领域编排 + 实现效果端口，位置不变。 |
| Agent 端口 | `AgentPort` | `src/domain/agent/ports.py::AgentPort`，含 `run` / `run_streaming` / `run_events` / `resume` 四方法。本 spec 不改其任何方法签名。 |
| 轮次循环控制 | `Round_Loop_Control` | `_iter_rounds` 的轮次推进骨架：轮次区间推进、`terminal_round` 边界、`budget_exceeded_pending_after_tools` 跨轮状态机、`Terminal_Round_Boundary_Assert`、`RoundOutcome` 五态产出协议与顺序。 |
| 终止判定 | `Termination_Decision` | 每轮/耗尽处终止原因决策：`text`→`completed`、`handoff` 短路、`token_budget_exceeded` 跨轮标记后终止、`max_rounds` 耗尽终止（纯决策，不含 span 写入/日志）。 |
| 审批中断筛选 | `Pending_Action_Collection` | `_collect_pending_actions` 纯筛选：按 tool_calls 顺序，依 `allowed_tool_names` 与已解析审批策略（`ApprovalPolicy.interrupt`）产出 `PendingActionRequest` 序列。策略解析经端口注入。 |
| 工具护栏分支判定 | `Tool_Guardrail_Branch_Interpretation` | 把 `GuardrailDecision.action`（`REQUIRE_APPROVAL` / `STOP` / 其它）映射为控制流分支的纯判定。 |
| 工具异常分类 | `Tool_Exception_Classification` | `_execute_tool_call` 工具执行异常分类纯判定：`HandoffPerformed`→非错误 + `handoff_target`；`ToolPermissionDeniedError` / `TimeoutError` / `Exception`→`is_error` + `error_class` + 回灌 content 形态。 |
| Agent Loop 效果端口 | `Agent_Loop_Effects_Port` | 本 spec 在领域层新增的 `Protocol`，承载 `Round_Loop_Control` 编排所需的全部 I/O/副作用回调（模型轮次执行、流式累加、guardrail、trace、checkpoint、审批持久化、context 变异、日志），由 `ReAct_Agent_Adapter` 实现；方法签名只引用领域类型。 |
| Agent Loop 编排器 | `Agent_Loop_Orchestrator` | 本 spec 在领域层新增的领域服务，承载 `Round_Loop_Control` + `Termination_Decision`，经 `Agent_Loop_Effects_Port` 驱动运行时；可脱离运行时单测（以 fake effects 注入）。 |
| 领域层 Agent Loop 模块 | `Domain_Agent_Loop_Module` | 首片建立的 `src/domain/agent/agent_loop_policy.py`；本片在其上扩充纯叶子构件，并新增承载编排器/端口的领域模块（落点由 design 定）。 |
| 行为等价纯重构 | `Behavior_Equivalent_Refactor` | 上提不改变任何对外可观测行为；被搬迁构件的输入→输出、字段与时序逐一等价，所有调用点行为字面等价。 |
| 循环耗尽不变量断言 | `Terminal_Round_Boundary_Assert` | `_iter_rounds` 循环耗尽分支的断言：唯一可达情形是最后一轮为 tool_calls 且 caller 已回写 ToolMessage；本片上提循环控制时保持该 assert 语义。 |
| P2 不变量清单 | `P2_Invariants` | ADR-0010 锁定的六条硬约束（见需求 2）。本 spec 全部遵守。 |
| 契约不变性 | `Contract_Invariance` | 对任何外部消费者，`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 字段与时序、`AgentTerminationReason` 取值、审批中断/恢复协议、流式协议、trace 观测点保持字面等价。 |
| v3 行为决策锁定 | `V3_Decisions_Frozen` | `agent-adapter-refactor` v3 已落定、本 spec 不得推翻：全程 stream、工具 `timeout`、`max_total_tokens` 预算终止、循环耗尽 assert、`tool_arguments_delta`。 |
| 既有测试全绿基线 | `Existing_Test_Suite_Green` | `PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）在本 spec 落地前后均全部通过，含特征化测试。 |
| 特征化测试安全网 | `Characterization_Tests` | `test/infrastructure/agent/test_react_agent_characterization_*.py` 及既有 `test/infrastructure/agent/` 单测，锁定 Agent Loop 对外可观测行为（终止四态/流式时序/审批中断恢复/handoff/token budget），作本片"行为等价"回归基线。 |
| 领域层依赖禁则 | `Domain_Dependency_Rule` | 领域层零 `application` / `infrastructure` / 框架 / Pydantic 依赖（`docs/steering/ddd-architecture.md`）。本片新增领域构件须满足。 |
| 端口回调解耦 | `Port_Callback_Decoupling` | ADR-0010 后果节预告的解耦形态：领域编排经领域侧 `Protocol` 端口回调驱动运行时副作用，副作用实现留基础设施。本片以 `Agent_Loop_Effects_Port` 落地。 |
| 范围缩小纪律 | `Scope_Shrink_Discipline` | 若某构件与运行时耦合无法零风险剥离，处置为"缩小该构件本片范围并登记 design / ADR 后果节，留后续片"，SHALL NOT 借本片之名扩张至 Out of Scope 或做大爆炸。 |
| 首片垫片清理 | `Shim_Cleanup` | 首片 `infrastructure/agent/round_outcome.py` re-export 垫片，在本片 `_iter_rounds` 主体上提、确认无外部依赖后删除（ADR-0011 后果节已登记）。 |
| 架构决策记录 | `Architecture_Decision_Record` | `docs/adr/` 下的 ADR，写作规则见 `docs/steering/adr.md`，`Accepted` 后只增不改。 |
| 第二片落地 ADR | `Second_Slice_ADR` | 本 spec 新增的 ADR（编号从 **0012** 起），记录"引入 `Agent_Loop_Orchestrator` 领域服务 + `Agent_Loop_Effects_Port` 端口回调承载循环编排主体"的架构级决策；落地 ADR-0010 方向，不 supersede ADR-0001 / 0010 / 0011。 |

## 需求

### 需求 1：将 Agent Loop 循环编排主体与终止判定上提领域层（`Round_Loop_Control` + `Termination_Decision`）

**用户故事：** 作为推进 `P2_Relocation` 的后端架构维护者，我希望把 `_iter_rounds` 的轮次循环控制骨架与终止判定上提到领域层的 `Agent_Loop_Orchestrator`，以便自研的"推理→行动→观察"编排算法回归其真实的领域归属，且循环控制可脱离运行时被独立验证。

#### 验收标准

1. THE `Agent_Loop_Orchestrator` SHALL 位于 `src/domain/agent/` 下，承载 `Round_Loop_Control`：轮次区间推进（`start_round` 到 `effective_terminal` 含）、`terminal_round` 边界解析（`None` 回退 `config.max_rounds`）、`budget_exceeded_pending_after_tools` 跨轮状态机、`RoundOutcome` 五态（`text`/`tool_calls`/`approval`/`final`/`handoff`）产出协议与顺序，与源 `_iter_rounds` 逐一等价。
2. THE `Agent_Loop_Orchestrator` SHALL 承载 `Termination_Decision`：`text` 路径→`completed` 自然终止并 `return`；上一轮 handoff 命中（`round_num > start_round` 且 `detect_handoff` 命中）→产出 `handoff` outcome 并 `return`；`token_budget_exceeded` 跨轮 pending 标记在下一轮入口→产出 `token_budget_exceeded` final 并 `return`；循环耗尽→`max_rounds` final，与源逐一等价。
3. THE `Agent_Loop_Orchestrator` SHALL 保持 `Terminal_Round_Boundary_Assert` 语义：循环耗尽分支断言"最后一轮为 tool_calls 且 caller 已回写 ToolMessage"（`last_response.tool_calls` 非空 且 尾消息为 `ToolMessage`），`last_response is None` 的数学边界直接返回不产出 outcome，与源等价。
4. THE `Agent_Loop_Orchestrator` SHALL 保持源 `_iter_rounds` 的**协作式生成器协议**：`tool_calls` kind 产出后由调用方执行工具并 `context.add_tool_result` 回写、再驱动下一轮；`text`/`approval`/`final`/`handoff` kind 产出后生成器终止迭代——该协议对四入口（`run`/`run_streaming`/`run_events`/`resume`）字面不变。
5. WHEN `Agent_Loop_Orchestrator` 需要模型调用、流式累加、guardrail 评估与记录、OTel span、checkpoint sink、审批持久化、context 变异、日志等运行时副作用, THE `Agent_Loop_Orchestrator` SHALL 仅经 `Agent_Loop_Effects_Port` 回调触发，SHALL NOT 直接 import 或引用任何 `infrastructure` / OTel / 框架符号（`Domain_Dependency_Rule`）。
6. THE `ReAct_Agent_Adapter._iter_rounds` SHALL 改为委托 `Agent_Loop_Orchestrator` 驱动循环，并作为 `Agent_Loop_Effects_Port` 的实现宿主承接全部副作用；四入口消费 `_iter_rounds` 的方式（`async for outcome in self._iter_rounds(...)` 及按 kind 分支）SHALL 保持字面等价。
7. THE 上提 SHALL 为 `Behavior_Equivalent_Refactor`：`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 字段与时序、`AgentTerminationReason` 取值、OTel span（`react_agent.round` / `react_agent.terminated` 的名称、属性、异常 ERROR 状态）、checkpoint sink 调用序列与时机、日志（`Token_Budget_Exceeded_Warning` / `Max_Rounds_Termination_Warning`）位置与内容对外字面等价。
8. THE `Agent_Loop_Orchestrator` SHALL 复用首片已上提的 `detect_handoff` / `is_token_budget_exceeded` / `compute_total_tokens` / `outcome_to_agent_result` / `RoundOutcome`，SHALL NOT 重复定义或再次上提这些首片构件。

### 需求 2：将工具执行控制流决策上提领域层（`Tool_Guardrail_Branch_Interpretation` + `Tool_Exception_Classification` + `Pending_Action_Collection`）

**用户故事：** 作为对工具执行安全语义敏感的维护者，我希望把 `_execute_tool_call` / `_prepare_tool_calls_for_execution` / `_collect_pending_actions` 中"给定输入即定输出"的控制流决策上提为领域纯判定，把 guardrail 累加、abuse 检测、checkpoint、trace、context 变异等副作用留基础设施，以便控制流决策可独立回归、副作用归属清晰。

#### 验收标准

1. THE `Tool_Guardrail_Branch_Interpretation` SHALL 上提到领域层，把 `GuardrailDecision.action`（`GuardrailAction.REQUIRE_APPROVAL` / `GuardrailAction.STOP` / 其它 / `decision is None`）映射为控制流分支（如 `PROCEED` / `REQUIRE_APPROVAL` / `STOP`）的纯判定，与源 `_execute_tool_call`（1140-1200 附近）与 `_prepare_tool_calls_for_execution`（1470-1543 附近）的分支判据逐一等价。
2. THE `Tool_Exception_Classification` SHALL 上提到领域层，纯判定给定工具执行异常（或 handoff 信号）时的分类结果：`HandoffPerformed`→`is_error=False` + `handoff_target=signal.target_agent` + content 取 `signal.content`；`ToolPermissionDeniedError`→`is_error=True` + `error_class="ToolPermissionDeniedError"` + content 取 `str(exc)`；`TimeoutError`→`is_error=True` + `error_class="TimeoutError"` + content 取 `f"工具执行超时（{timeout}s)"`；其它 `Exception`→`is_error=True` + `error_class=type(exc).__name__` + content 取 `str(exc)`，与源逐一等价。
3. THE `Pending_Action_Collection` SHALL 上提到领域层为纯函数：按模型 tool_calls 顺序，跳过 `tool_call.name not in allowed_tool_names` 的调用，对命中 `policy.interrupt` 的产出 `PendingActionRequest`（`tool_call_id` / `tool_name` / `arguments` / `allowed_decisions` / `reason=risk_label`），与源 `_collect_pending_actions` 逐一等价；审批策略的**解析**（`self._approval_policy.policy_for(name)`）经 `Agent_Loop_Effects_Port` / 入参注入，纯判定不直接调端口。
4. WHEN `ReAct_Agent_Adapter._execute_tool_call` / `_prepare_tool_calls_for_execution` / `_collect_pending_actions` 使用上述判定, THE `ReAct_Agent_Adapter` SHALL 改为委托领域判定，且 guardrail 运行时累加（`_GuardrailRuntimeAccumulator`）、abuse 检测（`ToolAbuseDetector`）、checkpoint（`_checkpoint_before_tool_call` / `after_tool_call`）、trace、`context.add_tool_result` / `_stamp_event` 变异、`_log_tool_failure` 日志、`_save_interrupt` 审批持久化 I/O 的**位置与时机字面不变**（副作用留基础设施）。
5. THE 本片 SHALL NOT 改变工具执行的**副作用顺序**：guardrail before/after 观测记录顺序、checkpoint before/after 调用时机、handoff `metadata["handoff_target"]` 与 error `metadata["error"]` 写入、`_stamp_event` 触发点、`set_parent_context` / `reset_parent_context` 边界对外字面等价。
6. THE `Tool_Guardrail_Branch_Interpretation` / `Tool_Exception_Classification` / `Pending_Action_Collection` SHALL 满足 `Domain_Dependency_Rule`：仅引用领域层类型（`GuardrailDecision` / `GuardrailAction` / `ToolCallRequest` / `PendingActionRequest` / `ApprovalPolicy` / `HandoffPerformed` 等，须据实核验其领域归属），零 `infrastructure` / 框架 / Pydantic 依赖。

### 需求 3：在领域层定义 Agent Loop 效果端口，承载运行时副作用回调（`Agent_Loop_Effects_Port`）

**用户故事：** 作为守护分层依赖方向的维护者，我希望循环编排所需的全部 I/O 与副作用经领域侧 `Protocol` 端口回调驱动、由基础设施实现，以便领域编排零反向依赖、副作用实现（OTel/checkpoint/guardrail/持久化）严格留基础设施。

#### 验收标准

1. THE `Agent_Loop_Effects_Port` SHALL 定义为领域层 `Protocol`（落点对齐既有 `domain/agent/ports.py` 端口风格），其方法覆盖 `Round_Loop_Control` 编排所需的全部运行时副作用：至少包括"执行单轮模型调用并返回 `LLMResponse` 与合并后 usage（内含 context 构建、流式累加、guardrail model_completed、`react_agent.round` span）"、"记录携 tool_calls 的 AssistantMessage 并返回其索引"、"解析审批策略以供 `Pending_Action_Collection`"、"保存审批中断并返回 `ApprovalRequiredPayload`"、"checkpoint model_completed / approval_interrupt"、"准备可执行工具集（guardrail 前置 + abuse + checkpoint replay 筛选）并返回 `(executable, approval|None)`"、"记录终止 span 与预算/耗尽日志"。
2. THE `Agent_Loop_Effects_Port` 的方法签名 SHALL 只引用领域层类型（`ConversationContext` / `AgentConfig` / `ModelAccessPort` / `LLMResponse` / `ToolCallRequest` / `ApprovalRequiredPayload` / `ApprovalPolicy` / `RoundOutcome` 等及原生类型）；SHALL NOT 在签名中出现 `_GuardrailRuntimeAccumulator` / `_RoundStreamAccumulator` / OTel `Span` / checkpoint 具体类型等基础设施符号。
3. THE `ReAct_Agent_Adapter` SHALL 实现 `Agent_Loop_Effects_Port`（复用其现有私有方法 `_context_builder.build` / `model_access.stream` / `_RoundStreamAccumulator` / `_guardrail_runtime_accumulator` / `_record_*` / `get_run_checkpoint_context` / `_save_interrupt` / `_prepare_tool_calls_for_execution` 等作为实现细节），使每个端口方法内部行为与源 `_iter_rounds` 对应片段字面等价。
4. THE `Agent_Loop_Effects_Port` 的引入 SHALL NOT 违反 ADR-0001：端口回调是同步/异步方法调用（`Protocol`），SHALL NOT 是领域事件 / 事件总线 / 发布订阅机制。
5. IF 某副作用无法用只引用领域类型的端口方法表达（如必须暴露基础设施类型）, THEN design SHALL 以领域值对象封装该输入/输出，或将相关编排片段按 `Scope_Shrink_Discipline` 留基础设施并登记，不得让基础设施类型泄漏进领域端口签名。

### 需求 4：全程遵守 ADR-0010 的 P2_Invariants 六条硬约束

**用户故事：** 作为对 Agent Loop 契约敏感的维护者，我希望第二片深度解耦严格遵守 ADR-0010 锁定的不变量，以便对外可观测行为零变化、既有测试与前端不受影响。

#### 验收标准

1. THE 本 spec SHALL NOT 改动 `AgentPort` 的四方法签名（`P2_Invariants` 第 1 条）。
2. THE `Contract_Invariance` SHALL 成立：`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 字段与时序、`AgentTerminationReason` 取值、审批中断/恢复协议、流式协议、OTel span 与 checkpoint 观测点对外字面等价（`P2_Invariants` 第 2 条）。
3. THE `V3_Decisions_Frozen` SHALL 成立：全程 stream、工具 `timeout`、`max_total_tokens` 预算终止、循环耗尽 assert、`tool_arguments_delta` 决策不因本片解耦而改变（`P2_Invariants` 第 3 条）。
4. THE `Existing_Test_Suite_Green` SHALL 在本 spec 落地前后均成立（`PYTHONPATH=src uv run --frozen pytest`，含 `Characterization_Tests`）（`P2_Invariants` 第 4 条）。
5. THE 本 spec SHALL NOT 回退 ADR-0001，SHALL NOT 引入领域事件 / 事件总线承载循环逻辑（`P2_Invariants` 第 5 条）。
6. WHEN 因构件移动导致既有生产代码或测试的 import 路径变化, THE `Behavior_Equivalent_Refactor` SHALL 仅调整 import / 调用形式，不改任何断言语义（`P2_Invariants` 第 6 条）。

### 需求 5：为上提构件补齐领域层单元测试，复用首片特征化安全网

**用户故事：** 作为维护者，我希望循环编排、终止判定与工具控制流判定都有可脱离运行时的领域层单测（以 fake `Agent_Loop_Effects_Port` 注入），以便第二片解耦的行为等价性可被独立回归验证，且不与既有特征化测试重复。

#### 验收标准

1. THE 新增单元测试 SHALL 置于 `test/domain/agent/` 下，命名清晰标识锁定构件（如 `test_agent_loop_orchestrator_unit.py` / `test_agent_loop_tool_policy_unit.py`）。
2. FOR `Agent_Loop_Orchestrator`（`Round_Loop_Control` + `Termination_Decision`）, THE 单元测试 SHALL 以 fake `Agent_Loop_Effects_Port` 驱动，覆盖：text 自然终止、tool_calls 协作协议（产出后回写继续）、approval 中断、handoff 短路、token_budget_exceeded 跨轮 pending、max_rounds 耗尽、`Terminal_Round_Boundary_Assert` 触发与 `last_response is None` 边界。
3. FOR `Tool_Guardrail_Branch_Interpretation` / `Tool_Exception_Classification` / `Pending_Action_Collection`, THE 单元测试 SHALL 覆盖其全部分支：guardrail `REQUIRE_APPROVAL`/`STOP`/`PROCEED`/`None`；异常 `HandoffPerformed`/`ToolPermissionDeniedError`/`TimeoutError`/其它 `Exception`；审批筛选 not-allowed 跳过/命中 interrupt/未命中。
4. THE 新增单元测试 SHALL 不依赖 `application` / `infrastructure` 或框架运行时即可执行（脱离运行时单测；`Agent_Loop_Effects_Port` 以领域侧 fake 实现注入）。
5. THE 新增单元测试 SHALL 复用或对齐 `Characterization_Tests` 的既有断言，SHALL NOT 与已充分覆盖处添加等价重复断言（`docs/steering/change-discipline.md`）；`Characterization_Tests` 作本片行为等价回归基线，SHALL 在本片前后保持全绿。
6. WHEN 既有测试因构件移动需调整 import / 调用形式, THE `Behavior_Equivalent_Refactor` SHALL 只改 import / 调用形式、不改断言语义。

### 需求 6：新增 ADR 记录第二片解耦与端口回调架构（`Second_Slice_ADR`），并清理首片垫片

**用户故事：** 作为架构负责人，我希望"引入 Agent Loop 编排领域服务 + 效果端口回调、上提循环主体"这一架构级决策被 ADR 记录、首片临时垫片被清理，以便决策可追溯、分层归属最终收敛、且不与既有 ADR 冲突。

#### 验收标准

1. THE `Second_Slice_ADR` SHALL 新增一条编号从 **0012** 起的记录，采用 `docs/steering/adr.md` 四段式（背景/决策/后果含正面负面后续/备选方案含未采纳原因），状态为 `Accepted`。
2. THE `Second_Slice_ADR` SHALL 记录"引入 `Agent_Loop_Orchestrator` 领域服务 + `Agent_Loop_Effects_Port` 端口回调（`Port_Callback_Decoupling`）承载 `Round_Loop_Control` + `Termination_Decision` + 工具控制流判定"的决策、内部分波增量策略、本片范围，以及为何工具并发执行/流式累加/guardrail 累加实现等留基础设施（回链 ADR-0010 判据与 `Infrastructure_Encapsulation_Candidates`）。
3. THE `Second_Slice_ADR` SHALL 声明本决策为 `Behavior_Equivalent_Refactor`，不改变任何对外可观测行为，并声明遵守 `P2_Invariants` 六条。
4. THE `Second_Slice_ADR` SHALL NOT supersede ADR-0001 / ADR-0010 / ADR-0011——而是**落地 ADR-0010 方向、承接 ADR-0011 首片**；SHALL NOT 复活领域事件 / 事件总线（端口回调是 `Protocol` 方法调用，非事件机制）。
5. WHEN 本片 `_iter_rounds` 主体上提完成且 `infrastructure/agent/round_outcome.py` 垫片确认无外部依赖, THE `Shim_Cleanup` SHALL 删除该垫片并把其余引用改指领域模块，仅调 import 不改断言（ADR-0011 后果节登记的清理项）；IF 仍存在外部依赖使垫片无法安全删除, THEN 按 `Scope_Shrink_Discipline` 保留垫片并在 `Second_Slice_ADR` 后果节登记。
6. WHEN `Second_Slice_ADR` 落地, THE `Architecture_Decision_Record` SHALL 在 `docs/adr/README.md` 索引表新增该条目（`docs/steering/doc-sync.md`）；WHERE 上提使 `docs/domain-model.md` / `docs/architecture.md` 与代码脱节, THE 相应主题文档 SHALL 同步更新（说明 Agent Loop 编排领域服务 + 效果端口的分层归属）。

### 需求 7：严格限定第二片范围，不越界与不做大爆炸

**用户故事：** 作为对 3000+ 行核心算法风险敏感的维护者，我希望第二片以领域服务 + 端口回调的清晰边界解耦、以内部分波降风险，不把技术封装实现下沉领域层、不触碰工具并发骨架，以便深度解耦风险被波次隔离、可回滚。

#### 验收标准

1. THE 本 spec SHALL NOT 把 `Infrastructure_Encapsulation_Candidates` 的**实现本体**下沉领域层（`_GuardrailRuntimeAccumulator` / `ToolAbuseDetector` / OTel / checkpoint sink / `ApprovalStateStorePort` I/O / 序列化 / `_RoundStreamAccumulator` / `handoff_context` / `workflow_capability_runtime` / `merge_usage`），只把对它们的**调用编排**经 `Agent_Loop_Effects_Port` 上提。
2. THE 本 spec SHALL NOT 改动工具并发执行骨架（`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）的 `asyncio.gather` 并发、事件配对相邻、`set_parent_context` / `reset_parent_context` 语义。
3. THE 本 spec SHALL NOT 改动 DI 装配的对外行为、前端 `epsilon-client/`、依赖管理方式（仍仅 `uv`），SHALL NOT 改 `AgentPort` 四签名。
4. THE `ReAct_Agent_Adapter` 文件 SHALL 保持位于 `src/infrastructure/agent/react_agent_adapter.py`（本片上提编排逻辑，不移动适配器本体）。
5. THE 本片 SHALL 采用**内部分波（Wave）增量**：先上提纯叶子控制流判定（低风险波，首片委托范式），再引入领域服务 + 端口回调承载循环骨架（高风险波），每波以 Checkpoint 门禁保 `Existing_Test_Suite_Green`。
6. IF 本片实施中发现某构件（如 `_iter_rounds` 循环骨架整体上提）与运行时耦合无法在本片零风险剥离, THEN THE 处置 SHALL 依 `Scope_Shrink_Discipline`——缩小该构件本片范围、登记于 design / `Second_Slice_ADR` 后果节、留后续片，SHALL NOT 借本片之名扩张搬迁或强行大爆炸。
