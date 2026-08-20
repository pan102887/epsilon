# Spec B — llm-and-tool-resilience 需求文档

## 背景

在《Agent 模块设计质量评估》中识别了三处与业界主流（OpenAI Agents SDK / Resilience4j /
LangChain `MultiServerMCPClient`）相比偏弱的可靠性短板，本 Spec 集中处理：

1. **LLM API 重试/退避（🟡）**：`OpenAICompatibleAdapter` 仅依赖 OpenAI SDK 自带
   `max_retries`，不覆盖 stream 调用、且无 jitter；远端瞬时抖动（429/5xx/网络断流）
   会直接传播到 Agent Loop。
2. **MCP 连接池/session 复用（🟡）**：`MCPTool.execute` 每次调用都 `async with self._client:`
   触发一次会话协商；高频调用下握手开销显著。
3. **工具级 circuit breaker（🟡）**：`Tool.run` 没有熔断保护，远端工具长时间故障会被
   Agent Loop 反复触发同一异常路径，浪费 token、放大下游压力。

附加：在 Spec A 实现复核中发现的 **P3 off-by-one** 已作为 hotfix 一并修复
（`delegation_adapter._one()` 中重复 `+1` 校验导致并行链最大有效深度比单条链少 1）。

## 范围

| 主题 | 含 | 不含 |
|---|---|---|
| LLM 重试/退避 | `ModelAccessPort` 实现层（OpenAI 兼容适配器）chat / stream 两条路径 | 跨提供商 fallback、调度层重试、缓存（属 `model-routing` 主题） |
| MCP session 复用 | `MCPToolBridge` / `MCPTool` 的连接生命周期 | 跨进程连接池、auth token 刷新、stream/elicitation 高级特性 |
| 工具熔断器 | `Tool.run` 装饰器层 + 单工具 in-memory 状态机 | 分布式状态、Prometheus 指标暴露 |

业内对标参考实现（已调研，本 Spec 不直接依赖其源码）：

- **tenacity**（Python 重试事实标准） — `AsyncRetrying` + `wait_random_exponential` + `retry_if_exception_type`。
- **fastmcp Client 引用计数 with**（fastmcp 2.x 起） — 嵌套 `async with` 不重复握手，外层退出关闭。
- **Resilience4j CircuitBreaker**（Java 标杆） — `CLOSED → OPEN（达阈值）→ HALF_OPEN（冷却到期）→ CLOSED/OPEN`。

## 需求清单（EARS）

### R1：LLM API 重试/退避（tenacity）

- **R1.1** When 调用 `OpenAICompatibleAdapter.chat(request)` 时，the system shall 在
  下列瞬时异常下自动重试：`ModelTimeoutError`、`ModelRateLimitError`、
  `ModelConnectionError`；其余异常（`ModelAccessError`、领域异常）not retried。
- **R1.2** While 重试退避策略，the system shall 使用"指数退避 + 随机 jitter"
  （tenacity `wait_random_exponential`），默认 `min=1s, max=30s`。
- **R1.3** While 总尝试次数（含首次），the system shall 配置上限 `LLM_RETRY_ATTEMPTS`
  默认 `3`（即 1 次首发 + 2 次重试）；上限耗尽后抛出最后一次异常的原始类型。
- **R1.4** When 调用 `OpenAICompatibleAdapter.stream(request)` 时，the system shall
  在**首次握手阶段**（`chat.completions.create(...)` 调用前的连接建立 + 头部接收）
  应用同等重试；**已开始 yield 之后的中途断流**（已写出过 `delta_content`）not retried。
  此约束避免向上游回放重复内容造成消息错乱。
- **R1.5** When 重试发生时，the system shall 输出 INFO 级日志，载荷为
  `{provider, model, attempt, exception_type, next_wait_s}`，便于运维定位抖动来源。
- **R1.6** When 配置项 `LLM_RETRY_ATTEMPTS=1` 时，the system shall 完全禁用
  tenacity 包装（无任何延迟开销），保持向下兼容。

### R2：MCP 连接池 / 持久 session 复用

- **R2.1** When `MCPToolBridge` 启动并执行 `discover()` 时，the system shall
  对底层 `fastmcp.Client` 持有一个**外层引用**（`await client.__aenter__()`），
  使后续工具调用嵌套 `async with` 不再重复握手。
- **R2.2** When 调用 `MCPTool.execute(**kwargs)` 时，the system shall **不**主动
  创建独立 session；通过共享 client 直接 `call_tool`，session 由桥接层维持。
- **R2.3** While 应用关闭（容器析构时），the system shall 通过
  `MCPToolBridge.aclose()` 显式 `await client.__aexit__()` 释放底层连接，
  避免事件循环关闭时的"asyncgen GeneratorExit"告警。
