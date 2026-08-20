# 实现计划：AI Agent 工作台系统性评估（spec-ai-evaluation）

## 概述

本计划将 `design.md` 落地为可独立交付的原子任务，覆盖评估基础设施、桩依赖、三项核心自动化指标、七个维度的评估子报告、主报告聚合与回归对比脚本。所有任务严格遵守以下硬约束：

- 产物仅落在 `docs/evaluation/`、`tests/evaluation/`、`scripts/evaluation/` 三个新目录，**不得修改任何 `epsilon-boot/src/`、`epsilon-client/` 业务代码**。
- 后端依赖管理一律使用 `uv`；本期不引入 `jsonschema` / `rich` 新依赖，全部用标准库实现。
- 所有 Python 模块、类、公开函数/方法提供中文 docstring，符合 `docs/steering/code-documentation.md`。
- 桩 `ModelAccessPort` 结构类型匹配真实签名 `chat(ChatRequest) -> LLMResponse` 与 `stream(ChatRequest) -> AsyncIterator[StreamingChunk]`；`stream` 抛 `NotImplementedError`（评测仅走非流式路径）。
- 评分唯一来源为 `docs/evaluation/scores.toml`；`scripts/evaluation/aggregate_scores.py` 聚合出 `scores.json` 并渲染主报告 `report.md`。
- 回归阈值语义为 **百分点差**（pp），默认 5.0，`scripts/evaluation/compare_baseline.py` 通过 `--threshold` 覆盖。
- 证据引用格式 `path:Lstart-Lend`，由 `scripts/evaluation/verify_evidence.py` 校验（路径存在 + 行号合法 + 摘录匹配）。
- 三项核心指标一律走桩 Port + 确定性判定，不调用真实 LLM。
- 回归对比退出码：`0` 成功 / `1` 脚本异常 / `2` 指标回退超阈值。

任务按"基础设施 → 桩与 fixture → 核心指标脚本 → 七维度子报告 → 主报告与回归联调 → 交付收尾"六个阶段推进，每个阶段末设 checkpoint 任务做综合验证。

## Tasks

