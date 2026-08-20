# Review Log：长任务智能调度与护栏阶段五

## 2026-06-07：需求收敛复核与最终验收

### 需求复核

- 角色：`spec_planner`
- 结论：原 `requirement.md` 不完全合理，过度承诺完整 guardrail 运行时闭环，超过阶段五收敛版 v1 的实现边界。
- 处理：已将阶段五范围收敛为确定性分类、guardrail 领域模型、静态策略、配置、工具风险分级、Run/API/CLI/TUI/Web 字段透传、`TASK_CLASSIFIED` 写入、`GUARDRAIL_EVALUATED` / `GUARDRAIL_BLOCKED` 枚举预留，以及 ReAct 工具真实执行前 critical enforce 阻断。
- 明确非范围：完整运行时 guardrail 事件闭环、`guardrail_summary` 动态累计更新、模型完成后或工具执行后运行时评估接入、guardrail `require_approval` 接入 HITL、checkpoint recovery guardrail 累计状态恢复。

### 设计与任务同步

- 角色：`spec_designer`
- 结论：旧 `design.md` 仍包含更宽的运行时闭环边界，已重写为阶段五收敛版 v1 设计。
- 角色：`spec_tasker`
- 结论：旧 `tasks.md` 过度承诺“工具执行前后接入策略”和“在事件中暴露摘要”，已修订为 v1 边界，并补出缺少明确测试支撑的验证项。

### 实现修正

- 补充 `AGENT_GUARDRAILS_MODEL_PRICING` 配置校验测试，覆盖合法 JSON object、非法 JSON、非 object、非法模型名、负数或非数字价格。
- 补充 API/CLI/TUI/Web 字段透传测试，覆盖 `task_classification` 与 `guardrail_summary`，并确保前端不复制策略判断。
- 更新 `tasks.md`、`summary.md` 与 `docs/plan.md` 的最终验证结果和阶段五后优先级。

### 验证

- 受影响测试子集：`49 passed`
- 后端全量：`cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest`，结果为 `2186 passed, 2 skipped`
- 前端 lint：`cd epsilon-client && npm run lint`，通过
- 前端 build：`cd epsilon-client && npm run build`，通过，仅有 Next.js workspace root 推断警告

### Evaluator 复核

- 第一轮 verdict：`FAIL`
- 失败原因：`docs/plan.md` 当前优先级仍保留阶段四历史内容；`tasks.md` 7.1 验证计数仍为补测前 `2176 passed, 2 skipped`。
- 修复：更新 `docs/plan.md` 当前优先级为阶段五后的收敛深化与阶段六设计准备；统一 `tasks.md` 验证计数为 `2186 passed, 2 skipped`。
- 最终 verdict：`PASS`
- Implementation Defects：无
- Upstream Issues：无
