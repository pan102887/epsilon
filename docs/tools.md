# 工具系统

## 内置工具一览

所有工具实现位于 `epsilon-boot/src/infrastructure/tools/`，均继承 `domain/agent/tools.py` 中的 `Tool` ABC。文件系统与 exec 类工具统一接受 `Workspace` 注入，**不直接**访问宿主 `os` / `pathlib`。

> **工具返回类型**：`Tool.execute()` 已从返回 `str` 升级为返回领域 frozen 值对象 `ToolExecutionResult`（`content: str` + `metadata: dict[str, Any]`，见 [domain-model.md](domain-model.md)）。`content` 等价于原字符串、完整回灌 LLM；`metadata` 为工具特有的结构化 trace 元数据，透传到 `ToolCallTrace.metadata`（见 [architecture.md](architecture.md)），不影响 LLM 可见内容。各工具的 `metadata` 字段概览：

| 工具 | `metadata` 键 |
|---|---|
| `read_file` | `logical_path`、`operation="read"`、`line_range: [start, end]`、`lines_returned` |
| `write_file` | `logical_path`、`operation="write"`、`bytes_written` |
| `edit_file` | `logical_path`、`operation="edit"`、`bytes_written` |
| `list_dir` | `logical_path`、`operation="list"`、`recursive`、`entries_count` |
| `glob` | `operation="glob"`、`pattern`（≤128 字符）、`directory_path`、`match_count`、`truncated` |
| `grep` | `operation="grep"`、`query`（≤128 字符）、`mode`、`directory_path`、`include_pattern`（≤128 字符）、`files_scanned`、`files_skipped`、`matches_returned`、`truncated` |
| `read_many_files` | `operation="read_many_files"`、`requested_file_count`、`files_read`、`files_failed`、`total_lines_returned`、`truncated` |
| `git_status` | `operation="git_status"`、`exit_code`、`stdout_bytes`、`stderr_bytes`、`truncated` |
| `git_diff` | `operation="git_diff"`、`staged`、`file_count`、`exit_code`、`stdout_bytes`、`stderr_bytes`、`truncated` |
| `git_apply_patch` | `operation="git_apply_patch"`、`check_only`、`exit_code`、`patch_bytes`、`stdout_bytes`、`stderr_bytes`、`truncated` |
| `shell_exec` | `command_summary`（≤128 字符）、`working_dir`（工作区相对路径）、`exit_code`、`stdout_bytes`、`stderr_bytes`、`truncated` |
| `python_exec` | `code_summary`（≤128 字符）、`exit_code`、`stdout_bytes`、`stderr_bytes`、`memory_limited`、`truncated` |
| `web_search` | `query`（≤128 字符）、`result_count` |
| `http_request` | `method`、`url`（≤256 字符，脱敏）、`status_code`、`response_bytes` |
| `web_fetch` | `url`（≤256 字符，脱敏）、`response_bytes`、`content_type`（可为 `None`） |
| `delegate_to_agent` | `target_agent`、`success` |
| `delegate_parallel` | `targets: list[str]`、`results_count`、`success_count` |
| `handoff_to_agent` | `target_agent`、`success` |
| MCP 桥接工具（`McpToolBridge`） | `mcp_server`、`mcp_tool_name` |

> `metadata` 键统一 `snake_case`，路径字段仅记工作区相对 POSIX 路径、URL 剥离凭证与敏感查询参数（不含宿主绝对路径）；写入 trace 前由 `ReActAgentAdapter._truncate_metadata` 将单条序列化体积限制在 ≈2KB。约定细节见 [steering/tool-authoring.md](steering/tool-authoring.md) §2.1。

### 文件系统工具（始终注册）