- [x] 1. 基础设施与目录骨架
  - [x] 1.1 创建三份新增目录骨架与占位文件
    - 创建 `docs/evaluation/.gitkeep`、`docs/evaluation/dimensions/.gitkeep`、`docs/evaluation/results/.gitkeep`
    - 创建 `tests/evaluation/__init__.py`（空模块，含模块级中文 docstring 说明评测代码视图）
    - 创建 `tests/evaluation/reports/.gitkeep`
    - 创建 `scripts/evaluation/__init__.py`（空模块，含模块级中文 docstring）
    - 创建 `scripts/evaluation/README.md` 占位（后续任务 6.2 补全内容）
    - _需求: 7.1、14.1、14.4_
  - [x] 1.2 编写评测参数配置 `tests/evaluation/config/eval.toml`
    - 创建 `tests/evaluation/config/__init__.py` 与 `tests/evaluation/config/eval.toml`
    - 写入 `[runner]`、`[regression]`、`[metrics.*]` 三组参数，与 design.md "评测脚本自身的配置" 章节完全一致
    - 关键键：`output_dir`、`report_dir`、`default_window_n`、`threshold_percent_points=5.0`、三项指标的 `sample_count`
    - _需求: 6.1、6.3、10.4_
  - [x] 1.3 定义评测异常族 `tests/evaluation/errors.py`
    - 新增 `EvaluationError` 基类及以下子类：`EvidenceFormatError`、`EvidenceNotFoundError`、`RubricConsistencyError`、`SampleExecutionError`、`RegressionThresholdViolation`
    - 每个类提供中文 docstring，说明触发场景与建议退出码
    - 错误消息为中文，参照 design.md "错误处理" 章节
    - _需求: 5.5、6.4、12.4_
  - [x] 1.4 定义 Rubric 数据结构 `tests/evaluation/rubric/dimensions.py`
    - 创建 `tests/evaluation/rubric/__init__.py`
    - 实现 `DimensionId`（Enum，7 个成员）、`FrameworkCitation`、`RubricLevel`、`DimensionRubric` 四个 `@dataclass(frozen=True)`
    - 实现 `load_rubric() -> tuple[DimensionRubric, ...]`，内置 7 个维度 × 5 级判据 × 每级 ≥ 2 条 `FrameworkCitation`
    - 权重固定：architecture=0.18、agent_core=0.22、model_prompt=0.14、security=0.16、reliability=0.12、testability=0.10、frontend_ux=0.08（Σ=1.0）
    - 框架来源覆盖 OpenAI / Anthropic / LangChain / Google ADK / AgentBench / τ-bench / Berkeley FCL（每维度至少 2 个不同 framework）
    - _需求: 2.1、2.2、4.1、4.2、13.2_
  - [x] 1.5 实现证据模型与解析 `tests/evaluation/evidence/models.py`
    - 创建 `tests/evaluation/evidence/__init__.py`
    - 实现 `EvidenceKind`（Enum：`CODE_LINES` / `CONFIG_KEY` / `PATH_ONLY`）
    - 实现 `EvidenceReference` `@dataclass(frozen=True)`，字段对齐 design.md 组件 2
    - 实现 `parse_reference(raw: str, description: str) -> EvidenceReference`，正则 `^[^\s:]+(:L?\d+(-L?\d+)?)?$`；非法格式抛 `EvidenceFormatError`
    - 支持三种形式：`path:Lstart-Lend`、`path:Lstart`、`path`、`config.properties:<key>`（识别 key 前缀）
    - _需求: 3.2、3.3、3.4_
  - [x] 1.6 实现证据校验器 `tests/evaluation/evidence/verifier.py`
    - 实现 `EvidenceCheck` `@dataclass(frozen=True)` 与 `verify_evidence(references, repo_root, expected_excerpts=None)`
    - 校验：路径存在 → 行号范围 ≤ 文件总行数 → 若提供 `expected_excerpts[raw]` 则按行号读取原文并比对
    - 任一项失败时 `EvidenceCheck.error` 填中文描述；不抛异常，批量返回结果
    - _需求: 3.4_
  - [x] 1.7 建立证据目录 `tests/evaluation/evidence/catalog.py`（骨架）
    - 以 `DimensionId → list[EvidenceReference]` 形式登记每维度 ≥ 3 条占位证据
    - 本任务只登记键名与空列表；具体 Evidence 条目由任务 4.1~4.7 逐维度回填
    - 提供 `load_catalog() -> dict[str, list[EvidenceReference]]` 公开函数
    - _需求: 3.1、3.2_
  - [x]* 1.8 自测：`tests/evaluation/self_tests/test_rubric_consistency.py`
    - 断言 `load_rubric()` 返回 7 个维度、权重之和 = 1.0（误差 ≤ 1e-9）、每维度 5 级齐全、每维度 `citations` 跨级去重后 ≥ 2 个不同 framework
    - 不标 `@pytest.mark.evaluation`，通过 `uv run pytest tests/evaluation/self_tests` 运行
    - _对应 Property 5、Property 6；需求 2.2、4.1_
  - [x]* 1.9 自测：`tests/evaluation/self_tests/test_evidence_parse.py`
    - 覆盖 `parse_reference()` 合法 4 种形式 + 非法（空格、通配符、仅目录、负行号）5 种
    - 断言非法输入抛 `EvidenceFormatError`，合法输入 `kind` 正确分发
    - _对应 Property 4；需求 3.2_
  - [x]* 1.10 自测：`tests/evaluation/self_tests/test_evidence_verify.py`
    - 在 `tmp_path` 创建固定文件，构造"行号越界/路径不存在/摘录不匹配"三类输入
    - 断言 `verify_evidence()` 返回相应 `EvidenceCheck.error`，不抛异常
    - _需求: 3.4_
  - [x] 1.11 Checkpoint：基础设施自验证
    - 从 `epsilon-boot/` 目录执行 `uv run pytest tests/evaluation/self_tests -q`，断言全部通过
    - 执行 `uv run python -c "from tests.evaluation.rubric.dimensions import load_rubric; load_rubric()"`，无 `RubricConsistencyError`
    - 检查 `git diff --name-only` 输出全部以 `docs/evaluation/` / `tests/evaluation/` / `scripts/evaluation/` 之一开头
    - _对应 Property 1、Property 5、Property 6；需求 7.2、12.2_

