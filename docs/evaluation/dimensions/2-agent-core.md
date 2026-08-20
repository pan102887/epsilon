# 维度 2：Agent 核心能力

## 评估结论

**评分：4 / 5**。`ReActAgentAdapter` 覆盖了"最大轮次保护 + 权限拒绝 ToolMessage 回写 + tool_calls 序列化 + 异常吸收 + Agent 委派深度校验 + SystemMessage 无损压缩"全部核心路径；但缺少工具调用与委派的端到端 span / trace（仅日志级别），以及 `Tool_Call_Success_Rate` / `Delegation_Correctness` / `Context_Compaction_Effectiveness` 的**回归守护入口**（本次交付方才补齐，但 CI 尚未接入），因此未能达到 5 分。

## 证据与分析

### 2.1 ReAct_Loop

- [`epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:L128-L189`](../../../epsilon-boot/src/infrastructure/agent/react_agent_adapter.py)
  `run()` 在 `for round_num in range(1, config.max_rounds + 1)` 内完整实现 ReAct；`max_rounds` 为硬上限，无 `while True` 风险。权限拒绝构造 `ToolPermissionDeniedError(str(error))` 写回 `ToolMessage`，异常路径以 `result = str(e)` 吸收后继续；tool_calls 序列化由静态方法 `_serialize_messages` 对齐 OpenAI Chat Completions 规范。

### 2.2 Tool_Registry

- [`epsilon-boot/src/domain/agent/tools.py:L331-L401`](../../../epsilon-boot/src/domain/agent/tools.py)
  `ToolRegistry.create_scoped_view` 返回不可变 `frozenset` 快照的 `ScopedToolRegistry`；`execute` 入口先做 `request.name not in self._allowed_names` 校验，不在则抛 `ToolPermissionDeniedError(tool_name, allowed_tools)`。这就是 Agent 粒度的最小权限实现。
- 搭配 [`epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:L155-L178`](../../../epsilon-boot/src/infrastructure/agent/react_agent_adapter.py) 的权限拒绝回写，工具权限被双重保护：`allowed_tool_names` 在 Adapter 层过滤，`ScopedToolRegistry` 在 Registry 层过滤。

### 2.3 Agent_Delegation

- [`epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py:L128-L142`](../../../epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py)
  `execute` 计算 `next_depth = current + 1` 后对 `max_delegation_depth` 做越限校验，超限直接抛 `DelegationDepthExceededError(current_depth, max_depth, target_agent)`，避免任何"先调用后报错"造成的副作用；`config.properties:AGENT_MAX_DELEGATION_DEPTH=3` 为默认值。
- 配合 `DelegationAdapter.delegate`（[delegation_adapter.py:L52-L104](../../../epsilon-boot/src/infrastructure/agent/delegation_adapter.py)），`AgentNotFoundError` 与 `DelegationDepthExceededError` 两类失败路径被显式区分，成功时 `TaskResult.content` 作为 `DelegationResult.content` 返回，再由父 `ReActAgentAdapter` 以 `ToolMessage` 回写，形成完整闭环。

### 2.4 Context_Compaction

- [`epsilon-boot/src/infrastructure/chat/sliding_window_compaction_adapter.py:L42-L64`](../../../epsilon-boot/src/infrastructure/chat/sliding_window_compaction_adapter.py)
  先分离 `system` 与非 `system` 消息，再对非 system 截取 `-max_messages:`，SystemMessage **全量保留且顺序不变**。`max_messages` 由 `ChatConfig.max_messages`（经 `config.properties` → `PropertiesBaseSettings`）驱动。

## 业界框架对照

- **Anthropic — Tool use with Claude（Tool definition best practices）**：要求工具 schema 清晰、异常路径可被模型观察。项目通过 `Tool.to_schema` 生成 OpenAI 函数调用 schema，异常统一以 `ToolMessage` 回写让模型感知，对齐此条款。
- **LangChain — LangGraph Agent patterns / ReAct architecture**：强调 ReAct 循环要有最大步数保护与可观察的 state machine。项目有最大轮次保护，但缺少 LangGraph 推崇的可视化 graph / state 导出。
- **Berkeley Function-Calling Leaderboard — Tool selection accuracy**：项目当前没有公开的 tool selection 基准结果；本次评测新增的 `Tool_Call_Success_Rate` 指标部分弥补，但样本多样性与业界公开基准相比仍有限。

## 改进建议

1. **P0 — 打开工具调用与委派的 OpenTelemetry span**：在 `ReActAgentAdapter.run` 的每一轮以及 `DelegateToAgentTool.execute` 的 delegate 前/后增加 span 记录（工具名、delegation_depth、duration_ms、status）。预期收益：`Tool_Call_Success_Rate` / `Delegation_Correctness` 具备逐次调用的归因能力，比现在仅有 `logger.info/warning` 更有 SRE 价值。
2. **P1 — 把三项评测指标接入 CI**：在 PR 流水线对 `run_eval.py --baseline=<主干基线>` 加阈值守卫，任何 `ratio` 相对基线回退 ≥ 5pp 直接失败；填补当前"无回归守护"缺口。
3. **P2 — 引入 Anthropic "Building effective agents" 的组合式 Workflow**：为"可预测任务"（例如结构化抽取）新增 `WorkflowAgentAdapter`，与 `ReActAgentAdapter` 并存，按任务类型路由；避免所有场景都跑 ReAct。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：4 / 5，**权重**：0.22，**加权得分**：0.880

**人工打分理由**：`ReActAgentAdapter` 完整实现 ReAct 循环、最大轮次保护、权限拒绝与异常回写 `ToolMessage`，契合 Anthropic "Tool use with Claude — Tool definition best practices" 对"异常路径模型可感知"的要求；`ScopedToolRegistry.create_scoped_view` 按 `frozenset` 白名单暴露工具子集，实现 LangChain "LangGraph Agent patterns / ReAct architecture" 强调的状态可控与权限隔离。`DelegateToAgentTool` 对 `delegation_depth` 做 `next_depth = current + 1 ≤ MAX` 越限校验，`SlidingWindowCompactionAdapter` 全量保留 SystemMessage + 末尾 N 条非 system 消息，覆盖 Berkeley FCL 对工具 选择准确率的判据；4 → 5 的差距是缺少 tool_call / delegation 的端到端 span 与 CI 级回归守卫。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:128-189`
- `epsilon-boot/src/domain/agent/tools.py:331-401`
- `epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py:128-142`
- `epsilon-boot/src/infrastructure/chat/sliding_window_compaction_adapter.py:42-64`

<!-- AUTO-END: aggregate_scores -->
