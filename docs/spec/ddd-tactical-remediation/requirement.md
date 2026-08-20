# 需求文档：DDD 战术建模代码级纠偏（第一批：低风险行为等价重构）

## 简介

### 背景

前置 spec `docs/spec/ddd-implementation-review/` 完成了「本项目 DDD 落地 vs 业界主流」的调研，识别出 6 项差距，但**只落地了需求 6（补齐战术建模 steering 规范，纯文档）**——产物为 `docs/steering/ddd-tactical-modeling.md` 与 ADR-0007。需求 1–5 的**代码级纠偏**被明确登记为 follow-up，从未执行（见该 spec `summary.md` 的「后续事项」）。

本 spec 承接其中**两项已复核仍客观存在、且风险最低的差距**，各自独立成需求，全程为**行为等价的纯重构**：

- **需求 A（承接前置需求 5，`Domain_Purity_Blemish`，轻微）**：移除 `src/domain/chat/context.py` 领域模块对 `logging` 的直接依赖，使会话上下文子域回到零框架/零技术依赖。当前该文件第 17 行 `import logging`、第 25 行 `logger = logging.getLogger(__name__)`，仅在 `BaseMessage.from_dict` 的历史会话恢复分支（第 166、181 行）以两处 `logger.warning(...)` 使用（`raise` 策略前告警、`filter` 策略过滤告警）。
- **需求 B（承接前置需求 3，`Domain_Serialization_Concern`，低）**：把 `src/domain/` 内多个领域对象自带的 `to_dict()` 序列化职责移出领域层，改由基础设施层的序列化映射器 / 独立非侵入函数承担。序列化属基础设施关注点（违反 `docs/steering/srp-principle.md` 与 `docs/steering/ddd-tactical-modeling.md` 第 9 节「序列化、日志等技术关注点不入领域对象」）。

### 动机

让 `domain/` 严格只承载业务语义：领域层既不知道「如何写日志」，也不知道「如何变成 dict」。这既呼应前置规范的第 9 节护栏，也为后续更高风险的纠偏（需求 1/2/4）扫清最容易验证的两块地基——两项均可靠既有测试全绿 + 逐字段 diff 客观验收。

### 硬约束（贯穿需求 A、B）

