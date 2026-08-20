# 需求文档：Structured Tool Result — 结构化工具执行返回值

## 简介

### 背景与动机

`structured-agent-trace` spec 已交付 `ToolCallTrace` 值对象与 `TraceStorePort`，`local-trace-artifacts` spec 已交付 `StorageTier`、`ArtifactStorePort` 与 `ArtifactTrace`。两个 spec 均期望从工具执行结果中提取结构化元数据（如退出码、逻辑路径、操作类型、字节数等）并落入 trace。

然而当前工具基类契约 `Tool.execute(**kwargs) -> str` 只能返回纯文本字符串，导致：

1. `ReActAgentAdapter._record_tool_call_trace()` 中 `result_summary` 只能截断纯文本，结构化信息（exit_code、cwd、bytes_written 等）在截断后丢失，无法写入 `ToolCallTrace`。
2. `ToolCallTrace.error_class` / `error_message` 字段已在值对象中定义，但从未被填充——适配器无法在失败路径捕获异常类信息。
3. `ErrorTrace` 值对象已定义，但 `ReActAgentAdapter` 的 Agent Loop 异常路径（非工具执行异常）从未实例化或写入该值对象。
4. `max_rounds==1` 的快速路径（`run_streaming` / `run_events`）跳过 `ModelCallTrace` 的写入。
5. P0.3 待办（Shell/Python trace 记录命令与退出码、文件工具 trace 记录逻辑路径与操作类型）无法在当前纯文本契约下完成。

本项目未发布，不需要考虑向后兼容性，所有存量工具实现一并重构。

### In Scope

1. 在 domain 层定义 `ToolExecutionResult` 值对象（frozen dataclass），作为 `Tool.execute()` 的统一返回类型。
2. 修改 `Tool.execute()` / `Tool.run()` / `ToolRegistry.execute()` / `ScopedToolRegistry.execute()` 的返回类型为 `ToolExecutionResult`。
3. 重构全部 13 个存量工具实现，使其返回 `ToolExecutionResult` 并填充各自的 `metadata`。
4. 修改 `ReActAgentAdapter._record_tool_call_trace()` 以从 `ToolExecutionResult.metadata` 填充 `ToolCallTrace`，包括已有但未填充的 `error_class` / `error_message`。
5. 为 `ToolCallTrace` 新增 `metadata: dict[str, Any]` 字段。
6. 在 Agent Loop 异常路径补录 `ErrorTrace`（已有值对象，当前零调用点）。
7. 在 `max_rounds==1` 快速路径（`run_streaming` / `run_events`）补录 `ModelCallTrace`。

### Out of Scope

1. 不改变 `ToolMessage.content` 的语义——LLM 上下文中回灌的字符串内容不变。
2. 不修改工具的 JSON Schema（`parameters` 属性）或对外可见的 `description`。
3. 不实现 `ArtifactTrace` 写入（属后续 coding-workflow-commands spec）。
4. 不修改 `TraceStorePort` / `SessionTrace` 的公开接口签名。
5. 不增加 checkpoint 或 Run 存储层的新字段。
6. Trace 查询 API 的 metadata 字段过滤能力（后续 spec）。
7. 前端 trace timeline 展示工具 metadata（后续 spec）。
8. 工具 metadata 的 OTel span 属性双写对齐（后续增强）。

