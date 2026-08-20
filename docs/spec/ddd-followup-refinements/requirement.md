# 需求文档：DDD Follow-up Refinements（DDD 收尾清理）

## 简介

本特性承接已闭合 spec `ddd-infrastructure-logic-remediation`（见 `docs/spec/ddd-infrastructure-logic-remediation/summary.md` 的 Follow-ups 节）登记的三项**非阻塞 follow-up**，继续做**纯行为等价**的清理与重构。三项 follow-up 分别是：

1. **application/run serializer 受控例外逐项清理**：`test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` allowlist 目前登记了 5 条受控迁移例外，即 `application/run/*` 模块生产代码直接 import `infrastructure` serializer。目标是按 allowlist 逐项消除这些 `Application_Layer -> Infrastructure_Layer` 直接导入，每消除一项就同步从静态 guard allowlist 删除一项，最终收敛该例外集合。此项涉及依赖倒置，属**中风险**，建议**最高优先级**先行。
2. **ChatServiceAdapter prompt load 去重**：`infrastructure/chat/chat_service_adapter.py` 构造期加载 `chat-default` prompt 并调用 `append_workspace_path_guidance`；组合根 `application/container_config.py` 中存在功能相同的 prompt load 重复细节。目标是去重（行为等价）。此项改动面小，属**低风险**。
3. **react_agent_adapter.py SRP 拆分**：`infrastructure/agent/react_agent_adapter.py` 约 3146 行，在同一文件内混杂多个技术关注点（guardrail 运行时累加、guardrail/tool trace 记账、工具并发调度、审批/checkpoint 缝合等）。目标是按 SRP 在**基础设施层内部**拆分为多个协作类/模块，提升可读性与可维护性。此项属**低风险但改动面大**。

三项均为**行为等价重构**：重构前后对外可观测行为、状态迁移、事件类型、错误语义、API 响应与流式协议、既有测试断言保持一致；不新增对外功能，不改变契约。

本特性范围包括：

- 逐项消除 `application/run/*` 到 `infrastructure` serializer 的直接导入，并同步收敛静态 guard allowlist。
- 去重 `ChatServiceAdapter` 与组合根之间重复的 prompt load / workspace guidance 细节。
- 在基础设施层内部按 SRP 拆分 `react_agent_adapter.py` 的技术关注点。
- 全量测试与静态导入 guard 的验证闭环，以及受影响主题文档同步。

明确不在本特性范围内：

