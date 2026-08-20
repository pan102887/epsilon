# Agent / Coding Agent 质量评估调研

本文调研业界主流 Agent 应用与 Coding Agent 应用的质量评估方法、测试验证方案和工程落地路径，重点关注离线评估、在线评估、轨迹评估、工具调用评估、LLM-as-judge、Coding Agent benchmark 与 CI 回归门禁。

> 说明：本次结论基于已完成的 deep-research 工作流。该工作流按 5 个角度展开搜索，抓取 26 个来源，抽取 110 条候选 claim，对 25 条核心 claim 做 3 票对抗验证，确认 25 条，剔除 0 条，最终合成为 8 条核心发现。主要来源包括 LangSmith、Arize Phoenix、DeepEval、Ragas、SWE-bench、Terminal-Bench、Braintrust、OpenAI Agent Evals 等官方文档或项目资料。

## 一、总览结论

业界对 Agent 应用的质量评估，已经不再只看“最终回答是否正确”，而是逐步形成多层评估体系：

1. **离线评估**：发布前使用 golden set、benchmark、回归用例评估 Agent。
2. **在线评估**：生产环境基于 trace、run、thread、session 做持续监控和抽样评审。
3. **过程评估**：不仅评估最终输出，还评估工具选择、工具参数、执行轨迹、子 Agent、retriever、LLM call。
4. **工具调用评估**：对 tool name、tool args、调用顺序、漏调、多调、参数合法性分别打分。
5. **端到端任务成功率**：尤其是 Coding Agent，主流指标是任务是否真实解决，而不是文本主观质量。
6. **LLM-as-judge + 规则 / 代码 evaluator 混合**：LLM 评审适合语义判断，规则和代码执行适合确定性验证。
7. **可复现环境**：Coding Agent 越来越强调真实 repo、真实 issue、Docker / sandbox、自动测试和排行榜。
8. **工程落地闭环**：较完整方案应同时覆盖发布前回归、轨迹诊断、生产监控和外部 benchmark 对标。

一句话概括：Agent 质量评估应采用“数据集回归 + trace 观测 + 工具调用评分 + LLM/规则混合 evaluator + 生产在线监控 + benchmark 对标”的组合体系；Coding Agent 还必须加入真实代码执行、测试通过率、Docker / sandbox 可复现验证和 patch 质量审查。

## 二、Agent 评估的主流分层

### 2.1 离线评估 Offline Evaluation

离线评估主要用于：

- 发布前质量门禁；
- prompt / model / tool / memory 改动后的回归测试；
- 多 Agent 策略对比；
- 不同模型、不同路由策略的 A/B 实验；
- golden set 固定用例验证。

典型流程：

```text
准备测试集
  ↓
运行 Agent task
  ↓
收集 output + trace + tool calls
  ↓
执行 evaluator
  ↓
生成评分和失败样例
  ↓
决定是否发布
```

LangSmith 明确将 offline evaluation 定义为基于预编译数据集的预部署评估，用于在变更上线前判断质量。Phoenix 的实验式评估流程也强调先创建测试用例数据集，再定义 task、配置 evaluator，并在平台中查看或回传结果。

适合离线评估的用例包括：

| 类型 | 示例 |
| --- | --- |
| 问答类 Agent | 输入问题，期望回答包含关键事实 |
| 工具调用 Agent | 输入任务，期望调用指定工具和参数 |
| ReAct Agent | 输入目标，期望走出合理 action-observation 轨迹 |
| RAG Agent | 输入问题，期望检索到正确文档并回答 |
| Coding Agent | 输入 issue，期望生成 patch 并通过测试 |
| 工作流 Agent | 输入业务流程，期望完成多个步骤并产出结果 |

### 2.2 在线评估 Online Evaluation

在线评估用于生产环境，核心不是“跑测试集”，而是对真实用户流量进行观测和抽样评估。

常见目标：

- 发现线上幻觉；
- 发现工具调用失败；
- 发现 Agent 卡循环、路径过长；
- 监控成本、时延、token 消耗；
- 对用户低评分会话做自动诊断；
- 抽样使用 LLM judge 或人工复核。

LangSmith 的在线评估基于 tracing 中的 runs 和 threads；Phoenix、DeepEval 也都强调 trace/span 级监控和评估。

在线评估常见指标：

