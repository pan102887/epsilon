# 实现计划：P2 落地第二片——Agent Loop 循环编排主体上提与工具执行控制流解耦

> 本文件由已定稿的 `design.md` 展开为可执行、可勾选的任务清单。全程为 **`Behavior_Equivalent_Refactor`**：以 ADR-0010 后果节预告的**领域服务 + 端口回调**（`Port_Callback_Decoupling`）解耦 `_iter_rounds` 主体与 `_execute_tool_call` 控制流；复用首片（ADR-0011）`domain/agent/agent_loop_policy.py` 领域模块与委托范式，不重复上提首片构件（`RoundOutcome` / `detect_handoff` / `is_token_budget_exceeded` / `compute_total_tokens` / `outcome_to_agent_result`）。
> **全程硬约束**：领域新构件零 `application` / `infrastructure` / 框架 / Pydantic 依赖、不引领域事件（`P2_Invariants` 第 5 条，端口回调是 `Protocol` 方法调用）；不改 `AgentPort` 四签名（AC4.1）；`Infrastructure_Encapsulation_Candidates` 实现本体（guardrail 累加/abuse/OTel/checkpoint/`_RoundStreamAccumulator`/`merge_usage`/审批持久化 I/O/`handoff_context`/`workflow_capability_runtime`）与工具并发骨架（`_dispatch_concurrent_tool_calls` 等）**留基础设施**（需求 7）；ADR-0010 疑点 2 不修正；中文 docstring、全量类型标注、禁裸 `Any`、`ruff`/`pyright` 零新增；`Existing_Test_Suite_Green` 每波保持。
> **命令**：均在 `epsilon-boot/` 下执行（`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest ...`）。
> **行号说明**：本文 `@行号`（`_collect_pending_actions` 853、`_execute_tool_call` 1029-1822、`_prepare_tool_calls_for_execution` ~1420、`_iter_rounds` 1822-2432、`run` 2432、`resume` 2648、`run_streaming` 2911、`run_events` 3048）引自 design.md 现网核对，落地前以 grep 逐点复核防偏移。

## 概述

执行采用 **波次（Wave）+ Checkpoint 门禁** 结构（需求 7 AC7.5 内部分波增量），依赖方向 `infrastructure → domain` 自底向上，风险由低到高：

- **Wave 1（低风险叶子波，首片委托范式）**：扩充 `agent_loop_policy.py`——上提 `interpret_tool_guardrail_decision` / `classify_tool_execution` / `collect_pending_actions` 纯函数 + `ToolExecutionClassification` / `ToolGuardrailBranch` 值对象；新增领域单测；`react_agent_adapter.py` 的 `_execute_tool_call` / `_prepare_tool_calls_for_execution` / `_collect_pending_actions` 调用点直调领域判定（副作用顺序字面不变）。→ **Checkpoint 1**。
- **Wave 2（高风险编排波，领域服务 + 端口回调）**：`ports.py` 新增 `AgentLoopEffects` Protocol + `ModelRoundResult`；新增 `agent_loop_orchestration.py::AgentLoopOrchestrator.iter_rounds`（从源 `_iter_rounds` 骨架平移，副作用改经 effects）；`react_agent_adapter.py` 实现 `AgentLoopEffects` 端口方法（副作用实现从 `_iter_rounds` 片段平移）、`_iter_rounds` 降为委托编排器的薄驱动；新增编排器领域单测（fake effects）+ resume·handoff 特征化用例。→ **Checkpoint 2**。
- **Wave 3（Shim_Cleanup + ADR-0012 + 文档同步）**：删除 `infrastructure/agent/round_outcome.py` 垫片、改指领域模块；新建 ADR-0012 + README 索引；同步 `docs/architecture.md` / `docs/domain-model.md`。→ **Checkpoint 3（最终门禁）**。

> **反断裂顺序**：Wave 1 只加领域纯判定 + 调用点直调，`_iter_rounds` 结构不动；Wave 2 才引入编排器与端口、平移副作用；Wave 3 才清理垫片与文档，保证每个 Checkpoint 测试可绿。
> **`Scope_Shrink_Discipline`（需求 7 AC7.6）**：Wave 2 若发现 `_iter_rounds` 某片段与运行时耦合无法零风险经端口剥离（如 ContextVar 时序、span 嵌套），缩小该片段本片范围、登记 design/ADR-0012 后果节、留后续片，不强行大爆炸。

