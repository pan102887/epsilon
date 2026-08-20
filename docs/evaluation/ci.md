# Evaluation CI 策略

本文定义 `docs/evaluation/` 评测体系接入 GitHub Actions 的门禁边界、基线治理与本地复跑命令。

## 目标

- 让普通 PR 持续验证离线、确定性的 Agent 质量指标。
- 让 Nightly 保留完整评测节奏，但不把外部 Provider 密钥变成普通 PR 的硬依赖。
- 让回归判断统一使用固定基线与 5 个百分点阈值，避免口径漂移。

## PR 门禁相关入口盘点

仓库内已存在的 PR 门禁相关评测入口如下：

- `scripts/evaluation/run_eval.py`：主入口，执行三项离线指标并可对比基线。
- `scripts/evaluation/compare_baseline.py`：只做回归对比，退出码语义为 `0/1/2`。
- `scripts/evaluation/aggregate_scores.py`：聚合评测结果与人工评分，刷新 `scores.json` 与 `report.md`。
- `scripts/evaluation/verify_evidence.py`：校验报告证据路径与行号是否仍然有效。
- `tests/evaluation/self_tests/test_no_external_calls.py`：守卫评测流程不得调用 `httpx` / `openai` 外部通道。
- `tests/evaluation/self_tests/test_compare_baseline.py`：验证基线对比脚本的退出码与阈值语义。
- `tests/evaluation/self_tests/test_end_to_end.py`：验证 `run_eval` 与 `compare_baseline` 的端到端退出码契约。

经本次任务验证，适合作为 PR 门禁的最小离线命令是：

```bash
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval \
  --metric=all \
  --baseline=../docs/evaluation/results/2026-06-03_110744_feb5ec6.json \
  --regression-threshold=5.0
```

该命令只使用 `tests/evaluation/` 下的桩与 fixture，不依赖真实 LLM Provider、外部 HTTP 服务或密钥。

## PR 门禁策略

PR 必跑且必须通过的检查：

1. 离线评测自测：
   - `test_no_external_calls.py`
   - `test_compare_baseline.py`
   - `test_end_to_end.py`
2. 离线完整指标运行：
   - `run_eval --metric=all`
3. 基线回归判定：
   - 相对固定基线任一核心指标回退超过 5 个百分点即失败。

策略要求：

- PR 门禁不得要求任何 Provider 密钥。
- PR 门禁只运行可复跑、确定性的 fixture / stub 评测。
- 基线文件固定放在 `docs/evaluation/results/`。
- 更新基线文件时，必须在 PR 描述中说明：
  - 为什么旧基线不再适用；
  - 预期哪些指标会变化；
  - 变化属于产品行为调整、评测口径修正还是 bug 修复。

## Nightly 策略

Nightly 由 GitHub Actions `schedule` 触发，当前 cron 为：

```yaml
schedule:
  - cron: "17 19 * * *"
```

Nightly 当前执行的仍是离线完整评测：

```bash
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval \
  --metric=all \
  --output=../docs/evaluation/results/nightly-<run-id>.json \
  --baseline=../docs/evaluation/results/2026-06-03_110744_feb5ec6.json \
  --regression-threshold=5.0
```

说明：

- 仓库当前没有已落地、可在 CI 中稳定执行的真实 Provider 完整评测入口，因此 Nightly 暂时运行同一套离线完整指标。
- 若后续补充真实模型评测入口，应只放在 `if: github.event_name == 'schedule'` 的 job 中，并使用单独 secrets；不得回流为普通 PR 的必需条件。

## 非门禁项说明

`verify_evidence.py` 当前不纳入 PR 硬门禁，原因是仓库已有已知历史漂移：

- `epsilon-boot/src/application/routers/chat.py:L108-L134`

该证据行号已越界，会导致 `verify_evidence` 失败；这属于报告证据维护问题，不应阻断当前离线评测回归门禁。待证据目录刷新后，可再考虑把它提升为单独文档质量检查。

## 本地复跑方法

所有命令都在 `epsilon-boot/` 目录执行，因为 `uv.lock` 与 Python 项目在该目录；但评测模块位于仓库根的 `scripts/` 与 `tests/`，因此需要额外提供 `PYTHONPATH=../:src`。

### PR 同款命令

```bash
uv sync --frozen
uv run pytest ../tests/evaluation/self_tests/test_no_external_calls.py ../tests/evaluation/self_tests/test_compare_baseline.py ../tests/evaluation/self_tests/test_end_to_end.py -q --rootdir=..
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval --metric=all --baseline=../docs/evaluation/results/2026-06-03_110744_feb5ec6.json --regression-threshold=5.0
```

### Nightly 同款命令

```bash
uv sync --frozen
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval --metric=all --output=../docs/evaluation/results/nightly-local.json --baseline=../docs/evaluation/results/2026-06-03_110744_feb5ec6.json --regression-threshold=5.0
```

### 可选人工复核命令

```bash
PYTHONPATH=../:src uv run python -m scripts.evaluation.verify_evidence --repo-root=..
```

若该命令失败，先修复 `tests/evaluation/evidence/catalog.py` 中的过期行号，再考虑恢复为门禁项。
