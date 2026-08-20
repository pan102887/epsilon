# Agent Handoff & 链路追踪 — 需求文档

## 背景

业内主流多 Agent 框架已沉淀出三类与本项目相关的能力：

1. **Handoff（控制转移）** — 以 [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/handoffs/)
   为代表：将整个对话控制权从 Agent A 完全交给 Agent B，B 接管后直接面向用户回复，
   而非把结果作为子任务返回给 A。与"以工具方式委派子任务"（本项目现有 `DelegateToAgentTool`）
   形成正交语义。
2. **Agent Loop 链路嵌套** — OpenAI SDK Tracing、LangSmith、AutoGen 均把 Agent 每轮迭代
   作为父 trace 下的 child span，便于在可观测平台（Tempo、Jaeger、Langfuse）上还原
   "推理→工具→观察"循环结构。本项目已初始化 OTel TracerProvider，但 ReAct Agent Loop
   每轮没有显式 span，整条链路对外只有 HTTP 入口 + 自动埋点的 LLM `httpx` 客户端调用，
   缺少"轮次"层级。
3. **多 Agent 并行扇出** — LangGraph `Send()` 原语、AutoGen GroupChat 提供"同步对多个 Agent
   广播子任务并并行收集结果"的能力。本项目 `DelegationPort.delegate(...)` 仅支持单 Agent
   委派，未提供并行扇出的统一入口（虽然单工具内部理论上可手工 `asyncio.gather`，但缺少
   领域级抽象、错误隔离和审计粒度）。

本项目已具备：

- `DelegationPort`（单 Agent 委派）与 `DelegateToAgentTool`（暴露给 LLM 的桥接工具）；
- `ReActAgentAdapter._iter_rounds` 异步生成器统一驱动 4 个执行入口（`run` /
  `run_streaming` / `run_events` / `resume`），是嵌入"每轮 span"最自然的位置；
- OTel 全栈依赖（`opentelemetry-api/sdk/instrumentation-*`）+ `otel_setup.py` 初始化的全局
  TracerProvider，`ChatServiceAdapter` / `TaskAgentAdapter` 已使用
  `trace.get_current_span().set_attribute("prompt.id", ...)` 模式。

## 目标

在不破坏现有 ReAct Loop / DelegationPort / Tool / 审批 / 上下文压缩抽象的前提下，
为 Agent 抽象层补齐**控制转移（Handoff）**、**Agent Loop 链路嵌套（OTel 每轮 span）**、
**多 Agent 并行扇出（delegate_parallel）**三项业内主流能力，统一从工具层暴露给 LLM。

## 需求（EARS 格式）

### R1 — Handoff（完全控制转移）

- **R1.1** 系统应在领域层 `DelegationPort` 协议上新增 `handoff(...)` 方法，
  签名与 `delegate(...)` 类似但**返回新类型 `HandoffResult`**（含 `target_agent` /
  `content` / `success` / `usage`）。当 Agent A 调用 handoff 时，目标 Agent B 应基于
  A 当前会话上下文（消息列表）独立执行 ReAct Loop，并把最终回复作为 handoff 结果。
- **R1.2** 系统应在基础设施层提供 `HandoffToAgentTool`（继承 `Tool`），LLM 通过工具调用
  `handoff_to_agent` 触发控制转移；工具内部委派给 `DelegationPort.handoff(...)`。
- **R1.3** 当 `HandoffToAgentTool` 成功调用后，**当前 Agent Loop 应立即终止**，
  目标 Agent 的最终回复直接作为父 Agent `AgentResult.content` 返回，**不再发起下一轮
  LLM 调用**。这是 handoff 与 delegate 的关键差异：delegate 把结果作为 ToolMessage
  回灌给父 Agent 继续推理，handoff 则跳过下一轮。
- **R1.4** Handoff 必须遵守 `delegation_depth` / `max_delegation_depth` 不变量：
  超过最大深度时抛 `DelegationDepthExceededError`，与现有 `delegate` 行为一致。
- **R1.5** 当目标 Agent 在注册表中不存在时，`HandoffToAgentTool.execute` 应返回错误信息
  字符串（与 `DelegateToAgentTool` 错误处理一致），由 LLM 自我纠正；不抛异常。
- **R1.6** Handoff 不得自动开启对父会话上下文的写回。父 `ConversationContext` 仅追加：
  - 一条 `AssistantMessage`（携带 handoff 工具调用 tool_calls）；
  - 一条 `ToolMessage`（content = 目标 Agent 最终回复，`metadata["handoff_target"]`
    = 目标 Agent 名称）。
  目标 Agent 自己的工具调用细节不回灌到父上下文，以保持父侧上下文整洁。

### R2 — Agent Loop OTel 链路嵌套

- **R2.1** 系统应在 `ReActAgentAdapter` 模块顶部通过 `opentelemetry.trace.get_tracer(__name__)`
  获取 tracer 实例。