---

## Wave 1：领域纯控制流判定上提（叶子波，首片委托范式）

- [x] 1. 扩充 `agent_loop_policy.py` 领域纯判定 + 值对象，并委托调用点
  - [x] 1.1 落地前反向依赖 grep 核验
    - `cd /workspace/epsilon-boot && grep -rn "class HandoffPerformed\|class GuardrailDecision\|class GuardrailAction\|class ApprovalPolicy\|class PendingActionRequest" src/domain`（确认 `HandoffPerformed`@`domain/agent/exceptions.py`、`GuardrailDecision`/`GuardrailAction`@`domain/agent/guardrails.py`、`ApprovalPolicy`/`PendingActionRequest`@`domain/agent/value_objects.py` 均在领域层——已据实核验 `HandoffPerformed` 在 `domain/agent/exceptions.py`）。若任一非领域层，按 design 反向依赖复核以原生值入参替代类型引用。
    - _需求: 2.6, 3.2_ ; _design 反向依赖复核 / Property 6_
  - [x] 1.2 扩充 `src/domain/agent/agent_loop_policy.py`：`ToolGuardrailBranch` + `interpret_tool_guardrail_decision`
    - 新增 `ToolGuardrailBranch = Literal["proceed", "require_approval", "stop"]`（含 docstring）；`def interpret_tool_guardrail_decision(decision: GuardrailDecision | None) -> ToolGuardrailBranch`：`None`→`"proceed"`；`action is GuardrailAction.REQUIRE_APPROVAL`→`"require_approval"`；`action is GuardrailAction.STOP`→`"stop"`；其它→`"proceed"`。import `from domain.agent.guardrails import GuardrailAction, GuardrailDecision`（TYPE_CHECKING 或运行期按 `Enum` 需求）。与源 `_execute_tool_call`（guardrail 分支 ~1140-1200）/ `_prepare_tool_calls_for_execution`（~1470-1543）判据逐一等价。
    - _需求: 2.1, 2.6_ ; _design 组件 3 / Property 3_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_policy import interpret_tool_guardrail_decision, ToolGuardrailBranch; print('ok')"`。
  - [x] 1.3 扩充 `agent_loop_policy.py`：`ToolExecutionClassification` + `classify_tool_execution`
    - 新增 `@dataclass(frozen=True) class ToolExecutionClassification`（`is_error: bool` / `handoff_target: str | None` / `content: str` / `error_class: str | None`，各字段中文 docstring）；`def classify_tool_execution(exc: BaseException | None, *, handoff_signal: HandoffPerformed | None, timeout: float | None) -> ToolExecutionClassification`：handoff_signal 非空→`is_error=False, handoff_target=signal.target_agent, content=signal.content, error_class=None`；`ToolPermissionDeniedError`→`is_error=True, error_class="ToolPermissionDeniedError", content=str(exc)`；`TimeoutError`→`is_error=True, error_class="TimeoutError", content=f"工具执行超时（{timeout}s)"`；其它 `Exception`→`is_error=True, error_class=type(exc).__name__, content=str(exc)`。import `from domain.agent.exceptions import HandoffPerformed, ToolPermissionDeniedError`（据实核验后者归属，若非领域层则以入参标记替代）。与源 `_execute_tool_call`（1170-1220 附近异常分支）逐一等价。
    - _需求: 2.2, 2.6_ ; _design 组件 3 / Property 3_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_policy import classify_tool_execution, ToolExecutionClassification; print('ok')"`。
  - [x] 1.4 扩充 `agent_loop_policy.py`：`collect_pending_actions` 纯函数
    - 新增 `def collect_pending_actions(tool_calls: tuple[ToolCallRequest, ...], allowed_tool_names: frozenset[str] | set[str], policies: Mapping[str, ApprovalPolicy]) -> tuple[PendingActionRequest, ...]`：按顺序跳过 `name not in allowed_tool_names`；对 `policies[name].interrupt` 命中者产出 `PendingActionRequest(tool_call_id, tool_name, arguments, allowed_decisions=policy.allowed_decisions, reason=policy.risk_label)`。import `from domain.agent.value_objects import ApprovalPolicy, PendingActionRequest`、`from collections.abc import Mapping`。与源 `_collect_pending_actions`（853-882）逐一等价（not-allowed 的 `logger.warning` **不**进领域函数，留 adapter）。
    - _需求: 2.3, 2.6_ ; _design 组件 3 / Property 3_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_policy import collect_pending_actions; print('ok')"`。
  - [x] 1.5 新增领域单测 `test/domain/agent/test_agent_loop_tool_policy_unit.py`
    - 仅 import `domain.*`；覆盖：`interpret_tool_guardrail_decision`（None/REQUIRE_APPROVAL/STOP/其它→proceed 各一）；`classify_tool_execution`（handoff/permission/timeout/其它 Exception 四类，断言 is_error/handoff_target/content/error_class）；`collect_pending_actions`（not-allowed 跳过、命中 interrupt、未命中 interrupt、多工具顺序）。全量类型标注、禁裸 `Any`、中文 docstring。
    - _需求: 5.1, 5.3, 5.4_ ; _design 测试策略 2 / Property 3_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_agent_loop_tool_policy_unit.py -q`。
  - [x] 1.6 `react_agent_adapter.py` 调用点委托领域判定（副作用顺序不变）
    - `_execute_tool_call`：异常捕获后改用 `classify_tool_execution(exc, handoff_signal=..., timeout=timeout)` 得到 `is_error`/`handoff_target`/content/error_class，构造 `ToolExecutionResult`；guardrail 分支改用 `interpret_tool_guardrail_decision(guardrail_decision)` 分派 require_approval/stop/proceed。**不动**：checkpoint before/after、guardrail 累加、abuse、trace、`add_tool_result`、`_stamp_event`、`_log_tool_failure`、`_save_interrupt` 的位置与时机（需求 2 AC2.4/2.5）。
    - `_prepare_tool_calls_for_execution`：guardrail 分支改用 `interpret_tool_guardrail_decision`；副作用不动。
    - `_collect_pending_actions`：改为预解析 `policies = {tc.name: self._approval_policy.policy_for(tc.name) for tc in tool_calls if tc.name in config.allowed_tool_names}`、保留 not-allowed `logger.warning`，再调领域 `collect_pending_actions(tool_calls, config.allowed_tool_names, policies)`。
    - _需求: 1.8, 2.4, 2.5, 4.6_ ; _design 组件 4 / Property 3/4_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent -q`（含特征化测试全绿）。