---

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 工具执行返回值 | `Tool_Execution_Result` | 新增的 domain 层 `frozen=True` dataclass，`Tool.execute()` 的统一返回类型，含 `content: str`（回灌给 LLM 的文本，等价于当前 `execute()` 返回的 `str`）与 `metadata: dict[str, Any]`（工具类型特有的结构化元数据，供 trace 记录使用）。 |
| 工具基类 | `Tool_ABC` | domain 层抽象基类 `Tool`（`src/domain/agent/tools.py`），定义工具统一契约。本次修改 `execute()` 与 `run()` 的返回类型为 `Tool_Execution_Result`。 |
| 工具注册表 | `Tool_Registry` | `ToolRegistry`（`src/domain/agent/tools.py`），集中管理工具实例，`execute()` 返回类型随之升级为 `Tool_Execution_Result`。 |
| 作用域工具注册表 | `Scoped_Tool_Registry` | `ScopedToolRegistry`（`src/domain/agent/tools.py`），暴露工具子集，`execute()` 返回类型同步升级。 |
| 工具调用追踪 | `Tool_Call_Trace` | 既有 frozen dataclass `ToolCallTrace`（`src/domain/agent/trace_value_objects.py`），本次新增 `metadata: dict[str, Any]` 字段，并补全 `error_class` / `error_message` 填充逻辑。 |
| 错误追踪 | `Error_Trace` | 既有 frozen dataclass `ErrorTrace`（`src/domain/agent/trace_value_objects.py`），已定义但当前无任何构建/写入代码，本次在 Agent Loop 异常路径完成接入。 |
| 模型调用追踪 | `Model_Call_Trace` | 既有 frozen dataclass `ModelCallTrace`，本次在 `max_rounds==1` 快速路径补录。 |
| ReAct 适配器 | `ReAct_Agent_Adapter` | `ReActAgentAdapter`（`src/infrastructure/agent/react_agent_adapter.py`），本次修改 `_record_tool_call_trace()` 以消费 `Tool_Execution_Result`，并在 Agent Loop 异常路径写入 `Error_Trace`。 |
| trace 元数据 | `Trace_Metadata` | `Tool_Execution_Result.metadata` 与 `Tool_Call_Trace.metadata` 中工具类型特有的结构化字段，值类型为 `dict[str, Any]`。`Any` 值类型为 free-form trace 扩展字段，值异构，非 API 契约字段；各工具须在 docstring 中说明每个键的含义与类型。 |
| 逻辑路径 | `Logical_Path` | 工具层对 LLM 可见的工作区相对路径，不含宿主绝对路径。对齐 `docs/steering/tool-authoring.md` §5 Workspace 边界红线。 |
| Shell 执行工具 | `Shell_Exec_Tool` | `ShellExecTool`（`src/infrastructure/tools/shell_exec/shell_exec_tool.py`）。 |
| Python 执行工具 | `Python_Exec_Tool` | `PythonExecTool`（`src/infrastructure/tools/python_exec/python_exec_tool.py`）。 |
| 文件读取工具 | `Read_File_Tool` | `ReadFileTool`（`src/infrastructure/tools/filesystem/read_file_tool.py`）。 |
| 文件写入工具 | `Write_File_Tool` | `WriteFileTool`（`src/infrastructure/tools/filesystem/write_file_tool.py`）。 |
| 文件编辑工具 | `Edit_File_Tool` | `EditFileTool`（`src/infrastructure/tools/filesystem/edit_file_tool.py`）。 |
| 目录列举工具 | `List_Dir_Tool` | `ListDirTool`（`src/infrastructure/tools/filesystem/list_dir_tool.py`）。 |
| Web 搜索工具 | `Web_Search_Tool` | `WebSearchTool`（`src/infrastructure/tools/web_search/web_search_tool.py`）。 |
| HTTP 请求工具 | `Http_Request_Tool` | `HttpRequestTool`（`src/infrastructure/tools/http_request/http_request_tool.py`）。 |
| Web 抓取工具 | `Web_Fetch_Tool` | `WebFetchTool`（`src/infrastructure/tools/web_fetch/web_fetch_tool.py`）。 |
| 代理委派工具 | `Delegate_To_Agent_Tool` | `DelegateToAgentTool`（`src/infrastructure/agent/delegate_to_agent_tool.py`）。 |
| 并行委派工具 | `Delegate_Parallel_Tool` | `DelegateParallelTool`（`src/infrastructure/agent/delegate_parallel_tool.py`）。 |
| Handoff 工具 | `Handoff_To_Agent_Tool` | `HandoffToAgentTool`（`src/infrastructure/agent/handoff_to_agent_tool.py`）。 |
| MCP 工具桥接 | `Mcp_Tool_Bridge` | `McpToolBridge`（`src/infrastructure/tools/mcp/mcp_tool_bridge.py`）。 |

---

## 需求

### 需求 1：定义 `ToolExecutionResult` 值对象