- **行为等价纯重构（`Behavior_Equivalent_Refactor`）**：不改变任何对外可观测行为——HTTP 端点契约、SSE 流式协议、事件时序、终止/审批语义、以及**序列化产出 dict 的键集合、取值、嵌套形态与字段顺序**须字面等价。
- **既有测试全绿（`Existing_Test_Suite_Green`）**：`PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）重构前后全绿，当前基线约 **2824 passed / 3 skipped / 0 failed**；测试因文件移动需调 import 时**只改 import 不改断言语义**。
- **绑定 steering**：须满足 `docs/steering/` 下 `ddd-architecture.md`（分层依赖方向、明确禁止/例外）、`ddd-tactical-modeling.md`（第 9 节技术关注点不入领域）、`srp-principle.md`、`change-discipline.md`（最小改动、按规模选流程门、可追溯）、`code-documentation.md`（中文 docstring）、`python-typing-lint.md`（`ruff`/`pyright` 零新增错误、禁裸 `Any`）。
- **架构级决策须写 ADR**：若需求 B 的序列化外移引入**新的基础设施序列化抽象**或**改变职责归属**，按 `docs/steering/adr.md` 属架构级决策，落地阶段须新增 ADR（现有 ADR 已至 0007，**新增从 0008 起**）；ADR 须尊重且不 supersede ADR-0001（领域事件决策不回退）。本 requirement 层仅登记该约束。
- **依赖管理仅 `uv`**：不新增第三方依赖，不改依赖管理方式。

### 不包括（Out of Scope）

1. **不做前置需求 1**（`Domain_Logic_In_Infrastructure`，3310 行 Agent Loop 上提领域层，极高风险）——登记为独立后续 spec，且先写 ADR 再落地。
2. **不做前置需求 2**（`Anemic_Domain_Model` 充血化试点）——登记为后续 spec。
3. **不做前置需求 4**（`Application_Transaction_Script` 应用层大文件拆分）——登记为后续 spec。
4. **不纳入 `domain/agent/tools.py::to_schema`**：其语义为「生成工具 JSON schema 供 LLM function calling」，是领域对工具契约的自描述而非「领域数据的持久化/线格式序列化」，与需求 B 的 `Domain_Serialization_Concern` 性质不同；本 spec 将其登记为观察项，不在本期外移。
5. 不改前端 `epsilon-client/` 任何代码。
6. 不新增/替换第三方依赖，不改变依赖管理方式（仍仅 `uv`）。
7. 不回退 ADR-0001（禁止复活领域事件总线）。
8. 不改动前置 spec 已落地的 `docs/steering/ddd-tactical-modeling.md`、`docs/adr/0007-*` 等规范文档结论。
9. 不改变任何序列化产物的对外线格式（wire format）；持久化（Redis/本地文件会话与 trace）、HTTP 响应、事件 payload 所见的 JSON/字典结构保持字面不变。

## 术语表

沿用前置 spec `ddd-implementation-review` 的术语；本 spec 收窄至两项并补充落地判据术语。

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 领域纯净度瑕疵 | `Domain_Purity_Blemish` | `src/domain/chat/context.py` 第 17 行 `import logging` 且模块级 `logger = logging.getLogger(__name__)`；日志是技术关注点，领域层应对基础设施与框架 API 零依赖（`ddd-architecture.md`「明确禁止的依赖」）。当前仅 `BaseMessage.from_dict` 历史恢复分支两处 `logger.warning` 使用。 |
| 领域序列化职责 | `Domain_Serialization_Concern` | `src/domain/` 内多个领域对象自带 `to_dict()` 序列化方法：`domain/run/workflow.py`（8 处 `to_dict` + 模块级私有辅助 `_dataclass_to_json_safe_dict` / `_json_safe`）、`domain/agent/guardrails.py`（`GuardrailModelPricing`/`GuardrailRuntimeStats`/`GuardrailSummary` 共 3 处 `to_dict` + `_json_safe`）、`domain/health/value_objects.py`（`HealthCheckResult`/`ReadinessResult` 共 2 处）、`domain/agent/segmented_execution.py`（`SegmentBudgetUsage.to_dict` + `SegmentRunMetadata.to_http_dict`）。序列化属基础设施关注点，违反 SRP 与 `ddd-tactical-modeling.md` 第 9 节。 |
| 行为等价重构 | `Behavior_Equivalent_Refactor` | 本 spec 全程为纯重构：调整技术关注点归属，但对任何外部消费者（HTTP 客户端、前端、测试断言、日志观测点、事件时序、持久化线格式）的可观测行为保持字面等价。 |
| 既有测试全绿基线 | `Existing_Test_Suite_Green` | `PYTHONPATH=src uv run --frozen pytest`（在 `epsilon-boot/` 下执行）在重构前后均全部通过（基线约 2824 passed / 3 skipped）；测试因文件移动需调 import 时只改 import 不改断言语义。 |
| 领域日志解耦 | `Domain_Logging_Decoupling` | 从 `domain/chat/context.py` 移除对 `logging` 的直接依赖，使该领域模块回到零框架/零技术依赖；被移除的告警若确有必要，改由调用方（application/infrastructure 层）承担或以领域中立方式（结构化返回值/异常携带信息）表达，不在领域层直接调用 `logging`。 |
| 序列化职责外移 | `Serialization_Extraction` | 将领域对象上的 `to_dict()` / `to_http_dict()` / `to_event_payload()`（若涉及）等序列化职责移出领域对象自身，改由基础设施层的序列化映射器/独立非侵入函数承担；领域对象不再承载「如何变成 dict」的知识。 |
| 序列化映射器 | `Serialization_Mapper` | 基础设施层承担 `Serialization_Extraction` 落点的具体形态：接受领域对象、产出与既有 `to_dict()` 逐字段等价字典的独立函数或映射器类，位于 `infrastructure/`（具体模块与形态由 design 决定）。 |
| 领域中立告警信号 | `Domain_Neutral_Warning_Signal` | 需求 A 中原 `logger.warning` 所承载的可观测语义（`filter` 策略下「发现 N 项 tool_call 违约已过滤」及其 `details`、`raise` 策略下「按 raise 策略抛出」）以不依赖 `logging` 的方式保留：或经异常（`InvalidToolCallIdError` 的 `extra`）携带、或经领域返回的结构化信息由上层记录，确保告警信号不被静默丢弃。 |
| 序列化往返等价 | `Serialization_Roundtrip_Equivalence` | 对任一被外移对象，外移后产出的 dict 与外移前 `to_dict()` 的输出在**键集合、键顺序、取值、嵌套形态**上逐字段字面等价；且 `from_dict` 反序列化路径不受影响（`domain/chat/context.py::ConversationContext.from_dict` 等仍能还原）。 |
| 序列化观察项 | `Serialization_Observation_Item` | 本期不纳入外移但需登记的序列化相关方法，典型为 `domain/agent/tools.py::to_schema`（工具 JSON schema，语义为契约自描述而非数据序列化）；design 阶段须判定其是否需在后续 spec 处理并登记结论。 |
| 架构决策记录 | `Architecture_Decision_Record` | `docs/adr/` 下的 ADR。凡引入新的基础设施序列化抽象或改变序列化职责归属属架构级决策，须按 `docs/steering/adr.md` 新增 ADR（从 0008 起），在 design 回链且不 supersede ADR-0001。 |

## 需求

### 需求 A：`Domain_Purity_Blemish` 领域日志解耦（轻微）

**用户故事：** 作为领域模型的维护者，我希望 `domain/chat/context.py` 不再直接依赖 `logging`，以便会话上下文子域回到零框架/零技术依赖、符合 `ddd-architecture.md`「明确禁止的依赖」，同时不丢失原有的历史会话恢复告警信号。

#### 验收标准

1. THE `Domain_Logging_Decoupling` SHALL 移除 `domain/chat/context.py` 第 17 行 `import logging` 与第 25 行 `logger = logging.getLogger(__name__)`；重构后 `grep -nE 'import logging|getLogger|logging\.' src/domain/chat/context.py` 零命中。
   - 验证：`cd epsilon-boot && grep -nE 'import logging|getLogger|logging\.' src/domain/chat/context.py`（期望无输出）。
2. WHEN 历史会话恢复命中 `raise` 策略（`_HISTORY_RESTORE_STRATEGY == "raise"` 且存在被跳过的 `tool_call`）, THE `BaseMessage` SHALL 保持抛出 `InvalidToolCallIdError` 且其构造参数（`source`/`raw_id_value`/`tool_name`/`tool_call_index`/`extra` 含 `skipped_count`、`session_id`）与解耦前逐字段等价，不因移除 `logger.warning` 而改变异常语义。
3. WHEN 历史会话恢复命中 `filter` 策略（存在被跳过的 `tool_call`）, THE `filter` 分支 SHALL 保持其对外可观测行为等价（非法 `tool_call` 被过滤、`tool_calls` 仅含合法项、`metadata` 原样保留、返回 `AssistantMessage` 不变）。
   - **实施修订（Checkpoint 1 复核后确认，2026-07-06）**：原 `filter` 分支的 `logger.warning("历史会话恢复发现 %d 项 tool_call 违约，已过滤", ...)` 被认定为**纯领域内部诊断 telemetry**——它不经异常上抛、无下游业务消费方，不属 `Behavior_Equivalent_Refactor` 所保护的对外可观测面（HTTP 契约 / wire format / 持久化 / 异常语义 / 控制流）。经用户批准，该内部日志信号随需求 A 的领域日志解耦**一并移除**，属**有意的、最小的可观测面变更**，并非静默丢弃：raise 分支信号完整保留于异常（见 AC 2），filter 分支仅移除内部日志、行为不变。相应地，3 个原断言该日志的既有单测（`test_t10_...`、`test_t8_...`、`test_session_id_propagated_into_log_extra`）已**保留全部行为/异常断言、删除日志断言**（`test_session_id_...` 因纯测日志改为断言 filter 分支的 metadata 保留行为）——此为 `Existing_Test_Suite_Green` 的**受控例外**：因领域日志有意移除而删除对应日志断言，不改任何行为断言语义。该决策记入 ADR-0008 与 summary。
4. WHILE `domain/chat/context.py` 原 `logger` 调用被判定为「移除后无可观测信息损失」, WHEN 执行 `Domain_Logging_Decoupling`, THE `Domain_Logging_Decoupling` MAY 直接删除该调用，前提是不改变任何控制流、返回值与异常语义（`filter`/`raise` 分支的输出逐一等价）。
5. THE `Domain_Logging_Decoupling` SHALL 保持 `Behavior_Equivalent_Refactor`：`ConversationContext` 与同模块消息类型（`BaseMessage`/`SystemMessage`/`UserMessage`/`AssistantMessage`/`ToolMessage`）的行为（校验、`to_dict`/`from_dict` 序列化、历史恢复策略 `filter`/`raise` 分支的返回值与异常）与解耦前逐一等价。
6. IF 告警信号的承载被移交调用方（application/infrastructure 层）, THEN THE 该调用方 SHALL 在领域层之外完成日志记录，且 `domain/chat/context.py` SHALL NOT 直接或间接 import 任何 `logging`/framework 技术模块。
7. THE `Existing_Test_Suite_Green` SHALL 成立；覆盖 `domain/chat/context.py` 的既有测试（含 `test/domain/chat/test_context_serialization_roundtrip_property.py`、`test/domain/chat/test_base_message_from_dict_raise_strategy_unit.py`、`test/domain/chat/test_base_message_from_dict_id_validation_unit.py`、`test/domain/chat/test_message_hierarchy_unit.py` 等）在解耦后全部通过。
   - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/chat`。
