# 需求文档：P2 前置——Agent Loop 归属重划的方向 ADR + 特征化测试安全网

## 简介

### 背景

后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构。整合评估报告（`docs/spec/ddd-gap-analysis/report.md`）识别出的**最大差距（🔴 高风险）**是 `Domain_Logic_In_Infrastructure`：核心业务算法 **ReAct Agent Loop** 位于 `src/infrastructure/agent/react_agent_adapter.py`（约 3313 行），模块 docstring 自称"本模块属于基础设施层"，但它并非封装外部 SDK（未 `import openai` / `agents` / `litellm`），而是**自研的"推理→行动→观察"编排算法**，本质属领域关注点。三层代码量失衡（domain ≈ 8.3k / application ≈ 9.9k / infrastructure ≈ 24.5k）的主因即此。

此前已完成的相关工作构成本 spec 的既定前提，均不得回退：

- `docs/spec/ddd-implementation-review`（需求 6：战术建模 steering 规范，产出 ADR-0007）
- `docs/spec/ddd-tactical-remediation`（领域日志解耦、序列化职责外移，产出 ADR-0008）
- `docs/spec/ddd-anemic-domain-pilot`（P1：`domain/task` 充血化试点，已落地 PASS，产出 ADR-0009）

真正把 3313 行 Agent Loop 上提到领域层的动作（下称 **P2 搬迁**）风险极高：牵动 `AgentPort` 契约、DI 装配、大量既有测试的 import 路径，且紧邻 `agent-adapter-refactor` v3 刚落定的行为决策。

### 动机

**本 spec 是 P2 的"前置降风险轮"，不是 P2 本身。** 它的唯一目的，是让未来的 P2 搬迁"可安全落地"——在**不移动、不重写 `react_agent_adapter.py` 任何一行业务逻辑**的前提下，先备齐两样降风险资产：

1. **方向 ADR（ADR-0010）**：确立"ReAct Agent Loop 应归属领域层"的判断，划出「领域编排逻辑（应上提）vs 真技术封装（留基础设施）」的**切分线**，并锁定 P2 搬迁不可破坏的**不变量清单**。
2. **特征化测试（Characterization Tests）安全网**：在搬迁前，把 Agent Loop 当前**对外可观测行为**固化为回归基线，作为未来 P2 重构"行为等价"的判据。

### 本 spec 的核心硬约束（贯穿所有需求，反复强调）

- **零业务逻辑搬迁（`Zero_Logic_Relocation`）**：本 spec **不移动、不重写、不删除** `react_agent_adapter.py` 中 Agent Loop 的任何一行业务逻辑。它只产出文档（ADR）与新增测试。若为可测试性确需对生产代码做极小改动（例如把某段内联逻辑抽为一个纯函数以便直接断言），该改动 SHALL 最小化、单独标注、并保持 `Behavior_Equivalent_Refactor`；本 spec 默认**倾向于通过既有对外入口测试，不做任何生产代码改动**。
- **不改契约与可观测行为（`Contract_Invariance`）**：不改 `AgentPort` 的 `run` / `run_streaming` / `run_events` / `resume` 四方法签名；不改任何对外可观测行为——`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 的字段与时序、`AgentTerminationReason` 取值、审批中断/恢复协议、流式协议。
- **不推翻 `agent-adapter-refactor` v3 已定行为决策（`V3_Decisions_Frozen`）**：全程 stream、工具 `timeout`、`max_total_tokens`、循环耗尽 assert、`tool_arguments_delta` 等结论不动。
- **既有测试全绿基线（`Existing_Test_Suite_Green`）**：`PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）在本 spec 落地前后均全部通过；新增特征化测试也必须全绿。

### 不包括（Out of Scope）