| 指标 | 含义 |
| --- | --- |
| Task success rate | 任务成功率 |
| Tool error rate | 工具调用失败率 |
| Retry count | 重试次数 |
| Loop count | 是否陷入循环 |
| Trace length | Agent 执行路径长度 |
| Latency | 总耗时、每步耗时 |
| Cost | token 成本、模型调用成本 |
| User feedback | 点赞、点踩、投诉、人工评分 |
| Safety violation | 越权、敏感信息、危险操作 |

### 2.3 过程评估 Trajectory / Trace Evaluation

对于 ReAct、Planner-Executor、多 Agent 系统，仅看最终答案是不够的。Agent 可能出现以下情况：

- 虽然答对，但调用了危险工具；
- 虽然完成任务，但路径极其低效；
- 虽然最终成功，但中间泄露了敏感信息；
- 虽然测试通过，但用了不可接受的 hack；
- 虽然结果正确，但执行过程不可复现。

因此需要评估完整 trajectory：

```text
User task
  ↓
Thought / plan
  ↓
Tool call
  ↓
Observation
  ↓
Next action
  ↓
Final answer
```

常见轨迹指标：

| 指标 | 含义 |
| --- | --- |
| Path correctness | 路径是否符合预期 |
| Step efficiency | 是否绕路 |
| Convergence | 是否逐步接近目标 |
| Loop detection | 是否反复执行同类步骤 |
| Tool-use coherence | 工具调用是否和目标一致 |
| Recovery behavior | 失败后是否能纠正 |
| Safety boundary | 中间步骤是否越权 |

LangSmith 支持 agent trajectory evaluation；Phoenix 示例中也包含 agent path / convergence、SQL 生成、Python 代码执行等过程级评估；DeepEval 则强调 trace-based agent eval，把 LLM calls、tools、retrievers、sub-agents 表示为 spans，并在 trace 或 span 上挂载 metrics。

### 2.4 组件级评估 Component-Level Evaluation

复杂 Agent 系统通常不是单一模型调用，而是多个组件组合：

```text
User Input
  ↓
Intent Classifier
  ↓
Planner
  ↓
Retriever
  ↓
Tool Executor
  ↓
Memory
  ↓
Sub-agent
  ↓
Final Synthesizer
```

因此每个组件都可以单独评估：

| 组件 | 评估方式 |
| --- | --- |
| Router | 是否路由到正确 Agent / model |
| Planner | 计划是否完整、可执行 |
| Retriever | recall、precision、MRR、context relevance |
| Tool Executor | 工具名、参数、结果处理 |
| Memory | 是否正确读取 / 写入长期记忆 |
| Sub-agent | 子任务是否完成 |
| Synthesizer | 是否正确整合结果 |
| Guardrail | 是否拦截风险输入 / 输出 |

DeepEval、Phoenix、LangSmith 都在向 trace/span 级评估靠拢，本质上就是把 Agent 执行链路拆开看。

## 三、工具调用评估

Agent 应用最关键的质量问题之一是工具调用是否正确。工具调用评估通常既包含严格的参考匹配，也包含更宽松的集合 / IR 指标和 LLM-as-judge 判断。

评估维度包括：

| 维度 | 问题 |
| --- | --- |
| Tool selection | 是否选择了正确工具？ |
| Tool arguments | 参数是否正确？ |
| Tool order | 调用顺序是否合理？ |
| Missing calls | 是否漏调工具？ |
| Extra calls | 是否多调无关工具？ |
| Error handling | 工具失败后是否正确恢复？ |
| Permission boundary | 是否调用了不该调用的工具？ |
| Side effect safety | 是否错误执行了有副作用操作？ |

Ragas 的 `ToolCallAccuracy` 会把实际工具调用与 `reference_tool_calls` 比较，覆盖工具名称、参数和默认严格顺序；如果顺序错误，即使工具正确也可能得 0。Ragas 的 `ToolCallF1` 使用 order-insensitive matching，并按 matching、extra、missing calls 计算 precision、recall、F1。

Phoenix 同时提供 ToolSelectionEvaluator、ToolInvocationEvaluator 和基于 ground truth 的自定义 `tools_match`；LangSmith 也支持工具选择、参数格式和轨迹评估。

工具调用评估的一个示例结构：

