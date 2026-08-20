# Summary: DDD Infrastructure Logic Remediation

## Feature Slug

`ddd-infrastructure-logic-remediation`

## Final Artifacts

- `requirement.md`、`design.md`、`tasks.md`、`review-log.md`：完整 spec-dev 流程产物。
- `epsilon-boot/src/domain/run/outcome.py`：Run execution outcome 与 outcome persistence decision 纯领域判定。
- `epsilon-boot/src/infrastructure/run/worker_contracts.py`：Run worker 所需结构化 collaborator 协议。
- `epsilon-boot/src/application/api/presenters/`：health / task API presenter 边界。
- `epsilon-boot/src/application/chat/`：`ChatSessionContextWorkflow` 与 `ChatApplicationService`。
- `epsilon-boot/src/domain/agent/handoff_policy.py`：handoff depth / workflow handoff count 纯判定。
- `docs/adr/0016-application-chat-workflow-and-handoff-policy-boundaries.md`：Chat workflow/service 与 Handoff policy 边界 ADR。
- `docs/architecture.md`、`docs/domain-model.md`、`docs/di-container.md`、`docs/api.md`、`docs/agent.md`：架构主题文档同步。

## Notable Design Decisions

- Run worker 依赖反转：`RunWorker` / `RunWorkerManager` 不再导入 `application.run.*` concrete classes；组合根注入 `RunSegmentExecutor` / `RunRecoverySweep` / metrics sink 协议实现，worker runtime 技术职责仍留 infrastructure。
- Run outcome 判定归 domain：`decide_run_outcome_persistence(...)` 只把 outcome status 映射为 store mutation 与 terminal event，覆盖 missing approval id fallback，不执行 I/O。
- API presenter 边界收敛：health/task router 改用 `application/api/presenters/`；剩余 `application/run/*` serializer 是静态 guard 精确登记的受控迁移例外。
- Chat 拆分：session load/save/index/system prompt 幂等注入归 `ChatSessionContextWorkflow`；continue/resume approval 用例编排归 `ChatApplicationService`；模型解析、stream/chunk/event 包装、prompt load/workspace guidance 仍留 `ChatServiceAdapter`。
- Handoff policy：depth/count 判定归 `domain/agent/handoff_policy.py`；ContextVar、DelegationPort、ToolExecutionResult、recorder、HandoffPerformed 仍留 infrastructure。
- ADR 判断：ADR-0016 记录 Chat workflow/service 与 Handoff policy 新一等抽象；Run worker 第一切片和 API presenter 收敛未新增单独 ADR。

## Test Coverage

- Checkpoint 12 static boundary: `8 passed`.
- Checkpoint 12 focused regression: `87 passed, 1 warning`.
- Full backend suite: `3072 passed, 2 skipped, 1 warning`.
- Final evaluator verdict: PASS.

## Follow-ups

- 继续按静态 guard allowlist 逐项迁移 `application/run/*` 对 infrastructure serializer 的受控例外。
- 可后续清理 `ChatServiceAdapter` 与组合根中重复的 prompt load 细节，但当前行为等价且非阻塞。
- 当前共享工作树存在本 spec 范围外的 `epsilon-boot/pyproject.toml` / `uv.lock` 依赖变更；最终 evaluator 已确认不影响本 checkpoint，但提交前需要单独归属或处理。