8. THE 需求 A SHALL 复核 `domain/` 其余模块是否仍有同类框架/技术 import（`logging` 等）；若发现同类瑕疵，SHALL 在 design 或后续 spec 登记，但按 `change-discipline` 本期只处理 `domain/chat/context.py` 一处，其余登记待议。
   - 验证：`cd epsilon-boot && grep -rnE '^import logging|getLogger' src/domain/`（登记命中项）。
9. THE 需求 A SHALL 遵循 `code-documentation.md`（改动处保留/更新中文 docstring）与 `python-typing-lint.md`（`ruff`/`pyright` 零新增错误、禁裸 `Any`）。

### 需求 B：`Domain_Serialization_Concern` 序列化职责外移（低）

**用户故事：** 作为领域模型的维护者，我希望领域对象不再自带 `to_dict()` 序列化职责，以便领域层专注业务语义、序列化关注点回归基础设施层，符合 SRP 与 `ddd-tactical-modeling.md` 第 9 节，且不改变任何对外线格式。

#### 验收标准

1. THE 需求 B SHALL 在 design 阶段清点 `domain/` 下全部待外移序列化方法并逐一给出外移目标（`Serialization_Mapper` / 独立映射函数），至少包含：
   - `domain/run/workflow.py`：`WorkflowCapabilityDecision.to_dict`、`WorkflowExecutionPolicy.to_dict`、`WorkflowPhaseRecord.to_dict`、`CollaborationStepTraceLink.to_dict`、`ParentChildRunLink.to_dict`、`ChildRunOrchestrationState.to_dict`、`CollaborationSummary.to_dict`、`WorkflowRunState.to_dict` 共 8 处，以及模块级私有辅助 `_dataclass_to_json_safe_dict` / `_json_safe`；
   - `domain/agent/guardrails.py`：`GuardrailModelPricing.to_dict`、`GuardrailRuntimeStats.to_dict`、`GuardrailSummary.to_dict` 共 3 处，以及模块级 `_json_safe`（`to_event_payload`、`to_summary`、`from_raw`、`from_model_usage` 等是否随迁由 design 判定其性质）；
   - `domain/health/value_objects.py`：`HealthCheckResult.to_dict`、`ReadinessResult.to_dict` 共 2 处；
   - `domain/agent/segmented_execution.py`：`SegmentBudgetUsage.to_dict`、`SegmentRunMetadata.to_http_dict` 共 2 处。
   - 验证：`cd epsilon-boot && grep -rnE 'def to_dict|def to_http_dict' src/domain/`（外移后领域层命中数应降为 design 记录的目标基线）。