```json
{
  "expected_tool_calls": [
    {
      "tool": "search_docs",
      "args": {
        "query_contains": "refund policy"
      }
    }
  ],
  "actual_tool_calls": [
    {
      "tool": "search_docs",
      "args": {
        "query": "refund policy for enterprise customers"
      }
    }
  ],
  "score": {
    "tool_selection": 1.0,
    "argument": 0.8,
    "sequence": 1.0
  }
}
```

## 四、端到端质量评估与 LLM-as-judge

端到端质量评估常用目标达成、分类 / 规则 evaluator、代码 evaluator 和 LLM-as-judge。其中 LLM judge 可分为有参考答案和无参考答案两类。

| Evaluator | 适用场景 | 优点 | 风险 |
| --- | --- | --- | --- |
| Exact / regex | 格式固定、关键字段固定 | 稳定、便宜 | 只能评估形式 |
| Code evaluator | 可执行逻辑、结构化输出、代码结果 | 可复现、可靠 | 需要写验证逻辑 |
| Unit tests | Coding Agent、工具结果验证 | 强确定性 | 覆盖不足时会误判 |
| LLM-as-judge | 语义正确性、目标达成、解释质量 | 灵活 | 有偏见、不稳定、成本高 |
| Human review | 高风险、高价值样例 | 最可靠 | 贵、慢、不可大规模 |
| User feedback | 线上真实体验 | 真实 | 噪声大、滞后 |

LangSmith 支持 LLM-as-judge evaluator，并区分 reference-free 和 reference-based；reference-based 需要 expected outputs，通常只适用于离线评估。Ragas 提供二值 Agent Goal Accuracy，并有 with-reference 与 without-reference 变体，用于判断用户目标是否达成。Phoenix 示例包含 ClassificationEvaluator、自定义代码评估器、SQL 结果评估、Python 代码可运行性评估和路径长度评估。

LLM judge 不应无校准地作为唯一门禁。建议：

- 准备人工标注校准集；
- 比较 LLM judge 和人类判断一致率；
- 使用固定 prompt 和固定模型版本；
- 对高风险评估使用多 judge 投票；
- 定期检查 judge drift；
- 对 bad cases 做人工复核。

常见 judge 输出应结构化：

```json
{
  "score": 0.0,
  "pass": false,
  "reason": "...",
  "failure_type": "tool_error | hallucination | incomplete | unsafe | inefficient"
}
```

## 五、Coding Agent 评估主流方案

Coding Agent 和普通聊天 Agent 最大不同是：可以通过代码执行、测试、构建、静态检查来验证结果。因此 Coding Agent 评估更强调可复现 benchmark 和自动化验证。

### 5.1 SWE-bench

SWE-bench 是 Coding Agent 领域最重要的 benchmark 之一。

核心思想：

```text
真实 GitHub issue
  ↓
给定 repo + issue description
  ↓
Agent 生成 patch
  ↓
在 Docker 环境中应用 patch
  ↓
运行测试
  ↓
判断 issue 是否解决
```

特点：

- 来自真实 GitHub issue；
- 要求修改真实代码仓库；
- 使用 Docker harness 保证可复现；
- 以测试是否通过作为核心评价；
- 有多个榜单：Full、Lite、Verified、Multilingual、Multimodal 等；
- SWE-bench Verified 是 500 个经人工软件工程师确认可解的问题子集。

核心指标不是“代码看起来好不好”，而是：

```text
Resolved rate = 成功解决的 issue 数 / 总 issue 数
```

这对 Coding Agent 很关键，因为自然语言评分不足以判断代码是否真正工作。

### 5.2 Terminal-Bench

Terminal-Bench 面向能在终端环境中工作的 Agent。

它评估的不只是“写代码”，还包括：

- 软件工程任务；
- 机器学习任务；
- 数据处理；
- 安全相关任务；
- 需要命令行操作的多步骤任务。

典型评估方式：

```text
给 Agent 一个终端任务
  ↓
Agent 在 sandbox / terminal 中操作
  ↓
运行验证脚本或检查最终状态
  ↓
计算 task resolution success-rate
```

Terminal-Bench 更接近真实 Coding Agent / Terminal Agent 的工作方式，因为很多工程任务不只是改一个文件，而是需要查代码、安装依赖、跑测试、调试、修改配置和验证运行结果。