- [x] 2. 桩 Port、Runner 与 pytest fixture
  - [x] 2.1 实现桩 `ScriptedModelAccess` `tests/evaluation/stubs/model_access.py`
    - 创建 `tests/evaluation/stubs/__init__.py`
    - 按结构类型匹配 `epsilon-boot/src/domain/model_access/ports.py` 的 `ModelAccessPort`，真实签名为 `async def chat(ChatRequest) -> LLMResponse` 与 `def stream(ChatRequest) -> AsyncIterator[StreamingChunk]`
    - `chat(request)` 按顺序弹出 `scripted_responses`，耗尽后返回空 `LLMResponse(model="scripted-exhausted")`
    - `stream(request)` 抛 `NotImplementedError("评测阶段仅走非流式路径")`
    - 不 import `infrastructure/` 模块；仅 import `domain/model_access/value_objects` 中的 `ChatRequest` / `LLMResponse` / `StreamingChunk`（允许的例外，见 ddd-architecture.md）
    - _需求: 5.3、12.1_
  - [x] 2.2 实现桩 `StaticAgentRegistry` `tests/evaluation/stubs/agent_registry.py`
    - 结构类型匹配 `domain/agent/ports.py` 中的 `AgentRegistryPort`
    - `get(name)` 名称不存在时抛 `AgentNotFoundError`（复用 `domain/agent/exceptions.py`）
    - `list_names() -> tuple[str, ...]`
    - _需求: 5.3、12.1_
  - [x] 2.3 实现桩 `InMemorySessionContextStore` `tests/evaluation/stubs/session_context_store.py`
    - 结构类型匹配 `domain/chat/ports.py` 中 `SessionContextStorePort`
    - 内部用 `dict[str, ConversationContext]` 作为会话持久化
    - 仅为委派指标服务，实现 `load` / `save` / `delete` 最小方法集
    - _需求: 5.3、12.1_
  - [x] 2.4 实现评测 Runner 数据模型 `tests/evaluation/runner/models.py`
    - 创建 `tests/evaluation/runner/__init__.py`
    - 实现 `MetricId`、`SampleOutcome`、`EvalCase`、`EvalSampleResult`、`DimensionMetric`、`DimensionScore`、`EvalResult` 七个 `@dataclass(frozen=True)` / `Enum`
    - 签名严格对齐 design.md 组件 3
    - 实现 `EvalResult.to_dict()` 便于 JSON 序列化（datetime → ISO8601）
    - _需求: 5.2、5.4_
  - [x] 2.5 实现 Runner 主体 `tests/evaluation/runner/runner.py`
    - 定义 `RunnerConfig` 与 `EvalRunner` 类（签名对齐 design.md 组件 3）
    - `run()` 内部以编程方式调用 `pytest.main(["-q", "tests/evaluation/metrics", "-m", "evaluation", ...])`，通过 `sample_sink` 收集样本
    - `aggregate(samples)` 按 `MetricId` 分组求 `numerator_sum` / `denominator_sum` / `ratio` / `failed_samples` / `error_samples`
    - `write_json(result)` 落盘到 `config.output_dir`，文件名 `<YYYY-MM-DD_HHMMSS>_<git_short>.json`；目录缺失时 `mkdir(parents=True, exist_ok=True)`
    - 捕获样本异常包装为 `SampleExecutionError`，写入 `EvalSampleResult(outcome=ERROR)`
    - _需求: 5.4、5.5、14.4_
  - [x] 2.6 实现 `sample_sink` fixture 与 pytest 标记 `tests/evaluation/conftest.py`、`tests/evaluation/runner/sample_sink.py`
    - `sample_sink.py` 暴露进程级 `SampleSink` 类（列表包装，含 `append` / `drain`）
    - `conftest.py` 注册 `evaluation` 标记（`pytest.ini_options` 也可）、提供 `sample_sink` fixture（scope="session"）
    - 自 `pytest_configure` 中读取 `tests/evaluation/config/eval.toml` 并注入到 fixture
    - _需求: 5.4、6.1_
  - [x]* 2.7 自测：`tests/evaluation/self_tests/test_scripted_model_access.py`
    - 覆盖：耗尽后返回空 `LLMResponse(model="scripted-exhausted")`、`stream` 抛 `NotImplementedError`、按序返回脚本
    - _对应设计决策可用性_
  - [x]* 2.8 自测：`tests/evaluation/self_tests/test_runner_aggregation.py`
    - 构造 PASS/FAIL/ERROR 混合样本列表，断言 `aggregate()` 分类计数与 `ratio` 精度
    - 覆盖分母为 0 时 `ratio = 0.0` 防御分支
    - _对应 Property 3；需求 5.5_
  - [x]* 2.9 自测：`tests/evaluation/self_tests/test_no_external_calls.py`
    - `monkeypatch` 拦截 `httpx.AsyncClient.request` 与 `openai.*` 调用点
    - 跑一次空样本 `EvalRunner.run()`，断言无任何外部请求被发起
    - _需求: 5.3、12.3_
  - [x] 2.10 Checkpoint：桩与 Runner 自验证
    - 从 `epsilon-boot/` 执行 `uv run pytest tests/evaluation/self_tests -q`
    - 执行 `uv run python -c "from tests.evaluation.runner.runner import EvalRunner, RunnerConfig; print('ok')"`
    - 确认无新增业务代码改动：`git diff --name-only epsilon-boot/src epsilon-client` 输出为空
    - _对应 Property 1、Property 2；需求 7.2、7.4_

