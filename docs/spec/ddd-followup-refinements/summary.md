# Summary: DDD Follow-up Refinements（DDD 收尾清理）

## Feature Slug

`ddd-followup-refinements`

## 背景

承接已闭合 spec `ddd-infrastructure-logic-remediation` 的 `summary.md` 登记的三项**非阻塞 follow-up**，做纯行为等价清理与重构：不新增对外功能，不改 API 契约 / 事件类型 / 流式协议 / 错误语义。

## Final Artifacts

规格产物：
- `docs/spec/ddd-followup-refinements/requirement.md` / `design.md` / `tasks.md` / `review-log.md` / `summary.md`

### 切片 A：serializer 受控例外清理（allowlist 5 → 0）
- 新增 `epsilon-boot/src/application/run/serialization_ports.py`：应用侧序列化 Protocol（`WorkflowSerializerPort` / `GuardrailSerializerPort` / `SegmentSerializerPort`），仅依赖 domain 值对象与标准库。
- 新增 `epsilon-boot/src/infrastructure/run/run_serialization_adapters.py`：3 个无状态 delegating adapter，逐一委托既有 serializer 自由函数（序列化实现仍留基础设施，ADR-0008）。
- 改造 5 个消费方消除对 infrastructure serializer 的直接导入（required keyword 注入 + 组合根装配）：`run_execution_coordinator.py`、`run_guardrail_recorder.py`、`run_checkpoint_recovery_service.py`、`run_application_service.py`、`workflow_orchestrator.py`。
- `test/static/test_architecture_import_boundaries.py` 的 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` **收敛为空 `{}`**。
- 新增等价性单测 `test/infrastructure/run/test_run_serialization_adapters_property.py`。

### 切片 B：ChatServiceAdapter prompt 去重
- 新增 `epsilon-boot/src/infrastructure/chat/chat_default_prompt.py`：`resolve_chat_default_system_prompt` 单一来源（`get("chat-default")` + workspace guidance 追加 + `prompt_id` 提取）。
- `ChatServiceAdapter.__init__` 与组合根 `_create_chat_service` 两处共用该 helper。
- 新增单测 `test/infrastructure/chat/test_chat_default_prompt_unit.py`。

### 切片 C：react_agent_adapter.py SRP 拆分（3146 → 2502 行）
在基础设施层内部按 SRP 拆出 4 个协作模块，`ReActAgentAdapter` 保留为门面（`AgentPort` + `AgentLoopEffects` 契约与四入口签名不变）：
- `guardrail_runtime_accumulator.py`：guardrail 运行时统计累加器 + ContextVar。
- `react_trace_recorder.py`：结构化 trace / OTel 记账（8 个 trace 方法）。
- `react_concurrent_tool_executor.py`：同轮多工具并发骨架（**依 ADR-0013 仍留基础设施**，逐字平移，通过 `ToolExecutionRuntime` 窄协议回调门面）。
- `react_approval_checkpoint.py`：审批中断状态缝合（`collect_pending_actions` / `save_interrupt` / `latest_tool_calls_by_id`）。

### 文档同步
- `docs/architecture.md`：serializer 例外收敛为空 + react adapter 协作模块布局。
- `docs/agent.md`：ReAct Loop 模块切分 + chat-default 单一来源。
- `docs/di-container.md`：serializer adapter 装配 + chat prompt helper 消费。

## Notable Design Decisions

- **serializer 依赖倒置**：序列化 Protocol 定义在**应用层** `application/run/serialization_ports.py`（类比既有 `ApprovalResumer` / `worker_contracts` 先例，且 ADR-0008 已把序列化词汇移出 domain），实现留 infrastructure，组合根注入。全部 serializer 形参为 **required keyword，无 `None` 回退**（确保真正消除 import）。
- **SRP 拆分保守取舍**（design §5.4）：与门面核心执行链路深度耦合的审批决策应用（`_apply_approval_decisions` / `_record_rejected_tool_call` / workflow capability 中断，需回调 `_execute_tool_call` / checkpoint / `_tool_registry` / `_run_event_store`）以及 `AgentLoopEffects` 的 checkpoint sink 方法**保留在门面**，避免过度拆分引入行为风险。门面对已抽出方法保留薄委托包装，既有调用点与测试零改动。
- **内部符号别名**：`guardrail_runtime_accumulator` 导出去下划线的公开名，门面 `import ... as _GuardrailRuntimeAccumulator` 保留原私有名，零改既有测试。

## ADR 判断

三项 follow-up 均**不新增 ADR、不 supersede 任何已 Accepted ADR**：
- 切片 A 是依赖反转的**恢复**（消除 app→infra 反向导入，回到 `application → domain ← infrastructure` 默认方向），序列化 Protocol 为 feature-local 窄抽象（类比 `worker_contracts`，当时判定不新增 ADR）；ADR-0008 被严格遵守。
- 切片 B 是纯基础设施内部去重，无抽象、无依赖方向变化。
- 切片 C 是单层（infrastructure）内模块重排 + 门面委托，`ToolExecutionRuntime` 为 infra 内部窄协议；**不重开 ADR-0013**（工具并发骨架仍留基础设施）、不上提领域层、不改跨层契约。

## Test Coverage

- 全量后端测试：**3080 passed, 3 skipped, 1 warning**（基线 3072 passed，+8 来自新增单测；无劣化）。
- 静态导入守卫 `test/static/test_architecture_import_boundaries.py`：**8 passed**，`APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS` 精确等于空集。
- `uv run ruff check src`：All checks passed。
- `uv run pyright src`：67 errors（与 HEAD 基线一致，全部为既有文件），7 个新增文件贡献 **0 error**。
- 行为等价由既有回归测试网守护（`test/infrastructure/agent` 341 passed、`test/infrastructure/chat` 157 passed、`test/application/run` 系列不变断言）。

## 换行符纪律

改动全程遵守 CLAUDE.md「保留原文件换行符」硬约束：`container_config.py`（HEAD 原生 CRLF）保持统一 CRLF、零 lone-LF；其余改动文件保持原 LF。

## Follow-ups

- 已知 flaky：`test/application/test_run_*_container_wiring_unit.py` 在特定测试执行顺序下偶发失败（container_config 模块级 singleton 跨测试状态污染），单独运行恒绿；属**本 spec 之前既存**的潜在 flakiness，与本次 serializer 注入无关，建议后续单独治理（如给这些 wiring 测试加 singleton 重置 fixture）。
- pyright 67 项既有基线错误（如 `_safe_int(None)` 的 SupportsInt/SupportsIndex）属历史遗留，不在本 spec 范围。
