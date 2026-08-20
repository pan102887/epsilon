# 真实任务 Golden Set

## 目标

`real_task_golden_success_rate` 用于把评测从组件桩测试推进到真实任务链路回归。
第一版覆盖 Run 核心能力：Chat/Task Run、暂停继续、审批恢复、workflow 选择、
guardrail 摘要透传、checkpoint recovery 成功与失败边界。

## 数据集

数据集位于 `tests/evaluation/datasets/real_task_golden.jsonl`，每行一个 case。
case 使用 deterministic script 描述模型 / worker outcome、workflow 选择或 checkpoint
fixture，避免真实 LLM、HTTP、Redis、外部文件系统和凭证进入 CI。

最小字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 全局唯一 case id |
| `description` | 场景说明 |
| `entrypoint` | `run` 或 `recovery` |
| `input` | Run 输入，如 `kind`、`message`、`goal`、`session_id`、`model` |
| `script` | 预设 outcome、workflow 或 checkpoint fixture |
| `expected` | 终态、关键事件、结果字段、workflow/guardrail/recovery 字段 |

## 运行

```bash
cd epsilon-boot
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval \
  --metric=real_task_golden_success_rate
```

`run_eval --metric=all` 会自动包含该指标；CI 的 evaluation job 无需额外配置。

## 新增 Case 原则

- 优先覆盖生产风险语义：终态、关键 `RunEvent`、恢复边界、审批恢复、workflow 状态和 guardrail 摘要。
- 每条 case 应有明确 `must_events` 和 `final_status`。
- 禁止引入真实外部网络、真实 LLM provider、真实 Redis 或凭证依赖。
- 若要覆盖线上 LLM 波动，应另建 nightly-only 评测，不放入此确定性 golden set。