1. **不实际执行 P2 搬迁**：不把 Agent Loop 上提到领域层、不新增领域服务承载循环逻辑、不改 DI 装配、不动 import 路径。P2 搬迁是独立后续 spec（例如 `ddd-agent-loop-relocation`），本 spec 仅产出其"前置降风险"资产。
2. 不改动 P1（`ddd-anemic-domain-pilot`）及此前各 spec 的既有成果，不改 ADR-0007 / 0008 / 0009 的结论。
3. 不修改 `AgentPort` 及其它领域 Port 的方法签名。
4. 不修改前端 `epsilon-client/` 任何代码。
5. 不新增/替换第三方依赖，不改变依赖管理方式（仍仅 `uv`）。
6. 不引入领域事件总线（已由 ADR-0001 `Accepted` 否决，不得回退）；ADR-0010 SHALL 尊重该决策。
7. 不推翻 `agent-adapter-refactor` v1/v2/v3 任一行为决策。
8. `Characterization_Tests` **只锁定"当前实际行为"**（characterization，快照当下真相），**不**表达"理想行为"、**不**顺手修复其发现的任何行为瑕疵；若测试暴露出可疑行为，只登记（写入 design 或 `TODO.md`），修复留待后续 spec 决策。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 逻辑下沉基础设施层 | `Domain_Logic_In_Infrastructure` | ReAct Agent Loop 自研编排算法位于 `src/infrastructure/agent/react_agent_adapter.py`（约 3313 行），模块 docstring 自称"属于基础设施层"，但未封装任何外部 SDK（无 `import openai`/`agents`/`litellm`），承载核心业务算法。三层 LOC 失衡 domain ≈ 8.3k / application ≈ 9.9k / infrastructure ≈ 24.5k 的主因。 |
| ReAct Agent 适配器 | `ReAct_Agent_Adapter` | `src/infrastructure/agent/react_agent_adapter.py::ReActAgentAdapter`，`AgentPort` 的具体实现，承载 Agent Loop。本 spec 不移动、不重写其任何业务逻辑。 |
| Agent 端口 | `AgentPort` | `src/domain/agent/ports.py::AgentPort`，以 Protocol 定义"接收任务、自主执行、返回结果"的能力边界，含 `run` / `run_streaming` / `run_events` / `resume` 四方法。本 spec 及未来 P2 均不改其方法签名。 |
| P2 搬迁 | `P2_Relocation` | 未来把 `ReAct_Agent_Adapter` 中「领域编排逻辑」上提到领域层的独立后续 spec。**本 spec 不执行 `P2_Relocation`**，只产出令其可安全落地的方向 ADR 与特征化测试安全网。 |
| 零业务逻辑搬迁 | `Zero_Logic_Relocation` | 本 spec 不移动/不重写/不删除 `react_agent_adapter.py` 中 Agent Loop 的任何一行业务逻辑；仅产出 ADR 文档与新增测试。为可测试性所需的生产代码改动须最小化、单独标注、行为等价，且默认倾向零生产代码改动。 |
| 方向决策 ADR | `Direction_ADR` | 本 spec 拟新增的 **ADR-0010**（当前 ADR 已至 0009）。四段式（背景/决策/后果/备选）、状态 `Accepted`，记录"ReAct Agent Loop 应归属领域层"的判断、`Orchestration_Infrastructure_Split_Line` 的判据、以及 `P2_Invariants` 清单；不 supersede ADR-0001，须在 `docs/adr/README.md` 索引登记。 |
| 编排/技术切分线 | `Orchestration_Infrastructure_Split_Line` | ADR-0010 须据实划出的判据：`ReAct_Agent_Adapter` 内**哪些是「领域编排逻辑」（应经 `P2_Relocation` 上提）**、**哪些是「真技术封装」（应留在基础设施层）**。判据须基于"是否封装外部技术/SDK、是否为可复用的业务判定"给出可操作的分类标准，而非逐行罗列。 |
| 领域编排逻辑（切分候选：上提） | `Domain_Orchestration_Candidates` | ADR-0010 据实识别为"领域关注点、应上提"的逻辑，候选包含：`_iter_rounds` 的**轮次循环控制**、**终止判定**（`AgentTerminationReason` 四态判定 + `handoff` 短路 + `max_rounds` 耗尽判定，见 `_is_token_budget_exceeded`）、**审批中断决策**（tool_calls 是否命中审批策略而中断）、**handoff 检测**（`_detect_handoff`）、**`_outcome_to_agent_result` 的 `RoundOutcome→AgentResult` 翻译**、以及 `RoundOutcome`（`round_outcome.py`）所表达的轮次终止形态本身。最终清单以 ADR-0010 据实认定为准。 |
| 真技术封装（切分候选：留基础设施） | `Infrastructure_Encapsulation_Candidates` | ADR-0010 据实识别为"技术关注点、应留在基础设施层"的逻辑，候选包含：guardrail 运行时累加器 `_GuardrailRuntimeAccumulator`、`ToolAbuseDetector`（`tool_abuse_detector.py`）、OTel `tracer` 与 `_record_*` trace 记录方法（`_record_trace`/`_record_error_trace`/`_record_tool_call_trace` 等）、审批状态持久化调用（`ApprovalStateStorePort`）、序列化（`approval_serialization` / `guardrail_serialization`）、审批日志（`approval_logging`）、流式分片累加器 `_RoundStreamAccumulator`、handoff 上下文栈（`handoff_context`）、workflow 能力运行时（`workflow_capability_runtime`）、usage 合并（`chat.usage.merge_usage`）。最终清单以 ADR-0010 据实认定为准。 |
| P2 不变量清单 | `P2_Invariants` | ADR-0010 须锁定的、`P2_Relocation` 落地时**不可破坏**的约束集合：`AgentPort` 四方法签名不变；`Contract_Invariance`（对外可观测行为字面等价）；`V3_Decisions_Frozen`；`Existing_Test_Suite_Green`；不回退 ADR-0001；因文件移动导致的 import 路径调整只改 import、不改断言语义。 |
| 契约不变性 | `Contract_Invariance` | 对任何外部消费者（HTTP 客户端、前端、CLI/TUI、既有测试断言、trace/日志观测点、事件时序）而言，`AgentResult` / `AgentStreamEvent` / `StreamingChunk` 的字段与时序、`AgentTerminationReason` 取值、审批中断/恢复协议、流式协议保持字面等价。 |
| v3 行为决策锁定 | `V3_Decisions_Frozen` | `agent-adapter-refactor` v3 已落定且本 spec 与未来 P2 均不得推翻的行为决策：全程 stream、工具 `timeout`（`AgentConfig.tool_timeout_seconds`）、`max_total_tokens` 预算终止、循环耗尽 assert、`tool_arguments_delta` 流式分片。 |
| 行为等价 | `Behavior_Equivalent_Refactor` | 任何允许的结构调整对外部可观测行为保持字面等价，不改变任何控制流、返回值与观测点。 |
| 既有测试全绿基线 | `Existing_Test_Suite_Green` | `PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）在本 spec 落地前后均全部通过。 |
| 特征化测试 | `Characterization_Tests` | 本 spec 新增的、**仅锁定 `ReAct_Agent_Adapter` 当前实际对外可观测行为**的回归测试（characterization/黄金主测试，非理想行为规约）。经既有测试缺口分析后补齐 `Observable_Behavior_Surface` 覆盖，作为 `P2_Relocation` 的"行为等价"回归基线。置于 `test/` 下合理位置（对齐现有 `test/infrastructure/agent/` 组织）。 |
| 对外可观测行为面 | `Observable_Behavior_Surface` | `Characterization_Tests` 须锁定的 Agent Loop 对外行为集合：(a) `AgentTerminationReason` 四态——`completed` / `max_rounds` / `token_budget_exceeded`，以及 `RoundOutcome` 层的 `handoff` 终止形态；(b) `run_streaming` / `run_events` 的**流式事件时序**（`AgentStreamEventKind` 各 kind 的产出顺序，含 `tool_arguments_delta` 仅在最后一轮、累积期间不发事件）；(c) **审批中断/恢复语义**（`run` 返回 `status="approval_required"` + `ApprovalRequiredPayload`，`resume` 依 `ApprovalDecision` 续跑）；(d) **handoff** 控制转移语义；(e) **token budget 超限**终止语义（`AgentConfig.max_total_tokens`）。 |
| 既有测试缺口 | `Existing_Test_Coverage_Gap` | `test/infrastructure/agent/` 下既有测试对 `Observable_Behavior_Surface` 的**覆盖缺口**——`Characterization_Tests` 要补的正是缺口，而非重复已充分覆盖处。design 阶段须据实清点既有覆盖（如 `test_react_agent_max_rounds_terminated_reason_unit.py` / `test_react_agent_token_budget_unit.py` / `test_react_agent_events_unit.py` / `test_react_agent_hitl_unit.py` / `test_react_agent_handoff_unit.py` / `test_react_agent_streaming_unit.py` 等）后，仅对缺口补测。 |
| 终止原因值对象 | `AgentTerminationReason` | `src/domain/agent/value_objects.py::AgentTerminationReason`，`Literal["completed", "max_rounds", "token_budget_exceeded"]`，刻画"为何停止"，与 `AgentRunStatus`（`completed`/`approval_required`）正交。`Characterization_Tests` 须逐值锁定其判定。 |
| 流式事件值对象 | `AgentStreamEvent` | `src/domain/agent/value_objects.py::AgentStreamEvent`，携带 `kind` / `content` / `tool_name` / `tool_call_id` / `arguments` / `usage` / `metadata`；`kind` 取值见 `AgentStreamEventKind`。属 `Observable_Behavior_Surface` 的一部分。 |
| 架构决策记录 | `Architecture_Decision_Record` | `docs/adr/` 下的 ADR，写作规则见 `docs/steering/adr.md`；`Accepted` 后只增不改。 |

## 需求

### 需求 1：确立 Agent Loop 归属方向并划出编排/技术切分线（`Direction_ADR`）

**用户故事：** 作为后端架构维护者，我希望以一份方向 ADR 明确"ReAct Agent Loop 应归属领域层"的判断，并划出「领域编排逻辑 vs 真技术封装」的切分线，以便未来的 `P2_Relocation` 有据可依、不必在搬迁时临场判断哪些该上提、哪些该留下。

#### 验收标准

1. THE `Direction_ADR` SHALL 作为 **ADR-0010** 落地于 `docs/adr/`，采用 `docs/steering/adr.md` 规定的四段式（背景/决策/后果/备选方案，含未采纳原因），状态为 `Accepted`。
2. THE `Direction_ADR` SHALL 记录"`ReAct_Agent_Adapter` 中的 Agent Loop 编排逻辑属领域关注点、应经 `P2_Relocation` 上提到领域层"这一方向判断，并列明判定依据：未封装外部 SDK（无 `import openai`/`agents`/`litellm`）、自研业务编排算法、docstring 自称基础设施层、三层 LOC 失衡（domain ≈ 8.3k / application ≈ 9.9k / infrastructure ≈ 24.5k）。
3. THE `Direction_ADR` SHALL 给出 `Orchestration_Infrastructure_Split_Line` 的**可操作判据**（基于"是否封装外部技术/SDK、是否为可复用业务判定"），而非逐行罗列代码。
4. THE `Direction_ADR` SHALL 据实列出 `Domain_Orchestration_Candidates`（应上提），至少涵盖 `_iter_rounds` 轮次循环控制、`AgentTerminationReason` 四态终止判定（含 `_is_token_budget_exceeded` 与 `handoff` 短路、`max_rounds` 耗尽判定）、审批中断决策、`_detect_handoff`、`_outcome_to_agent_result` 翻译、`RoundOutcome` 轮次终止形态。
5. THE `Direction_ADR` SHALL 据实列出 `Infrastructure_Encapsulation_Candidates`（应留基础设施），至少涵盖 `_GuardrailRuntimeAccumulator`、`ToolAbuseDetector`、OTel `tracer` 与 `_record_*` trace 记录、`ApprovalStateStorePort` 审批状态持久化调用、`approval_serialization` / `guardrail_serialization` 序列化、`approval_logging`、`_RoundStreamAccumulator`、`handoff_context`、`workflow_capability_runtime`、`chat.usage.merge_usage`。
6. THE `Direction_ADR` SHALL 锁定 `P2_Invariants` 清单，明确列出 `P2_Relocation` 落地时不可破坏的约束：`AgentPort` 四方法签名不变、`Contract_Invariance`、`V3_Decisions_Frozen`、`Existing_Test_Suite_Green`、不回退 ADR-0001、import 路径调整只改 import 不改断言语义。
7. THE `Direction_ADR` SHALL NOT supersede ADR-0001（领域事件总线决策不回退），并 SHALL NOT 把「领域事件」列为 `P2_Relocation` 的推荐落地形态。
8. WHEN `Direction_ADR` 落地, THE `Architecture_Decision_Record` SHALL 在 `docs/adr/README.md` 索引表中新增 ADR-0010 条目（遵循 `docs/steering/doc-sync.md`）。

### 需求 2：本 spec 不搬迁、不改写任何 Agent Loop 业务逻辑（`Zero_Logic_Relocation`）

**用户故事：** 作为对 3313 行核心算法风险敏感的维护者，我希望本 spec 严格限定为"只降风险、不搬迁"，以便这一前置轮本身零行为风险，把真正的搬迁风险隔离到未来独立 spec。

#### 验收标准

1. THE 本 spec SHALL NOT 移动、重写或删除 `src/infrastructure/agent/react_agent_adapter.py` 中 Agent Loop 的任何一行业务逻辑；`ReAct_Agent_Adapter` 的文件位置保持不变。
2. THE 本 spec SHALL NOT 执行 `P2_Relocation`：不新增承载循环逻辑的领域服务、不改 DI 装配、不改任何生产代码的 import 路径以实现搬迁。
3. IF 为使某段对外行为可被 `Characterization_Tests` 直接断言而确需对生产代码做极小改动（如将一段内联逻辑抽为纯函数、暴露一个只读探针）, THEN THE 该改动 SHALL 最小化、在 design 与变更说明中单独标注为"可测试性改动"，且 SHALL 保持 `Behavior_Equivalent_Refactor`（不改任何控制流、返回值与观测点）。
4. THE 本 spec SHALL 默认倾向于"零生产代码改动"——优先经 `AgentPort` 既有对外入口（`run` / `run_streaming` / `run_events` / `resume`）观测行为，仅当既有入口无法暴露目标行为时才动用验收标准 3 的例外。
5. THE 本 spec SHALL NOT 改动 `AgentPort` 及其它领域 Port 的方法签名（`Contract_Invariance` 的契约维度）。
6. THE 本 spec SHALL NOT 改动前端、不新增/替换第三方依赖、不改变依赖管理方式（仍仅 `uv`）。

### 需求 3：清点既有测试对可观测行为面的覆盖缺口（`Existing_Test_Coverage_Gap`）

**用户故事：** 作为特征化测试的作者，我希望先摸清 `test/infrastructure/agent/` 下既有测试对 Agent Loop 对外行为的覆盖程度，以便只对缺口补测，避免与既有测试重复、也避免遗漏关键行为面。

#### 验收标准

1. THE 需求 3 SHALL 在 design 阶段据实清点 `test/infrastructure/agent/` 下针对 `Observable_Behavior_Surface` 的既有覆盖，逐项标注 (a) 终止原因四态、(b) 流式事件时序、(c) 审批中断/恢复、(d) handoff、(e) token budget 超限 各自"已覆盖 / 部分覆盖 / 未覆盖"。
2. THE `Existing_Test_Coverage_Gap` SHALL 明确列出 `Characterization_Tests` 需要新增覆盖的缺口项，并说明每一项为何是缺口（既有测试未锁定的具体行为断言）。
3. THE 需求 3 SHALL NOT 删除或弱化任何既有测试的断言；缺口分析只用于指导新增，不改动既有测试语义。
4. WHERE 某行为面已被既有测试充分锁定, THE `Characterization_Tests` SHALL NOT 重复添加等价断言（遵循 `docs/steering/change-discipline.md` 最小改动）。

### 需求 4：补齐终止原因四态的特征化测试（`AgentTerminationReason`）

**用户故事：** 作为准备 `P2_Relocation` 的维护者，我希望 Agent Loop 的每一种终止原因判定都有特征化测试固化，以便搬迁后能逐值验证终止判定行为等价。

#### 验收标准

1. FOR ALL `AgentTerminationReason` 取值（`completed` / `max_rounds` / `token_budget_exceeded`）, THE `Characterization_Tests` SHALL 锁定对应触发条件下 `AgentResult.terminated_reason` 的当前实际取值。
2. WHEN 模型自然给出纯文本回复或工具循环正常收尾, THE `Characterization_Tests` SHALL 断言 `AgentResult.terminated_reason == "completed"` 且 `AgentResult.status == "completed"`。
3. WHEN Agent Loop 达到 `AgentConfig.max_rounds` 上限且最后一轮仍返回 `tool_calls`, THE `Characterization_Tests` SHALL 断言 `AgentResult.terminated_reason == "max_rounds"`，并锁定此时 `AgentResult.content` 的当前实际取值（通常为空字符串）。
4. WHILE `AgentConfig.max_total_tokens` 已配置, WHEN 累计 `usage` 达到该上限, THE `Characterization_Tests` SHALL 断言本轮工具执行完成后即终止且 `AgentResult.terminated_reason == "token_budget_exceeded"`，不再发起新一轮模型调用。
5. IF 触发 handoff 短路（上一轮工具执行产生 `HandoffPerformed`）, THEN THE `Characterization_Tests` SHALL 锁定 `run` 返回的 `AgentResult`（`content` 取目标 Agent 最终回复、`terminated_reason == "completed"`）的当前实际形态。
6. WHILE `AgentRunStatus == "approval_required"`, THE `Characterization_Tests` SHALL 断言 `AgentResult.terminated_reason` 保持 `"completed"`（HITL 中断由 `status` 表达，不属于轮数超限），以锁定二者正交关系。
7. THE 需求 4 的测试 SHALL 仅锁定当前实际行为（characterization），SHALL NOT 断言任何"理想应有"但当前未实现的行为。

### 需求 5：补齐流式事件时序的特征化测试（`AgentStreamEvent` / `StreamingChunk`）

**用户故事：** 作为准备 `P2_Relocation` 的维护者，我希望 `run_streaming` 与 `run_events` 的分片与事件时序被特征化测试固化，以便搬迁后流式协议行为等价可回归验证。

#### 验收标准

1. WHEN 通过 `run_streaming` 执行含中间工具轮次的 Agent Loop, THE `Characterization_Tests` SHALL 锁定 `StreamingChunk` 分片的当前实际产出顺序（中间轮次同步执行工具、最终轮次流式产出分片）。
2. WHEN 通过 `run_events` 执行 Agent Loop, THE `Characterization_Tests` SHALL 锁定 `AgentStreamEvent.kind` 各事件（`status` / `assistant_delta` / `assistant_done` / `tool_start` / `tool_result` / `tool_error` / `approval_required` / `error` / `tool_arguments_delta`）的当前实际产出顺序。
3. WHILE 处于中间累积轮次, WHEN 观察 `run_events` 输出, THE `Characterization_Tests` SHALL 断言累积期间**不**产出 `tool_arguments_delta` 事件（`V3_Decisions_Frozen` 决策 7 约束），仅在最后一轮 stream 阶段产出。
4. WHEN 最后一轮 stream 观察到工具调用 `arguments` 分片, THE `Characterization_Tests` SHALL 锁定 `tool_arguments_delta` 事件的当前实际形态（`content` 恒为空串、`usage` 恒为 `None`、同一 `tool_call_id` 的多个 delta 按 SDK 产出顺序到达、`tool_call_id`/`tool_name` 仅首个 delta 携带非 `None`）。
5. WHEN Agent Loop 因 `max_rounds` 或 `token_budget_exceeded` 终止, THE `Characterization_Tests` SHALL 锁定 `run_events` 在该分支跳过最终轮 stream 且在事件 `metadata` 中携带 `terminated_reason` 的当前实际行为。
6. THE 需求 5 的测试 SHALL 仅锁定当前实际行为（characterization），SHALL NOT 顺手修复其暴露的任何时序瑕疵；如发现可疑时序，SHALL 仅登记（design 或 `TODO.md`）待后续 spec 决策。

### 需求 6：补齐审批中断/恢复语义的特征化测试（HITL）

**用户故事：** 作为准备 `P2_Relocation` 的维护者，我希望审批中断与恢复的语义被特征化测试固化，以便搬迁后 HITL 协议行为等价可回归验证。

#### 验收标准

1. WHEN Agent Loop 遇到命中审批策略（`ApprovalPolicy.interrupt == True`）的工具调用, THE `Characterization_Tests` SHALL 断言 `run` 返回 `AgentResult.status == "approval_required"` 且携带 `ApprovalRequiredPayload`（含 `session_id` / `approval_id` / `actions` / `prompt_id`）。
2. WHEN 携带与待审批动作顺序一致的 `ApprovalDecision` 序列调用 `resume`, THE `Characterization_Tests` SHALL 锁定 `approve` / `edit` / `reject` 三种决策下续跑执行的当前实际行为与返回 `AgentResult`。
3. IF `resume` 收到的 `ApprovalDecision` 数量、顺序或允许集合与 `ApprovalInterrupt.actions` 不匹配, THEN THE `Characterization_Tests` SHALL 锁定当前抛出的领域异常类型（如 `ApprovalDecisionCountMismatchError` / `ApprovalDecisionOrderMismatchError` / `ApprovalDecisionNotAllowedError`）。
4. WHEN 从审批中断恢复后再次命中审批策略, THE `Characterization_Tests` SHALL 锁定 `resume` 再次返回 `status == "approval_required"` 的当前实际行为。
5. THE 需求 6 的测试 SHALL 通过 `AgentPort` 的 `run` / `resume` 既有入口观测审批语义，SHALL NOT 为此改写审批状态持久化（`ApprovalStateStorePort`）或审批序列化逻辑。
6. THE 需求 6 的测试 SHALL 仅锁定当前实际行为（characterization）。

### 需求 7：补齐 handoff 与 token budget 超限的特征化测试

**用户故事：** 作为准备 `P2_Relocation` 的维护者，我希望 handoff 控制转移与 token 预算超限这两条终止路径被特征化测试固化，以便搬迁后这两处易受循环控制改动影响的行为可回归验证。

#### 验收标准

1. WHEN 某轮工具执行触发 `HandoffPerformed` 信号, THE `Characterization_Tests` SHALL 断言 Agent Loop 立即终止本 Agent 循环、不再发起新一轮 LLM 调用，且目标 Agent 最终回复成为对外 `AgentResult.content`。
2. FOR ALL 四个执行入口（`run` / `run_streaming` / `run_events` / `resume`）中当前实际支持 handoff 终止的入口, THE `Characterization_Tests` SHALL 锁定 handoff 终止时对外产出（`AgentResult.content` / 相应流式事件）的当前实际形态。
3. WHILE `AgentConfig.max_total_tokens` 已配置且 tool_calls 路径命中预算, THE `Characterization_Tests` SHALL 锁定"先让调用方执行完工具回写 `ToolMessage`、下一轮入口处再终止并产出 `token_budget_exceeded`"的当前实际时序。
4. WHEN `max_total_tokens` 与 `max_rounds` 可能同时逼近, THE `Characterization_Tests` SHALL 锁定"先命中者优先终止"的当前实际判定（二者告警在同一执行内互斥）。
5. THE 需求 7 的测试 SHALL 仅锁定当前实际行为（characterization），SHALL NOT 改写 `_detect_handoff` / `_is_token_budget_exceeded` / `handoff_context` 等生产逻辑。

### 需求 8：特征化测试放置、全绿基线与"仅锁定当前行为"纪律

**用户故事：** 作为仓库维护者，我希望特征化测试落在合理位置、全绿可执行、且严格遵守"只快照现状、不修复瑕疵"的 characterization 纪律，以便它们成为可信的 P2 回归基线而非引入新的行为主张。

#### 验收标准

1. THE `Characterization_Tests` SHALL 置于 `test/` 下与现有组织一致的位置（对齐 `test/infrastructure/agent/`），命名清晰标识其为特征化/回归基线测试。
2. THE `Characterization_Tests` SHALL 在 `PYTHONPATH=src uv run --frozen pytest`（`epsilon-boot/` 下执行）中全部通过；`Existing_Test_Suite_Green` SHALL 在本 spec 落地前后均成立。
3. THE `Characterization_Tests` SHALL 遵循 `docs/steering/code-documentation.md`（中文 docstring 说明所锁定的行为面）与 `docs/steering/python-typing-lint.md`（全量类型标注、禁裸 `Any`、`ruff`/`pyright` 零新增错误）。
4. THE `Characterization_Tests` SHALL 仅锁定 `ReAct_Agent_Adapter` 当前实际对外可观测行为，SHALL NOT 断言任何"理想应有"但当前未实现的行为。
5. IF `Characterization_Tests` 编写过程中暴露出可疑或非预期的当前行为, THEN THE 处置 SHALL 为"照当前行为写断言 + 在 design 或 `TODO.md` 登记该疑点"，SHALL NOT 在本 spec 内修复该行为（修复留待后续 spec 决策）。
6. THE `Characterization_Tests` SHALL NOT 断言任何触及 `V3_Decisions_Frozen` 的"改后"行为——它们只固化 v3 现状，不表达对 v3 决策的任何修订意图。