**用户故事：** 作为平台开发者，我希望工具执行返回值携带结构化元数据，以便 trace 存储能记录 exit_code、逻辑路径、操作类型等信息，而不仅仅是截断的纯文本摘要。

#### 验收标准

1. THE `Tool_Execution_Result` SHALL 定义为 domain 层 `frozen=True` dataclass，位于 `src/domain/agent/tools.py`（与 `Tool_ABC` 同模块）或独立的 `src/domain/agent/tool_result.py`，不得位于 `src/infrastructure/` 下。
2. THE `Tool_Execution_Result` SHALL 包含 `content: str` 字段，语义等价于当前 `Tool_ABC.execute()` 的返回 `str`，即回灌给 LLM 的文本内容。
3. THE `Tool_Execution_Result` SHALL 包含 `metadata: dict[str, Any]` 字段，默认为空字典；docstring 须说明 `Any` 值类型的使用理由（free-form trace 扩展字段，值类型异构，非 API 契约字段）。
4. THE `Tool_Execution_Result` SHALL NOT 导入任何 `src/infrastructure/*` 模块、Web 框架、HTTP SDK 或外部持久化库，满足 DDD 分层依赖方向约束。
5. THE `Tool_Execution_Result` SHALL 具备完整中文 docstring，说明 `content` 与 LLM 上下文的等价关系，以及 `metadata` 的截断约定（`content` 可按 `RESULT_SUMMARY_MAX_LEN` 截断后写入 trace，原始值回灌 LLM）。

### 需求 2：升级 `Tool_ABC` / `Tool_Registry` / `Scoped_Tool_Registry` 契约

**用户故事：** 作为工具开发者，我希望 `Tool.execute()`、`ToolRegistry.execute()` 与 `ScopedToolRegistry.execute()` 的返回类型统一为 `ToolExecutionResult`，以便在实现新工具时有类型约束保证元数据能被传递到 trace，不需要手动维护两套返回类型。

#### 验收标准

1. THE `Tool_ABC` 的抽象方法 `execute` SHALL 签名变更为 `async execute(self, **kwargs: Any) -> ToolExecutionResult`。
2. THE `Tool_ABC` 的 `run` 方法 SHALL 返回类型变更为 `ToolExecutionResult`，并在执行流水线（JSON 解析 → cast_params → validate_params → execute）完成后直接透传 `execute()` 的返回值。
3. WHEN `Tool_ABC.run()` 在内部通用异常捕获分支（`except Exception`）将异常包装为 `ToolExecutionError` 时，THE `Tool_ABC` SHALL 继续抛出异常，不在该分支构造 `Tool_Execution_Result`——异常路径由调用方统一处理。
4. THE `Tool_Registry` 的 `execute` 方法 SHALL 返回类型变更为 `ToolExecutionResult`，内部委托 `Tool_ABC.run()` 并直接透传结果；FOR ALL 异常路径（`ToolNotFoundError`、`ToolParameterValidationError`、`ToolExecutionError`），THE `Tool_Registry` SHALL 继续向上抛出，不捕获为 `Tool_Execution_Result`。
5. THE `Scoped_Tool_Registry` 的 `execute` 方法 SHALL 返回类型变更为 `ToolExecutionResult`，委托底层 `Tool_Registry.execute()` 并直接透传结果；对 `ToolPermissionDeniedError` 的抛出语义不变。

### 需求 3：重构执行类工具（`Shell_Exec_Tool` / `Python_Exec_Tool`）

**用户故事：** 作为运维人员，我希望 Shell/Python 工具的 trace 中包含命令摘要、工作目录、退出码、输出字节数和截断标志，以便按 exit_code 过滤失败命令、按 working_dir 定位执行上下文，而不是从截断的纯文本中手动解析。

#### 验收标准