- [x] 3. 三项核心自动化指标脚本
  - [x] 3.1 指标 1 评测用例 `tests/evaluation/metrics/test_tool_call_success_rate.py`
    - 创建 `tests/evaluation/metrics/__init__.py`
    - 定义 `TOOL_CALL_CASES: list[EvalCase]`，至少 20 条样本（对齐 `eval.toml.metrics.tool_call_success_rate.sample_count`）
    - 覆盖：全部成功、权限拒绝（走 `ScopedToolRegistry.create_scoped_view`）、未知工具（`ToolNotFoundError`）、执行异常（`ToolExecutionError`）、返回空字符串
    - 构造真实 `ReActAgentAdapter(model_access=ScriptedModelAccess, tool_registry=ToolRegistry)` + 内置 `FakeEchoTool`
    - 使用 `@pytest.mark.evaluation` + `@pytest.mark.parametrize`
    - 样本每次调用 `sample_sink.append(EvalSampleResult(...))`
    - 分子 = 未抛 `ToolExecutionError` / `ToolPermissionDeniedError` / `ToolNotFoundError` 且返回长度 > 0 的调用次数；分母 = 本次 run 中观测到的 `tool_calls` 总数
    - _需求: 5.1、5.2、8.1、8.2_
  - [x] 3.2 指标 1 元测试 `tests/evaluation/metrics/test_meta_tool_call_success_rate.py`
    - 使用 `@pytest.mark.evaluation_self` 标记，便于 `run_eval.py` 过滤
    - 构造"3 成功 / 1 失败 / 1 权限拒绝"定长样本，断言 `numerator_sum=3`、`denominator_sum=5`、`ratio≈0.6`
    - _对应设计 "测试策略 — 元测试"；需求 10.2_
  - [x] 3.3 指标 2 评测用例 `tests/evaluation/metrics/test_delegation_correctness.py`
    - 定义 ≥ 12 条样本（覆盖"目标正确"、"深度越限"、"目标不存在"、"循环依赖"、"返回正确拼回"五类）
    - 使用真实 `DelegationAdapter` + 真实 `TaskAgentAdapter` + 桩 `ScriptedModelAccess` + 桩 `StaticAgentRegistry`
    - 父 Agent 脚本：第 1 轮返回 `delegate_to_agent(target=<name>)` tool_call，第 2 轮返回 finish；子 Agent 一轮内返回可识别 answer
    - 成功判据三项必须全通过：(a) 实际目标 = expected；(b) `child_depth = parent_depth + 1 ≤ AGENT_MAX_DELEGATION_DEPTH`（通过 `config_proxy` 从 `config.properties` 读取）；(c) 子任务 `TaskResult.content` 作为 `ToolMessage` 写回父上下文
    - _需求: 5.1、8.3_
  - [x] 3.4 指标 2 元测试 `tests/evaluation/metrics/test_meta_delegation_correctness.py`
    - 使用 `@pytest.mark.evaluation_self` 标记
    - 三种样本：目标正确但深度越限（期望 FAIL）、目标不存在（期望 ERROR 记失败）、正常委派（期望 PASS）
    - 断言 `DimensionMetric.failed_samples` 与 `error_samples` 分类正确
    - _对应 Property 9；需求 10.2_
  - [x] 3.5 指标 3 评测用例 `tests/evaluation/metrics/test_context_compaction_effectiveness.py`
    - 定义 ≥ 30 条样本，参数化 `(L, S, N)` 组合（如 `L∈{10,20,50,100}`、`S∈{0,1,3}`、`N∈{5,10,20}`）
    - 构造 `Message` 序列后直接调用 `SlidingWindowCompactionAdapter(window_n=N).compact(messages)`
    - 成功判据：(a) 压缩后 SystemMessage 数 = S；(b) 非 system 消息数 = `min(L - S, N)`；(c) 保留的非 system 消息是原序列的末尾 N 条（按原始顺序）
    - 样本通过 → `numerator = 1, denominator = 1`；任一判据失败 → `numerator = 0`
    - _需求: 5.1、8.4_
  - [x] 3.6 指标 3 元测试 `tests/evaluation/metrics/test_meta_context_compaction_effectiveness.py`
    - 使用 `@pytest.mark.evaluation_self` 标记
    - 固定 `L=30, S=3, N=10`，断言压缩后长度 = `3 + 10`，SystemMessage 计数 = 3，顺序与原始一致
    - _对应 Property 8；需求 8.4_
  - [x] 3.7 Checkpoint：三项指标本地联调
    - 从 `epsilon-boot/` 执行 `uv run pytest tests/evaluation/metrics -m evaluation -q`，断言评测样本全部收集
    - 执行 `uv run pytest tests/evaluation/metrics -m evaluation_self -q`，断言元测试全通过
    - 确认 `sample_sink` 三项指标累计样本数 ≥ 62（20 + 12 + 30）
    - _需求: 5.1、5.2、10.2_