| 工具类 | `name` | 参数 | 说明 |
|---|---|---|---|
| `ReadFileTool` | `read_file` | `file_path`（必填）、`offset`（起始行，默认 1）、`limit`（最大行数，默认 200） | 按行范围读取文件，返回带行号前缀的文本 |
| `WriteFileTool` | `write_file` | `file_path`、`content` | 写入文件，自动创建父目录；存在则覆盖 |
| `EditFileTool` | `edit_file` | `file_path`、`old_str`、`new_str` | 首个匹配替换；精确匹配失败时自动回退为行级去空白模糊匹配（`old_str=""` 由工具层拒绝） |
| `ListDirTool` | `list_dir` | `directory_path`（空串/`"."`/`"/"` 视作工作区根）、`recursive`（默认 `true`） | 递归列出条目，输出路径以工作区根为基准 |
| `GlobTool` | `glob` | `pattern`（必填）、`directory_path`（默认 `/`）、`max_results`（默认 200，1..1000） | 按 POSIX glob pattern 返回稳定排序的工作区文件路径；超过上限追加截断提示 |
| `GrepTool` | `grep` | `query`（必填）、`mode`（`literal`/`regex`，默认 `literal`）、`directory_path`（默认 `/`）、`include_pattern`（默认 `**/*`）、`case_sensitive`（默认 `true`）、`max_matches`（默认 100，1..1000）、`max_files`（默认 2000，1..10000）、`max_line_chars`（默认 300，40..1000） | 在工作区文本文件中搜索 literal 或 regex，返回 `path:line: preview`；不可读、二进制或非 UTF-8 文件跳过并计数 |
| `ReadManyFilesTool` | `read_many_files` | `file_paths`（必填，最多 50 个）、`offset`（默认 1）、`limit`（默认 200，1..1000）、`max_total_chars`（默认 60000，1000..200000） | 批量读取多个文件的受控片段，使用 `===== /path =====` 文件头；单文件错误生成 `[error]` 条目并继续 |

### Git 工具（始终注册）

| 工具类 | `name` | 参数 | 风险 / 副作用 / 重放 | 说明 |
|---|---|---|---|---|
| `GitStatusTool` | `git_status` | `max_chars`（默认 20000，1..200000） | `LOW` / `NONE` / `REPLAY_RESULT` | 固定执行 `git status --short --branch --untracked-files=all`，读取工作区状态 |
| `GitDiffTool` | `git_diff` | `staged`（默认 `false`）、`file_paths`（最多 50 个）、`max_chars`（默认 60000，1..500000） | `LOW` / `NONE` / `REPLAY_RESULT` | 固定执行 `git diff` 或 `git diff --cached`，可带经 Workspace 校验的 pathspec |
| `GitApplyPatchTool` | `git_apply_patch` | `patch`（必填）、`check_only`（默认 `false`）、`max_output_chars`（默认 20000，1..200000） | `HIGH` / `LOCAL_WRITE` / `MANUAL_REVIEW` | 固定执行 `git apply --check -` 或 `git apply -`，patch 通过 stdin 传入；`check_only=false` 会修改工作区 |

> **Workspace 边界**（对齐 `docs/spec/workspace/design.md`）：
>
> - 所有文件系统工具通过注入的 `Workspace`（`domain.workspace.ports.Workspace`）完成 I/O；路径参数均为**工作区相对 POSIX 路径**，解析后不得越出 `WORKSPACE_ROOT`。
> - 工具层不再直接依赖 `os` / `pathlib`；路径归一化由 `WorkspacePolicy` 完成，符号链接逃逸与大小写折叠越界由后端 `SymlinkGuard` / `IdentityGuard` 兜底。
> - 工具的 `description` 在运行期动态拼入 `Workspace.display_root_hint()`，引导 LLM 使用相对路径。
> - `glob`、`grep`、`read_many_files` 为代码检索只读工具，均声明 `ToolRiskLevel.LOW`、`ToolSideEffectLevel.NONE`、`ToolReplayPolicy.REPLAY_RESULT`，默认注册且不新增配置开关。
> - `git_status`、`git_diff`、`git_apply_patch` 为受控 Git 工具，只执行固定 Git 子命令和参数数组，不接受任意 shell 字符串；三者默认注册且不新增配置开关。
> - 字节级实现位于 `infrastructure/workspace/local_filesystem/_common_impl.py`；历史 `common/tools/common_tools.py` 薄壳已删除，避免 `common` 反向依赖 `infrastructure`。

### 网络工具

| 工具类 | `name` | 配置键 | 默认 | 参数 | 说明 |
|---|---|---|---|---|---|
| `HttpRequestTool` | `http_request` | `HTTP_REQUEST_ENABLED` | true | `url` | httpx 请求，HTML → 可读文本（readability-lxml），超时截断 |
| `WebFetchTool` | `web_fetch` | `WEB_FETCH_ENABLED` | true（仅当 `infrastructure.tools.web_fetch.web_fetch_config` 可导入时） | `url` | 网页抓取，支持超时和响应体截断 |
| `WebSearchTool` | `web_search` | `TAVILY_API_KEY` 非空 | 关闭 | `query` | Tavily 搜索，返回格式化结果 |