### 5.3 内部 Coding Agent Eval Harness

企业内部 Coding Agent 不一定要直接使用 SWE-bench 全量，但可以借鉴其 harness 思路。

推荐测试结构：

```text
evals/
  coding_agent/
    cases/
      case_001/
        repo_snapshot/
        task.md
        expected_behavior.md
        test_command.sh
        grading.py
      case_002/
        ...
    run_eval.py
    report.json
```

每个 case 至少包含：

| 内容 | 说明 |
| --- | --- |
| 初始代码状态 | repo snapshot 或 git commit |
| 任务描述 | issue / requirement |
| 允许工具 | shell、edit、test、browser 等 |
| 验证命令 | pytest、npm test、build、lint 等 |
| 评分规则 | tests passed、diff quality、安全检查 |
| 期望限制 | 不允许修改哪些文件，不允许跳过测试 |
| 成本限制 | 最大轮数、最大 token、最大耗时 |

验证指标：

| 指标 | 说明 |
| --- | --- |
| Solve rate | 任务解决率 |
| Test pass rate | 测试通过率 |
| Build pass rate | 构建通过率 |
| Regression rate | 是否引入回归 |
| Patch size | 修改规模 |
| Files touched | 修改文件数 |
| Time to solve | 完成耗时 |
| Tool calls | 工具调用次数 |
| Human intervention | 是否需要人工介入 |
| Reproducibility | 是否可重复通过 |

## 六、主流工具和平台对比

| 平台 / 框架 | 主要能力 | 适合场景 |
| --- | --- | --- |
| LangSmith | offline / online eval、trace、run、thread、LLM judge、trajectory eval | LangChain / LangGraph Agent 评估与生产监控 |
| Arize Phoenix | tracing、dataset experiment、LLM eval、tool selection / invocation eval、可视化 | OpenTelemetry / LLM observability / Agent 诊断 |
| DeepEval | 单元测试式 LLM eval、agent trace/span eval、component-level metrics | 把 eval 接入 CI/CD |
| Ragas | RAG + Agent metrics、ToolCallAccuracy、ToolCallF1、Agent Goal Accuracy | RAG Agent 和工具调用评估 |
| SWE-bench | 真实 GitHub issue patch benchmark | Coding Agent 能力对标 |
| Terminal-Bench | 终端任务 benchmark | Terminal / CLI Agent 能力对标 |
| Braintrust | Eval dataset、experiment、LLM judge、生产反馈闭环 | LLM 产品迭代评估 |
| OpenAI Agent Evals | Agent 任务评估、模型行为评测 | OpenAI 生态或自定义 eval harness |

## 七、工程落地建议

较完整的 Agent 质量体系应同时覆盖发布前 golden-set 回归、工具 / 轨迹级诊断、生产 trace 监控和 benchmark 对标。

### 7.1 第一层：Golden Set 回归测试

目标：保证每次改 prompt、模型、工具、memory、Agent loop 后，不破坏核心场景。

数据结构建议：

```text
golden_set:
  - input
  - expected_output / expected_behavior
  - expected_tools
  - allowed_variations
  - evaluator
```

适合放入 CI：

```text
PR 提交
  ↓
运行 agent eval
  ↓
计算 pass rate
  ↓
低于阈值则阻断合并
```

建议指标：

| 指标 | 建议门槛 |
| --- | --- |
| 核心任务成功率 | >= 90% |
| 高优先级用例成功率 | 100% |
| 工具调用准确率 | >= 95% |
| 严重安全失败 | 0 |
| 平均成本增长 | 不超过基线 20% |
| 平均时延增长 | 不超过基线 20% |

### 7.2 第二层：Trace / Trajectory 诊断

目标：发现“结果看似正确，但过程有问题”的情况。

每次 Agent 运行都记录：

```json
{
  "task_id": "...",
  "input": "...",
  "steps": [
    {
      "type": "llm_call",
      "model": "...",
      "prompt_tokens": 1234,
      "completion_tokens": 567
    },
    {
      "type": "tool_call",
      "tool": "search_docs",
      "args": {},
      "status": "success"
    }
  ],
  "final_output": "...",
  "latency_ms": 12345,
  "cost": 0.012
}
```

评估点：

