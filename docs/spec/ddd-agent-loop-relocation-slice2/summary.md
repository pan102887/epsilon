# ddd-agent-loop-relocation-slice2 — 落地总结（P2 第二片）

## Feature

`ddd-agent-loop-relocation-slice2`：ADR-0010 `P2_Relocation` **第二片**，承接首片（ADR-0011）。以 ADR-0010 后果节预告的**领域服务 + 端口回调**（`Port_Callback_Decoupling`）解耦，把 `_iter_rounds` **循环编排主体**与 `_execute_tool_call` **控制流决策**上提领域层，全部 I/O / 副作用经领域端口 `AgentLoopEffects` 留基础设施。全程 `Behavior_Equivalent_Refactor`，遵守 ADR-0010 `P2_Invariants` 六条。

## 最终产物清单

### 新增（源码）
- `src/domain/agent/agent_loop_orchestration.py` — 领域服务 `AgentLoopOrchestrator.iter_rounds`（异步生成器）：承载 `Round_Loop_Control`（轮次区间推进、`budget_exceeded_pending_after_tools` 跨轮状态机、`Terminal_Round_Boundary_Assert`、`RoundOutcome` 五态产出协议）+ `Termination_Decision`（text/handoff/token_budget_exceeded/max_rounds），全部副作用经 `effects.*` 回调，复用首片 `detect_handoff`/`is_token_budget_exceeded`/`collect_pending_actions`/`RoundOutcome`。零 `infrastructure`/OTel 依赖。

### 修改（源码）
- `src/domain/agent/ports.py` — 新增 `ModelRoundResult` 值对象 + `AgentLoopEffects`（`Protocol`）端口：`prepare_runtime` / `perform_model_round` / `record_assistant_with_tool_calls` / `resolve_approval_policies` / `save_interrupt` / `prepare_tool_calls_for_execution` / `checkpoint_model_completed` / `checkpoint_approval_interrupt` / `record_terminated`；签名只引领域类型，无基础设施类型泄漏。
- `src/domain/agent/agent_loop_policy.py`（扩充首片模块）— 新增纯判定 `interpret_tool_guardrail_decision`（`ToolGuardrailBranch`）、`classify_tool_execution`（`ToolExecutionClassification` 值对象）、`collect_pending_actions`；复用首片构件不重复上提。
- `src/infrastructure/agent/react_agent_adapter.py` — 实现 `AgentLoopEffects` 端口方法（副作用实现从 `_iter_rounds` 片段平移，`perform_model_round` 内 `react_agent.round` span 闭合后返回，规避 OTel span/yield contextvars 冲突）；`_iter_rounds` 降为委托 `self._orchestrator.iter_rounds(effects=self, ...)` 薄驱动；`_execute_tool_call`/`_prepare_tool_calls_for_execution`/`_collect_pending_actions` 调用点直调领域判定，副作用顺序字面不变。

### 删除（源码）
- `src/infrastructure/agent/round_outcome.py` — 首片 re-export 兼容垫片按 `Shim_Cleanup` 删除，引用改指 `domain.agent.agent_loop_policy`（ADR-0011 后果节登记的清理项）。

### 新增（测试）
- `test/domain/agent/test_agent_loop_orchestrator_unit.py` — 以领域侧 fake `AgentLoopEffects` 驱动编排器，脱离运行时覆盖 text/tool_calls 协作协议/approval/handoff 短路/token_budget 跨轮/max_rounds 耗尽/`Terminal_Round_Boundary_Assert`/`last_response is None` 边界。
- `test/domain/agent/test_agent_loop_tool_policy_unit.py` — 覆盖三纯判定全分支。
- `test/infrastructure/agent/test_react_agent_characterization_resume_handoff.py` — 新增 resume+handoff 特征化用例，锁定共享循环控制骨架的恢复路径行为（ADR-0010 疑点 1，不改行为语义）。

