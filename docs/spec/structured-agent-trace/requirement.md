# 需求文档：Structured Agent Trace — 结构化 Agent 追踪

## 简介

### 背景

`epsilon-boot` 后端在 `agent-adapter-refactor-v3`、`agent-quality-assessment-2`、`llm-and-tool-resilience` 三轮 spec 后，ReAct Agent Loop 已具备完善的工具调用并发、重试、熔断和 HITL 审批机制。当前 OpenTelemetry span 记录了分布式链路信息（HTTP 请求 → ChatService → Agent Loop → LLM 调用），但这些 span 仅在 OTel 后端（Jaeger / Tempo）中可见，不满足以下场景：

1. **本地 coding-agent 操作台审查**：TUI 用户执行一次多轮 Agent 任务后，需要在本地快速回溯"哪个轮次调用了哪个工具、耗时多少、成功还是失败、是否触发审批"，无需部署 Jaeger。
2. **后续 coding workflow 命令前置**：`/status`、`/diff`、`/tests`、`/files` 等 TUI 命令需要读取结构化 trace 来展示会话摘要。
3. **API trace 查询**：Web 控制台前端需要通过 HTTP API 获取 trace 时间线，展示工具事件、审批节点和 artifacts。
4. **测试与评估**：evaluation 体系需要机器可读的 trace 数据来计算工具调用成功率、平均延迟、审批等待时长等 SLO 指标。

当前 `domain/task/value_objects.py` 中的 `TraceEntry` 是 Task 模块专用的简单 dataclass（`step + action + detail + timestamp_ms`），无法表达工具参数、返回值、审批决策、错误上下文和嵌套委派等丰富语义。

### 动机

- **可观测性本地化**：让 coding-agent 用户不依赖外部 OTel 后端即可审查 Agent 执行过程。
- **统一 trace 模型**：Chat、Task、TUI、HTTP 四个入口共用同一套 trace port，避免各入口各记一套。
- **后续 feature 前置**：P0.2（`.epsilon/` 本地 trace 存储）、P0.4（coding workflow 命令）和 P0.5（API trace 查询）都依赖本 spec 产出的领域模型和 port。
- **可评估性**：将 Agent 行为序列化为结构化数据后，evaluation 体系可直接消费。

### 范围（In Scope）

1. **定义领域层 trace 值对象**：`AgentStepTrace`、`ToolCallTrace`、`ApprovalTrace`、`ModelCallTrace`、`SessionTrace`（聚合根级容器）。
2. **定义 `TraceStorePort`**：领域层 Protocol，支持 `append_step`、`get_session_trace`、`list_traces` 三个核心操作。
3. **在 `ReActAgentAdapter` 每轮记录**：模型请求摘要（model、prompt_id、input_tokens、output_tokens）、工具调用（tool_name、arguments 摘要、结果摘要、耗时、成功/失败）、审批中断（approval_id、actions 摘要）和错误（exception class、message）。
4. **实现本地文件 adapter**：`LocalFileTraceStoreAdapter`，持久化到 `.epsilon/traces/{session_id}.jsonl`，每行一个 `AgentStepTrace` JSON。
5. **DI 容器装配**：在 `container_config.py` 中注册 trace store，通过配置决定启用/禁用。
6. **Chat 和 Task 入口共用同一 trace port 实例**（由 DI 容器保证单例）。
7. **配置项**：`TRACE_ENABLED=true`、`TRACE_STORE_DIR=.epsilon/traces` 写入 `config.properties`。

### 非目标（Out of Scope）

1. 不实现 Redis / DB / OSS 等远程 trace store adapter（预留 port 即可）。
2. 不实现 trace 查询 API 端点或 TUI 命令（本 spec 仅提供 port + 本地 adapter + Agent 写入）。
3. 不重写现有 `domain/task/value_objects.TraceEntry`（保持向后兼容，仅在新模块定义新类型）。
4. 不替换 OpenTelemetry span（OTel 保持既有职责，本 trace 是补充而非替代）。
5. 不记录完整 tool arguments / results 明文（须脱敏或截断摘要，防止敏感信息泄露）。
6. 不记录会话完整消息历史（trace 记录行为步骤，不是会话快照）。

