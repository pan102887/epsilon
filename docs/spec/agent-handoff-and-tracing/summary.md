# Agent Handoff & 链路追踪 — 交付总结

## Feature
`agent-handoff-and-tracing`：在不破坏既有 `AgentPort` / `DelegationPort` / `Tool` /
HITL 抽象的前提下，为 Agent 抽象层补齐三项业内主流能力：

- 🔴 **Agent 间 Handoff**（OpenAI Agents SDK 风格的"完全控制转移"）
- 🔴 **Agent Loop OTel 链路嵌套**（每轮 ReAct 迭代作为 child span）
- 🟡 **多 Agent 并行扇出**（LangGraph `Send()` / AutoGen GroupChat 风格的并行 `delegate_parallel`）

## 产出物

### 规范文档
- `docs/spec/agent-handoff-and-tracing/{requirement,design,tasks,summary}.md`

### 源码（13 个文件）

| 文件 | 改动类型 | 摘要 |
|---|---|---|
| `domain/agent/value_objects.py` | 增 | 新增 `DelegationRequest` / `HandoffResult` 值对象 |
| `domain/agent/exceptions.py` | 增 | 新增 `HandoffPerformed` 信号异常（成功信号，非错误） |
| `domain/agent/ports.py` | 改 | `DelegationPort` 增 `delegate_parallel` / `handoff` 方法 |
| `domain/agent/__init__.py` | 改 | 导出新值对象与信号 |
| `domain/chat/context.py` | 改 | `ConversationContext` 增 `append_message(msg)` 通用追加 |
| `infrastructure/agent/handoff_context.py` | 新 | ContextVar 模块，传递父 ConversationContext 给 HandoffTool |
| `infrastructure/agent/handoff_to_agent_tool.py` | 新 | `HandoffToAgentTool`（继承 Tool） |
| `infrastructure/agent/delegate_parallel_tool.py` | 新 | `DelegateParallelTool`（继承 Tool） |
| `infrastructure/agent/delegation_adapter.py` | 改 | 实现 `delegate_parallel`（错误隔离）+ `handoff`（绕过 TaskAgentPort） |
| `infrastructure/agent/round_outcome.py` | 改 | `RoundOutcomeKind` 增 `"handoff"`；增 `handoff_target` / `handoff_content` 字段 |
| `infrastructure/agent/react_agent_adapter.py` | 改 | 模块级 `tracer`；`_iter_rounds` 每轮 OTel span；`_execute_tool_call` 捕获 `HandoffPerformed`；3 个并发入口 ContextVar；3 个执行入口 handoff 分支 |
| `infrastructure/agent/__init__.py` | 改 | 导出新工具与适配器 |
| `application/container_config.py` | 改 | `_create_delegation_adapter` 注入 model_registry / agent_provider / tool_registry_provider；`_register_delegate_tool` 追加 HandoffToAgentTool + DelegateParallelTool |

### 测试（5 个新测试文件，37 个测试用例）

| 文件 | 用例数 | 覆盖内容 |
|---|---|---|
| `test_delegation_adapter_handoff_and_parallel_unit.py` | 11 | `delegate_parallel` 顺序 / 错误隔离 / 深度超限 / 空请求；`handoff` 上下文克隆 / 翻译 / 异常 / max_rounds 失败语义 / 缺依赖 RuntimeError |
| `test_handoff_and_parallel_tools_unit.py` | 15 | `HandoffToAgentTool` 成功抛信号 / 4 类失败返回字符串 / schema / description；`DelegateParallelTool` 聚合格式 / 深度超限 / schema / validate_params 边界 / 端到端 run() |
| `test_react_agent_handoff_unit.py` | 4 | run / run_streaming / run_events 三入口的 handoff 终止形态；不写 `metadata["error"]` |
| `test_react_agent_otel_span_unit.py` | 6 | 每轮 round span / 多轮 / 终止 span 含 terminated_reason / 异常 ERROR / parent-child 嵌套 / NoOpTracer 兜底 |
| `test_delegation_parallel_property.py` | hypothesis 50 examples | 顺序保持 + 错误隔离不变量 |

## 关键设计决策

1. **Handoff 信号用异常承载控制流（非返回值）**：`Tool.execute -> str` 协议无法传递结构化
   控制信号；`HandoffPerformed`（`Exception` 子类）以"成功信号"语义命名，与
   `ToolExecutionError` / `BizException` 分离不视为错误；`ReActAgentAdapter._execute_tool_call`
   显式捕获该信号写入 `ToolMessage.metadata["handoff_target"]`，**不**写入
   `metadata["error"]`。

2. **DelegationAdapter.handoff 直接调用 AgentPort.run**（不走 TaskAgentPort）：
   `TaskAgentAdapter` 会硬编码 `add_user_message(task.goal)`，与 handoff "原样转交父侧
   消息序列"语义冲突；选择 `DelegationAdapter.handoff` 直接组 `ConversationContext` +
   `AgentConfig` 调用 `AgentPort.run`，避免污染父侧消息。