---

## Checkpoint 1：领域纯判定就绪 + 调用点委托 + 副作用不变（门禁）

- [x] 2. CP1 Wave 1 门禁校验
  - 领域构件可 import：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_policy import interpret_tool_guardrail_decision, classify_tool_execution, collect_pending_actions, ToolExecutionClassification, ToolGuardrailBranch; print('ok')"`。
  - 领域单测 + 该子域回归全绿：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent test/infrastructure/agent -q`。
  - 领域零反向依赖（Property 6）：`cd /workspace/epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic)|from (application|infrastructure|fastapi|pydantic)" src/domain/agent/agent_loop_policy.py`（零命中）。
  - 规范合规：`cd /workspace/epsilon-boot && uv run ruff check src/domain/agent/agent_loop_policy.py src/infrastructure/agent/react_agent_adapter.py && uv run pyright src/domain/agent/agent_loop_policy.py`（零新增错误）。
  - _需求: 2.1, 2.2, 2.3, 2.6, 4.4_ ; _design Property 3/6_

---

## Wave 2：领域服务 + 端口回调承载循环编排主体（编排波，高风险）

- [x] 3. `ports.py` 新增 `AgentLoopEffects` Protocol + `ModelRoundResult`
  - [x] 3.1 在 `src/domain/agent/ports.py` 新增 `ModelRoundResult` 值对象与 `AgentLoopEffects` Protocol
    - `ModelRoundResult`（`@dataclass(frozen=True)`，`response: LLMResponse`、`total_usage: dict[str, int]`）；`AgentLoopEffects(Protocol)` 方法照 design 组件 1：`prepare_runtime` / `perform_model_round` / `record_assistant_with_tool_calls` / `resolve_approval_policies` / `save_interrupt` / `prepare_tool_calls_for_execution` / `checkpoint_model_completed` / `checkpoint_approval_interrupt` / `record_terminated`。签名只引领域类型（`ConversationContext` / `AgentConfig` / `ModelAccessPort` / `LLMResponse` / `ToolCallRequest` / `ApprovalPolicy` / `PendingActionRequest` / `ApprovalRequiredPayload` / `AgentTerminationReason` / `Mapping` / 原生），TYPE_CHECKING import 对齐既有 `ports.py` 风格。中文 docstring + 全量类型标注。
    - _需求: 3.1, 3.2, 3.4, 3.5_ ; _design 组件 1 / 反向依赖复核 / Property 6/8_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.ports import AgentLoopEffects, ModelRoundResult; print('ok')"`；`grep -nE "_GuardrailRuntimeAccumulator|_RoundStreamAccumulator|opentelemetry|Span" src/domain/agent/ports.py`（期望零命中，基础设施类型不入端口）。
