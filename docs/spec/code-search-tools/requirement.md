# 需求文档：Code Search Tools

## 简介

`docs/research/tools.md` 将 `glob`、`grep`、`read_many_files` 识别为追赶主流 coding agent 的优先工具缺口。本项目当前已有 `read_file`、`write_file`、`edit_file`、`list_dir` 等 Workspace 文件工具，但缺少面向代码库理解的路径模式匹配、内容检索和批量读取能力，导致 Agent 只能通过递归列目录与单文件读取低效探索代码库，或退回到高风险的 `shell_exec`。

本特性在既有工具系统中补齐三个低风险、只读、Workspace 边界内的代码检索工具：`glob`、`grep`、`read_many_files`。三者作为基础设施工具注册到 `ToolRegistry`，通过注入的 `Workspace` 访问文件，不直接访问宿主 `os` / `pathlib` / `open`，并返回 `ToolExecutionResult(content, metadata)`，使结果可回灌 LLM 且可被 trace 结构化记录。

本特性范围仅限上述三个读类工具、注册、测试与文档同步。不包含 LSP / symbol search、语义搜索、Git 工具、PR / CI 集成、浏览器工具、sandbox backend、动态工具加载窗口或 MCP connector 管理。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 工作区 | Workspace | `domain.workspace.ports.Workspace` 定义的文件 I/O 边界，所有路径均解析为工作区相对 POSIX 逻辑路径。 |
| 工具注册表 | Tool_Registry | `ToolRegistry`，集中管理可供 Agent 调用的工具，并支持 `ScopedToolRegistry` 权限视图。 |
| 路径匹配工具 | Glob_Tool | 新增 `glob` 工具，按路径模式查找工作区内文件路径。 |
| 内容搜索工具 | Grep_Tool | 新增 `grep` 工具，在工作区文本文件中执行内容搜索。 |
| 批量读取工具 | Read_Many_Files_Tool | 新增 `read_many_files` 工具，一次读取多个工作区文本文件的受控内容。 |
| 工具执行结果 | Tool_Execution_Result | `ToolExecutionResult`，包含 LLM 可见 `content` 与 trace 用 `metadata`。 |
| 工具元数据 | Tool_Metadata | `Tool_Execution_Result.metadata` 中记录的结构化字段，只用于 trace，不回灌 LLM。 |
| 输出限制 | Output_Limit | 工具对匹配数量、文件数量、单文件行数和总输出体积施加的上限。 |
| 工具文档 | Tool_Documentation | `docs/tools.md` 中对内置工具、参数、注册条件、metadata 和风险策略的当前状态说明。 |

## 需求

### 需求 1：提供路径模式匹配工具

**用户故事：** 作为 coding agent，我希望通过路径模式快速定位候选文件，以便在读取文件前缩小代码库探索范围。

#### 验收标准

1. THE Glob_Tool SHALL expose tool name `glob` with an English description and JSON Schema parameters for a workspace-relative POSIX pattern.
2. WHEN Glob_Tool receives a valid pattern, THE Glob_Tool SHALL return matching file paths inside Workspace in deterministic sorted POSIX form.
3. WHEN Glob_Tool receives a pattern that would traverse outside Workspace, THE Glob_Tool SHALL reject the request before returning any path.
4. THE Glob_Tool SHALL apply Output_Limit to the number of returned paths and indicate truncation in both content and Tool_Metadata when the limit is exceeded.
5. THE Glob_Tool SHALL declare low-risk, no-side-effect, replayable semantics consistent with a read-only Workspace tool.

### 需求 2：提供内容搜索工具

**用户故事：** 作为 coding agent，我希望在工作区文本文件中搜索关键词或正则表达式，以便快速找到相关函数、类、配置项和文档位置。

#### 验收标准

1. THE Grep_Tool SHALL expose tool name `grep` with English description and JSON Schema parameters for query, search mode, path scope, and output limits.
2. WHEN Grep_Tool runs in literal mode, THE Grep_Tool SHALL match query text without treating it as a regular expression.
3. WHEN Grep_Tool runs in regex mode, THE Grep_Tool SHALL validate the regular expression and return a Tool execution error for invalid regex before scanning files.
4. WHEN Grep_Tool finds matches, THE Grep_Tool SHALL return workspace-relative file path, line number, and a bounded line preview for each match.
5. THE Grep_Tool SHALL skip unreadable, binary, or non-UTF-8 files without leaking host absolute paths or internal backend details.
6. THE Grep_Tool SHALL apply Output_Limit to match count and returned content size, and SHALL reflect truncation in Tool_Metadata.
7. THE Grep_Tool SHALL declare low-risk, no-side-effect, replayable semantics consistent with a read-only Workspace tool.