- [x] 4. 七个维度的评估子报告与证据回填
  - [x] 4.1 维度 1：架构与工程化 `docs/evaluation/dimensions/1-architecture.md`
    - 文件由 `aggregate_scores.py` 生成骨架；本任务在 `tests/evaluation/evidence/catalog.py` 回填 `DimensionId.ARCHITECTURE` 的 ≥ 3 条 Evidence（DI 容器、Port/Adapter 装配、`container_config.py` 锚点、`docs/steering/ddd-architecture.md`）
    - 人工在骨架文件的 `<!-- TBD -->` 占位区写 "评估结论"、"5 级判据打分依据"、"改进建议"（中文）
    - 引用 OpenAI / Anthropic / Google ADK 至少 2 个 Industry_Framework 条款
    - _需求: 2.1、2.3、3.1、4.1_
  - [x] 4.2 维度 2：Agent 核心能力 `docs/evaluation/dimensions/2-agent-core.md`
    - 在 `catalog.py` 回填证据：`react_agent_adapter.py` 最大轮次/工具权限拒绝路径、`ScopedToolRegistry.create_scoped_view`、`DelegationAdapter` 深度校验、`SlidingWindowCompactionAdapter`
    - 子报告骨架内人工撰写四个子条目结论：ReAct_Loop / Tool_Registry / Agent_Delegation / Context_Compaction
    - 引用 Anthropic Tool Use、LangGraph Agent Patterns 等条款 ≥ 2 条
    - _需求: 8.1、8.2、8.3、8.4_
  - [x] 4.3 维度 3：模型与提示工程 `docs/evaluation/dimensions/3-model-prompt.md`
    - 在 `catalog.py` 回填 ≥ 3 条证据（多 Provider 注册、路由策略、热重载、`config.properties:MODEL_*`）
    - 引用 OpenAI Assistants 最佳实践、Anthropic Prompt Caching ≥ 2 条
    - _需求: 2.1、4.1_
  - [x] 4.4 维度 4：安全与合规 `docs/evaluation/dimensions/4-security.md`
    - 在 `catalog.py` 回填证据：`ShellExecTool` / `PythonExecTool` 环境变量脱敏代码行、`Workspace` 路径归一化、`SymlinkGuard` / `IdentityGuard`、`config.properties` 凭证来源
    - 人工写 ≥ 2 条 prompt 注入与工具滥用改进建议，引用 OWASP LLM Top 10 + Anthropic Safety Best Practices
    - _需求: 9.1、9.2、9.3、9.4_
  - [x] 4.5 维度 5：可靠性与性能 `docs/evaluation/dimensions/5-reliability.md`
    - 在 `catalog.py` 回填证据：SSE 错误恢复（`streaming_chat_router`）、ReAct 失败路径、模型 Provider Round-Robin、延迟与 token 可观测性（`observability/` 模块）
    - 人工撰写结论；至少引用 Google SRE、Anthropic 可观测性建议 ≥ 2 条
    - _需求: 10.1_
  - [x] 4.6 维度 6：可测试性与质量 `docs/evaluation/dimensions/6-testability.md`
    - 在 `catalog.py` 回填证据：`epsilon-boot/test/` 目录分层（unit / property / integration）、当前回归缺口
    - 人工说明 Evaluation_Script 如何填补回归空白，如何接入 CI（`uv run python -m scripts.evaluation.run_eval --baseline=<path>`）
    - 引用 Berkeley FCL / τ-bench ≥ 2 条
    - _需求: 10.2、10.3_
  - [x] 4.7 维度 7：前端 / UX `docs/evaluation/dimensions/7-frontend-ux.md`
    - 在 `catalog.py` 回填前端证据：`ChatPanel` 流式渲染、`TaskWorkspace`、SSE `[DONE]`、AbortController、`execution_trace` 暴露情况
    - 新增 `tests/evaluation/frontend/ux_probe.md` 人工巡检清单（对齐 design.md 组件 7）
    - 人工撰写结论，缺失 trace / 反馈通道时以 Improvement_Recommendation 登记并引用 Human Feedback 条款
    - _需求: 11.1、11.2、11.3_
  - [x] 4.8 Checkpoint：证据目录与 Rubric 交叉校验
    - 从 `epsilon-boot/` 执行 `uv run python -m scripts.evaluation.verify_evidence --catalog=tests/evaluation/evidence/catalog.py --repo-root=../`，断言零失败
    - 断言每维度证据数 ≥ 3、每维度 Rubric citations 跨级去重后 framework ≥ 2
    - _对应 Property 4、Property 6；需求 3.1、3.2、4.1_