- **R2.4** When `discover()` 失败时（远端不可达 / 协议握手失败），the system shall
  保留既有 fail-soft：本 server 整体跳过、不影响其他 server。
- **R2.5** When 持久 session 失败（如远端主动断连）后下次 `call_tool` 抛出连接异常，
  the system shall 不在 `MCPTool.execute` 内重新建立 session（保持范围最小化）；
  通过 R3（熔断器）+ 现有 `_max_retries` 内置退避兜底；**重连恢复留作后续 Feature**。
- **R2.6** While `MCPConfig` 配置面，the system shall 不引入新配置项（保持现有
  `MCP_TIMEOUT` / `MCP_MAX_RETRIES` / `MCP_RETRY_BASE_DELAY` 语义不变）。

### R3：工具级 circuit breaker

- **R3.1** While `Tool.run(request)` 调用前后，the system shall 可选包装一层
  per-tool circuit breaker；启用与否由配置项 `TOOL_CIRCUIT_BREAKER_ENABLED`
  控制，默认 `False`（向下兼容）。
- **R3.2** While 状态机，the system shall 实现三态：
  - `CLOSED`：默认态，所有调用直通；连续失败计数 ≥ `failure_threshold` 时切换到 `OPEN`。
  - `OPEN`：直接抛 `ToolCircuitOpenError`（`ToolExecutionError` 子类）拒绝调用；
    经过 `recovery_timeout` 秒后切换到 `HALF_OPEN`。
  - `HALF_OPEN`：放行**单次**探测调用：成功 → 重置计数到 `CLOSED`；
    失败 → 回到 `OPEN` 重新计时。
- **R3.3** While 失败认定，the system shall 仅对**真实工具失败**计数：
  - 计入：`ToolExecutionError`（非熔断器自身抛出）、`asyncio.TimeoutError`、
    `Exception` 兜底；
  - 不计入：`ToolNotFoundError`、`ToolParameterValidationError`、
    `ToolPermissionDeniedError`、`HandoffPerformed`（控制信号）。
- **R3.4** While 配置项，the system shall 暴露：
  - `TOOL_CB_FAILURE_THRESHOLD` 默认 `5`（达此值 OPEN）；
  - `TOOL_CB_RECOVERY_TIMEOUT_SECONDS` 默认 `30`（OPEN 等多久 HALF_OPEN）；
  - `TOOL_CB_HALF_OPEN_MAX_CALLS` 默认 `1`（HALF_OPEN 探测并发数）。
- **R3.5** When 触发 `ToolCircuitOpenError` 时，the system shall 输出 WARN 日志
  `tool=<name> state=OPEN failures=<n> recovery_in=<seconds>`；
  Agent Loop 收到该错误后按 ToolMessage（`metadata["error"]=True`）回灌给 LLM。
- **R3.6** While 熔断器实例，the system shall 用 **per-tool 单例**（按
  `tool.name` 索引）；同一 Tool 实例的多个并发调用共享同一状态机；不同 Tool
  完全独立。

### R4：Spec A P3 hotfix（已落地）

- **R4.1** `DelegationAdapter._one()` 内深度判定语义已改为
  "`delegation_depth > max_delegation_depth` 即超限"，与 `DelegateToAgentTool` 单条链对齐。
- **R4.2** 受影响测试 `test_delegate_parallel_returns_failure_when_depth_exceeded`
  已更新为 `delegation_depth=4, max_delegation_depth=3` 的场景。

## 非功能需求（NFR）

- **NFR-1** 不破坏既有抽象：`ModelAccessPort` / `Tool` / `ToolRegistry` 协议签名
  保持不变；新行为通过装饰器 / 内部包装实现。
- **NFR-2** 默认行为兼容：所有新配置项默认值要么禁用功能、要么与现有行为等价；
  既有部署不需变更 `config.properties` 即可继续工作。
- **NFR-3** 单测无外部网络：tenacity 重试用 mock 异常驱动；MCP session 用 fastmcp
  in-memory 服务；circuit breaker 纯内存状态机。
- **NFR-4** OTel 不破坏：本 Spec 不修改 OTel span 结构；circuit breaker 拒绝调用
  作为现有 ToolMessage error 路径的一种，沿用既有 `chat.tool` span 属性。
- **NFR-5** 新增依赖最小：仅引入 `tenacity`（成熟、零运行时副作用、约 80KB）。

## 验收标准

- 单测：每条 R1 / R2 / R3 至少 1 个用例覆盖；circuit breaker 状态机
  CLOSED→OPEN→HALF_OPEN→CLOSED 完整路径覆盖。
- 全量回归：`pytest test -q` 在本 Spec 改动后保持先前基线（1616 passed + 3 skipped
  + 1 已知 web_search hypothesis 边界），不引入新失败。
- 端到端验证：保留之前 P3 hotfix 实测脚本输出（`current_depth=0~2 OK / 3+ blocked`
  并行单条一致）。