- [x] 4. 新增 `agent_loop_orchestration.py::AgentLoopOrchestrator`
  - [x] 4.1 新建 `src/domain/agent/agent_loop_orchestration.py`
    - `class AgentLoopOrchestrator` + `async def iter_rounds(self, *, context, config, model_access, effects: AgentLoopEffects, start_round=1, initial_usage=None, terminal_round=None, preserve_guardrail_runtime=False) -> AsyncIterator[RoundOutcome]`。逻辑从源 `_iter_rounds`（1822-2432）骨架**平移**：轮次区间 `range(start_round, effective_terminal+1)`、`budget_exceeded_pending_after_tools` 状态机、入口 handoff 检测（复用 `detect_handoff`）、每轮经 `effects.perform_model_round` 取 `ModelRoundResult`、无 tool_calls→`effects.checkpoint_model_completed`+yield text、有 tool_calls→`effects.record_assistant_with_tool_calls`+`effects.resolve_approval_policies`+`collect_pending_actions`→命中则 `effects.save_interrupt`+`effects.checkpoint_approval_interrupt`+yield approval、否则 `effects.prepare_tool_calls_for_execution`（返回 executable+guardrail_approval）→`is_token_budget_exceeded` 标记→yield tool_calls、循环耗尽 `Terminal_Round_Boundary_Assert`+`effects.record_terminated(max_rounds)`+yield final。终止分支经 `effects.record_terminated(reason=...)`（span/日志在 effect 内）。**span/OTel/checkpoint/guardrail 具体调用一律不出现在本文件**（经 effects）。复用首片 `detect_handoff`/`is_token_budget_exceeded`/`RoundOutcome`，不重复定义。中文 docstring、全量类型标注。
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.8_ ; _design 组件 2 / Property 1/2/6_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_orchestration import AgentLoopOrchestrator; print('ok')"`；`grep -rnE "import (application|infrastructure|fastapi|pydantic)|opentelemetry|tracer" src/domain/agent/agent_loop_orchestration.py`（零命中）。
  - [x] 4.2 新增编排器领域单测 `test/domain/agent/test_agent_loop_orchestrator_unit.py`
    - 定义领域侧 fake `AgentLoopEffects`（可编程 `perform_model_round` 返回序列、记录调用序列）；覆盖 text 终止、tool_calls 协作协议（yield 后回写继续下一轮）、approval 中断、handoff 短路（构造带 handoff_target ToolMessage）、token_budget_exceeded 跨轮 pending、max_rounds 耗尽、`Terminal_Round_Boundary_Assert` 触发、`last_response is None` 边界。仅 import `domain.*`。
    - _需求: 5.1, 5.2, 5.4_ ; _design 测试策略 1 / Property 1/2/5_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_agent_loop_orchestrator_unit.py -q`。
- [x] 5. `react_agent_adapter.py` 实现 `AgentLoopEffects` + `_iter_rounds` 委托
  - [x] 5.1 实现 `AgentLoopEffects` 端口方法（副作用从 `_iter_rounds` 片段平移）
    - `ReActAgentAdapter` 增补端口方法：`perform_model_round`（平移源 1900-2050 的 context 构建 + `_RoundStreamAccumulator` + `merge_usage` + guardrail model_completed + `react_agent.round` span，**span 内闭合后返回** `ModelRoundResult`）；`record_terminated`（平移 `react_agent.terminated` span + `_log_token_budget_exceeded` / max_rounds `logger.warning`）；`record_assistant_with_tool_calls`（调 `self._record_assistant_with_tool_calls`）；`resolve_approval_policies`（`policy_for` 预解析 + not-allowed warning）；`save_interrupt`（调 `self._save_interrupt`）；`prepare_tool_calls_for_execution`（调既有同名方法）；`checkpoint_model_completed`/`checkpoint_approval_interrupt`（平移 checkpoint sink 调用）；`prepare_runtime`（平移 ContextVar guardrail 累加器 + abuse detector 初始化 + `_ensure_agent_system_prompt`）。每方法内部行为与源片段字面等价。
    - _需求: 1.7, 3.1, 3.3, 4.2_ ; _design 组件 1/4 / Property 4_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent -q`（阶段性，5.2 后全绿）。
  - [x] 5.2 `_iter_rounds` 降为委托编排器的薄驱动
    - `__init__` 构造 `self._orchestrator = AgentLoopOrchestrator()`；`_iter_rounds(...)` 改为 `return self._orchestrator.iter_rounds(context=context, config=config, model_access=model_access, effects=self, start_round=start_round, initial_usage=initial_usage, terminal_round=terminal_round, preserve_guardrail_runtime=preserve_guardrail_runtime)`。保持签名与四入口调用方式字面不变（`run` 2432 / `resume` 2648 / `run_streaming` 2911 / `run_events` 3048 的 `async for outcome in self._iter_rounds(...)` 不改）。删除已平移进端口方法/编排器的 `_iter_rounds` 原主体。
    - _需求: 1.6, 1.7, 4.1, 4.2, 4.3_ ; _design 组件 4 / Property 4/5/7_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent -q`；`grep -nE "def run|def run_streaming|def run_events|def resume" src/domain/agent/ports.py`（AgentPort 四签名未变）。
  - [x] 5.3 新增 resume+handoff 特征化用例（ADR-0010 疑点 1）
    - 在既有 `test/infrastructure/agent/test_react_agent_characterization_*.py` 相应文件新增一条 `resume` 恢复路径 handoff 短路特征化用例，锁定当前行为（不改行为语义），作本片安全网。
    - _需求: 5.5, 1.6_ ; _design 测试策略 3 / Property 5_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_*.py -q`。