2. THE `Serialization_Extraction` SHALL 把上述领域对象的序列化逻辑移出领域对象自身，改由 `Serialization_Mapper` 承担；重构后领域对象不再承载「如何变成 dict」的知识，模块级私有序列化辅助（`_dataclass_to_json_safe_dict` / `_json_safe`）SHALL 随之迁往基础设施层或独立映射函数，不残留于 `domain/`。
3. THE `Serialization_Extraction` SHALL 保持 `Serialization_Roundtrip_Equivalence`：对任一被外移对象，外移后产出的 dict 在**键集合、键顺序、取值、嵌套形态**上与外移前 `to_dict()`/`to_http_dict()` 输出逐字段字面等价（含 `frozenset` 排序、`StrEnum`/`Enum` 取 `.value`、`datetime` 取 `.isoformat()`、`event_timestamps` int 键的 stringify 等既有细节）。
4. THE `Serialization_Extraction` SHALL NOT 改变任何序列化产物的对外线格式（wire format）；上游消费方（会话持久化 `infrastructure/session/*`、trace 持久化 `infrastructure/trace/*`、HTTP 响应 `application/api/routers/*`、Run 事件与 checkpoint `application/run/*`）看到的 JSON/字典结构保持字面不变。
5. WHILE 某个待外移序列化方法被现有测试覆盖（如 `test/domain/run/*`、`test/domain/agent/test_guardrail_*`、`test/domain/health/test_value_objects_property.py`、`test/domain/agent/test_segmented_execution_value_objects_unit.py` 等）, WHEN 执行 `Serialization_Extraction`, THE `Existing_Test_Suite_Green` SHALL 成立：测试只按新调用位置调整 import 而不改断言语义。
   - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（全量，期望 2824 passed / 0 failed 或更高 passed 数、0 failed）。