1. THE `Shell_Exec_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，其中 `content` 等价于当前返回的 shell 输出字符串（含截断标记）；`metadata` SHALL 包含 `command_summary: str`（命令首 128 字符摘要）、`working_dir: str`（工作区相对逻辑路径）、`exit_code: int`、`stdout_bytes: int`（截断前原始字节数）、`stderr_bytes: int`（截断前原始字节数）、`truncated: bool`。
2. THE `Python_Exec_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前返回字符串；`metadata` SHALL 包含 `code_summary: str`（代码首 128 字符摘要）、`exit_code: int`、`stdout_bytes: int`、`stderr_bytes: int`、`memory_limited: bool`（`PYTHON_EXEC_MAX_MEMORY_MB` 配置生效时为 `True`）、`truncated: bool`。
3. WHEN `Shell_Exec_Tool` 或 `Python_Exec_Tool` 的执行因超时终止时，THE `metadata` 的 `exit_code` SHALL 为 `-1`，`truncated` SHALL 为 `True`。
4. FOR ALL 执行类工具，THE `metadata` 中 `working_dir` SHALL 为工作区相对 POSIX 路径，SHALL NOT 包含宿主绝对路径，对齐 `docs/steering/tool-authoring.md` §5 Workspace 边界红线。
5. THE `command_summary` 与 `code_summary` SHALL NOT 包含通过环境变量传递的敏感凭证，与既有敏感词剥离规则（`KEY`/`SECRET`/`PASSWORD`/`TOKEN`/`CREDENTIAL`）一致。

### 需求 4：重构文件系统工具（`Read_File_Tool` / `Write_File_Tool` / `Edit_File_Tool` / `List_Dir_Tool`）

**用户故事：** 作为 trace 查询用户，我希望文件工具的 trace 中包含逻辑路径、操作类型和字节数等结构化字段，以便按路径或操作类型筛选 trace 记录，了解本次会话读写了哪些文件。

#### 验收标准

1. THE `Read_File_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前带行号前缀的文本；`metadata` SHALL 包含 `logical_path: str`、`operation: str`（固定值 `"read"`）、`line_range: list[int]`（`[start, end]` 实际读取行号）、`lines_returned: int`。
2. THE `Write_File_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前成功消息；`metadata` SHALL 包含 `logical_path: str`、`operation: str`（固定值 `"write"`）、`bytes_written: int`。
3. THE `Edit_File_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前成功消息；`metadata` SHALL 包含 `logical_path: str`、`operation: str`（固定值 `"edit"`）、`bytes_written: int`（编辑后文件字节数）。
4. THE `List_Dir_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前目录列表文本；`metadata` SHALL 包含 `logical_path: str`、`operation: str`（固定值 `"list"`）、`recursive: bool`、`entries_count: int`（列举到的条目总数）。
5. FOR ALL 文件系统工具，THE `metadata` 中 `logical_path` SHALL 为工作区相对 POSIX 路径，SHALL NOT 包含宿主绝对路径前缀。

### 需求 5：重构网络工具（`Web_Search_Tool` / `Http_Request_Tool` / `Web_Fetch_Tool`）

**用户故事：** 作为 trace 查询用户，我希望网络工具的 trace 中包含请求目标与响应概要，以便审计网络访问行为并快速定位失败请求。

#### 验收标准

1. THE `Web_Search_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前格式化搜索结果；`metadata` SHALL 包含 `query: str`（查询文本首 128 字符摘要）、`results_count: int`。
2. THE `Http_Request_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前可读文本；`metadata` SHALL 包含 `url: str`（截断至 256 字符）、`status_code: int | None`（网络异常时为 `None`）、`response_bytes: int`（截断前原始字节数）、`truncated: bool`。
3. THE `Web_Fetch_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前抓取文本；`metadata` SHALL 包含 `url: str`（截断至 256 字符）、`status_code: int | None`、`response_bytes: int`、`truncated: bool`。
4. FOR ALL 网络工具，THE `metadata` 中 `url` SHALL NOT 包含认证凭证（如 `http://user:password@...` 形式），须脱敏后记录，与 `docs/steering/tool-authoring.md` §5 红线一致。

### 需求 6：重构委派与 Handoff 工具及 MCP 桥接

**用户故事：** 作为 trace 查询用户，我希望委派工具的 trace 中包含目标 Agent 信息与执行状态，以便追踪多 Agent 协作链路；同时希望 MCP 工具桥的 trace 中包含 MCP server 与工具名称，以便区分本地工具与远程 MCP 工具。

#### 验收标准