3. **ContextVar 传递父消息快照**：`HandoffToAgentTool` 需要父 `ConversationContext`
   消息列表，但 `Tool.execute(**kwargs)` 接口没有传 context 的入口；选择 `contextvars`
   方案而非修改 Tool ABC 或对 HandoffTool 做 isinstance 特判：
   - `infrastructure/agent/handoff_context.py` 暴露 `set_parent_context(ctx)` /
     `reset_parent_context(token)` / `get_parent_context()`；
   - `ReActAgentAdapter` 的 3 个并发工具调度入口（`_dispatch_concurrent_tool_calls` /
     `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）在入口
     `set_parent_context` + `finally` `reset_parent_context`；
   - 天然适配 `asyncio.gather` 派生协程（contextvars 在 task-local 域内继承）。

4. **OTel span 不可跨 async generator 的 yield**：实现期发现
   `tracer.start_as_current_span(...)` 内部用 contextvars Token 管理"current span 栈"，
   异步生成器在 `yield` 时可能切换 contextvar 上下文，导致 `__exit__` 时
   `Token created in a different Context` ValueError。**最终方案**：
   - 每轮 OTel span 在 `with` 块内**只**完成模型调用 + attribute 写入，退出 `with`
     （span 已结束）后再 `yield RoundOutcome`；
   - `react_agent.terminated` 终止形态 span 用 `with ...: pass` 形态记录 attribute，
     不在 with 块内 yield；
   - 因此 round span **仅覆盖模型调用本身**（含 httpx 自动埋点的 child span），工具
     调用作为兄弟 span 挂在外层（如 `chat.chat`）下。这是与 OTel SDK 默认行为
     兼容的最稳妥模式。

5. **错误隔离用"内部吞异常 + asyncio.gather"模式**：`DelegationAdapter.delegate_parallel`
   每条 request 在 `_one()` 内 try/except 后返回失败 `DelegationResult` 而非抛异常，
   使 `asyncio.gather(...)` 不会因单条失败而短路；与 LangGraph `Send()` 的"map-reduce"
   模式语义一致。

6. **ConversationContext 加 `append_message` 最小补丁**：现有 API 仅暴露按角色分类的
   专用追加方法（`add_system_message` / `add_user_message` / 等），缺少"通用消息追加"。
   `handoff` 上下文克隆需求自然引入此方法；不破坏既有不变量，仅暴露已有
   `_messages.append` 的安全包装。

7. **container_config 用 provider 函数延迟解析规避循环依赖**：`DelegationAdapter.handoff`
   需要 `AgentPort` / `ToolRegistry`，而它们又通过 `DelegateToAgentTool` /
   `DelegateParallelTool` 反向引用 `DelegationPort`，形成循环。解法沿用既有
   "延迟注册"模式：构造期不解析 AgentPort/ToolRegistry，改用两个异步 provider 函数
   在 `handoff` 调用时通过容器懒解析。`HandoffToAgentTool` / `DelegateParallelTool`
   的注册同样推迟到 `_register_delegate_tool` 阶段（与现有 `DelegateToAgentTool` 共用
   `AGENT_DELEGATE_TOOL_ENABLED` 开关）。

## 测试覆盖与回归

- **新增 37 个测试**全部 PASS。
- **回归扫描**：`python -m pytest test/infrastructure/agent test/infrastructure/telemetry
  test/infrastructure/task test/infrastructure/chat -q` → 410 passed。
- **全量回归**：`python -m pytest test -q` → **1616 passed + 3 skipped + 1 failure**。
  唯一失败为 `test_web_search_tool.py::test_format_completeness`（hypothesis 边界
  用例），与本次改动无关，是 `mcp-protocol-adapter` summary 已记录的既有问题。

## 后续可选项（不在本期范围）

- **Handoff 链可视化**：在 OTel span 上增加 `react.handoff_chain`（数组），记录从
  根 Agent 到当前 Agent 的全部 handoff 路径，便于可观测平台还原"转交链"。
- **Handoff 成功率指标**：把 `react_agent.terminated.terminated_reason="handoff"`
  累计为 Prometheus counter，与 `max_rounds` / `token_budget_exceeded` 一同纳入
  Agent 终止原因 dashboard。
- **DelegateParallel 子任务级 OTel span**：当前 `delegate_parallel` 内每条子委派的
  执行 span 由 `task_agent.execute` 自身创建，没有显式"扇出"父 span。可在
  `DelegationAdapter.delegate_parallel` 入口增加一个 `delegate.parallel.fanout`
  span 包裹整批，并把 request 数量、成功数量记为 attribute。
- **Handoff 仅保留必要消息**：当前实现把父侧全部消息整体转交；可参考 OpenAI Agents SDK
  的 `input_filter` 概念，在 `DelegationAdapter.handoff` 增加可选回调过滤消息（如丢弃
  与目标 Agent 无关的工具调用历史）。
- **跨 process / 跨 service 的 Handoff**：当前仅同进程内；扩展到分布式需要持久化
  会话上下文 + 跨服务 trace 透传，与 cloud-sandbox-runtime 主题强相关。
