# 交付总结：long-task-runtime-convergence

## 特性范围

长任务运行时收敛修复已按 `requirement.md`、`design.md` 与 `tasks.md` 完成 P0 / P1 / P2 全部任务：

- P0：guardrail Run 事件闭环、`guardrail_summary` 单一事实源、HITL 审批恢复复用、`risk_gate_required` 分段接线、协作摘要 `latest_steps` schema 归一。
- P1：基于真实模型 usage、工具执行、上下文增长、耗时与价格配置的确定性运行时统计，并保证 checkpoint/recovery 不双计数。
- P2：workflow role capability 最小权限治理、workflow 级 handoff 可观测与 phase 策略深化、默认关闭的保守 child run 编排与恢复语义。

## 最终 artifact 列表

- `docs/spec/long-task-runtime-convergence/requirement.md`
- `docs/spec/long-task-runtime-convergence/design.md`
- `docs/spec/long-task-runtime-convergence/tasks.md`
- `docs/spec/long-task-runtime-convergence/review-log.md`
- `docs/spec/long-task-runtime-convergence/summary.md`

## 主要实现位置

- 领域模型与 Port：
  - `epsilon-boot/src/domain/agent/guardrails.py`
  - `epsilon-boot/src/domain/agent/ports.py`
  - `epsilon-boot/src/domain/agent/segmented_execution.py`
  - `epsilon-boot/src/domain/run/runtime_context.py`
  - `epsilon-boot/src/domain/run/value_objects.py`
  - `epsilon-boot/src/domain/run/workflow.py`
  - `epsilon-boot/src/domain/run/workflow_context.py`
  - `epsilon-boot/src/domain/run/ports.py`
  - `epsilon-boot/src/domain/task/ports.py`
  - `epsilon-boot/src/domain/task/value_objects.py`
- 应用编排：
  - `epsilon-boot/src/application/run/run_guardrail_recorder.py`
  - `epsilon-boot/src/application/run/run_approval_resumer.py`
  - `epsilon-boot/src/application/run/run_application_service.py`
  - `epsilon-boot/src/application/run/run_execution_coordinator.py`
  - `epsilon-boot/src/application/run/run_checkpoint_recovery_service.py`
  - `epsilon-boot/src/application/run/workflow_orchestrator.py`
  - `epsilon-boot/src/application/container_config.py`
- 基础设施与适配器：
  - `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
  - `epsilon-boot/src/infrastructure/agent/delegation_adapter.py`
  - `epsilon-boot/src/infrastructure/agent/handoff_to_agent_tool.py`
  - `epsilon-boot/src/infrastructure/agent/workflow_capability_runtime.py`
  - `epsilon-boot/src/infrastructure/agent/workflow_collaboration_recorder.py`
  - `epsilon-boot/src/infrastructure/run/local_file_run_store_adapter.py`
  - `epsilon-boot/src/infrastructure/run/redis_run_store_adapter.py`
  - `epsilon-boot/src/infrastructure/run/run_config.py`
  - `epsilon-boot/src/infrastructure/run/workflow_config.py`
  - `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
  - `epsilon-boot/src/infrastructure/task/task_agent_adapter.py`
- API / CLI / Web 展示：
  - `epsilon-boot/src/application/api/routers/runs.py`
  - `epsilon-boot/src/application/cli/commands.py`
  - `epsilon-boot/src/application/cli/tui.py`
  - `epsilon-client/src/lib/chat-api.ts`
  - `epsilon-client/src/components/run/run-view.tsx`
  - `epsilon-client/src/components/run/run-event-list.tsx`
  - `epsilon-client/src/app/layout.tsx`
  - `epsilon-client/src/app/globals.css`

## 关键设计决策

- Guardrail 事实源收敛到 `RunObservationStorePort.record_runtime_observation(...)`，由 file/Redis store 在同一原子区追加事件并更新 snapshot 摘要。
- Guardrail 审批不新增审批系统，直接复用既有 `ApprovalInterrupt` / HITL approval recovery，并支持同一 Run 审批恢复后再次进入 `awaiting_approval`。
- `RunExecutionContext` 与 `workflow_context` 负责跨运行时路径传递 Run / workflow 状态，避免 Web、CLI 或前端重算策略。
- `CollaborationSummary` 新写路径仅输出 `latest_steps`，旧 `recent_steps` 只作为读取 fallback。
- P2 workflow capability 与 child run 默认关闭：
  - `RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED=true`
  - `RUN_WORKFLOW_ROLE_CAPABILITY_ENABLED=false`
  - `RUN_WORKFLOW_CHILD_RUN_ENABLED=false`
- Role capability 已接入真实 ReAct 工具执行、delegation/handoff adapter 与 child-run 创建前路径；未声明能力默认拒绝，且通过既有 HITL 兜底。
- 成功的真实 `handoff_to_agent` 不再只停留在 `ToolMessage.metadata` / collaboration step，而是写入 workflow 级 `handoff_state` 并追加 `WORKFLOW_HANDOFF_RECORDED`。
- Child run 编排保持保守：显式启用时才创建/链接真实 child Run，父 Run 等待前保存 checkpoint，恢复时只从已持久化 reconciliation 节点或保守失败态继续，不扩大 exactly-once 承诺。
- 前端移除 `next/font/google` 构建期外部字体下载，改用系统字体 CSS 变量，保证离线/受限网络构建可复现。

## 测试覆盖与最终验证

最终验证已通过：

- 后端全量：`cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest`
  - 结果：`2522 passed, 2 skipped`
- 前端 lint：`cd epsilon-client && npm run lint`
  - 结果：通过
- 前端 build：`cd epsilon-client && npm run build`
  - 结果：通过；仅保留 Next workspace-root 推断 warning 与 Node `module.register()` deprecation warning。
- 最终 spec-evaluator：PASS。

重点新增/增强测试覆盖：

- Guardrail summary / runtime stats 属性测试。
- Run observation store file/Redis 原子写入与 owner/lease 冲突测试。
- Guardrail recorder、Run approval resumer、Task approval resume 测试。
- ReAct / Chat / Task guardrail runtime、风险门禁与审批恢复测试。
- Run view schema、CLI/TUI rendering、前端契约测试。
- P0 / P1 / P2 integration tests。
- Role capability property/application tests。
- Runtime handoff persistence tests。
- Child run waiting/reconciliation/recovery tests。
- 静态架构边界与中文 docstring 约束测试。

## 后续建议

- 可追加一个更接近生产的 workflow integration test：在 `WorkflowRunOrchestrator` 启用的真实 phase 内触发 `handoff_to_agent`，进一步保护 orchestrator merge path。
- 若同一 segment 同时发生真实 `handoff_to_agent` 与 phase role transition，建议补充文档或测试明确 `WORKFLOW_HANDOFF_RECORDED` 重复/合并语义。
- 发布灰度建议先在 `AGENT_GUARDRAILS_MODE=observe` 下观察默认开启的 guardrail 收敛写路径，再逐步开启 enforce / require_approval 场景；P2 capability 与 child run 继续保持按 workflow 显式灰度。
