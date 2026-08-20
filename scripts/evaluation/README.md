# 评测脚本集合（scripts/evaluation）

面向 QA / 平台工程师的一站式评测工具文档。

## 硬约束

- **依赖管理**：仅允许 `uv`；禁止 `pip` / `poetry` / `pipenv` / `conda`。
- **产物路径**：所有文件只落在 `docs/evaluation/` / `tests/evaluation/` / `scripts/evaluation/` 三目录。
- **不改业务代码**：禁止修改 `epsilon-boot/src/` / `epsilon-client/` / `config.properties`。
- **评分源唯一性**：人工评分只在 `docs/evaluation/scores.toml` 维护；`scores.json` 与 `report.md` 自动生成段落由脚本覆盖，不要手工修改。

## 命令速查

所有命令从仓库根目录 `/workspace` 执行（或使用 `PYTHONPATH=/workspace`）：

### 1. 评测主入口（run_eval）

```bash
# 全部指标
uv run python -m scripts.evaluation.run_eval --metric=all

# 单指标
uv run python -m scripts.evaluation.run_eval --metric=tool_call_success_rate
uv run python -m scripts.evaluation.run_eval --metric=real_task_golden_success_rate

# 指定输出路径
uv run python -m scripts.evaluation.run_eval --metric=all --output=docs/evaluation/results/latest.json

# 带回归对比
uv run python -m scripts.evaluation.run_eval --metric=all \
  --baseline=docs/evaluation/results/2026-05-01_120000_abc.json \
  --regression-threshold=5.0
```

**参数**：
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--metric` | `all` | `all` 或具体 MetricId value |
| `--output` | 自动生成 | 结果 JSON 输出路径 |
| `--baseline` | 无 | 回归对比基线 JSON 路径 |
| `--regression-threshold` | `5.0` | 回退百分点阈值 |

**退出码**：`0` 成功 / `1` 脚本异常 / `2` 指标回退超阈值。

### 1.1 真实任务 Golden Set

`real_task_golden_success_rate` 读取 `tests/evaluation/datasets/real_task_golden.jsonl`。
该指标走真实 Run 应用服务、worker、workflow selection 与 checkpoint recovery
路径，但模型和外部工具保持 deterministic stub，不访问真实 LLM、HTTP、Redis 或文件系统外部资源。

每行 JSONL 至少包含：

| 字段 | 说明 |
|---|---|
| `id` | 全局唯一 case id |
| `description` | 中文或英文场景说明 |
| `entrypoint` | `run` 或 `recovery` |
| `input` | Run 输入，如 `kind`、`message`、`goal`、`session_id`、`model` |
| `script` | 预设 outcome、workflow 或 checkpoint fixture |
| `expected` | 预期终态、必须/禁止事件、结果字段、workflow/guardrail/recovery 字段 |

新增 case 时优先覆盖生产风险语义：终态、关键 `RunEvent`、恢复边界、审批恢复、
workflow 状态、guardrail 摘要。不要在该数据集中加入真实外部网络调用或凭证依赖。

### 2. 回归对比（compare_baseline）

```bash
uv run python -m scripts.evaluation.compare_baseline \
  --baseline=docs/evaluation/results/2026-05-01_120000_abc.json \
  --latest=docs/evaluation/results/latest.json \
  --threshold=5.0
```

**参数**：
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--baseline` | （必填） | 基线 JSON 路径 |
| `--latest` | （必填） | 最新 JSON 路径 |
| `--threshold` | `5.0` | 回退百分点阈值 |

**退出码**：`0` 成功或基线不存在 / `1` 脚本异常 / `2` 触发回归。

### 3. 评分聚合（aggregate_scores）

```bash
uv run python -m scripts.evaluation.aggregate_scores \
  --result=docs/evaluation/results/latest.json
```

**参数**：
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--result` | 无 | 最新评测 JSON 路径 |
| `--scores` | `docs/evaluation/scores.toml` | 评分源 TOML |
| `--output-root` | `docs/evaluation` | 产出根目录 |

**产出**：
- `docs/evaluation/scores.json` — 机器可读聚合结果
- `docs/evaluation/report.md` — 主报告（刷新 AUTO 区块，保留人工段落）

**退出码**：`0` 成功 / `1` 异常。

### 4. 证据校验（verify_evidence）

```bash
uv run python -m scripts.evaluation.verify_evidence

# 指定仓库根
uv run python -m scripts.evaluation.verify_evidence --repo-root=/workspace
```

**参数**：
| 参数 | 默认值 | 说明 |
|---|---|---|
| `--catalog` | 内置 catalog | 证据目录模块路径 |
| `--repo-root` | 自动推断 | 仓库根目录 |

**退出码**：`0` 全部通过 / `1` 有证据失败。

## scores.toml 维护规则

1. 按 7 维度分 `[dimension_id]` 段落。
2. 每段包含 `score`（1-5）、`rationale`（中文，≥ 3 句）、`evidence_refs`（≥ 3 条）。
3. 权重不在此文件出现，由 `tests/evaluation/rubric/dimensions.py` 唯一持有。
4. 修改评分后执行 `aggregate_scores` 刷新 `scores.json` 与 `report.md`。
5. 不要手工修改 `scores.json`（会被下次聚合覆盖）。
6. 不要修改 `report.md` 中 `<!-- AUTO-START -->` 与 `<!-- AUTO-END -->` 之间的内容。

## 排错速查

| 问题 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'tests'` | 缺少 `PYTHONPATH` | 从仓库根执行或设置 `PYTHONPATH=/workspace` |
| `ModuleNotFoundError: No module named 'scripts'` | 同上 | 同上 |
| pytest 退出码 5 | 无评测样本被收集 | 检查 `tests/evaluation/metrics/` 下是否有 `@pytest.mark.evaluation` 用例 |
| 证据校验失败 | 业务代码行号漂移 | 更新 `catalog.py` 中对应证据的行号 |
| `scores.toml` 语法错误 | TOML 格式问题 | 检查多行字符串闭合、引号配对 |