- **不重开、不推翻 ADR-0013**：工具并发骨架（`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）继续留在 `Infrastructure_Layer`；本特性第 3 项只做纯技术层内拆分，**不做分层纠偏、不上提领域层**。
- 不把 `asyncio` 并发原语、`ContextVar`、OTel trace、Redis/file persistence、模型 SDK/HTTP 适配等运行时技术关注点迁入 `Domain_Layer`。
- 不新增对外功能、不改变 API 契约、事件类型或流式协议语义。
- 不修复 `ddd-infrastructure-logic-remediation` 范围外遗留的 handoff model discrepancy。
- 不做全仓大爆炸搬迁，不借本特性重排无关模块、重命名无关符号或批量格式化。
- 不推翻任何已 `Accepted` 的 ADR 方向决策（ADR-0008 serialization 归 infrastructure mappers、ADR-0013、ADR-0016 等）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 本收尾特性 | DDD_Followup_Refinements | 本 spec 描述的、承接 `ddd-infrastructure-logic-remediation` 三项非阻塞 follow-up 的行为等价清理 initiative。 |
| 领域层 | Domain_Layer | `epsilon-boot/src/domain`，承载领域值对象、领域服务、策略与 Port，禁止依赖 application/infrastructure。 |
| 应用层 | Application_Layer | `epsilon-boot/src/application`，承载用例编排、HTTP 路由、lifespan、异常映射与组合根。 |
| 基础设施层 | Infrastructure_Layer | `epsilon-boot/src/infrastructure`，承载 Port 实现、外部系统接入、序列化/持久化/运行时技术转换。 |
| 组合根例外 | Application_Composition_Root | `application/container_config.py`、`application/api/server_app.py` 等启动装配代码中允许同时引用 domain Port 与 infrastructure Adapter 的例外位置。 |
| 行为等价 | Behavior_Equivalence | 重构前后外部可观测行为、状态迁移、事件类型、错误语义、API 响应、流式协议和既有测试断言保持一致。 |
| 下游设计阶段 | Downstream_Design | 本 requirement 获批后的 `design.md` 阶段，负责技术切分方案与 ADR 判断。 |
| 实现切片 | Implementation_Slice | 后续 `tasks.md` 中可独立实现、验证和评审的最小行为等价改动单元。 |
| 后端静态导入守卫 | Backend_Static_Import_Guard | `test/static/test_architecture_import_boundaries.py` 中通过 AST 校验分层 import 边界的静态测试集合。 |
| 架构导入边界测试 | Architecture_Import_Boundary_Test | `test/static/test_architecture_import_boundaries.py` 内以 AST 解析源码（不导入生产模块）判定分层依赖的具体测试用例。 |
| 序列化受控例外 allowlist | Serialization_Exception_Allowlist | `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 字典，精确登记 `application/run/*` 模块允许直接导入的 `infrastructure` serializer 模块。 |
| 例外精确范围测试 | Exception_Scope_Exact_Test | `test_application_infrastructure_exception_scope_is_exact`，要求实际命中与 allowlist 完全相等、防止路径或模块静默扩大的测试。 |
| Run 应用模块 | Application_Run_Module | `application/run/*` 中的 Run 应用服务、执行协调器、checkpoint recovery、guardrail recorder 与 workflow orchestrator 等模块。 |
| Run serializer 导入例外 | Run_Serializer_Import_Exception | `Serialization_Exception_Allowlist` 中登记的单条 `application/run/* -> infrastructure.*_serialization` 直接导入例外。 |
| 序列化技术转换 | Serialization_Technical_Conversion | `infrastructure.agent.segment_serialization`、`infrastructure.run.workflow_serialization`、`infrastructure.agent.guardrail_serialization` 等把运行时摘要转成持久化/事件 payload 的技术转换实现。 |
| 结构协议注入 | Structural_Protocol_Injection | 由组合根将实现某结构化协议（Protocol）的 serializer/collaborator 注入 `Application_Run_Module`，使其不直接 import 具体 infrastructure 实现的解耦手段。 |
| 应用侧序列化抽象 | Application_Serialization_Abstraction | `Application_Run_Module` 依赖的、由领域侧或应用侧定义的序列化能力抽象接口，其具体实现留在 `Infrastructure_Layer`。 |
| 应用到基础设施导入规则 | Application_To_Infrastructure_Import_Rule | 除 `Application_Composition_Root` 与登记例外外，生产代码 `Application_Layer` 不得直接导入具体 infrastructure Adapter/serializer 的静态规则。 |
| Chat service adapter | Chat_Service_Adapter | `infrastructure/chat/chat_service_adapter.py`，实现 ChatServicePort 并承载顶层聊天编排。 |
| Prompt 加载去重项 | Prompt_Load_Deduplication | `Chat_Service_Adapter` 构造期与 `Application_Composition_Root` 中重复的 `chat-default` prompt 加载与 workspace guidance 拼接细节的去重。 |
| chat-default 系统提示词 | Chat_Default_System_Prompt | 通过 `PromptRegistryPort.get("chat-default")` 加载并经 `append_workspace_path_guidance` 处理后得到的系统 prompt 内容与 prompt_id。 |
| Workspace 路径引导拼接 | Workspace_Path_Guidance_Append | `infrastructure.prompt.workspace_guidance.append_workspace_path_guidance` 对 prompt 内容追加 workspace 路径引导的行为。 |
| ReAct agent 适配器 | ReAct_Agent_Adapter_Module | `infrastructure/agent/react_agent_adapter.py`，实现 AgentPort 的约 3146 行大文件，混杂多个基础设施技术关注点。 |
| Guardrail 运行时累加器 | Guardrail_Runtime_Accumulator | `react_agent_adapter.py` 中的 `_GuardrailRuntimeAccumulator` 及其 ContextVar，负责 guardrail 运行时统计累加。 |
| Guardrail/tool trace 记账 | Guardrail_Tool_Trace_Recording | `_record_error_trace` / `_record_tool_call_trace` / `_build_*_trace` 等 OTel trace 记账职责。 |
| 工具并发骨架 | Concurrent_Tool_Skeleton | `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls` 等基于 `asyncio.gather` 的并发/流式时序骨架，ADR-0013 已定案留 infrastructure。 |
| 审批/checkpoint 缝合 | Approval_Checkpoint_Stitching | `react_agent_adapter.py` 中审批筛选、checkpoint metadata 与 pending action 收集等运行时缝合职责。 |
| SRP 拆分项 | ReAct_Adapter_SRP_Split | 在 `Infrastructure_Layer` 内部按单一职责把 `ReAct_Agent_Adapter_Module` 的技术关注点拆分为多个协作类/模块的重构。 |
| 基础设施内部拆分 | Infrastructure_Internal_Refactor | 只在 `Infrastructure_Layer` 内重排类/模块归属、不改变分层方向、不上提领域层、不动 Port 契约的重构。 |
| 运行时技术关注点 | Runtime_Technical_Concern | `asyncio.gather`、OTel trace、ContextVar、Redis/file persistence、模型 SDK/HTTP 适配等技术实现关注点。 |
| Agent 端口 | Agent_Port | `domain/agent` 侧定义、由 `ReAct_Agent_Adapter_Module` 实现的 AgentPort 契约。 |
| 全量测试基线 | Full_Test_Suite_Baseline | `ddd-infrastructure-logic-remediation` 收官时记录的后端全量测试基线：3072 passed、2 skipped、1 warning。 |
| 全量测试集 | Full_Test_Suite | `PYTHONPATH=src uv run --frozen pytest` 执行的后端全量测试集合。 |
| 后端工作目录 | Backend_Working_Directory | `epsilon-boot/`，后端 `uv` 命令执行目录。 |
| 验证命令集合 | Verification_Command_Set | 每个 `Implementation_Slice` 完成时必须运行或记录无法运行原因的最小后端验证命令集合（含 `PYTHONPATH=src uv run --frozen pytest`）。 |
| 类型与 lint 基线 | Typing_Lint_Baseline | `docs/steering/python-typing-lint.md` 约束的全量类型标注、禁裸 `Any`、`ruff`/`pyright` 通过基线。 |
| 中文 docstring 规范 | Chinese_Docstring_Standard | `docs/steering/code-documentation.md` 要求的模块/类/公开函数中文 docstring。 |
| 最小改动纪律 | Minimal_Change_Discipline | `docs/steering/change-discipline.md` 的最小改动、单一意图、不夹带无关改动纪律。 |
| ADR 判断 | ADR_Decision_Need | `Downstream_Design` 对是否引入新一等抽象、是否改分层方向从而需新增 ADR 的判断。 |
| 已接受 ADR 基线 | Accepted_ADR_Baseline | ADR-0008、ADR-0013、ADR-0016 等已 `Accepted` 且本特性不得静默推翻的决策集合。 |
| ADR-0013 工具并发结论 | ADR_0013_Concurrency_Decision | ADR-0013 「工具并发骨架留基础设施、不开 P2 第三片」的方向结论。 |
| 受影响主题文档 | Affected_Topic_Documents | 本特性改动可能触发同步的 `docs/architecture.md`、`docs/agent.md`、`docs/di-container.md` 等主题文档集合。 |
| 优先级建议 | Priority_Recommendation | 三项 follow-up 的建议实现顺序：serializer 清理（中风险）优先，其后 prompt 去重（低风险）与 SRP 拆分（低风险大改动面）。 |

