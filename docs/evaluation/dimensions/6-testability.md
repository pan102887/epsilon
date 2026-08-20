# 维度 6：可测试性与质量

## 评估结论

**评分：3 / 5**。后端测试按 `domain / application / infrastructure / integration` 分层组织，覆盖值对象、Adapter、Router、多进程并发与启动期校验等场景；本次交付的 `Evaluation_Script` 补齐了 Tool_Call_Success_Rate / Delegation_Correctness / Context_Compaction_Effectiveness 三项回归指标。距离 4 / 5 的差距在：**评测脚本尚未接入 CI 门禁**，以及**缺少 Prompt 评估集与 LLM-as-judge 选项**。

## 证据与分析

- [`epsilon-boot/test`](../../../epsilon-boot/test)
  既有测试树按层次分布：`test/domain/{agent, chat, task, workspace, ...}`、`test/infrastructure/{agent, chat, model_access, session, telemetry, tools, workspace, ...}`、`test/application/routers`、`test/integration/{domain_event_decommission_gate, local_persistence_startup_validation, multiprocess_concurrency, ...}`。覆盖关键 Adapter 与 Router，形成"单元 + 集成"的基础回归。
- [`tests/evaluation/metrics/test_tool_call_success_rate.py`](../../../tests/evaluation/metrics/test_tool_call_success_rate.py)
  本次交付：通过 `ScriptedModelAccess` + 真实 `ReActAgentAdapter` 产出 20 条样本，覆盖 5 类场景；走 `@pytest.mark.evaluation` 由 `run_eval.py` 收集，填补"Agent 工具调用层无回归"缺口。
- [`tests/evaluation/metrics/test_delegation_correctness.py`](../../../tests/evaluation/metrics/test_delegation_correctness.py)
  本次交付：15 条样本覆盖 success / depth_exceeded / not_found / cycle_depth_exceeded / content_echo 五类；委派深度以 `config.properties:AGENT_MAX_DELEGATION_DEPTH` 驱动，精确到 `child_depth = parent + 1 ≤ MAX` 判据。
- [`tests/evaluation/metrics/test_context_compaction_effectiveness.py`](../../../tests/evaluation/metrics/test_context_compaction_effectiveness.py)
  本次交付：36 条 `(L, S, N)` 参数化样本直接驱动 `SlidingWindowCompactionAdapter.compact`，按 SystemMessage 数 / 非 system 数 / 保序 三项判据确定性判定。
- [`tests/evaluation/self_tests/test_rubric_consistency.py`](../../../tests/evaluation/self_tests/test_rubric_consistency.py)
  评测脚本自身的门禁：断言 `load_rubric()` 返回 7 个维度、权重 Σ=1.0、每维度跨 5 级去重框架 ≥ 2；任一失败触发 `RubricConsistencyError` 立即终止脚本。

**回归空白填补路径**：
- 以前：Agent Loop / 委派 / 压缩三条主链路只有"Adapter 单元测试"级别的覆盖，无"端到端 + 聚合比例"维度。
- 现在：三项指标走"真实 Adapter + 桩 Port" → 产出 `EvalResult.metrics[*].ratio`；`run_eval.py --baseline=<path>` 可以做相对基线的百分点差对比，退出码 2 用于 CI 失败信号。

## 业界框架对照

- **Berkeley Function-Calling Leaderboard（BFCL）**（<https://gorilla.cs.berkeley.edu/leaderboard.html>）：强调"工具选择准确率 / 参数正确率 / 执行成功率"三类独立指标。本次项目的 `Tool_Call_Success_Rate` 接近 BFCL 的"Execution accuracy"，但缺少"Tool selection accuracy"与"Parameter accuracy"的拆分；样本空间也远小于 BFCL。
- **τ-bench — Tool-use reliability & task completion metrics**（<https://arxiv.org/abs/2406.12045>）：提倡"任务完成率 + 多轮交互 + 真实业务协议"评测。项目当前评测多为单样本 / 单回合，距离 τ-bench 的多轮真实任务集有差距；未来可引入"委派链端到端完成率"。
- **LangSmith / LangChain — Evaluation & regression testing**（<https://docs.smith.langchain.com/evaluation>）：建议同一数据集在 PR 级 CI + 每日 cron 双轨跑。项目脚本已经支持 `--baseline`，距离 LangSmith 建议仅差"接入 CI + 结果存证"。

## 改进建议

1. **P0 — 评测脚本接 CI 门禁**：在仓库 CI（或内部 Jenkins）加入 step：`cd epsilon-boot && uv run python -m scripts.evaluation.run_eval --metric=all --baseline=docs/evaluation/results/<pinned>.json`；退出码 2 → fail PR。预期收益：PR 层就能发现 Agent 核心能力回退。
2. **P1 — 扩展指标维度**：参照 Berkeley FCL 把 `Tool_Call_Success_Rate` 拆成 "Tool selection" + "Parameter accuracy" + "Execution success"；参照 τ-bench 增加"多轮委派任务完成率"。在 `tests/evaluation/metrics/` 下新增独立样本文件。
3. **P1 — 引入可选 LLM-as-judge 评测通道**：在 `tests/evaluation/judges/` 新增"判官"模块（默认关闭，通过 `--enable-live-llm` 打开），对 Prompt 质量 / 中文流畅度做定性评分；沿用 τ-bench "judge with N-vote"模式。
4. **P2 — 文档化指标演进流程**：在 `scripts/evaluation/README.md` 加"新增指标步骤"章节，对齐 LangSmith 的"Dataset + Evaluator + Experiment" 三段式组织。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：3 / 5，**权重**：0.10，**加权得分**：0.300

**人工打分理由**：后端 `epsilon-boot/test/` 按 `domain / application / infrastructure / integration` 分层组织测试，覆盖值对象、Adapter、Router、多进程并发与启动期校验；本次交付的 `Evaluation_Script` 补齐了 Tool_Call_Success_Rate / Delegation_Correctness / Context_Compaction_Effectiveness 三项回归指标，接近 Berkeley Function-Calling Leaderboard 所强调的"Execution accuracy"判据。距离 4 分的差距：指标未按 BFCL 进一步拆成"Tool selection / Parameter accuracy / Execution success"三层，评测脚本尚未接入 CI 门禁，LangSmith 建议的"Dataset + Evaluator + Experiment"流程还没文档化；τ-bench 所建议的多轮委派端到端任务集 也尚未引入。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-boot/test`
- `tests/evaluation/metrics/test_tool_call_success_rate.py`
- `tests/evaluation/metrics/test_delegation_correctness.py`
- `tests/evaluation/metrics/test_context_compaction_effectiveness.py`
- `tests/evaluation/self_tests/test_rubric_consistency.py`

<!-- AUTO-END: aggregate_scores -->