---

## Checkpoint 2：全量绿 + 特征化基线绿 + 编排零反向依赖（门禁）

- [x] 6. CP2 Wave 2 门禁校验
  - 全量测试绿（`Existing_Test_Suite_Green`）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed）。
  - 特征化基线绿（含新增 resume+handoff）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_*.py -q`。
  - 编排/端口零反向依赖 + 无事件机制（Property 6/8）：`cd /workspace/epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic)|opentelemetry" src/domain/agent/agent_loop_orchestration.py src/domain/agent/ports.py`（零命中除既有 ports 合法项）；`grep -rnE "EventBus|DomainEvent|publish|subscribe" src/domain/agent/agent_loop_orchestration.py`（零命中）。
  - AgentPort 四签名未变（AC4.1）：`grep -nE "def run|def run_streaming|def run_events|def resume" src/domain/agent/ports.py`（人工核对）。
  - lint：`cd /workspace/epsilon-boot && uv run ruff check src/domain/agent/agent_loop_orchestration.py src/domain/agent/ports.py src/infrastructure/agent/react_agent_adapter.py && uv run pyright src/domain/agent/agent_loop_orchestration.py`。
  - _需求: 1.1–1.8, 3.1–3.5, 4.1–4.6_ ; _design Property 1–8_

---

## Wave 3：Shim_Cleanup + ADR-0012 + 文档同步