- 是否用了正确工具；
- 是否出现无效重复调用；
- 是否有不必要的大模型调用；
- 是否超出最大步骤数；
- 是否错误处理工具异常；
- 是否访问了不该访问的数据；
- 是否在没有足够证据时给出结论。

### 7.3 第三层：生产在线监控

目标：上线后持续发现质量下降和异常行为。

建议监控：

| 类型 | 指标 |
| --- | --- |
| 成功率 | task success、user thumbs up/down |
| 稳定性 | error rate、timeout rate、retry rate |
| 成本 | token、model cost、tool cost |
| 性能 | p50 / p95 / p99 latency |
| 行为 | tool call distribution、loop count |
| 安全 | sensitive data exposure、permission violation |
| 质量 | LLM judge sample score、human review score |

生产评估要特别注意：

- 不一定有 reference answer；
- 需要抽样；
- 需要脱敏；
- LLM judge 不能直接接触敏感数据；
- 高风险任务要保留人工审核；
- 用户反馈要和 trace 关联。

### 7.4 第四层：外部 Benchmark 对标

目标：判断 Agent 能力是否和业界主流水平接近。

对 Coding Agent：

- SWE-bench Lite / Verified；
- Terminal-Bench；
- 自建真实 issue benchmark；
- 内部历史 bug 修复集；
- 内部重构 / 测试补全 / 文档生成任务集。

对普通业务 Agent：

- 自建行业数据集；
- 用户真实问题脱敏集；
- 典型失败样例集；
- adversarial cases；
- long-context cases；
- multi-turn cases。

## 八、针对本项目的建议方案

本项目是 FastAPI + DDD + ReAct Agent Loop + 多模型路由 + 工具调用 + 会话存储的 Agent 工作台。建议后续如果落地 Agent 评估体系，可以优先采用以下结构。

### 8.1 建立 evals 目录

```text
evals/
  datasets/
    chat_agent_golden.jsonl
    tool_agent_golden.jsonl
    react_loop_golden.jsonl
    coding_agent_golden.jsonl
  evaluators/
    answer_judge.py
    tool_call_accuracy.py
    trajectory_judge.py
    cost_latency.py
    safety_policy.py
  runners/
    run_offline_eval.py
    run_single_case.py
  reports/
```

### 8.2 每条 eval case 记录完整期望

```json
{
  "id": "tool-search-001",
  "category": "tool_use",
  "input": "帮我查询订单 A123 的状态",
  "expected": {
    "must_call_tools": ["order_query"],
    "forbidden_tools": ["refund_order"],
    "answer_contains": ["订单状态"],
    "max_steps": 4
  },
  "evaluators": [
    "tool_call_accuracy",
    "answer_contains",
    "trajectory_length",
    "safety_policy"
  ]
}
```

### 8.3 每次 Agent 执行输出标准 trace

```json
{
  "case_id": "tool-search-001",
  "model": "claude-opus-4-8",
  "agent_config": "...",
  "messages": [],
  "tool_calls": [],
  "spans": [],
  "final_answer": "...",
  "usage": {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cost": 0.01
  }
}
```

### 8.4 CI 门禁分层

| 层级 | 运行频率 | 覆盖 |
| --- | --- | --- |
| Smoke eval | 每个 PR | 10-20 条核心用例 |
| Regression eval | 合并前 / 每日 | 100-500 条 golden set |
| Full eval | 每周 / 发布前 | 全量测试集 |
| Benchmark eval | 版本发布 | 外部或内部 benchmark |

### 8.5 发布门禁示例

```yaml
quality_gate:
  task_success_rate: ">= 0.90"
  critical_case_success_rate: "== 1.00"
  tool_call_accuracy: ">= 0.95"
  safety_violation_count: "== 0"
  p95_latency_growth: "<= 0.20"
  cost_growth: "<= 0.20"
```

## 九、Coding Agent 推荐评估用例类型

建议至少覆盖这些任务：

| 类型 | 目标 |
| --- | --- |
| Bug fix | 根据失败测试或 issue 修 bug |
| Feature implementation | 根据需求新增功能 |
| Refactor | 不改变行为的结构优化 |
| Test generation | 补测试并验证失败场景 |
| Dependency/config fix | 修构建、依赖、配置 |
| API integration | 接入新接口 |
| Data migration | 修改 schema / migration |
| Frontend behavior | UI 交互验证 |
| Security fix | 修越权、注入、敏感信息泄漏 |
| Performance fix | 优化慢查询、重复调用、缓存 |

