# 实施任务清单：结构化工具执行结果（Structured Tool Result）

## 第 1 组：Domain 层基础变更

- [x] **T1.1** 在 `src/domain/agent/tools.py` 中定义 `ToolExecutionResult` 值对象（`frozen=True` dataclass，含 `content: str` 和 `metadata: dict[str, Any]`），并修改 `Tool.execute()` 抽象方法签名为 `async execute(**kwargs) -> ToolExecutionResult`。
  - 文件：`src/domain/agent/tools.py`
  - 验证：`ToolExecutionResult` 可正常实例化，`frozen` 不可变，`metadata` 默认空 dict

- [x] **T1.2** 修改 `Tool.run()` 方法返回类型为 `ToolExecutionResult`，透传 `execute()` 的返回值；异常路径仍抛出 `ToolExecutionError`，不构造 `ToolExecutionResult`。
  - 文件：`src/domain/agent/tools.py`
  - 验证：`run()` 正常路径返回 `ToolExecutionResult`，异常路径不变

- [x] **T1.3** 修改 `ToolRegistry.execute()` 和 `ScopedToolRegistry.execute()` 返回类型为 `ToolExecutionResult`，直接透传底层 `tool.run()` 的结果。
  - 文件：`src/domain/agent/tools.py`
  - 验证：两个 `execute()` 方法返回类型正确，权限拒绝仍抛异常

- [x] **T1.4** 在 `src/domain/agent/trace_value_objects.py` 中为 `ToolCallTrace` 新增 `metadata: dict[str, Any]` 字段（默认空 dict），位于 `error_message` 之后、`kind` 之前。
  - 文件：`src/domain/agent/trace_value_objects.py`
  - 验证：`ToolCallTrace` 可正常实例化，含 `metadata`；不传 `metadata` 时默认空 dict

### ✅ Checkpoint 1：Domain 层变更完成
- [x] **CP1** 运行 `PYTHONPATH=src uv run --frozen pytest test/domain/agent/` 确认 domain 层既有测试通过（注意：此时 infrastructure 层工具实现尚未适配，部分集成测试可能失败，仅验证 domain 层自身一致性）。214 passed。

---

## 第 2 组：工具重构

### 2A 执行类工具

- [x] **T2.1** 重构 `ShellExecTool.execute()` 返回 `ToolExecutionResult`，`content` 保持原返回字符串，`metadata` 包含 `command_summary`、`working_dir`、`exit_code`、`stdout_bytes`、`stderr_bytes`、`truncated`。超时路径由 `_execute_tool_call` 处理，工具内部超时抛 `ToolExecutionError` 语义不变。
  - 文件：`src/infrastructure/tools/shell_exec/shell_exec_tool.py`
  - 验证：`execute()` 返回 `ToolExecutionResult`，metadata 各字段类型正确

- [x] **T2.2** 重构 `PythonExecTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `code_summary`、`exit_code`、`stdout_bytes`、`stderr_bytes`、`memory_limited`、`truncated`。
  - 文件：`src/infrastructure/tools/python_exec/python_exec_tool.py`
  - 验证：`execute()` 返回 `ToolExecutionResult`，metadata 各字段类型正确

### 2B 文件类工具

- [x] **T2.3** 重构 `ReadFileTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `logical_path`、`operation="read"`、`line_range`、`lines_returned`。
  - 文件：`src/infrastructure/tools/filesystem/read_file_tool.py`
  - 验证：metadata 中 `logical_path` 为 workspace 相对路径

- [x] **T2.4** 重构 `WriteFileTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `logical_path`、`operation="write"`、`bytes_written`。
  - 文件：`src/infrastructure/tools/filesystem/write_file_tool.py`

- [x] **T2.5** 重构 `EditFileTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `logical_path`、`operation="edit"`、`bytes_written`。
  - 文件：`src/infrastructure/tools/filesystem/edit_file_tool.py`

- [x] **T2.6** 重构 `ListDirTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `logical_path`、`operation="list"`、`recursive`、`entries_count`。
  - 文件：`src/infrastructure/tools/filesystem/list_dir_tool.py`

### 2C Web/HTTP 类工具

- [x] **T2.7** 重构 `WebSearchTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `query`、`result_count`。
  - 文件：`src/infrastructure/tools/web_search/web_search_tool.py`