## 功能性需求

### R1：领域层 Trace 值对象

- **R1.1** 定义 `ModelCallTrace` frozen dataclass：`round_num: int`、`model: str`、`prompt_id: str`、`input_tokens: int`、`output_tokens: int`、`latency_ms: float`、`timestamp_epoch: float`。
- **R1.2** 定义 `ToolCallTrace` frozen dataclass：`round_num: int`、`tool_name: str`、`tool_call_id: str`、`arguments_summary: str`（≤128 字符截断）、`result_summary: str`（≤256 字符截断）、`success: bool`、`latency_ms: float`、`error_class: str | None`、`error_message: str | None`、`timestamp_epoch: float`。
- **R1.3** 定义 `ApprovalTrace` frozen dataclass：`round_num: int`、`approval_id: str`、`actions_summary: list[str]`（每个 action 的 `tool_name` 列表）、`timestamp_epoch: float`。
- **R1.4** 定义 `ErrorTrace` frozen dataclass：`round_num: int`、`error_class: str`、`error_message: str`（≤512 字符截断）、`timestamp_epoch: float`。
- **R1.5** 定义 `AgentStepTrace` Union 类型：`ModelCallTrace | ToolCallTrace | ApprovalTrace | ErrorTrace`，带一个 `kind` 判别字段（`"model_call" | "tool_call" | "approval" | "error"`）。
- **R1.6** 定义 `SessionTrace` frozen dataclass：`session_id: str`、`started_at_epoch: float`、`steps: list[AgentStepTrace]`、`metadata: dict[str, Any]`。
- **R1.7** 所有 trace 值对象置于 `src/domain/agent/trace_value_objects.py`，不与既有 `value_objects.py` 合并。
- **R1.8** 所有 trace 值对象使用 `frozen=True` 确保不可变性。
- **R1.9** `arguments_summary` 和 `result_summary` 的截断逻辑由工具方传入或由 adapter 内部截断（domain 层定义 max_length 常量但不实现截断）。

### R2：TraceStorePort 端口

- **R2.1** 定义 `TraceStorePort` Protocol，位于 `src/domain/agent/ports.py`。
- **R2.2** 方法 `async append_step(session_id: str, step: AgentStepTrace) -> None`：追加一步到指定 session trace。
- **R2.3** 方法 `async get_session_trace(session_id: str) -> SessionTrace | None`：获取完整 session trace；不存在时返回 None。
- **R2.4** 方法 `async list_traces(limit: int = 20) -> list[SessionTrace]`：按时间倒序列出最近的 session trace（仅元数据 + step count，不含完整 steps）。
- **R2.5** Port 不感知存储介质（本地文件 / Redis / DB），由基础设施层 adapter 实现。

### R3：ReActAgentAdapter 追踪记录

- **R3.1** `ReActAgentAdapter.__init__` 接受可选 `trace_store: TraceStorePort | None` 参数；为 None 时所有追踪操作静默跳过（no-op）。
- **R3.2** 在每轮模型调用完成后记录 `ModelCallTrace`（从 `usage` 和 `latency_ms` 提取）。
- **R3.3** 在每个工具调用完成后记录 `ToolCallTrace`（从 tool_call_id、tool_name、arguments、result、elapsed_ms 提取）。
- **R3.4** 在审批中断产生时记录 `ApprovalTrace`。
- **R3.5** 在 Agent 循环内部异常（非工具异常）时记录 `ErrorTrace`。
- **R3.6** trace 记录操作不得影响 Agent Loop 主流程：任何 trace store 异常都应被捕获、记录到 logger.warning 并跳过，绝不向上冒泡。
- **R3.7** trace 记录在 `run`、`resume`、`run_streaming`、`run_events` 四个入口统一生效。
- **R3.8** trace 记录不增加 Agent Loop 的 async IO 阻塞：本地文件写入使用 append-only + sync IO 包裹在 `asyncio.to_thread` 中，或使用 buffered writer。