- [x] 5. 脚本入口、主报告聚合与回归对比
  - [x] 5.1 证据校验脚本 `scripts/evaluation/verify_evidence.py`
    - CLI 参数：`--catalog`（默认 `tests/evaluation/evidence/catalog.py`）、`--repo-root`（默认 `..`）
    - 载入 `catalog.load_catalog()` → 调用 `verify_evidence()` → 人类可读表格打印 + 非零退出码（任一失败 → 1）
    - 含模块级中文 docstring 说明用途、参数、退出码
    - _需求: 3.4、5.4_
  - [x] 5.2 评测主入口 `scripts/evaluation/run_eval.py`
    - CLI 参数：`--metric={all|tool_call_success_rate|delegation_correctness|context_compaction_effectiveness}`、`--output`、`--baseline`、`--regression-threshold`
    - 解析 → 构造 `RunnerConfig` → `EvalRunner.run()` → `write_json()` → 若带 `--baseline` 调用 `compare_baseline.compare()`
    - 退出码：0 成功 / 1 脚本异常（含 `RubricConsistencyError` / `OSError` / 参数非法）/ 2 回归触发
    - 打印人类可读表格摘要（指标名、分子/分母、比例、样本数、失败数）
    - _需求: 1.3、5.2、5.4、10.4_
  - [x] 5.3 回归对比脚本 `scripts/evaluation/compare_baseline.py`
    - CLI 参数：`--baseline`（必填）、`--latest`（必填）、`--threshold`（默认 5.0，单位百分点）
    - 实现 `RegressionReport` dataclass（`metric`、`baseline_ratio`、`latest_ratio`、`delta_pp`、`violated`）
    - `compare(baseline_path, latest_path, threshold) -> RegressionReport`：按 `MetricId` 匹配，计算 `delta_pp = (latest - baseline) * 100`；`delta_pp <= -threshold` → `violated=True`
    - 基线文件不存在 → 打印 warning，退出码 0（首次运行允许）
    - 任一 violated → 退出码 2；脚本自身异常 → 退出码 1
    - _需求: 10.4_
  - [x] 5.4 评分聚合与主报告生成 `scripts/evaluation/aggregate_scores.py`
    - CLI 参数：`--result`（最新 JSON 路径）、`--scores`（默认 `docs/evaluation/scores.toml`）、`--output-root`（默认 `docs/evaluation`）
    - 使用标准库 `tomllib` 解析 `scores.toml`（格式见 design.md "评分源文件"）
    - 产出：
      - `docs/evaluation/scores.json`（机器可读聚合结果，含 `total_score = Σ(score × weight) / Σ(weight)`）
      - `docs/evaluation/report.md`（主报告骨架 + 自动注入的评分表、指标数值、读者导览、附录交付物清单）
      - 七份 `docs/evaluation/dimensions/<n>-<slug>.md` 骨架（若已存在则保留人工撰写段落，仅重写"自动生成"标记区块）
    - 所有"结论" / "改进建议详解"段落以 `<!-- TBD: 人工撰写 -->` 占位
    - 主报告章节至少：执行摘要、读者导览、评估方法（列全部 `FrameworkCitation`）、评分汇总表、维度链接、改进清单、附录交付物清单
    - _需求: 1.1、1.2、1.4、2.1、13.1、13.2、13.3、14.1、14.3_
  - [x] 5.5 编写人工评分源 `docs/evaluation/scores.toml`
    - 按 7 维度 × `[<dimension>]` 分段写入 `score`、`rationale`（中文）、`evidence_refs`（从 `catalog.py` 选取关键 ≥ 3 条）
    - 每维度 `rationale` ≥ 3 句，显式引用对应维度在 Rubric 中的业界框架条款
    - _需求: 2.2、3.1、4.3、13.1_
  - [x]* 5.6 自测：`tests/evaluation/self_tests/test_compare_baseline.py`
    - 覆盖：阈值内不触发（delta = -3pp < -5pp 阈值？实际 delta=-3 > -5，未触发）、阈值外触发（delta=-6）、基线文件不存在 → 退出码 0
    - 通过 `capsys` 断言 stdout 含中文摘要
    - _对应 Property 7；需求 10.4_
  - [x]* 5.7 自测：`tests/evaluation/self_tests/test_delivery_path_guard.py`
    - 通过 `git diff --name-only HEAD` 收集变更，断言全部以 `docs/evaluation/` / `tests/evaluation/` / `scripts/evaluation/` 之一开头
    - 跳过策略：若非 git 仓库或无 HEAD，`pytest.skip`
    - _对应 Property 1；需求 7.2_
  - [x]* 5.8 端到端回归集成测试 `tests/evaluation/self_tests/test_end_to_end.py`
    - 在 `tmp_path` 以 `run_eval.main(["--metric=all", "--output=<tmp>/first.json"])` 跑一次，断言 JSON schema 字段齐全、`exit_code=0`
    - 以第一次输出作为基线复跑，断言 `exit_code=0`
    - 篡改 `first.json` 的 `numerator_sum`（下降至 `ratio` 低 6pp）后再跑 `compare_baseline.main()`，断言 `exit_code=2`
    - _对应 Property 3、Property 7；需求 5.5、10.4_
  - [x] 5.9 Checkpoint：脚本与主报告联调
    - 从 `epsilon-boot/` 执行 `uv run python -m scripts.evaluation.run_eval --metric=all`，断言 `exit_code=0` 且 `docs/evaluation/results/*.json` 写入成功
    - 执行 `uv run python -m scripts.evaluation.aggregate_scores --result=<最新>`，断言生成 `docs/evaluation/report.md`、`scores.json`、`dimensions/*.md` 七份
    - 以当前结果为基线复跑 `run_eval.py --baseline=<path>`，断言 `exit_code=0`
    - 执行 `uv run python -m scripts.evaluation.verify_evidence`，断言无失败
    - _对应全部 Property；需求 1.3、5.4、10.4、14.1、14.4_