- [x] **T2.8** 重构 `HttpRequestTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `method`、`url`（截断 256 字符）、`status_code`、`response_bytes`。
  - 文件：`src/infrastructure/tools/http_request/http_request_tool.py`

- [x] **T2.9** 重构 `WebFetchTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `url`（截断 256 字符）、`response_bytes`、`content_type`。
  - 文件：`src/infrastructure/tools/web_fetch/web_fetch_tool.py`

### 2D 委派类工具

- [x] **T2.10** 重构 `DelegateToAgentTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `target_agent`、`success`。
  - 文件：`src/infrastructure/agent/delegate_to_agent_tool.py`

- [x] **T2.11** 重构 `DelegateParallelTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `targets`、`results_count`、`success_count`。
  - 文件：`src/infrastructure/agent/delegate_parallel_tool.py`

- [x] **T2.12** 重构 `HandoffToAgentTool.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `target_agent`、`success`。注意：`HandoffPerformed` 异常仍需抛出，`ToolExecutionResult` 用于正常返回路径或异常前的部分元数据。
  - 文件：`src/infrastructure/agent/handoff_to_agent_tool.py`

### 2E MCP 工具桥

- [x] **T2.13** 重构 `McpToolBridge.execute()` 返回 `ToolExecutionResult`，`metadata` 包含 `mcp_server`、`mcp_tool_name`。
  - 文件：`src/infrastructure/tools/mcp/mcp_tool_bridge.py`

### ✅ Checkpoint 2：全部工具重构完成
- [x] **CP2** 运行 `PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/ test/infrastructure/agent/test_delegate* test/infrastructure/agent/test_handoff*` 确认工具层既有测试通过（需先适配测试中对 `execute()` 返回值的断言）。242 passed。

---

## 第 3 组：ReActAgentAdapter 改造

- [x] **T3.1** 修改 `_execute_tool_call` 方法：返回值类型从 `tuple[str, bool]` 改为 `tuple[ToolExecutionResult, bool]`。正常路径取 `ToolRegistry.execute()` 的 `ToolExecutionResult`；异常路径（权限拒绝、超时、一般异常）构造 `ToolExecutionResult(content=..., metadata={"error_class": ...})`。`ToolMessage.content` 取 `result.content`；checkpoint `after_tool_call` 中 `result` 参数取 `result.content`。
  - 文件：`src/infrastructure/agent/react_agent_adapter.py`
  - 验证：`_execute_tool_call` 返回 `ToolExecutionResult`，`ToolMessage.content` 正确

- [x] **T3.2** 修改 `_record_tool_call_trace` 方法：`result` 参数类型从 `str` 改为 `ToolExecutionResult`；从 `result.metadata` 提取 `error_class`/`error_message`；传入 `ToolCallTrace` 的 `metadata` 字段。新增 `_truncate_metadata` 静态方法（控制 metadata 总序列化大小 ≤ 2KB）。
  - 文件：`src/infrastructure/agent/react_agent_adapter.py`
  - 验证：trace 记录包含 metadata、error_class/error_message

- [x] **T3.3** 适配三个并发工具调度方法：`_dispatch_concurrent_tool_calls`、`_stream_concurrent_tool_progress`、`_events_concurrent_tool_calls`。内部闭包返回类型同步变更，所有 `_record_tool_call_trace` 调用点传入 `ToolExecutionResult` 而非 `str`。
  - 文件：`src/infrastructure/agent/react_agent_adapter.py`
  - 验证：并发工具调度路径下 trace 记录正常

- [x] **T3.4** 新增 `_record_error_trace` 方法，在 `run()` / `resume()` / `run_streaming()` / `run_events()` 四个入口方法的异常处理中调用，记录 Agent Loop 级别非工具异常为 `ErrorTrace`（fire-and-forget，不阻止异常传播）。
  - 文件：`src/infrastructure/agent/react_agent_adapter.py`
  - 验证：模拟异常时 ErrorTrace 被写入 trace store

- [x] **T3.5** 在 `run_streaming` 和 `run_events` 的 `max_rounds==1` 快速路径末尾补录 `ModelCallTrace`。
  - 文件：`src/infrastructure/agent/react_agent_adapter.py`
  - 验证：max_rounds==1 路径下 ModelCallTrace 被写入

### ✅ Checkpoint 3：Adapter 改造完成
- [x] **CP3** 运行 `PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent*` 确认 adapter 层既有测试通过。162 passed（离线执行）；全量回归 2787 passed / 3 skipped / 0 failed。

---

## 第 4 组：存储层适配

- [x] **T4.1** 修改 `LocalFileTraceStoreAdapter._dict_to_step()`：对 `kind=="tool_call"` 分支，在构造 `ToolCallTrace` 前 `pop("metadata", {})` 并传入构造函数，兼容旧 JSONL 数据。
  - 文件：`src/infrastructure/trace/local_file_trace_store_adapter.py`
  - 验证：含/不含 `metadata` 的 JSONL 行均可正确读回

### ✅ Checkpoint 4：存储层适配完成
- [x] **CP4** 运行 `PYTHONPATH=src uv run --frozen pytest test/infrastructure/trace/` 确认 trace 存储层测试通过。10 passed（离线执行）；全量回归 2787 passed / 3 skipped / 0 failed。

---

## 第 5 组：测试补充与全量回归

- [x] **T5.1** 补充 `ToolExecutionResult` 值对象单元测试：frozen 不可变、default metadata、content 赋值、与 `Tool.run()` 的集成。
  - 文件：`test/domain/agent/test_tools_unit.py`（新增，11 用例）

- [x] **T5.2** 补充各工具 metadata 测试：验证每个重构工具的 `execute()` 返回 `ToolExecutionResult` 且 `metadata` 字段名和类型正确。（先检查既有覆盖，仅补齐 python_exec/read_file/shell/list_dir/web_search/mcp/handoff 的字段缺口与全字段集合断言）
  - 文件：各工具对应的测试文件

- [x] **T5.3** 补充 adapter trace 集成测试：验证 `_execute_tool_call` → `_record_tool_call_trace` 路径下 `ToolCallTrace.metadata` 被正确填充；验证 `error_class`/`error_message` 在异常路径下被填充；验证 `ErrorTrace` 在 Agent Loop 异常时被写入。
  - 文件：`test/infrastructure/agent/test_react_agent_trace_unit.py`（扩展，+4 用例）

- [x] **T5.4** 补充 JSONL 兼容测试：含/不含 `metadata` 字段的旧/新 JSONL 行均可正确读写和反序列化。
  - 文件：`test/infrastructure/trace/test_local_file_trace_store_unit.py`（扩展，+3 用例）

### ✅ Checkpoint 5：全量测试通过
- [x] **CP5** 运行 `PYTHONPATH=src uv run --frozen pytest` 确认全量测试通过（0 failures）。全量回归（离线）2814 passed / 3 skipped / 0 failed（较基线 2787 净增 27）。

---

## 第 6 组：文档同步

- [x] **T6.1** 更新 `docs/steering/tool-authoring.md` §2 契约说明：`execute()` 返回类型改为 `ToolExecutionResult`；说明 metadata 的用途和约定。新增 §2.1 说明 content/metadata 语义、snake_case、128/256 字符截断、_truncate_metadata ≈2KB、无宿主绝对路径与脱敏、异常路径由 adapter 统一构造；同步检查清单第 2 步。
  - 文件：`docs/steering/tool-authoring.md`

- [x] **T6.2** 更新 `docs/domain-model.md`：在「工具调用」章节新增 `ToolExecutionResult` 值对象说明（frozen、content=回灌 LLM 文本、metadata=结构化 trace 扩展、位于 domain/agent/tools.py），并将 Tool/ToolRegistry/ScopedToolRegistry 签名改为 `-> ToolExecutionResult`。
  - 文件：`docs/domain-model.md`

- [x] **T6.3** 更新 `docs/tools.md`：说明工具返回类型已变更为 `ToolExecutionResult`，新增 13 个工具的 metadata 字段概述表（严格以源码为准），并同步注册步骤 2。
  - 文件：`docs/tools.md`

- [x] **T6.4** 更新 `docs/architecture.md`：新增「结构化 Agent 追踪（TraceStorePort）」章节，说明 `ToolCallTrace.metadata` 字段、_truncate_metadata 截断、error_class/error_message 填充、JSONL 兼容，以及 ErrorTrace 补录（仅 Agent Loop 级非工具异常）与 max_rounds==1 快速路径 ModelCallTrace 补录。
  - 文件：`docs/architecture.md`

### ✅ Checkpoint 6：文档同步完成
- [x] **CP6** 检查 `docs/steering/tool-authoring.md`、`docs/domain-model.md`、`docs/tools.md`、`docs/architecture.md` 均已同步更新，与代码实际状态一致。生成方自校验通过（逐工具 grep metadata 字面量、核对 adapter trace 逻辑），四文档与源码一致；review-log.md 已记录 doc-only slice 跳过 evaluator 的原因。