- **R2.2** `_iter_rounds` 每一轮迭代必须在 `tracer.start_as_current_span("react_agent.round", ...)`
  上下文管理器内执行，使每轮都成为当前活跃 span 的 child span，从而在跨进程链路中可见
  "推理→工具→观察"层级。
- **R2.3** 每轮 span 应至少记录以下属性：
  - `react.round_num`（int）
  - `react.tool_call_count`（int，本轮 LLM 返回的 tool_calls 数量）
  - `react.has_tool_calls`（bool）
  - `gen_ai.usage.prompt_tokens` / `gen_ai.usage.completion_tokens` /
    `gen_ai.usage.total_tokens`（int，本轮模型 usage；缺失则不写）
  - `react.terminated_reason`（仅 `kind == "final"` 时写，取
    `terminated_reason` 字段）
  - 当 `kind == "approval"` 时：`react.approval_required = True`
- **R2.4** 当模型调用或工具执行抛异常时，本轮 span 必须调用 `span.record_exception(exc)`
  并设置 `span.set_status(Status(StatusCode.ERROR))`，使 OTel 后端能识别失败轮次。
- **R2.5** 当 OTel 未启用（`OTEL_ENABLED=false`）时，仍可调用
  `trace.get_tracer(...).start_as_current_span(...)`：默认全局 TracerProvider 是 no-op，
  调用零开销且不影响功能。本需求**不**新增条件分支，依赖 OTel SDK 默认行为。

### R3 — 多 Agent 并行扇出

- **R3.1** 系统应在 `DelegationPort` 协议上新增 `delegate_parallel(...)` 方法，
  接受一个 `DelegationRequest` 列表（含 `agent_name` / `task_goal` / `input_data`），
  并发执行所有委派并返回 `list[DelegationResult]`，顺序与输入 requests 一致。
- **R3.2** 并行委派必须实现**错误隔离**：单个子委派失败（包括目标 Agent 未注册、
  执行异常、超时）只影响对应位置的 `DelegationResult.success=False`，
  其余子委派继续执行，不中断整批。
- **R3.3** 并行委派的每个子任务应独立递增 `delegation_depth`，每个子任务都需校验
  `delegation_depth + 1 <= max_delegation_depth`；超限的子任务返回
  `DelegationResult(success=False, content="委派深度超限...")`，不抛异常。
- **R3.4** 系统应提供 `DelegateParallelTool`（继承 `Tool`），LLM 可通过工具调用
  `delegate_parallel` 一次性派发多个子任务并获得聚合结果（按输入顺序拼接为可读文本）。
- **R3.5** 并行委派工具的注册条件复用 `AGENT_DELEGATE_TOOL_ENABLED`：与
  `DelegateToAgentTool` 一同启用或禁用，配置语义保持向后兼容；不引入新的开关键。

## 非功能需求

- **NFR-1** 不破坏现有 `AgentPort` / `DelegationPort` / `Tool` / `ToolRegistry` 抽象与既有
  Adapter 实现。`DelegationPort.delegate(...)` 签名保持不变，新增方法以可选方式追加；
  Protocol 不可继承默认实现，新增方法属于协议契约的扩展，所有 `DelegationPort` 实现者
  都必须显式实现新方法（本项目当前仅 `DelegationAdapter` 一个实现）。
- **NFR-2** 遵循项目 DDD 分层：值对象（`HandoffResult` / `DelegationRequest`）置于
  `domain/agent/value_objects.py`；信号异常 `HandoffPerformed` 置于
  `domain/agent/exceptions.py`；新增 `Tool` 子类置于 `infrastructure/agent/`。
- **NFR-3** 单测不得依赖真实网络或真实 OTel 后端。OTel 验证使用
  `opentelemetry.sdk.trace.export.in_memory_span_exporter.InMemorySpanExporter`
  收集 span 后断言。
- **NFR-4** Handoff 与并行 delegate 必须遵守现有审批流程不变量：当目标 Agent 内部触发
  HITL 审批中断时，子 Agent 自行返回 `approval_required` 状态，父 Agent 把该状态作为
  错误信息字符串回灌（不向上传播 ApprovalInterrupt）。这与现有 `DelegateToAgentTool`
  对子 Agent `TaskResult(status=FAILED)` 的处理方式一致。
- **NFR-5** 新增 OTel span 在禁用 OTel 时必须零额外延迟（< 1μs/轮）。借助 OTel SDK
  默认 `NoOpTracer` 行为达成；不引入条件 `if otel_config.enabled` 分支。

## 范围

- 本期聚焦 Spec A 三项能力：Handoff / OTel 嵌套 / 并行 delegate。
- **不包括**：Plan-and-Execute Adapter（Spec C）、LLM 重试退避（Spec B）、MCP 连接池
  （Spec B）、工具熔断器（Spec B）。
- **不包括**：跨 process / 跨 service 的 Handoff（仅同进程内 Agent 间转移）；
  分布式 trace 上下文从 HTTP 入口透传已由 `OTEL_INSTRUMENT_FASTAPI=true` 自动埋点完成，
  本期不再扩展。