1. THE `Delegate_To_Agent_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前子 Agent 最终回复；`metadata` SHALL 包含 `target_agent: str`、`success: bool`（不抛异常视为成功）、`delegation_depth: int`（执行时的递归深度）。
2. THE `Delegate_Parallel_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前格式化并行结果；`metadata` SHALL 包含 `target_agents: list[str]`（所有并行委派的目标 Agent 名称）、`success_count: int`、`total_count: int`。
3. THE `Handoff_To_Agent_Tool` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`；WHILE `Handoff_To_Agent_Tool` IN 正常完成状态，`metadata` SHALL 包含 `target_agent: str`、`success: bool`（固定为 `True`）。当 `HandoffPerformed` 信号由工具内部抛出时，信号携带的 `content` 与 `target_agent` 已被 `ReAct_Agent_Adapter` 直接消费，工具层现有抛出语义不变。
4. THE `Mcp_Tool_Bridge` 的 `execute` 方法 SHALL 返回 `Tool_Execution_Result`，`content` 等价于当前 MCP 调用返回的文本；`metadata` SHALL 至少包含 `mcp_server: str`（MCP server 标识）、`mcp_tool_name: str`（MCP 侧工具名）、`success: bool`。

### 需求 7：`ReAct_Agent_Adapter` 消费 `Tool_Execution_Result` 并升级 trace 写入

**用户故事：** 作为可观测性工程师，我希望 trace 中的 `Tool_Call_Trace` 能携带工具执行的结构化元数据，并在失败时完整记录异常类与消息，以便通过 trace 文件精确定位工具失败原因，不需要重现执行。

#### 验收标准

1. THE `Tool_Call_Trace` 值对象 SHALL 新增 `metadata: dict[str, Any]` 字段，默认为空字典；须有中文 docstring 说明该字段来源（`Tool_Execution_Result.metadata` 透传）与使用 `Any` 的理由。
2. THE `ReAct_Agent_Adapter._record_tool_call_trace()` SHALL 接受 `result: ToolExecutionResult`（替代原 `result: str`），将 `result.content` 用于 `result_summary` 截断（`RESULT_SUMMARY_MAX_LEN`），将 `result.metadata` 直接赋给 `Tool_Call_Trace.metadata`。
3. WHEN 工具执行抛出异常（`is_error=True`）时，THE `ReAct_Agent_Adapter` SHALL 填充 `Tool_Call_Trace.error_class`（`type(exc).__name__`）与 `error_message`（`str(exc)` 截断至 `ERROR_MESSAGE_MAX_LEN`）。
4. WHEN 工具执行成功（`is_error=False`）时，THE `Tool_Call_Trace` 的 `error_class` 与 `error_message` SHALL 保持 `None`。
5. THE `_execute_tool_call()` 中工具成功路径的局部变量 `result` 类型 SHALL 从 `str` 升级为 `ToolExecutionResult`；WHEN 异常捕获分支将 `str(exc)` 作为结果回灌时，THE `ReAct_Agent_Adapter` SHALL 构造 `ToolExecutionResult(content=str(exc), metadata={})` 以保持类型统一。
6. WHEN `ToolMessage.content` 写入 `context.add_tool_result()` 时，THE 写入值 SHALL 为 `result.content`（纯 `str`），保持 LLM 上下文内容与当前行为完全等价。
7. WHEN checkpoint 的 `after_tool_call` 写入 `result` 参数时，THE 写入值 SHALL 为 `result.content`（`str`），保持 checkpoint JSON 序列化兼容性。
8. THE `LocalFileTraceStoreAdapter` 的 `_dict_to_step` 方法 SHALL 兼容含 `metadata` 字段的 JSONL 行，并在旧 JSONL 行缺少该字段时静默回退为空字典（`dict.pop("metadata", {})`）。

### 需求 8：Agent Loop 异常路径补录 `Error_Trace`

**用户故事：** 作为运维人员，我希望 Agent Loop 执行期间的非工具异常（如模型调用失败、上下文构建错误）被记录为 `ErrorTrace`，以便 trace 时间线不遗漏异常事件，可完整审查失败链路。

#### 验收标准

1. WHEN `ReAct_Agent_Adapter._iter_rounds()` 中模型调用或上下文构建抛出非 `_GuardrailApprovalRequired` 异常时，THE `ReAct_Agent_Adapter` SHALL 构造 `Error_Trace` 并通过 `_record_trace()` 写入，然后继续向上传播原始异常，不吞掉异常。
2. THE 构造的 `Error_Trace` SHALL 包含 `round_num`（当前轮次号）、`error_class`（`type(exc).__name__`）、`error_message`（`str(exc)` 截断至 `ERROR_MESSAGE_MAX_LEN`）、`timestamp_epoch`（`time.time()`）。
3. WHEN trace 写入本身失败时，THE `ReAct_Agent_Adapter` SHALL 按 `_record_trace()` 的既有故障隔离语义（捕获异常、记录 `logger.warning`）静默跳过，不影响原始异常的传播。
4. THE `Error_Trace` 补录路径 SHALL NOT 覆盖工具异常记录——工具执行失败仍通过 `Tool_Call_Trace.error_class` / `error_message` 记录；`Error_Trace` 仅用于 Agent Loop 级别的非工具异常。
5. THE 补录逻辑 SHALL 在 `run`、`resume`、`run_streaming`、`run_events` 四个入口均生效，与 `ModelCallTrace` / `ToolCallTrace` 的写入范围一致。

### 需求 9：`max_rounds==1` 快速路径补录 `Model_Call_Trace`

**用户故事：** 作为可观测性工程师，我希望 `max_rounds==1` 的单轮调用路径也能产生 `ModelCallTrace`，以便所有入口的 trace 时间线完整一致，不因快速路径而出现遗漏。

#### 验收标准

1. WHEN `AgentConfig.max_rounds == 1` 且调用路径为 `run_streaming` 或 `run_events` 的快速路径时，THE `ReAct_Agent_Adapter` SHALL 在模型调用完成后写入 `Model_Call_Trace`，与 `_iter_rounds` 多轮路径行为一致。
2. THE 补录的 `Model_Call_Trace` SHALL 包含 `round_num=1`、`model`、`prompt_id`、`input_tokens`、`output_tokens`、`latency_ms`、`timestamp_epoch`，与 `_build_model_call_trace()` 的构造逻辑保持一致。
3. WHEN `trace_store` 为 `None` 时，THE 快速路径 SHALL 静默跳过 `Model_Call_Trace` 写入，与多轮路径的 no-op 语义一致。

---

## 非功能性需求

- **NFR-1** 全量类型标注：`Tool_Execution_Result` 及所有受影响方法须有完整 Python 类型注解，禁裸 `Any`（`metadata: dict[str, Any]` 除外，须在 docstring 说明理由）；满足 `docs/steering/python-typing-lint.md`。
- **NFR-2** 工具执行的 LLM 上下文回灌内容（`ToolMessage.content`）在改造前后必须等价，对 LLM 行为零影响。
- **NFR-3** 改造后全量回归测试（`PYTHONPATH=src uv run --frozen pytest`）无新增失败。
- **NFR-4** 每个被重构的工具须有单元测试覆盖 `execute()` 返回 `Tool_Execution_Result` 且 `metadata` 字段正确，并覆盖至少一个边界条件（如截断、超时、空结果）。
- **NFR-5** `Tool_Execution_Result` 定义于 domain 层，不依赖任何 `src/infrastructure/*` 模块；满足 `docs/steering/ddd-architecture.md` 依赖方向约束。
- **NFR-6** 所有 trace 记录继续保持 fire-and-forget 语义（`_record_trace` 的 `try/except` + `logger.warning`）；`metadata` 序列化失败不中断工具执行或 Agent Loop。
- **NFR-7** 改造后 `docs/tools.md`、`docs/domain-model.md`、`docs/architecture.md` 须同步更新，反映 `Tool.execute()` 返回类型变更与各工具 `metadata` 字段说明；满足 `docs/steering/doc-sync.md`。
- **NFR-8** 因 `Tool.execute()` 契约属架构级变更，须在 `docs/adr/` 补充 ADR 说明决策动机、方案对比与影响面；满足 `docs/steering/adr.md`。
