# AI Agent 工作台系统性评估报告

<!-- AUTO-START: report_header -->
> 生成时间：2026-06-03T11:08:13.604959+00:00
> 对应评测 JSON：`/workspace/docs/evaluation/results/2026-06-03_110744_feb5ec6.json`
> 总加权得分：**3.560 / 5**
<!-- AUTO-END: report_header -->

> 当前状态备注（2026-06-14）：本报告是 2026-06-03 的评估快照，未重新聚合长任务 phase3-6、checkpoint recovery、guardrail runtime convergence、workflow/collaboration 和 RunView 后续改动。当前项目现状以 [../project-overview.md](../project-overview.md) 与 [../plan.md](../plan.md) 为准；本报告保留为历史基线和评估体系说明，评分与 P0/P1/P2 清单不代表最新完成度。

## 执行摘要

**整体结论**：`epsilon-boot` + `epsilon-client` 的 Agent 工作台在"基础架构 / Agent 核心路径 / 沙箱安全"三个维度已达业界可用线，加权总分 **3.56 / 5**。七维度离散评分呈 **"双峰"分布**：DDD 六边形架构（`architecture=4`）、ReAct + 委派 + 压缩四件套（`agent_core=4`）、工具沙箱与 Workspace 守卫（`security=4`）三项已落地并被本次评测样本与真实 Adapter 交叉验证；模型与提示工程（`model_prompt=3`）、可靠性与性能（`reliability=3`）、可测试性（`testability=3`）、前端 UX（`frontend_ux=3`）四项处于"结构已有、深度不足"阶段。

**整体风险等级：中**。沙箱与 Workspace 守卫已具备防御深度（见维度 4），业务代码不直接处理凭证；但 **Prompt Injection 分层** 与 **工具滥用检测** 缺位是当前最高风险面，一旦被诱导会绕过 Agent 层面做任意指令；可靠性侧 **无 SLO / 无 retry / 无 Provider 健康探测**，在 Provider 抖动时可观测性不足；可测试性侧 **Evaluation_Script 未进 CI**，回归守卫目前只靠手工触发，是风险"次高优先级"。

**前三位高优先级 Improvement_Recommendation**：

1. **P0-安全 / IR-SEC-01 — Prompt Injection 防御分层（维度 4）**：系统提示拆成不可被用户覆盖的 `system` 段与可由用户影响的 `user` 段；对 `ShellExecTool.command` 与 `HttpRequestTool.url` 做白名单正则 + 黑名单关键词双层校验；引用 OWASP LLM01 / Anthropic "Safety best practices" 作为执行清单。
2. **P0-安全 / IR-SEC-02 — 工具调用滥用检测（维度 4）**：在 `ReActAgentAdapter.run` 的工具执行节点加"同工具高频调用"与"异常参数模式"探测（例如一轮内 ≥ 5 次 ShellExec、Python 代码命中 `BLOCKED_CALLS` 次数），命中即告警并短路；事件落 OpenTelemetry event。引用 OWASP LLM09 / Google ADK "Safety & guardrails"。
3. **P0-可测 / IR-TEST-01 — Evaluation_Script 接入 CI 门禁（维度 5 + 6）**：在 PR 流水线跑 `uv run python -m scripts.evaluation.run_eval --metric=all --baseline=docs/evaluation/results/<pinned>.json`，任一 `ratio` 相对基线回退 ≥ 5pp 直接 fail PR。预期收益：覆盖 ReAct / 委派 / 压缩三条主链路的回归守卫，补齐本次评估中识别出的"无回归门禁"缺口。引用 τ-bench / LangSmith "Evaluation & regression testing"。

## 读者导览