## 需求

### 需求 1：application/run serializer 受控例外逐项清理

**用户故事：** 作为后端架构维护者，我希望逐项消除 `Application_Run_Module` 对 `Infrastructure_Layer` serializer 的直接导入，以便 `Application_To_Infrastructure_Import_Rule` 不再依赖受控例外并最终收敛 `Serialization_Exception_Allowlist`。

#### 验收标准

1. THE DDD_Followup_Refinements SHALL treat serializer 受控例外清理 as a Behavior_Equivalence refactor that preserves all externally observable behavior of Application_Run_Module.
2. THE Priority_Recommendation SHALL rank Run_Serializer_Import_Exception cleanup as the highest priority follow-up because it involves dependency inversion and carries medium risk.
3. FOR ALL Run_Serializer_Import_Exception entries in Serialization_Exception_Allowlist, THE Downstream_Design SHALL choose either Application_Serialization_Abstraction relocation or Structural_Protocol_Injection to eliminate the direct import.
4. THE Serialization_Technical_Conversion SHALL remain in Infrastructure_Layer after each Run_Serializer_Import_Exception is eliminated.
5. THE Application_Run_Module SHALL NOT directly import Serialization_Technical_Conversion modules after the corresponding Run_Serializer_Import_Exception is eliminated.
6. WHEN a Run_Serializer_Import_Exception is eliminated, THE Serialization_Exception_Allowlist SHALL have the corresponding entry removed in the same Implementation_Slice.
7. WHEN all Run_Serializer_Import_Exception entries are eliminated, THE Serialization_Exception_Allowlist SHALL be empty and Application_To_Infrastructure_Import_Rule SHALL pass without serializer exceptions.
8. THE Exception_Scope_Exact_Test SHALL pass after each Implementation_Slice, reflecting exactly the remaining Serialization_Exception_Allowlist entries.
9. IF a Run_Serializer_Import_Exception cannot be eliminated within a slice, THEN THE remaining entry SHALL stay recorded with reason, exact import scope, and cleanup plan.