`HttpRequestTool` 配置：`HTTP_REQUEST_TIMEOUT=30`、`HTTP_REQUEST_MAX_RESPONSE_SIZE=51200`。
`WebFetchTool` 配置键 `WEB_FETCH_TIMEOUT` / `WEB_FETCH_MAX_RESPONSE_SIZE`，注册时按 `web_fetch_config.enabled` 判断（`config.properties` 中若未列出则采用默认值）。
`WebSearchTool` 配置：`TAVILY_API_KEY`、`TAVILY_SEARCH_MAX_RESULTS`（默认 5）。

### 代码执行工具

| 工具类 | `name` | 配置键 | 默认 | 参数 | 说明 |
|---|---|---|---|---|---|
| `ShellExecTool` | `shell_exec` | `SHELL_EXEC_ENABLED` | true（`config.properties` 默认开启；字段安全默认 false） | `command` | 执行 shell 命令，子进程 `cwd` 锁定工作区 |
| `PythonExecTool` | `python_exec` | `PYTHON_EXEC_ENABLED` | true（`config.properties` 默认开启；字段安全默认 false） | `code` | 执行 Python 脚本，AST 白名单 + 子进程 `cwd` 锁定 |

**ShellExecTool 安全机制**：
- 自动选择 `bash -c`（Linux/macOS）或 `powershell -Command`（Windows）
- 剥离包含 `API_KEY` / `PASSWORD` / `SECRET` / `TOKEN` / `CREDENTIAL` 的环境变量
- 子进程 `cwd` 通过 `Workspace.materialize_cwd` 锁定在 `WORKSPACE_ROOT` 内（越界抛 `ToolExecutionError`）
- 超时强制 kill（`SHELL_EXEC_TIMEOUT`，默认 30s）
- 输出截断（`SHELL_EXEC_MAX_OUTPUT_SIZE`，默认 51200）

**PythonExecTool 安全机制**：
- 执行前 AST 静态分析，校验 blocked imports / builtins
- 子进程 `cwd` 通过 `Workspace.materialize_cwd` 锁定在工作区根，临时 `.py` 文件落在 `WORKSPACE_ROOT` 内
- 捕获 stdout/stderr，超时强制终止（`PYTHON_EXEC_TIMEOUT`，默认 30s；`PYTHON_EXEC_MAX_OUTPUT_SIZE` 默认 51200；`PYTHON_EXEC_MAX_MEMORY_MB` 默认 256）
- `PYTHON_EXEC_ALLOWED_MODULES` 控制允许的模块白名单

> **Workspace 能力要求**：当 `Workspace.capabilities().local_materialization = False` 时，`ShellExecTool` / `PythonExecTool` 立即以 `ToolExecutionError("当前工作区后端不支持本地命令执行")` 拒绝（本期 `LocalFilesystemWorkspace` 恒为 `True`）。
>
> **启动期二次校验**：`SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 若配置了工作区外的路径，`configure_container()` 会在 `container.start()` 阶段 fail-fast，提示"请将 `SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 设置到工作区内，或留空使用默认"。
>
> Workspace 抽象的完整设计见 [`spec/workspace/design.md`](spec/workspace/design.md)。

### 委派与 handoff 工具

| 工具类 | `name` | 配置键 | 默认 |
|---|---|---|---|
| `DelegateToAgentTool` | `delegate_to_agent` | `AGENT_DELEGATE_TOOL_ENABLED` | true |
| `HandoffToAgentTool` | `handoff_to_agent` | `AGENT_DELEGATE_TOOL_ENABLED` | true |
| `DelegateParallelTool` | `delegate_parallel` | `AGENT_DELEGATE_TOOL_ENABLED` | true |

调用方式：Agent A 可通过 `delegate_to_agent` 将子任务委派给已注册命名 Agent，通过 `handoff_to_agent` 将控制权交接给目标 Agent，或通过 `delegate_parallel` 并行拆分子任务。三者共享委派开关与最大递归深度（`AGENT_MAX_DELEGATION_DEPTH`，默认 3）。当处于 workflow Run 且 `RUN_WORKFLOW_ROLE_CAPABILITY_ENABLED=true` 时，真实 delegation/handoff 执行前会按当前 active role 校验能力；成功 handoff 会写入 workflow 级 handoff state 与 Run 事件。