每个 case 最好有：

```text
初始失败验证 → Agent 修改 → 最终验证 → diff 审查
```

对于 bug fix，最理想是 red-green：

```text
1. 初始状态：测试失败，能复现 bug
2. Agent 修改代码
3. 测试通过
4. 回滚关键修复，测试再次失败
5. 恢复修复，测试再次通过
```

## 十、风险和注意事项

### 10.1 Benchmark 分数不等于真实生产力

SWE-bench、Terminal-Bench 很有价值，但仍然可能存在：

- benchmark contamination；
- reward hacking；
- 测试 oracle 不充分；
- 通过测试但代码质量差；
- 修了 benchmark case 但不适合长期维护；
- 与真实业务仓库分布不同。

因此最好结合内部真实任务集。

### 10.2 LLM judge 本身也需要评估

LLM-as-judge 可能受以下因素影响：

- judge 模型偏见；
- prompt 写法；
- reference answer 质量；
- 输出格式不稳定；
- 语言差异；
- 对复杂代码 diff 理解不足；
- 被被评估输出诱导。

建议对 judge 做人工标注一致性验证、多模型 judge 对比、多 judge 投票、置信度阈值和人工抽样复核。

### 10.3 生产在线评估要注意隐私

真实 trace 里可能包含：

- 用户输入；
- 内部数据；
- 工具返回结果；
- API 参数；
- 代码片段；
- 凭证或敏感路径。

因此在线评估需要脱敏、最小化采集、权限隔离，并避免把敏感 trace 发送给外部 judge 模型。高风险样例应只做内部评估或人工复核。

## 十一、推荐推进顺序

1. **先做 golden set + 离线 eval runner**：覆盖核心业务场景，形成最小质量门禁。
2. **再做 trace 标准化**：每次 Agent 执行都能记录 tool calls、spans、latency、cost、final answer。
3. **加入工具调用 evaluator**：这是 Agent 应用最容易出问题、也最有诊断价值的部分。
4. **引入 LLM-as-judge，但不要单独依赖它**：和规则、代码测试、人工抽检一起用。
5. **Coding Agent 必须执行真实验证命令**：例如 test、build、lint、typecheck、security scan，而不是只让模型自评。
6. **生产环境做在线监控和抽样评估**：关注成功率、失败类型、成本、时延、安全和用户反馈。

## 十二、参考来源

- [LangSmith Evaluation Concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
- [LangSmith Online Evaluations](https://docs.langchain.com/langsmith/online-evaluations)
- [LangSmith Trajectory Evals](https://docs.langchain.com/langsmith/trajectory-evals)
- [Ragas Agent Metrics v0.4.2](https://docs.ragas.io/en/v0.4.2/concepts/metrics/available_metrics/agents/)
- [Ragas Stable Agent Metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents)
- [Arize Phoenix: Evaluate an Agent](https://arize.com/docs/phoenix/cookbook/evaluation/evaluate-an-agent)
- [Phoenix Tool Selection Evaluator](https://arize.com/docs/phoenix/evaluation/server-evals/pre-built-metrics/tool-selection)
- [Phoenix Tool Invocation Evaluator](https://arize.com/docs/phoenix/evaluation/server-evals/pre-built-metrics/tool-invocation)
- [DeepEval Agent Evaluation](https://deepeval.com/docs/getting-started-agents)
- [DeepEval Component-Level LLM Evals](https://deepeval.com/docs/evaluation-component-level-llm-evals)
- [SWE-bench](https://www.swebench.com/)
- [SWE-bench GitHub](https://github.com/swe-bench/SWE-bench)
- [SWE-bench Verified](https://www.swebench.com/verified.html)
- [Terminal-Bench](https://www.tbench.ai/)
- [Terminal-Bench Docs](https://www.tbench.ai/docs)
- [Terminal-Bench Leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0)
- [Terminal-Bench GitHub](https://github.com/harbor-framework/terminal-bench)
- [Braintrust: Agent Evaluation](https://www.braintrust.dev/articles/agent-evaluation)
- [OpenAI Agent Evals](https://platform.openai.com/docs/guides/agent-evals)