### R4：本地文件 Adapter

- **R4.1** 创建 `src/infrastructure/trace/local_file_trace_store_adapter.py`，实现 `TraceStorePort`。
- **R4.2** trace 文件路径为 `{TRACE_STORE_DIR}/{session_id}.jsonl`，每行一个 JSON 编码的 `AgentStepTrace`。
- **R4.3** `append_step` 为 append-only 文件写入，使用 `asyncio.to_thread` 避免阻塞事件循环。
- **R4.4** `get_session_trace` 读取 jsonl 文件，反序列化为 `SessionTrace`。
- **R4.5** `list_traces` 扫描 `TRACE_STORE_DIR` 目录，按文件 mtime 倒序，返回元数据。
- **R4.6** 目录不存在时自动创建；文件读取失败时返回 None 或空列表（不抛异常）。
- **R4.7** JSON 序列化使用标准库 `json`，不引入新依赖。

### R5：DI 容器装配

- **R5.1** 在 `container_config.py` 中新增 `_create_trace_store` 工厂方法。
- **R5.2** 当 `TRACE_ENABLED=true`（默认）时构造 `LocalFileTraceStoreAdapter`；`false` 时返回 None。
- **R5.3** 将 trace_store 注入到 `ReActAgentAdapter` 构造函数。
- **R5.4** Chat 和 Task 入口通过 DI 容器共享同一 trace store 实例。

### R6：配置

- **R6.1** 在 `config.properties` 追加 `TRACE_ENABLED=true` 和 `TRACE_STORE_DIR=.epsilon/traces`。
- **R6.2** 创建 `src/infrastructure/trace/trace_config.py`，`TraceConfig(PropertiesBaseSettings)` 读取上述配置。

## 非功能性需求

- **NFR-1** trace 记录不增加 Agent Loop 每轮延迟超过 5ms（本地 SSD append-only 预期 < 1ms）。
- **NFR-2** trace 记录异常不影响主流程正确性（故障隔离）。
- **NFR-3** trace 文件格式为 JSONL，人类可读、可 grep、可被外部工具消费。
- **NFR-4** 不引入新第三方运行时依赖（仅使用标准库 json + os + pathlib + asyncio）。
- **NFR-5** 所有新增代码覆盖单元测试；`append_step` / `get_session_trace` / `list_traces` 覆盖边界用例。
- **NFR-6** 不破坏既有测试（全量回归 0 新增失败）。

## 已知约束

1. `TRACE_STORE_DIR` 默认为相对路径 `.epsilon/traces`，相对于进程 CWD（本地模式即 workspace root）。
2. 多进程并发写入同一 session trace 文件的场景暂不处理（单进程 + append-only 在 POSIX 下原子写 ≤ PIPE_BUF）。
3. 本 spec 不实现 trace 清理/轮转策略（后续 spec 补充）。
4. `arguments_summary` / `result_summary` 截断为固定长度，不做智能脱敏（后续可增强）。

## 验收标准

- AC1：`ReActAgentAdapter` 执行含工具调用的多轮对话后，`.epsilon/traces/{session_id}.jsonl` 文件存在，内容包含 `model_call` 和 `tool_call` 类型的 trace 步骤。
- AC2：trace 记录中 `tool_call` 步骤的 `tool_name`、`success`、`latency_ms` 字段正确反映工具执行结果。
- AC3：当 `TRACE_ENABLED=false` 时，Agent 执行后无 trace 文件产生，无额外开销。
- AC4：trace store 异常不影响 Agent 正常返回结果。
- AC5：全量回归测试无新增失败。