### 需求 3：提供批量读取工具

**用户故事：** 作为 coding agent，我希望一次读取多个候选文件的受控片段，以便在一次工具调用中获得足够上下文。

#### 验收标准

1. THE Read_Many_Files_Tool SHALL expose tool name `read_many_files` with English description and JSON Schema parameters for file paths, per-file line range, per-file line limit, and total output limit.
2. WHEN Read_Many_Files_Tool receives valid file paths, THE Read_Many_Files_Tool SHALL read each requested file through Workspace and render each file with a clear workspace-relative file header.
3. WHEN one requested file is missing or unreadable, THE Read_Many_Files_Tool SHALL include a per-file error entry while continuing to process remaining requested files.
4. WHEN any requested path escapes Workspace, THE Read_Many_Files_Tool SHALL reject that path without leaking host absolute paths.
5. THE Read_Many_Files_Tool SHALL apply Output_Limit to file count, per-file lines, and total returned content, and SHALL reflect truncation in Tool_Metadata.
6. THE Read_Many_Files_Tool SHALL declare low-risk, no-side-effect, replayable semantics consistent with a read-only Workspace tool.

### 需求 4：保持 Workspace 与分层安全边界

**用户故事：** 作为平台维护者，我希望新增检索工具遵守现有 Workspace 和 DDD 分层边界，以便不扩大文件系统访问面。

#### 验收标准

1. THE Glob_Tool SHALL access filesystem data only through injected Workspace operations.
2. THE Grep_Tool SHALL access filesystem data only through injected Workspace operations.
3. THE Read_Many_Files_Tool SHALL access filesystem data only through injected Workspace operations.
4. THE Glob_Tool SHALL NOT import or call host filesystem APIs directly in its tool implementation.
5. THE Grep_Tool SHALL NOT import or call host filesystem APIs directly in its tool implementation.
6. THE Read_Many_Files_Tool SHALL NOT import or call host filesystem APIs directly in its tool implementation.
7. THE Tool_Registry SHALL register these tools from the composition root without introducing domain-to-infrastructure dependencies.

### 需求 5：提供结构化 metadata 与可追踪输出

**用户故事：** 作为运维和调试人员，我希望新增工具输出结构化 metadata，以便在 trace 中理解 Agent 的代码检索行为。

#### 验收标准

1. THE Glob_Tool SHALL include Tool_Metadata fields for operation, pattern summary, match count, and truncation state.
2. THE Grep_Tool SHALL include Tool_Metadata fields for operation, query summary, search mode, files scanned, matches returned, and truncation state.
3. THE Read_Many_Files_Tool SHALL include Tool_Metadata fields for operation, requested file count, files read, files failed, total lines returned, and truncation state.
4. THE Tool_Metadata SHALL use snake_case keys and SHALL NOT contain host absolute paths, credentials, full file content, or unbounded query text.
5. THE Tool_Execution_Result content SHALL remain LLM-friendly, deterministic, and bounded by Output_Limit.

### 需求 6：完成注册、测试和文档同步

**用户故事：** 作为平台维护者，我希望这些工具具备测试和文档覆盖，以便后续 Agent 能稳定使用并维护它们。

#### 验收标准

1. THE Tool_Registry SHALL include Glob_Tool, Grep_Tool, and Read_Many_Files_Tool in the default built-in tool set when their modules are importable.
2. THE Glob_Tool SHALL have offline deterministic unit tests for normal matches, no matches, workspace boundary rejection, output truncation, metadata, and risk semantics.
3. THE Grep_Tool SHALL have offline deterministic unit tests for literal search, regex search, invalid regex, unreadable or binary file handling, output truncation, metadata, and risk semantics.
4. THE Read_Many_Files_Tool SHALL have offline deterministic unit tests for multi-file success, partial per-file failure, workspace boundary rejection, output truncation, metadata, and risk semantics.
5. THE Tool_Documentation SHALL document the three new tools, their parameters, metadata keys, risk level, side-effect level, replay policy, and registration behavior.
6. THE implementation SHALL pass the relevant backend unit tests and introduce no new ruff or pyright violations.