- [x] 6. 交付收尾与文档
  - [x] 6.1 撰写主报告结论段落（人工）
    - 在 `docs/evaluation/report.md` 骨架的 `<!-- TBD -->` 位置写执行摘要、读者导览、整体风险等级判定、前三位高优先级 Improvement_Recommendation
    - 在七份 `dimensions/*.md` 骨架填每维度结论段落（≥ 200 字）
    - 保证所有自动注入区块（打分表 / 指标数值 / 评估方法框架清单）未被人工修改
    - _需求: 1.1、1.2、1.4、2.2、13.1、13.3_
  - [x] 6.2 完善 `scripts/evaluation/README.md`
    - 面向 QA / 平台工程师，列出全部 `uv run` 命令（`run_eval`、`aggregate_scores`、`compare_baseline`、`verify_evidence`），每条含示例、输入、输出、退出码含义
    - 显式声明禁止使用 `pip` / `poetry` / `pipenv` / `conda`
    - 说明 `scores.toml` 维护规则（不手工改 `scores.json` 与 `report.md` 自动生成段落）
    - _需求: 6.1、6.3、6.4、14.2、14.3_
  - [x] 6.3 在主报告"附录：交付物清单"章节（由 `aggregate_scores.py` 自动生成）人工补校对
    - 确认列出全部新增文件路径（7 份子报告、scores.toml、scores.json、results/*.json、7 个 catalog 维度、3 个 metric 脚本、4 个 scripts/evaluation 入口、self_tests 清单）
    - 每条标注对应 Evaluation_Dimension 或指标，附可执行 `uv run` 命令示例
    - _需求: 14.3_
  - [x] 6.4 端到端回归演练
    - 干净环境下从 `epsilon-boot/` 顺序执行：
      1. `uv run pytest tests/evaluation/self_tests -q`
      2. `uv run pytest tests/evaluation/metrics -m evaluation -q`
      3. `uv run python -m scripts.evaluation.verify_evidence`
      4. `uv run python -m scripts.evaluation.run_eval --metric=all`
      5. `uv run python -m scripts.evaluation.aggregate_scores --result=<latest>`
      6. 以 4 的输出为 baseline，再跑 `run_eval.py --baseline=<path>`，断言 `exit_code=0`
    - 记录完整 stdout 至 `docs/evaluation/results/dry-run-<timestamp>.log`（或附录说明），作为一次性验证证据
    - _需求: 1.3、5.3、5.4、10.4、14.4_
  - [x] 6.5 最终 Checkpoint：交付守卫与硬约束复核
    - 执行 `git diff --name-only HEAD` 断言输出路径全部落在三白名单目录
    - 执行 `git diff -- epsilon-boot/src epsilon-client epsilon-boot/config.properties` 断言输出为空（未修改业务代码与业务配置）
    - 复核 `pyproject.toml` 与 `uv.lock` 未改动（本期不引入新依赖）
    - 复核所有新增 `.py` 文件首行含中文模块 docstring，公开类/函数含中文 docstring
    - _对应 Property 1、Property 2；需求 6.3、6.4、7.1、7.2、7.3、7.4、12.1、12.2、12.4_

## 备注

### 任务与需求追溯

| 需求 | 主要覆盖任务 |
|---|---|
| 需求 1 多角色交付 | 5.4、5.9、6.1 |
| 需求 2 维度全覆盖 | 1.4、4.1-4.7、5.4 |
| 需求 3 证据可追溯 | 1.5、1.6、1.7、4.1-4.7、4.8、5.1 |
| 需求 4 业界框架引用 | 1.4、4.1-4.7、4.8 |
| 需求 5 三项核心指标 | 2.1-2.6、3.1-3.6、3.7、5.2 |
| 需求 6 uv / 配置合规 | 1.2、2.1、6.2、6.5 |
| 需求 7 不改业务代码 | 1.11、2.10、5.7、6.5 |
| 需求 8 Agent 核心条目 | 3.1、3.3、3.5、4.2 |
| 需求 9 安全条目 | 4.4 |
| 需求 10 可靠/可测性 | 3.3、3.5、4.5、4.6、5.3、5.8、5.9、6.4 |
| 需求 11 前端 UX | 4.7 |
| 需求 12 Steering 合规 | 1.3、2.1-2.3、5.7、6.5 |
| 需求 13 改进建议 | 4.1-4.7、5.4、5.5、6.1 |
| 需求 14 交付清单 | 1.1、2.5、5.4、6.3、6.5 |

### 任务与正确性属性追溯

| 属性 | 覆盖任务 |
|---|---|
| Property 1 交付路径闭包 | 1.1、1.11、2.10、5.7、6.5 |
| Property 2 不写业务路径 | 2.10、6.5 |
| Property 3 样本异常不中止 | 2.5、2.8、5.8 |
| Property 4 证据格式严格 | 1.5、1.9、4.8 |
| Property 5 权重归一 | 1.4、1.8 |
| Property 6 每维度 ≥ 2 框架 | 1.4、1.8、4.8 |
| Property 7 回归阈值语义 | 5.3、5.6、5.8 |
| Property 8 SystemMessage 无损 | 3.5、3.6 |
| Property 9 委派深度不超限 | 3.3、3.4 |

### 执行顺序说明

- 阶段 1 建立所有后续任务依赖的数据结构（Rubric、Evidence、异常族）；是最严格的前置条件。
- 阶段 2 的桩 Port 是阶段 3 三项指标脚本的唯一被测驱动源。
- 阶段 3 的指标样本必须先跑通，阶段 5 的 `run_eval.py` 才能收集到结果并写 JSON。
- 阶段 4 与阶段 3 可以并行（证据目录与指标脚本彼此独立），但阶段 5.4 `aggregate_scores.py` 同时依赖两者产出，故把证据回填收敛在阶段 4。
- 阶段 6 全部是"人工撰写 + 最终守卫"，不引入新代码。

### 命令速查

所有命令从 `epsilon-boot/` 目录执行：

```bash
# 自测
uv run pytest tests/evaluation/self_tests -q

# 评测指标（pytest 方式）
uv run pytest tests/evaluation/metrics -m evaluation -q

# 评测主入口
uv run python -m scripts.evaluation.run_eval --metric=all

# 主报告聚合
uv run python -m scripts.evaluation.aggregate_scores --result=docs/evaluation/results/latest.json

# 回归对比
uv run python -m scripts.evaluation.compare_baseline \
  --baseline=docs/evaluation/results/2026-05-01_120000_abc.json \
  --latest=docs/evaluation/results/latest.json \
  --threshold=5.0

# 证据校验
uv run python -m scripts.evaluation.verify_evidence
```

> 禁止使用 `pip` / `poetry` / `pipenv` / `conda`；禁止修改 `epsilon-boot/src/`、`epsilon-client/`、`epsilon-boot/config.properties`。