<!-- AUTO-START: report_reader_guide -->
- **技术负责人**：优先阅读 [执行摘要](#执行摘要)、[评分汇总表](#评分汇总表)、[改进清单](#改进清单)。
- **开发工程师**：优先阅读 [改进清单](#改进清单) 与各维度子报告的「改进建议」。
- **QA / 平台工程师**：优先阅读 [评估方法](#评估方法) 与 [附录：交付物清单](#附录交付物清单)，按 `scripts/evaluation/README.md` 入口复跑。
<!-- AUTO-END: report_reader_guide -->

## 评估方法

<!-- AUTO-START: report_framework_table -->
评估使用 7 个维度的 1-5 级 Rubric，评分加权平均；每维度判据显式引用业界公开框架条款。下表列出本次评估引用的全部 Industry_Framework 条款：

| 框架 | 条款 | 来源 |
| --- | --- | --- |
| AgentBench | AgentBench — Evaluating LLMs as Agents (task success metrics) | https://arxiv.org/abs/2308.03688 |
| Anthropic | Building effective agents — Workflow & Agent patterns | https://www.anthropic.com/research/building-effective-agents |
| Anthropic | Long context prompting — Context window management | https://docs.anthropic.com/en/docs/build-with-claude/long-context |
| Anthropic | Prompt caching — Efficient long-context usage | https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching |
| Anthropic | Tool use with Claude — Tool definition best practices | https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview |
| Berkeley FCL | Berkeley Function-Calling Leaderboard — Tool selection accuracy | https://gorilla.cs.berkeley.edu/leaderboard.html |
| Google ADK | Agent Development Kit — Multi-agent architecture patterns | https://google.github.io/adk-docs/ |
| Google ADK | Agent Development Kit — Safety & guardrails | https://google.github.io/adk-docs/safety/ |
| LangChain | LangGraph — Agent patterns & ReAct architecture | https://langchain-ai.github.io/langgraph/tutorials/introduction/ |
| LangChain | LangSmith — Evaluation & regression testing | https://docs.smith.langchain.com/evaluation |
| OpenAI | A Practical Guide to Building Agents — Agent design patterns | https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf |
| OpenAI | OpenAI Platform — Function calling best practices | https://platform.openai.com/docs/guides/function-calling |
| τ-bench | τ-bench — Tool-use reliability & task completion metrics | https://arxiv.org/abs/2406.12045 |
<!-- AUTO-END: report_framework_table -->

## 评分汇总表

<!-- AUTO-START: report_score_table -->
| 维度 | 中文标题 | 评分 | 权重 | 加权得分 |
| --- | --- | --- | --- | --- |
| `architecture` | 架构与工程化 | 4 | 0.18 | 0.720 |
| `agent_core` | Agent 核心能力 | 4 | 0.22 | 0.880 |
| `model_prompt` | 模型与提示工程 | 3 | 0.14 | 0.420 |
| `security` | 安全与合规 | 4 | 0.16 | 0.640 |
| `reliability` | 可靠性与性能 | 3 | 0.12 | 0.360 |
| `testability` | 可测试性与质量 | 3 | 0.10 | 0.300 |
| `frontend_ux` | 前端/UX | 3 | 0.08 | 0.240 |

加权总分：**3.560 / 5**。
<!-- AUTO-END: report_score_table -->

### 自动化指标

<!-- AUTO-START: report_metric_table -->
| 指标 | 分子 | 分母 | 比率 | 样本数 | 失败 | 错误 |
| --- | --- | --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 | 2 | 0 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 | 0 | 0 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 | 0 | 0 |
<!-- AUTO-END: report_metric_table -->

## 维度链接

<!-- AUTO-START: report_dimension_links -->
- [架构与工程化](dimensions/1-architecture.md)（评分 4/5）
- [Agent 核心能力](dimensions/2-agent-core.md)（评分 4/5）
- [模型与提示工程](dimensions/3-model-prompt.md)（评分 3/5）
- [安全与合规](dimensions/4-security.md)（评分 4/5）
- [可靠性与性能](dimensions/5-reliability.md)（评分 3/5）
- [可测试性与质量](dimensions/6-testability.md)（评分 3/5）
- [前端/UX](dimensions/7-frontend-ux.md)（评分 3/5）
<!-- AUTO-END: report_dimension_links -->

## 改进清单

> 合并七维度子报告中的 Improvement_Recommendation，按 P0 / P1 / P2 排序；每条标注编号、标题、涉及维度、涉及文件、预期收益、实施难度（S/M/L）、关联业界框架条款。详细展开见各子报告的「改进建议」章节。

### P0（高优先级，合计 4 条）

| 编号 | 标题 | 维度 | 涉及文件 | 预期收益 | 难度 | 框架引用 |
| --- | --- | --- | --- | --- | --- | --- |
| IR-SEC-01 | Prompt Injection 防御分层 | 4 安全 | `infrastructure/agent/react_agent_adapter.py`、`infrastructure/tools/shell_exec/`、`infrastructure/tools/http_request/` | 阻断"用户输入覆盖系统指令"类攻击，遵循 OWASP LLM01 | M | OWASP LLM01 / Anthropic Safety best practices |
| IR-SEC-02 | 工具调用滥用检测 | 4 安全 | `infrastructure/agent/react_agent_adapter.py` + `infrastructure/telemetry/` | 识别高频滥用并短路，补齐 OWASP LLM09 指出的"Overreliance" 风险面 | M | OWASP LLM09 / Google ADK Safety & guardrails |
| IR-AGENT-01 | 工具调用与委派的 OpenTelemetry span | 2 Agent / 5 可靠性 | `infrastructure/agent/react_agent_adapter.py`、`infrastructure/agent/delegate_to_agent_tool.py` | 获取逐次工具调用的归因数据，使 Tool_Call_Success_Rate / Delegation_Correctness 具备 SRE 价值 | M | Anthropic Observability best practices / Google SRE |
| IR-PROMPT-01 | Prompt 评估集与 A/B 对照 | 3 模型与提示 | `docs/evaluation/`、`tests/evaluation/`、`epsilon-boot/prompts/` | 将 Prompt 版本变化与任务成功率、工具调用成功率、人工反馈关联 | M | OpenAI Function calling best practices / Anthropic Prompt Caching |

### P1（中优先级，合计 10 条）

| 编号 | 标题 | 维度 | 涉及文件 | 预期收益 | 难度 | 框架引用 |
| --- | --- | --- | --- | --- | --- | --- |
| IR-ARCH-01 | 引入机器可读的架构守卫 | 1 架构 | 新增 `tools/import_guard.py` 或接入 `import-linter` | 固化 `domain → application → infrastructure` 导入方向，杜绝偶发绕过 | S | OpenAI Agent design patterns / Google ADK |
| IR-AGENT-02 | Evaluation_Script 接入 CI（等价 IR-TEST-01） | 2 Agent / 5 可靠 / 6 可测 | CI yaml + `scripts/evaluation/run_eval.py` | 覆盖三条主链路的回归守卫，回退 ≥ 5pp 直接 fail PR | S | τ-bench / LangSmith Evaluation & regression testing |
| IR-PROMPT-02 | 引入 Prompt Caching 结构化块 | 3 模型与提示 | `infrastructure/model_access/*` 兼容端点适配 | 降低长上下文成本、可观测 cache 命中率 | M | Anthropic Prompt caching |
| IR-PROMPT-03 | 补齐"按任务类型 / 成本"路由 | 3 模型与提示 | `infrastructure/model_access/router_config.py` | 升级多维路由，避免单一模型名映射 | M | OpenAI A Practical Guide to Building Agents |
| IR-SEC-03 | 凭证轮转手册与启动期校验 | 4 安全 | `docs/steering/config-source.md` + `common/configuration/` | 消除 `config.properties` 明文 API Key 风险，建立轮转闭环 | S | Anthropic Safety best practices |
| IR-REL-01 | 定义核心 SLO 并落 Prometheus recording rule | 5 可靠性 | `infrastructure/telemetry/*` + 监控平台 | chat 成功率 / p95 首 token 延迟 / token 成本 per session 可被告警 | L | Google SRE SLO book / Anthropic Observability |
| IR-REL-02 | Provider 健康探测 + 退避重试 | 5 可靠性 | `infrastructure/model_access/provider_registry.py`、`openai_compatible_adapter.py` | 连续失败后 TTL 期内跳过，避免"半移除"打空 Provider | M | Google SRE Handling Overload |
| IR-TEST-02 | 拆分"Tool selection / Parameter accuracy / Execution success" | 6 可测 | `tests/evaluation/metrics/` | 三层独立指标，对齐 Berkeley Function-Calling Leaderboard | M | Berkeley FCL |
| IR-UX-01 | 聊天侧暴露 trace 视图 | 7 前端 UX | `epsilon-client/src/components/chat/`、`epsilon-client/src/hooks/use-chat.ts` | trace 可见性从仅任务侧扩展到聊天侧 | M | Anthropic Show reasoning when helpful |
| IR-UX-02 | 用户反馈通道（Copy / Retry / 点赞点踩） | 7 前端 UX | `epsilon-client/src/components/chat/`，新增 `/api/feedback` | 形成人类反馈闭环，反馈满意率可作可选评测指标 | M | Anthropic Human feedback loops / NN/g |

### P2（低优先级，合计 7 条）

| 编号 | 标题 | 维度 | 涉及文件 | 预期收益 | 难度 | 框架引用 |
| --- | --- | --- | --- | --- | --- | --- |
| IR-ARCH-02 | Adapter 可替换策略落配置 | 1 架构 | `application/container_config.py` + `config.properties` | 替换 Plan-Execute / Reflect 等新 Adapter 免改组合根代码 | M | Anthropic Building effective agents |
| IR-ARCH-03 | Agent 注册表配置化 | 1 架构 | `infrastructure/agent/agent_registry_adapter.py` + 独立 TOML | 形成"配置驱动的多 Agent DAG" | S | Google ADK Multi-agent architecture patterns |
| IR-AGENT-03 | 引入组合式 Workflow | 2 Agent 核心 | 新增 `infrastructure/agent/workflow_agent_adapter.py` | 为可预测任务提供非 ReAct 通道 | L | Anthropic Building effective agents |
| IR-SEC-04 | 启动时打印 WORKSPACE_ROOT / FOLLOW_SYMLINKS | 4 安全 | `application/container_config.py` | 让运维直观确认守卫策略，避免"默认严格 = 实际宽松" | S | OWASP LLM05 |
| IR-REL-03 | 工具调用 wall-clock 预算 | 5 可靠性 | `domain/agent/value_objects.py`、`infrastructure/agent/react_agent_adapter.py` | 补齐 OpenAI "hard stop" 建议 | M | OpenAI Reliability & cost control |
| IR-TEST-03 | LLM-as-judge 可选评测通道 | 6 可测 | `tests/evaluation/judges/`（新增） | 引入 Prompt 质量与中文流畅度定性评分 | L | τ-bench judge with N-vote |
| IR-UX-03 | 可访问性 a11y 基线 + 错误分级 | 7 前端 UX | `epsilon-client/src/components/chat/` | `aria-live` / axe-core CI lint / 错误态分级 | M | W3C WAI-ARIA / NN/g |

### 合计计数

| 优先级 | 条数 |
| --- | --- |
| P0 | 4 |
| P1 | 10 |
| P2 | 7 |
| **合计** | **21** |

> 说明：子报告中同一问题出现在多个维度时已合并编号（如 IR-AGENT-02 = IR-TEST-01）；子报告"改进建议"章节是各条的完整展开，本清单仅做索引化汇总。

<!-- AUTO-START: report_improvement_links -->
- 查看 [dimensions/1-architecture.md](dimensions/1-architecture.md) 的「改进建议」章节。
- 查看 [dimensions/2-agent-core.md](dimensions/2-agent-core.md) 的「改进建议」章节。
- 查看 [dimensions/3-model-prompt.md](dimensions/3-model-prompt.md) 的「改进建议」章节。
- 查看 [dimensions/4-security.md](dimensions/4-security.md) 的「改进建议」章节。
- 查看 [dimensions/5-reliability.md](dimensions/5-reliability.md) 的「改进建议」章节。
- 查看 [dimensions/6-testability.md](dimensions/6-testability.md) 的「改进建议」章节。
- 查看 [dimensions/7-frontend-ux.md](dimensions/7-frontend-ux.md) 的「改进建议」章节。
<!-- AUTO-END: report_improvement_links -->

## 附录：交付物清单

<!-- AUTO-START: report_appendix -->
- `docs/evaluation/report.md` — 主报告
- `docs/evaluation/scores.toml` — 人工评分源
- `docs/evaluation/scores.json` — 机器可读聚合结果
- `docs/evaluation/results/*.json` — 评测运行结果
- `docs/evaluation/dimensions/1-architecture.md` — 架构与工程化子报告
- `docs/evaluation/dimensions/2-agent-core.md` — Agent 核心能力子报告
- `docs/evaluation/dimensions/3-model-prompt.md` — 模型与提示工程子报告
- `docs/evaluation/dimensions/4-security.md` — 安全与合规子报告
- `docs/evaluation/dimensions/5-reliability.md` — 可靠性与性能子报告
- `docs/evaluation/dimensions/6-testability.md` — 可测试性与质量子报告
- `docs/evaluation/dimensions/7-frontend-ux.md` — 前端/UX子报告
- `scripts/evaluation/` — 四个入口脚本（run_eval / compare_baseline / aggregate_scores / verify_evidence）
<!-- AUTO-END: report_appendix -->

### 附录：交付物清单（人工补校对）

> 本节为人工维护的完整交付物清单（上面 `AUTO-START: report_appendix` 区块由脚本粗略列出主要路径，本节做精细校对与分组）。所有命令从 `epsilon-boot/` 目录执行；禁止使用 `pip` / `poetry` / `pipenv` / `conda`。

#### A. 主报告与子报告（维度 Evaluation_Dimension）

| 路径 | 对应维度 | 复跑命令示例 |
| --- | --- | --- |
| `docs/evaluation/report.md` | 总体（7 维加权） | `uv run python -m scripts.evaluation.aggregate_scores --result=docs/evaluation/results/<latest>.json` |
| `docs/evaluation/scores.toml` | 7 维人工评分源 | —（人工维护） |
| `docs/evaluation/scores.json` | 7 维聚合评分（机器可读） | 由 `aggregate_scores` 写入 |
| `docs/evaluation/dimensions/1-architecture.md` | 1 架构与工程化 | 同 `aggregate_scores` |
| `docs/evaluation/dimensions/2-agent-core.md` | 2 Agent 核心能力 | 同 `aggregate_scores` |
| `docs/evaluation/dimensions/3-model-prompt.md` | 3 模型与提示工程 | 同 `aggregate_scores` |
| `docs/evaluation/dimensions/4-security.md` | 4 安全与合规 | 同 `aggregate_scores` |
| `docs/evaluation/dimensions/5-reliability.md` | 5 可靠性与性能 | 同 `aggregate_scores` |
| `docs/evaluation/dimensions/6-testability.md` | 6 可测试性与质量 | 同 `aggregate_scores` |
| `docs/evaluation/dimensions/7-frontend-ux.md` | 7 前端 / UX | 同 `aggregate_scores` |
| `docs/evaluation/results/*.json` | 所有维度（评测原始 JSON） | `uv run python -m scripts.evaluation.run_eval --metric=all` |
| `docs/evaluation/results/dry-run-<YYYY-MM-DD_HHMMSS>.log` | 交付回归演练证据（任务 6.4） | 见任务 6.4 的端到端演练流程 |

#### B. Rubric 与证据目录（tests/evaluation/*）

| 路径 | 对应维度 | 复跑命令示例 |
| --- | --- | --- |
| `tests/evaluation/rubric/dimensions.py` | 7 维 Rubric + 权重 + 业界框架条款 | `uv run pytest tests/evaluation/self_tests/test_rubric_consistency.py -q` |
| `tests/evaluation/evidence/catalog.py` | 7 维证据清单（33 条） | `uv run python -m scripts.evaluation.verify_evidence` |
| `tests/evaluation/evidence/models.py` | 证据引用数据结构 | 同上 |
| `tests/evaluation/evidence/verifier.py` | 证据校验器 | 同上 |

#### C. 三项核心自动化指标脚本（tests/evaluation/metrics/*）

| 路径 | 对应指标 | 复跑命令示例 |
| --- | --- | --- |
| `tests/evaluation/metrics/test_tool_call_success_rate.py` | Tool_Call_Success_Rate（20 条样本） | `uv run pytest tests/evaluation/metrics/test_tool_call_success_rate.py -q` |
| `tests/evaluation/metrics/test_delegation_correctness.py` | Delegation_Correctness（15 条样本） | `uv run pytest tests/evaluation/metrics/test_delegation_correctness.py -q` |
| `tests/evaluation/metrics/test_context_compaction_effectiveness.py` | Context_Compaction_Effectiveness（36 条样本） | `uv run pytest tests/evaluation/metrics/test_context_compaction_effectiveness.py -q` |
| `tests/evaluation/metrics/test_meta_tool_call_success_rate.py` | 指标 1 元测试 | `uv run pytest tests/evaluation/metrics/test_meta_tool_call_success_rate.py -q` |
| `tests/evaluation/metrics/test_meta_delegation_correctness.py` | 指标 2 元测试 | `uv run pytest tests/evaluation/metrics/test_meta_delegation_correctness.py -q` |
| `tests/evaluation/metrics/test_meta_context_compaction_effectiveness.py` | 指标 3 元测试 | `uv run pytest tests/evaluation/metrics/test_meta_context_compaction_effectiveness.py -q` |
| `tests/evaluation/metrics/_fakes.py` | 指标通用 fixture / fake 工具 | （被上述脚本复用） |

#### D. 四个评测脚本入口（scripts/evaluation/*）

| 路径 | 职责 | 复跑命令示例 | 退出码语义 |
| --- | --- | --- | --- |
| `scripts/evaluation/run_eval.py` | 评测主入口 | `uv run python -m scripts.evaluation.run_eval --metric=all` | 0 / 1 / 2 |
| `scripts/evaluation/aggregate_scores.py` | 聚合评分 + 生成主报告 / 子报告 AUTO 区块 | `uv run python -m scripts.evaluation.aggregate_scores --result=<latest>` | 0 / 1 |
| `scripts/evaluation/compare_baseline.py` | 回归对比 | `uv run python -m scripts.evaluation.compare_baseline --baseline=<old> --latest=<new>` | 0 / 1 / 2 |
| `scripts/evaluation/verify_evidence.py` | 证据存在性校验 | `uv run python -m scripts.evaluation.verify_evidence` | 0 / 1 |
| `scripts/evaluation/README.md` | 面向 QA / 平台工程师的操作手册 | （只读） |

#### E. self_tests（tests/evaluation/self_tests/*）

| 路径 | 职责 | 复跑命令示例 |
| --- | --- | --- |
| `tests/evaluation/self_tests/test_rubric_consistency.py` | Rubric 权重归一与框架条款数校验 | `uv run pytest tests/evaluation/self_tests/test_rubric_consistency.py -q` |
| `tests/evaluation/self_tests/test_evidence_parse.py` | 证据引用格式解析（Property 4） | `uv run pytest tests/evaluation/self_tests/test_evidence_parse.py -q` |
| `tests/evaluation/self_tests/test_evidence_verify.py` | 证据路径 / 行号校验 | `uv run pytest tests/evaluation/self_tests/test_evidence_verify.py -q` |
| `tests/evaluation/self_tests/test_scripted_model_access.py` | 桩 ModelAccess 行为 | `uv run pytest tests/evaluation/self_tests/test_scripted_model_access.py -q` |
| `tests/evaluation/self_tests/test_runner_aggregation.py` | Runner 聚合与失败分类（Property 3） | `uv run pytest tests/evaluation/self_tests/test_runner_aggregation.py -q` |
| `tests/evaluation/self_tests/test_no_external_calls.py` | 零外部网络调用守卫 | `uv run pytest tests/evaluation/self_tests/test_no_external_calls.py -q` |
| `tests/evaluation/self_tests/test_compare_baseline.py` | 回归阈值语义（Property 7） | `uv run pytest tests/evaluation/self_tests/test_compare_baseline.py -q` |
| `tests/evaluation/self_tests/test_delivery_path_guard.py` | 交付路径守卫（Property 1） | `uv run pytest tests/evaluation/self_tests/test_delivery_path_guard.py -q` |
| `tests/evaluation/self_tests/test_end_to_end.py` | 端到端回归集成测试（Property 3 / 7） | `uv run pytest tests/evaluation/self_tests/test_end_to_end.py -q` |

#### F. 运行期支撑物（tests/evaluation 其它目录）

| 路径 | 职责 |
| --- | --- |
| `tests/evaluation/conftest.py` | 注册 `evaluation` / `evaluation_self` mark + `sample_sink` fixture |
| `tests/evaluation/errors.py` | 评测异常族（`EvaluationError` 及子类） |
| `tests/evaluation/config/eval.toml` | 评测参数（sample_count、阈值、window_n） |
| `tests/evaluation/runner/models.py` | `EvalCase` / `EvalSampleResult` / `EvalResult` 等数据模型 |
| `tests/evaluation/runner/runner.py` | `EvalRunner` 主体 |
| `tests/evaluation/runner/sample_sink.py` | 进程级样本收集器 |
| `tests/evaluation/stubs/model_access.py` | 桩 `ScriptedModelAccess` |
| `tests/evaluation/stubs/agent_registry.py` | 桩 `StaticAgentRegistry` |
| `tests/evaluation/stubs/session_context_store.py` | 桩 `InMemorySessionContextStore` |
| `tests/evaluation/frontend/ux_probe.md` | 前端 / UX 维度人工巡检清单（对应维度 7） |


<!-- AUTO-START: report_scores_table -->
| 维度 | 评分 | 权重 | 加权贡献 |
| --- | --- | --- | --- |
| [架构与工程化](dimensions/1-architecture.md) | 4/5 | 0.18 | 0.720 |
| [Agent 核心能力](dimensions/2-agent-core.md) | 4/5 | 0.22 | 0.880 |
| [模型与提示工程](dimensions/3-model-prompt.md) | 3/5 | 0.14 | 0.420 |
| [安全与合规](dimensions/4-security.md) | 4/5 | 0.16 | 0.640 |
| [可靠性与性能](dimensions/5-reliability.md) | 3/5 | 0.12 | 0.360 |
| [可测试性与质量](dimensions/6-testability.md) | 3/5 | 0.10 | 0.300 |
| [前端/UX](dimensions/7-frontend-ux.md) | 3/5 | 0.08 | 0.240 |
| **加权总分** | **3.560/5** | 1.00 | — |
<!-- AUTO-END: report_scores_table -->

<!-- AUTO-START: report_metrics_table -->
| 指标 | 比率 | 样本数 | 失败 | 错误 |
| --- | --- | --- | --- | --- |
| tool_call_success_rate | 0.3000 | 20 | 2 | 0 |
| delegation_correctness | 0.4000 | 15 | 0 | 0 |
| context_compaction_effectiveness | 1.0000 | 36 | 0 | 0 |
<!-- AUTO-END: report_metrics_table -->
