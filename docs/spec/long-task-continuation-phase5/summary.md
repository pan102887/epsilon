# 总结：长任务智能调度与护栏阶段五

已落地收敛版阶段五：新增 guardrail 领域模型、静态策略、配置、工具风险分级、ReAct 工具执行前阻断、Run 快照/API/CLI/TUI/Web 字段透传，以及阶段五 spec 文档。

默认 `observe` 模式不改变现有行为；显式 `enforce` 时 critical 工具会在真实执行前被阻断，high-risk 工具仅在配置开启后要求审批。金额预算仅保留为后续估算扩展入口，本阶段不作为硬停止条件。

本阶段不承诺完整运行时 guardrail 事件闭环、`guardrail_summary` 动态累计更新、模型完成后或工具执行后运行时评估接入、guardrail `require_approval` 接入 HITL、checkpoint recovery guardrail 累计状态恢复。`GUARDRAIL_EVALUATED` 与 `GUARDRAIL_BLOCKED` 在 v1 作为事件枚举预留。

验证结果：

- 后端：`env PYTHONPATH=src uv run --frozen pytest`，`2186 passed, 2 skipped`
- 前端：`npm run lint` 通过
- 前端：`npm run build` 通过，仅有 Next.js workspace root 推断警告