- [x] 7. 首片垫片清理 + ADR-0012 + 文档
  - [x] 7.1 `Shim_Cleanup`：删除 `infrastructure/agent/round_outcome.py` 垫片
    - 先 `cd /workspace/epsilon-boot && grep -rn "infrastructure.agent.round_outcome" src test`（列出所有引用）；把生产/测试引用改指 `from domain.agent.agent_loop_policy import RoundOutcome, RoundOutcomeKind`（仅改 import 不改断言）；确认无剩余引用后删除 `src/infrastructure/agent/round_outcome.py`。IF 仍有无法安全改指的外部依赖，按 `Scope_Shrink_Discipline` 保留垫片并登记 ADR-0012 后果节。
    - _需求: 6.5, 4.6_ ; _design 组件 4 / Property 9_
    - 验证：`cd /workspace/epsilon-boot && grep -rn "round_outcome" src test`（期望无生产引用）；`PYTHONPATH=src uv run --frozen pytest`（全绿）。
  - [x] 7.2 新建 ADR-0012 + README 索引
    - `docs/adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md`（四段式、`Accepted`、`date: 2026-07-07`、`supersedes:` 留空、不 supersede 0001/0010/0011）：照 design「ADR-0012 草案要点」写背景/决策/后果/备选方案；`docs/adr/README.md` 索引表 0011 行后追加 0012 行。
    - _需求: 6.1, 6.2, 6.3, 6.4, 6.6_ ; _design ADR-0012 草案要点 / Property 8_
    - 验证：`test -f docs/adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md`；`grep -nE "^supersedes:" docs/adr/0012-*.md`（值为空）；`grep -n "0012" docs/adr/README.md`。
  - [x] 7.3 同步 `docs/architecture.md` / `docs/domain-model.md`
    - `architecture.md`：ReAct Agent Loop 流程 + Port/Adapter 章节补 `AgentLoopOrchestrator` 领域服务 + `AgentLoopEffects` 端口 + 垫片已清理；`domain-model.md`：Agent Loop 编排构件节新增 `AgentLoopOrchestrator` / `AgentLoopEffects` / `ToolExecutionClassification` / `ToolGuardrailBranch` / `ModelRoundResult`，回链 ADR-0012。
    - _需求: 6.6_ ; _design 文档同步 / Property 6_
    - 验证：`grep -n "agent_loop_orchestration\|AgentLoopEffects" docs/architecture.md docs/domain-model.md`（命中）。

---

## Checkpoint 3：最终门禁（Property 全量验收）

- [x] 8. CP3 最终门禁校验（必须全部通过）
  - Property 1–9（全量绿）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed，`Existing_Test_Suite_Green`）。
  - 特征化基线绿（含 resume+handoff）：`... pytest test/infrastructure/agent/test_react_agent_characterization_*.py -q`。
  - Property 6（领域零反向依赖）：`grep -rnE "import (application|infrastructure|fastapi|pydantic)|opentelemetry" src/domain/agent/agent_loop_orchestration.py src/domain/agent/agent_loop_policy.py`（零命中）。
  - Property 8（无事件机制）：`grep -rnE "EventBus|DomainEvent|publish|subscribe" src/domain/agent/agent_loop_orchestration.py src/domain/agent/ports.py`（零命中）。
  - Property 7（AgentPort 四签名 + V3 冻结）：`grep -nE "def run|def run_streaming|def run_events|def resume" src/domain/agent/ports.py`（人工核对未变）。
  - Property 5（疑点 2 不修正）：`grep -n "outcome.response.model if outcome.response" src/domain/agent/agent_loop_policy.py`（首片承载，有命中）。
  - Property 9（Shim_Cleanup）：`grep -rn "round_outcome" src test`（无生产引用或按 Scope_Shrink 登记）。
  - 范围锁定（需求 7）：`git diff --name-only` 源码仅落 `src/domain/agent/agent_loop_policy.py`（扩充）+ `src/domain/agent/agent_loop_orchestration.py`（新增）+ `src/domain/agent/ports.py`（加端口）+ `src/infrastructure/agent/react_agent_adapter.py`（实现端口+委托+调用点）+ `src/infrastructure/agent/round_outcome.py`（删除）+ `test/domain/agent/`（新增单测）+ `test/infrastructure/agent/`（resume+handoff + import 调整），文档仅落 `docs/`；**未动**工具并发骨架（`_dispatch_concurrent_tool_calls`/`_stream_concurrent_tool_progress`/`_events_concurrent_tool_calls`）、guardrail 累加器/abuse/`_RoundStreamAccumulator`/`merge_usage` 实现本体、前端、依赖清单、`AgentPort`。
  - lint：`cd /workspace/epsilon-boot && uv run ruff check src/domain/agent src/infrastructure/agent/react_agent_adapter.py && uv run pyright src/domain/agent/agent_loop_orchestration.py src/domain/agent/agent_loop_policy.py`。
  - ADR/文档合规：`test -f docs/adr/0012-*.md`；`grep -nE "^supersedes:" docs/adr/0012-*.md`（空）；`grep -n "0012" docs/adr/README.md`；`grep -n "agent_loop_orchestration\|AgentLoopEffects" docs/architecture.md docs/domain-model.md`。
  - _需求: 1.1–1.8, 2.1–2.6, 3.1–3.5, 4.1–4.6, 5.1–5.6, 6.1–6.6, 7.1–7.6_ ; _design Property 1–9_