### 新增/修改（文档）
- `docs/adr/0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md`（`Accepted`，不 supersede 0001/0010/0011）+ `docs/adr/README.md` 索引追加 0012。
- `docs/architecture.md` / `docs/domain-model.md` — 同步 `AgentLoopOrchestrator` 领域服务、`AgentLoopEffects` 端口、垫片清理说明。

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 循环骨架落点 | 新增领域服务 `agent_loop_orchestration.py`（异步生成器）| 编排职责与首片纯叶子函数 SRP 不同、需引 `AgentLoopEffects` 端口；对齐 `state_machine.py` 具名样板 |
| 副作用解耦形态 | 领域端口 `AgentLoopEffects`（`Protocol`），adapter 实现 | ADR-0010 后果节明示「领域服务 + 端口回调」；`Protocol` 方法调用非事件总线，不违反 ADR-0001 |
| OTel span/yield 冲突 | `perform_model_round` 内 span 闭合后返回 `ModelRoundResult`，orchestrator 在 span 外 yield | 规避源码明示的 contextvars 冲突，领域编排零 OTel 依赖 |
| `_execute_tool_call` 上提粒度 | 只剥离两个纯判定，本体副作用顺序留 adapter | `Scope_Shrink_Discipline`，793 行整体上提超本片风险预算 |
| 疑点 2 | 不修正（首片承载） | 行为等价纯重构不改字段取值 |

## 验证结论（独立复核，均由本会话实跑）

- **全量测试**：`PYTHONPATH=src uv run --frozen pytest` → **2921 passed, 3 skipped, 0 failed**（首片基线 2893 passed，+28 新增测试；`Existing_Test_Suite_Green` 成立）。
- **特征化基线**：`test_react_agent_characterization_*.py` 全绿（含新增 resume+handoff）。
- **领域零反向依赖**（Property 6）：`grep` `agent_loop_orchestration.py` / `agent_loop_policy.py` 无 `application`/`infrastructure`/`fastapi`/`pydantic`/`opentelemetry` → 零命中。
- **无事件机制**（Property 8）：`grep` `EventBus`/`DomainEvent`/`publish`/`subscribe` → 零命中；端口为 `Protocol`，不回退 ADR-0001。
- **端口无基础设施类型泄漏**（Property 6）：`ports.py` 无 `_GuardrailRuntimeAccumulator`/`_RoundStreamAccumulator`/OTel `Span` → 零命中。
- **`AgentPort` 四签名未变**（Property 7）：`run`/`run_streaming`/`run_events`/`resume` 字面未变。
- **疑点 2 不修正**（Property 5）：`agent_loop_policy.py:186` handoff 分支 `model=outcome.response.model if outcome.response else ""` 仍在。
- **`Shim_Cleanup`**（Property 9）：`src`/`test` 无 `round_outcome` 生产/测试引用（仅 `SOURCES.txt` 构建产物残留，自动再生，无实质依赖）。
- **规范合规**：`ruff check` All checks passed；`pyright` 领域文件 0 errors。
- **结构真实性**：`AgentLoopOrchestrator.iter_rounds` 含真实 `for round_num in range(...)` 循环 + 状态机 + 五态 yield + assert；adapter `_iter_rounds` 确为 `async for ... self._orchestrator.iter_rounds(effects=self)` 薄委托——确认为真实上提，非空转。

## 执行方式

按 spec-dev 规范以 DAG 波次并发编排 development 子代理：`wave1_leaf_policy → wave2_orchestrator → {wave3_shim_cleanup ∥ wave3_adr_docs} → audit`。波次间串行（依赖 + Checkpoint 门禁），Wave 3 正交任务（垫片清理 ∥ ADR/文档）并发。编排器（本会话）独立复跑全部门禁完成最终验收，并清理子代理当时误建于后端局部 docs 目录的重复 spec 目录、勘定 canonical `tasks.md` 完成状态。

## 后续事项（Follow-ups）

- **工具并发骨架**（`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）本片按范围保留基础设施；后续片可评估其并发编排是否进一步纳入领域。
- **`Infrastructure_Encapsulation_Candidates` 实现本体**（guardrail 累加器/abuse/`_RoundStreamAccumulator`/`merge_usage`/OTel/checkpoint）按 ADR-0008/0010 永留基础设施。
- **`SOURCES.txt`** 中 `round_outcome.py` 残留为 egg-info 构建产物，下次打包自动再生，无需手动处理。
- **ADR-0010 疑点 2**（handoff 分支 model 取父模型）仍按登记留待另开 spec 决策。