6. IF `Serialization_Extraction` 引入新的基础设施序列化抽象或改变 `domain/`↔`infrastructure/` 的序列化职责归属, THEN THE `Architecture_Decision_Record` SHALL 记录该结构性决策（从 ADR-0008 起，含背景/决策/后果/备选方案与未采纳原因），在 `docs/adr/README.md` 索引登记，且 SHALL NOT supersede ADR-0001。
7. THE 需求 B SHALL 保持所有调用点行为等价：现有对 `to_dict`/`to_http_dict` 的 28 个源文件调用点（见 `grep -rlE 'to_dict|to_http_dict|to_event_payload' src/`）在外移后改为调用 `Serialization_Mapper`，调用结果与改前逐字段等价，`Behavior_Equivalent_Refactor` 成立。
8. THE `Serialization_Observation_Item` SHALL 明确将 `domain/agent/tools.py::to_schema` 登记为本期不外移的观察项，并在 design 说明其「工具契约自描述」性质与是否需后续 spec 处理的结论；本期 SHALL NOT 改动 `to_schema`。
9. THE 需求 B SHALL 遵循 `code-documentation.md`（`Serialization_Mapper` 及改动处中文 docstring）、`srp-principle.md`（领域对象只留业务语义）、`ddd-architecture.md`（映射器置于 `infrastructure/`、依赖方向 `infrastructure → domain`）与 `python-typing-lint.md`（`ruff`/`pyright` 零新增错误、禁裸 `Any`）。

### 需求 C：后续差距登记（本期不实施，仅登记）

**用户故事：** 作为后端架构维护者，我希望把前置 spec 中本期未纳入的三项差距（需求 1/2/4）与本 spec 识别的观察项显式登记为后续事项，以便它们不因本期只做 A/B 而被遗忘，且各自的风险档与流程门有据可查。

#### 验收标准

1. THE 需求 C SHALL 在 design 或 `TODO.md` 登记「前置需求 1（`Domain_Logic_In_Infrastructure`，3310 行 Agent Loop 上提，极高风险）作为独立后续 spec，且须先写 ADR 再落地」，不在本期实施。
2. THE 需求 C SHALL 登记「前置需求 2（`Anemic_Domain_Model` 单子域充血化试点，中）」与「前置需求 4（`Application_Transaction_Script` 应用层大文件拆分，低）」为后续 spec，标注建议流程门（是否需 ADR / 独立 spec），不在本期实施。
3. THE 需求 C SHALL 登记 `Serialization_Observation_Item`（`domain/agent/tools.py::to_schema`）的处置结论。
4. THE 需求 C SHALL NOT 改动任何 `epsilon-boot/` 源码；`Existing_Test_Suite_Green` 与 `Behavior_Equivalent_Refactor` 对需求 C 平凡成立（零代码改动）。

## 正确性属性（Correctness Properties）

- **Property 1（领域纯净度）**：重构后 `grep -rnE '^\s*import logging|getLogger|logging\.' src/domain/chat/context.py` 零命中；`domain/chat/context.py` 不 import 任何 `logging`/framework 技术模块。
- **Property 2（告警信号不丢失）**：需求 A 前后，历史会话恢复在 `raise` 与 `filter` 两条分支上承载的可观测信息（违约计数、`details`、异常 `extra`）无净损失——要么经异常携带、要么由上层等价记录。
- **Property 3（序列化逐字段等价）**：对需求 B 清单内每一个被外移对象，`Serialization_Mapper(obj)` 的输出与原 `obj.to_dict()`/`obj.to_http_dict()` 在键集合、键顺序、取值、嵌套形态上字面相等（含 `frozenset` 排序、enum `.value`、`datetime.isoformat()`、int 键 stringify 等细节）。
- **Property 4（线格式不变）**：会话/trace 持久化与 HTTP/事件消费方所见的 JSON/字典结构在本 spec 前后字面不变。
- **Property 5（领域序列化零残留）**：需求 B 落地后，`domain/` 下 `to_dict`/`to_http_dict` 与私有序列化辅助（`_dataclass_to_json_safe_dict`/`_json_safe`）命中数降至 design 记录的目标基线（`to_schema` 除外，属观察项）。
- **Property 6（测试全绿且断言不改）**：`PYTHONPATH=src uv run --frozen pytest` 在本 spec 前后均全绿（约 2824 passed / 0 failed）；因文件移动导致的 import 调整不改任何断言语义。
- **Property 7（依赖与规范合规）**：仅用 `uv`、不新增第三方依赖；`ruff`/`pyright` 零新增错误、禁裸 `Any`；新增/改动的公开单元有中文 docstring。