## 工具权限隔离（ScopedToolRegistry）

`ToolRegistry.create_scoped_view(tool_names: frozenset)` 创建 `ScopedToolRegistry`，只暴露指定工具子集：

- `get_schemas()` 只返回子集的 schema
- `execute()` 对子集外的工具调用抛出 `ToolPermissionDeniedError`

权限拒绝错误作为 ToolMessage 内容返回（非异常），LLM 可自我纠正。

每个 Agent 通过 `Task.tool_names` 或 `AgentConfig.allowed_tool_names` 指定允许工具集，在 `ReActAgentAdapter` 执行每次 tool_call 前检查。

## HITL 默认审批策略

配置键：`HITL_ENABLED=false`、`HITL_INTERRUPT_ON=`、`HITL_STATE_TTL_SECONDS=3600`。默认关闭；开启后可用 `HITL_INTERRUPT_ON` JSON object 覆盖工具策略，值支持 `true`、`false`、决策数组或 `{"allowed_decisions": [...], "risk_label": "..."}`。

默认敏感工具：

- `write_file`、`edit_file`、`git_apply_patch`、`shell_exec`、`python_exec`、`delegate_to_agent`：默认允许 `approve/reject`。
- `handoff_to_agent`、`delegate_parallel` 与其他工具可通过 `HITL_INTERRUPT_ON` 显式配置审批策略；workflow role capability 开启时还会在真实 delegation/handoff 前执行角色能力校验。
- `http_request`：默认允许 `approve/edit/reject`，用于修改 URL、method、headers 或 body 后再执行。

默认低风险工具：`read_file`、`list_dir`、`glob`、`grep`、`read_many_files`、`git_status`、`git_diff`、`web_fetch`、`web_search`，默认不触发审批。

**安全边界**：HITL 审批发生在工具执行前，不能替代 Workspace 边界、工具权限、参数 schema 校验、网络访问控制、命令沙箱或 OS 权限。

## 工具产物记录与 `ArtifactStorePort`

local-trace-artifacts 交付了任务产物存储抽象 `ArtifactStorePort`（domain）与本地文件实现 `LocalFileArtifactStoreAdapter`（写入 PROJECT tier `.epsilon/artifacts/`，详见 [architecture.md](architecture.md) 的 StorageTier 抽象与 [configuration.md](configuration.md) 的 `ARTIFACT_ENABLED`）。

- **抽象已就位、写入方待接入**：本 spec 只交付 `ArtifactTrace` 值对象、`ArtifactStorePort`、本地文件 adapter 与 DI 装配。**工具/入口把生成文件清单、命令输出摘要等记录为 `ArtifactTrace` 的写入侧接入属后续 spec**（如 coding-workflow-commands），当前工具执行链路尚未产生 artifact 记录。
- **可选注入零行为变化**：`ARTIFACT_ENABLED=false` 时工厂返回 `None`，写入方须静默跳过，与既有 `TraceStorePort` 的可选注入语义一致；不影响任何工具的现有执行与返回。
- **不记录完整敏感内容**：`ArtifactTrace` 只记录逻辑路径、类型、大小/摘要、来源工具与时间戳等元数据，大字段由写入方按截断常量截断，与工具 trace 的脱敏/截断范式一致。

## 注册新工具

1. 在 `infrastructure/tools/<tool_name>/` 继承 `Tool`
2. 实现 `name`、`description`、`parameters`（JSON Schema）和 `async execute(**kwargs) -> ToolExecutionResult`（`content` 回灌 LLM、`metadata` 记 trace，逐键在 docstring 说明）；文件 I/O 工具须通过构造参数接收 `Workspace`
3. 如有功能开关，通过 `PropertiesBaseSettings` 新增配置类
4. 在 `application/container_config.py` 的 `_create_tool_registry()` 中按条件注册
5. 只读 Workspace 检索类工具可不新增配置开关，但仍须显式声明 `LOW` / `NONE` / `REPLAY_RESULT`，并在 `docs/tools.md` 记录参数、metadata keys 与注册行为