### 需求 2：ChatServiceAdapter prompt load 去重

**用户故事：** 作为聊天子域维护者，我希望去除 `Chat_Service_Adapter` 与 `Application_Composition_Root` 之间重复的 prompt 加载细节，以便 `Chat_Default_System_Prompt` 的加载只有单一来源。

#### 验收标准

1. THE DDD_Followup_Refinements SHALL treat Prompt_Load_Deduplication as a Behavior_Equivalence refactor.
2. THE Priority_Recommendation SHALL classify Prompt_Load_Deduplication as low risk with a small change surface.
3. THE Prompt_Load_Deduplication SHALL produce Chat_Default_System_Prompt content and prompt_id identical to the current behavior for the Chat_Service_Adapter.
4. THE Workspace_Path_Guidance_Append SHALL continue to be applied to the loaded prompt content under Behavior_Equivalence.
5. WHEN Prompt_Load_Deduplication is complete, THE Chat_Default_System_Prompt loading logic SHALL exist in a single source consumed by both Chat_Service_Adapter and Application_Composition_Root.
6. THE Prompt_Load_Deduplication SHALL keep Workspace_Path_Guidance_Append and prompt registry access within Infrastructure_Layer and Application_Composition_Root, without introducing Runtime_Technical_Concern into Domain_Layer.

### 需求 3：react_agent_adapter.py SRP 基础设施内部拆分

**用户故事：** 作为 Agent 运行时维护者，我希望把 `ReAct_Agent_Adapter_Module` 混杂的技术关注点按 SRP 在 `Infrastructure_Layer` 内部拆分，以便提升可读性与可维护性而不改变任何对外行为。

#### 验收标准