---

## 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 |
| --- | --- | --- | --- |
| 1.1（反向依赖 grep） | 2.6/3.2 | 反向依赖复核 | Property 6 |
| 1.2（interpret_tool_guardrail_decision） | 2.1/2.6 | 组件 3 | Property 3 |
| 1.3（classify_tool_execution） | 2.2/2.6 | 组件 3 | Property 3 |
| 1.4（collect_pending_actions） | 2.3/2.6 | 组件 3 | Property 3 |
| 1.5（工具判定单测） | 5.1/5.3/5.4 | 测试策略 2 | Property 3 |
| 1.6（调用点委托） | 1.8/2.4/2.5/4.6 | 组件 4 | Property 3/4 |
| 2（CP1） | 2.1/2.2/2.3/2.6/4.4 | 全领域叶子 | Property 3/6 |
| 3.1（AgentLoopEffects+ModelRoundResult） | 3.1/3.2/3.4/3.5 | 组件 1 | Property 6/8 |
| 4.1（AgentLoopOrchestrator） | 1.1/1.2/1.3/1.4/1.5/1.8 | 组件 2 | Property 1/2/6 |
| 4.2（编排器单测） | 5.1/5.2/5.4 | 测试策略 1 | Property 1/2/5 |
| 5.1（实现端口） | 1.7/3.1/3.3/4.2 | 组件 1/4 | Property 4 |
| 5.2（_iter_rounds 委托） | 1.6/1.7/4.1/4.2/4.3 | 组件 4 | Property 4/5/7 |
| 5.3（resume+handoff 特征化） | 5.5/1.6 | 测试策略 3 | Property 5 |
| 6（CP2） | 1.1–1.8/3.1–3.5/4.1–4.6 | 全组件 | Property 1–8 |
| 7.1（Shim_Cleanup） | 6.5/4.6 | 组件 4 | Property 9 |
| 7.2（ADR-0012+索引） | 6.1/6.2/6.3/6.4/6.6 | ADR-0012 草案 | Property 8 |
| 7.3（主题文档同步） | 6.6 | 文档同步 | Property 6 |
| 8（CP3 最终门禁） | 全部 | 全组件 | Property 1–9 |

---

## 备注

- **复用首片、不重复上提**：`RoundOutcome` / `RoundOutcomeKind` / `detect_handoff` / `is_token_budget_exceeded` / `compute_total_tokens` / `outcome_to_agent_result` 已在首片 `agent_loop_policy.py`，本片直调（需求 1 AC1.8）。
- **端口回调非事件（P2_Invariants 第 5 条）**：`AgentLoopEffects` 是 `Protocol` 方法调用，非领域事件/事件总线，不回退 ADR-0001。
- **副作用留基础设施（需求 7 AC7.1）**：guardrail 累加/abuse/OTel/checkpoint/`_RoundStreamAccumulator`/`merge_usage`/审批持久化 I/O/`handoff_context`/`workflow_capability_runtime` 实现本体与时机不动，只把调用编排经 `AgentLoopEffects` 上提。
- **OTel span/yield 冲突**：`perform_model_round` 内 `react_agent.round` span 闭合后返回 `ModelRoundResult`，orchestrator 在 span 外 yield，规避源码警示的 contextvars 冲突。
- **`Scope_Shrink_Discipline`（需求 7 AC7.6）**：Wave 2 若某片段无法零风险经端口剥离，缩范围登记 ADR-0012 后果节留后续片，不大爆炸。
- **疑点 2 不修正**：`outcome_to_agent_result` 的 handoff 分支 `model` 取父模型由首片承载，本片不改。
- **回滚**：各波独立、领域新构件为新增、adapter 局部改动、垫片删除与 ADR/文档独立，可按波次 `git revert`；行为等价故回滚不影响测试基线。
- **行号说明**：`@行号` 引自 design.md 现网核对，落地前再 grep 逐点复核防上游偏移。