1. THE DDD_Followup_Refinements SHALL treat ReAct_Adapter_SRP_Split as a Behavior_Equivalence, Infrastructure_Internal_Refactor.
2. THE Priority_Recommendation SHALL classify ReAct_Adapter_SRP_Split as low risk with a large change surface.
3. THE ReAct_Adapter_SRP_Split SHALL separate Guardrail_Runtime_Accumulator, Guardrail_Tool_Trace_Recording, Concurrent_Tool_Skeleton, and Approval_Checkpoint_Stitching into distinct single-responsibility collaborators or modules.
4. THE ReAct_Adapter_SRP_Split SHALL keep Concurrent_Tool_Skeleton in Infrastructure_Layer in accordance with ADR_0013_Concurrency_Decision.
5. THE ReAct_Adapter_SRP_Split SHALL NOT relocate any Runtime_Technical_Concern into Domain_Layer.
6. THE ReAct_Adapter_SRP_Split SHALL NOT change the Agent_Port contract or its externally observable streaming, event, and error semantics.
7. THE ReAct_Adapter_SRP_Split SHALL keep the resulting Infrastructure_Layer modules within Infrastructure_Layer without introducing new cross-layer imports.
8. FOR ALL modules produced by ReAct_Adapter_SRP_Split, THE Chinese_Docstring_Standard SHALL be satisfied for modules, classes, and public functions.
9. THE ReAct_Adapter_SRP_Split SHALL NOT reopen ADR_0013_Concurrency_Decision.

### 需求 4：验证闭环与静态 guard 通过

**用户故事：** 作为架构治理负责人，我希望每项 follow-up 都由全量测试与静态导入 guard 守护，以便行为等价可被验证且分层边界不回归。

#### 验收标准

1. FOR ALL Implementation_Slice completions, THE Full_Test_Suite SHALL be executed from Backend_Working_Directory through `PYTHONPATH=src uv run --frozen pytest`.
2. FOR ALL Implementation_Slice completions, THE Full_Test_Suite SHALL remain passing at no worse than Full_Test_Suite_Baseline.
3. FOR ALL Implementation_Slice completions, THE Backend_Static_Import_Guard SHALL pass, including Architecture_Import_Boundary_Test and Exception_Scope_Exact_Test.
4. THE Verification_Command_Set SHALL include focused Backend_Static_Import_Guard execution and Full_Test_Suite execution.
5. WHEN a Verification_Command_Set command cannot be run, THE reason SHALL be recorded explicitly.
6. FOR ALL Implementation_Slice plans, THE Typing_Lint_Baseline SHALL be satisfied for changed production code.

### 需求 5：范围纪律、ADR 与文档同步保护

**用户故事：** 作为长期维护者，我希望本特性显式保护既定 ADR 结论、最小改动纪律和文档同步，以便后续 agent 不基于过时上下文跑偏。

#### 验收标准

1. THE DDD_Followup_Refinements SHALL preserve Accepted_ADR_Baseline unless a new ADR explicitly supersedes an existing decision.
2. THE DDD_Followup_Refinements SHALL NOT reopen or weaken ADR_0013_Concurrency_Decision through implementation-only changes.
3. FOR ALL Implementation_Slice plans, THE Minimal_Change_Discipline SHALL be enforced without unrelated renames, reordering, or bulk formatting.
4. THE Downstream_Design SHALL evaluate ADR_Decision_Need before introducing any new first-class abstraction for Application_Serialization_Abstraction or Structural_Protocol_Injection.
5. IF the Downstream_Design changes Port ownership or dependency direction, THEN THE ADR_Decision_Need SHALL be recorded with a recommendation.
6. WHEN a refactor changes layering structure, DI wiring, or Agent runtime module layout, THE Affected_Topic_Documents SHALL be synchronized.
7. FOR ALL three follow-ups, THE Priority_Recommendation SHALL be stated so implementation ordering reflects risk (serializer cleanup first, then prompt dedup and SRP split).
