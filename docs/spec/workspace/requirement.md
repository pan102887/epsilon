# 需求文档：Workspace 工作区抽象与本地文件系统实现

## 简介

### 背景

当前 `epsilon-boot` 后端服务启动后，Agent 通过内置工具可以读写宿主机上的**任意路径**：

- `ReadFileTool` / `WriteFileTool` / `EditFileTool` / `ListDirTool`（`src/infrastructure/tools/filesystem/`）接受任意绝对或相对路径，底层直接调用 `common/tools/common_tools.py` 中的 `read_file` / `write_file` / `edit_file` / `tree`，未做路径边界校验。
- `ShellExecTool`（`src/infrastructure/tools/shell_exec/shell_exec_tool.py`）虽然默认工作目录落在 `<tempdir>/agent_exec/`，但参数 `working_dir` 可被 LLM 任意指定，`bash -c` / `powershell -Command` 又能用 `cd` 逃出任意目录。
- `PythonExecTool`（`src/infrastructure/tools/python_exec/python_exec_tool.py`）子进程的 `cwd` 默认为 `<tempdir>/python_exec/`，但白名单模块（如 `io`、`csv`）配合 AST 分析放行的 `Path(...).read_bytes()` 等用法仍可在进程文件系统权限范围内读写任意位置。

这与实际部署安全需求不符：生产 Pod 中 Agent 进程对文件系统的能力应被限制在一个**明确的、可配置的逻辑工作区**之内。

### 动机

本特性希望解决两个互相耦合的问题：

1. **边界收敛**：将 Agent 可访问的存储区域收敛到一个配置化的逻辑工作区，所有文件类与命令类工具在执行前必须把请求路径解析并校验到工作区之内；越界必须被**拒绝并以 LLM 可读的错误消息返回**，复用现有 `ToolExecutionError` 机制保持 LLM 自我纠错能力。
2. **存储无关抽象**：Workspace 在未来可能需要支撑多种存储后端（本期基于本地文件系统，后续可能新增基于 OSS 对象存储的实现）。因此 Workspace 的领域抽象必须**与具体存储介质解耦**，工具层仅依赖抽象能力，后端切换对工具层透明。

本期以"抽象优先、只实现本地文件系统后端"为原则：对外建立稳定的、存储无关的 Port 接口与逻辑路径语义，对内仅落地 `Local_Filesystem_Workspace` 一个具体后端，并为后续的 `Oss_Object_Storage_Workspace` 预留扩展位置与语义空间。

### 范围

**纳入（In Scope）**：

- 在领域层定义存储无关的 Workspace 抽象接口（Port）、逻辑路径类型、Workspace_Policy 与领域错误类型。
- 实现一个基于本地文件系统的 Workspace 后端 `Local_Filesystem_Workspace`（位于基础设施层）。
- 将所有已注册的受控工具（`ReadFileTool`、`WriteFileTool`、`EditFileTool`、`ListDirTool`、`ShellExecTool`、`PythonExecTool`）改为通过注入的 Workspace 抽象完成 I/O；工具层**不感知**具体后端。
- Workspace_Root 的配置声明、启动期校验、fail-fast 启动失败语义（针对本期启用的 `local_filesystem` 后端）。
- 路径解析语义：所有 LLM 可见路径均为以工作区根为锚点的**逻辑路径**；受控工具不接触宿主绝对路径或 OSS `bucket+key`。
- 后端能力声明机制：接口须能表达"本后端是否支持符号链接 / 原子写 / 追加 / 流式 / 大文件"，供工具层做优雅降级或拒绝。
- 受控执行工具（Shell / Python）与非本地后端的兼容性规则（见需求 6）。
- 越界事件的结构化日志与可诊断性。
- DI 容器装配、`ScopedToolRegistry` 权限模型、向后兼容。

**不在本期范围（Out of Scope）**：

- **OSS 后端实现本身**：不落地任何 `Oss_Object_Storage_Workspace` 代码；但接口必须为 OSS 预留扩展点（如流式读写、大文件分片、最终一致性语义、不支持符号链接 / 原子写的后端差异、`bucket+key` 物理定位等）。占位位置约定在 `infrastructure/workspace/oss/`。
- 多租户 / 每会话 / 每 Agent 的独立子工作区（v1 仅单一全局根）。
- 前端 `epsilon-client/` 的任何改动（前端不直接访问宿主文件系统）。
- 对 `epsilon-boot/archive_docs/` 中历史归档实现的反向兼容改造。
- 对 `HttpRequestTool`、`WebFetchTool`、`WebSearchTool` 等纯网络工具施加 Workspace 边界（它们不访问本地文件系统）。
- 对 Workspace 之外的只读挂载目录（如系统证书、`/etc/resolv.conf`）做白名单豁免。
- Workspace 内部的更细粒度 ACL（例如对子目录的读/写分离）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 工作区 | `Workspace` | 代表一个 Agent 可操作的"逻辑工作区"的领域抽象（Port）。对工具层暴露与存储介质无关的操作语义（路径解析、存在性判定、读、写、编辑、列表、元数据、删除），不暴露任何宿主绝对路径或 `bucket+key`。是受控工具与底层存储之间的唯一通道。 |
| 工作区后端 | `Workspace_Backend` | `Workspace` 的具体实现，负责把领域层的逻辑操作翻译为某种具体存储介质的 I/O。本期仅存在一个实现 `Local_Filesystem_Workspace`；未来预留 `Oss_Object_Storage_Workspace` 等实现。 |
| 本地文件系统后端 | `Local_Filesystem_Workspace` | 基于本地文件系统实现的 `Workspace_Backend`，位于 `infrastructure/workspace/local_filesystem/`。把逻辑路径映射到以 Workspace_Root 为前缀的宿主绝对路径，并直接使用标准 `os` / `pathlib` 原语执行 I/O。 |
| OSS 后端（占位） | `Oss_Object_Storage_Workspace` | 基于 OSS 对象存储实现的 `Workspace_Backend` 的预留名称。**本期不实现**，仅在接口与需求层保留其兼容语义（流式、分片、最终一致性、无符号链接、无原子 rename 等）。 |
| 工作区根 | `Workspace_Root` | 由配置项 `WORKSPACE_ROOT` 指定的、Agent 文件影响面被限制在其中的逻辑根。对 `Local_Filesystem_Workspace` 而言解析为规范化的宿主绝对目录路径；对未来的 `Oss_Object_Storage_Workspace` 而言对应某个 `bucket` 下的某个 key 前缀。进程生命周期内不可变。 |
| 工作区策略 | `Workspace_Policy` | 封装逻辑路径准入规则的纯领域对象（位于 `domain/workspace/`），提供"把一个输入字符串解析并校验为合法逻辑路径"的纯函数式能力，不依赖任何基础设施，不触发 I/O。 |
| 逻辑路径 | `Workspace_Path` | 以 POSIX 正斜杠 `/` 为分隔符、总是以 Workspace_Root 为锚点表达的路径。LLM 可见的、工具参数中传入的、工具返回消息中呈现的所有路径都属于此类型。 |
| 入参路径 | `Requested_Path` | LLM / 工具调用方提供的原始路径字符串，可能是相对形式、绝对形式、含 `..`、含符号链接、Windows 盘符、UNC 等任意形态；必须经 `Workspace_Policy` 规范化为 `Workspace_Path` 后才能交给 `Workspace`。 |
| 解析路径 | `Resolved_Workspace_Path` | `Requested_Path` 经过"锚定到工作区根 → `..` 归一化 → POSIX 斜杠统一 → 非法字符拒绝"后得到的、可用于比较与下发的规范化 `Workspace_Path`。 |
| 物理定位 | `Backend_Location` | `Workspace_Backend` 内部使用的、面向具体存储介质的物理位置表示（本地文件系统下为宿主绝对路径；OSS 下为 `bucket+key`）。**不暴露给工具层**，仅由具体后端在自身内部使用。 |
| 越界违规 | `Workspace_Confinement_Violation` | `Resolved_Workspace_Path` 解析失败或逃出工作区边界的领域错误。与具体后端无关。 |
| 工作区未找到错误 | `Workspace_Not_Found_Error` | 请求的 `Workspace_Path` 在后端不存在的领域错误。由后端把底层"文件不存在 / 对象不存在"翻译得到。 |
| 工作区 I/O 错误 | `Workspace_Io_Error` | 后端执行 I/O 时发生的底层错误（权限不足、网络失败、磁盘满等）的统一领域错误。 |
| 不支持的操作错误 | `Workspace_Unsupported_Operation_Error` | 工具层请求了当前后端声明**不支持**的能力（例如对不支持原子写或符号链接的后端请求了相应操作）时抛出的领域错误。 |
| 工作区能力描述 | `Workspace_Capabilities` | `Workspace` 在运行期暴露的能力描述对象，至少包含"是否支持符号链接 / 原子写 / 追加写 / 流式读写 / 大文件 / 本地物化"等布尔或枚举字段。工具层以此做优雅降级或拒绝。 |
| 本地物化能力 | `Local_Materialization_Capability` | `Workspace_Capabilities` 中的一项：后端是否能保证某个 `Workspace_Path` 在宿主本地文件系统上存在一个可被子进程作为 `cwd` 使用的真实目录。`Local_Filesystem_Workspace` 天然具备；`Oss_Object_Storage_Workspace` 默认不具备（除非提供临时物化机制）。 |
| 工作区配置 | `Workspace_Config` | 基于 `PropertiesBaseSettings` 的配置类（`env_prefix="WORKSPACE_"`），承载 `backend`、`root`、`follow_symlinks`、`create_if_missing` 等字段，通过 `create_config(...)` 实例化。 |
| 工作区后端标识 | `Workspace_Backend_Kind` | 配置字段 `WORKSPACE_BACKEND` 的取值枚举。本期仅允许 `local_filesystem`；为未来的 `oss` 等取值预留扩展位置。 |
| 受控文件工具 | `Guarded_Filesystem_Tool` | `ReadFileTool`、`WriteFileTool`、`EditFileTool`、`ListDirTool` 这四个文件系统工具的统称，本期改为通过注入的 `Workspace` 完成 I/O。 |
| 受控执行工具 | `Guarded_Exec_Tool` | `ShellExecTool`、`PythonExecTool` 这两个命令/脚本执行工具的统称，其子进程 `cwd` 必须锁定在工作区之内；对不具备 `Local_Materialization_Capability` 的后端，其可用性由需求 6 明确规定。 |
| 受控底层函数 | `Guarded_Common_Function` | `common/tools/common_tools.py` 中的 `read_file` / `write_file` / `edit_file` / `tree`。它们要么迁移为 `Local_Filesystem_Workspace` 的内部实现细节（不再作为公共入口），要么保留时明确标注"仅供已完成 Guard 校验的调用方使用"，避免绕过 Workspace 抽象直接操作宿主路径。 |
| 启动失败 | `Startup_Failure` | 服务启动阶段 `Workspace_Config` 或所选后端无法初始化（未配置、不存在且未允许创建、不是目录、不可读、不支持的后端取值等）时，应用以 fail-fast 方式拒绝启动，而非静默降级。 |

## 需求

### 需求 1：Workspace 抽象 Port 的能力集合

**用户故事：** 作为架构维护者，我希望 `Workspace` 以与存储介质无关的 Port 形式存在于 `domain/workspace/`，只表达当前 6 个受控工具真正需要的语义，以便未来新增 OSS 后端时不需要修改工具层。

#### 验收标准

1. THE `Workspace` SHALL 位于 `domain/workspace/` 层，不得 import `infrastructure/`、FastAPI、pydantic-settings、具体文件系统或对象存储 SDK 等任何外部依赖。
2. THE `Workspace` SHALL 暴露以下与存储介质无关的核心操作语义（以逻辑路径 `Workspace_Path` 为参数），且仅暴露以下语义（不得为不存在的工具预留接口）：
   - `resolve_path`：将 `Requested_Path` 规范化为 `Resolved_Workspace_Path` 或抛出 `Workspace_Confinement_Violation`；
   - `exists`：判定 `Workspace_Path` 是否存在；
   - `stat`：返回与后端无关的元数据（至少包含 `is_file` / `is_dir` / `size` / `mtime`；对不适用的字段后端可返回 None）；
   - `read`：按 `Workspace_Path` 读取全部内容，支持可选的按行范围子集（对齐 `ReadFileTool` 的 `start_line` / `end_line` 语义）；
   - `write`：按 `Workspace_Path` 写入全部内容，必要时自动创建父级逻辑目录（对齐 `WriteFileTool` 的既有行为）；
   - `edit`：对已有 `Workspace_Path` 做"首个匹配片段替换"（对齐 `EditFileTool` 的精确匹配 + 行级去空白模糊回退语义）；
   - `list_dir`：列出某个 `Workspace_Path` 下的条目，每个条目包含其相对 `Workspace_Path` 与类型标记（file/dir）；
   - `delete`：删除指定 `Workspace_Path`（用于覆盖写入、编辑失败回滚等内部必要场景，LLM 工具层**不直接暴露**该能力）；
   - `capabilities`：返回 `Workspace_Capabilities`，声明本后端是否支持符号链接、原子写、追加写、流式读写、大文件与 `Local_Materialization_Capability`。
3. THE `Workspace` SHALL 不在接口中暴露 `Backend_Location`、宿主绝对路径、`bucket`、`key` 等任何后端物理定位字段，所有对外参数与返回值仅使用 `Workspace_Path`。
4. THE `Workspace` SHALL 为未来的流式 / 大文件语义预留扩展空间：接口设计不得以"一次性读取全部字节到内存"作为唯一语义（至少在接口文档层面允许后续新增流式变体），但本期的 `Local_Filesystem_Workspace` 实现可先只落地一次性读写形式。
5. FOR ALL 面向 LLM 的错误消息，THE `Workspace` 层 SHALL 使用中文可读文案，且不得拼接任何宿主绝对路径、`bucket+key`、API Key、Token 等实现细节。

### 需求 2：逻辑路径语义

**用户故事：** 作为 LLM Agent 的提示词作者，我希望工具参数中的路径有可预期、跨后端一致的语义，始终以工作区根为锚点，而不必关心宿主操作系统或存储介质。

#### 验收标准

1. THE `Workspace_Path` SHALL 始终使用 POSIX 正斜杠 `/` 作为路径分隔符，不允许混入反斜杠 `\`；WHEN 入参使用了反斜杠，THE `Workspace_Policy` SHALL 将其归一为正斜杠或直接判定为 `Workspace_Confinement_Violation`（实现可二选一，但必须在文档层面统一）。
2. THE `Workspace_Path` SHALL 允许在路径中间出现 `..`，但 `Workspace_Policy` SHALL 在归一化阶段将其解析掉；IF 归一化后的路径越出工作区根，THEN THE `Workspace_Policy` SHALL 判定为 `Workspace_Confinement_Violation`。
3. WHEN `Requested_Path` 是相对路径（不以 `/` 起始），THE `Workspace_Policy` SHALL 将其显式锚定到工作区根，不得使用进程当前工作目录、宿主用户家目录或任何宿主环境变量作为锚点。
4. WHEN `Requested_Path` 是绝对形式（以 `/` 起始），THE `Workspace_Policy` SHALL 把它解释为"相对于工作区根的绝对逻辑路径"（即 `/` 等价于工作区根本身），而不是宿主文件系统的根。
5. FOR ALL `Requested_Path` 含有 NUL 字符（`\x00`）、Windows 盘符（形如 `C:`、`D:\\`）、UNC 路径前缀（形如 `\\\\server\\share`）的情况, THE `Workspace_Policy` SHALL 判定为 `Workspace_Confinement_Violation`，且不得在 `Local_Filesystem_Workspace` 中把它们映射到任何宿主物理路径。
6. WHEN `Workspace_Policy` 判定违规，THE `Workspace_Policy` SHALL 通过返回值或异常向调用方传递 `Workspace_Confinement_Violation`，并**不得静默返回一个被裁剪过的路径**。
7. WHEN `ListDirTool` 或任何工具返回的成功消息中包含路径，THE 工具 SHALL 以 `Workspace_Path` 形式呈现（以工作区根为基准的逻辑路径），不得泄露宿主绝对路径、`bucket`、`key` 等物理定位信息。

### 需求 3：后端能力声明与优雅降级

**用户故事：** 作为后端开发者，我希望 `Workspace` 能在运行期声明自身的能力边界，让工具层在面对不同后端时可以做出一致的降级或拒绝决策，而不必在工具内部硬编码 `isinstance(workspace, Local_Filesystem_Workspace)` 之类的判断。

#### 验收标准

1. THE `Workspace` SHALL 通过 `capabilities` 暴露 `Workspace_Capabilities`，至少包含：`supports_symlinks`、`supports_atomic_write`、`supports_append`、`supports_streaming`、`supports_large_files`、`local_materialization`（`Local_Materialization_Capability`）。
2. THE `Local_Filesystem_Workspace` SHALL 在 `capabilities` 中声明 `local_materialization=true`，`supports_symlinks` 的取值随 `Workspace_Config.follow_symlinks` 变化，`supports_atomic_write`、`supports_append`、`supports_streaming`、`supports_large_files` 采用与本地文件系统一致的真实取值。
3. WHEN 工具层调用的操作被当前后端声明为**不支持**（例如对 `local_materialization=false` 的后端请求子进程 `cwd`，或对 `supports_atomic_write=false` 的后端强制要求原子写），THE `Workspace` SHALL 抛出 `Workspace_Unsupported_Operation_Error`，而不是给出一个行为不一致的实现。
4. THE `Workspace_Capabilities` 结构 SHALL 被设计为可扩展：新增能力字段时不得要求既有工具或后端做破坏性修改（例如采用带默认值的结构化对象，新增字段对旧调用方透明）。
5. THE `Guarded_Filesystem_Tool` SHALL 不得在自身实现中以任何方式判断后端的具体类别（不得 `isinstance(workspace, Local_Filesystem_Workspace)`）；所有与后端差异相关的分支 SHALL 通过 `Workspace_Capabilities` 表达。

### 需求 4：领域错误模型

**用户故事：** 作为后端开发者，我希望 Workspace 相关的错误在领域层有统一、与后端无关的表达，由具体适配器把 OSS / 文件系统的原生异常翻译过来，避免工具层直接处理 `FileNotFoundError` / `PermissionError` / OSS SDK 专有异常。

#### 验收标准

1. THE `domain/workspace/` SHALL 定义以下与后端无关的领域错误类型，且只定义以下类型（不为未使用的场景预留类型）：
   - `Workspace_Confinement_Violation`：逻辑路径逃出工作区边界或含非法字符；
   - `Workspace_Not_Found_Error`：请求的逻辑路径在后端不存在；
   - `Workspace_Io_Error`：后端 I/O 失败（权限不足、网络错误、磁盘满、对象存储服务端错误等的统一包装）；
   - `Workspace_Unsupported_Operation_Error`：调用了当前后端 `Workspace_Capabilities` 声明不支持的能力。
2. THE `Local_Filesystem_Workspace` SHALL 把本地文件系统相关的原生异常（`FileNotFoundError`、`NotADirectoryError`、`PermissionError`、`OSError` 等）翻译为上述领域错误之一，不得让原生异常穿透到工具层。
3. THE `Guarded_Filesystem_Tool` SHALL 把接收到的 `Workspace_Confinement_Violation` / `Workspace_Not_Found_Error` / `Workspace_Io_Error` / `Workspace_Unsupported_Operation_Error` 统一转换为 `ToolExecutionError`，其 `tool_name` 为当前工具名，`message` 为中文可读文案（例如"路径 {Workspace_Path} 超出工作区边界"、"路径 {Workspace_Path} 不存在"）。
4. THE `ToolExecutionError.message` 返回给 LLM 的内容 SHALL 是**对 LLM 友好的自然语言**，SHALL 不包含宿主绝对路径、`bucket+key`、API Key / Token / Secret 等敏感或实现细节信息；详细诊断信息仅出现在服务端日志中（见需求 8）。
5. WHEN 任一 `Guarded_Filesystem_Tool` 的底层调用抛出上述领域错误以外的异常, THE 工具 SHALL 沿用现有的 `ToolExecutionError` 转换逻辑，即 Workspace 错误模型不替换、也不吞掉既有错误处理路径。

### 需求 5：配置模型与启动期校验

**用户故事：** 作为平台运维工程师，我希望通过 `config.properties` 显式声明所选的 Workspace 后端与必要参数，并在服务启动阶段完成校验，以便服务部署后 Agent 的文件影响面是明确、可审计的，且未来新增后端时无需重写配置骨架。

#### 验收标准

1. THE `Workspace_Config` SHALL 通过 `PropertiesBaseSettings` 以 `env_prefix="WORKSPACE_"` 从 `config.properties` 加载，至少包含字段：
   - `backend`（`Workspace_Backend_Kind`，默认 `local_filesystem`）；
   - `root: str`；
   - `follow_symlinks: bool = false`；
   - `create_if_missing: bool = false`。
2. THE `Workspace_Backend_Kind` SHALL 被定义为一个可扩展的取值集合；本期仅 `local_filesystem` 被视为合法值，其他取值（包括未来的 `oss`）SHALL 在本期触发 `Startup_Failure`，但配置字段与枚举本身必须存在，以便未来新增后端时无需改动配置骨架。
3. THE `Workspace_Config` SHALL 通过 `create_config(...)` 工厂创建全局单例，遵循仓库内 `ShellExecConfig` / `PythonExecConfig` 的既有模式。
4. WHEN 服务启动时 `Workspace_Config.backend` 为本期不支持的取值，THE 应用 SHALL 以 `Startup_Failure` 终止启动，错误消息中明确指出"本期仅支持 `local_filesystem` 后端"。
5. WHILE `Workspace_Config.backend` IN `local_filesystem`, WHEN 服务启动时 `WORKSPACE_ROOT` 为空字符串或未提供, THE 应用 SHALL 以 `Startup_Failure` 终止启动并在日志中输出"`WORKSPACE_ROOT 未配置，服务拒绝启动`"级别的清晰错误消息。
6. WHILE `Workspace_Config.backend` IN `local_filesystem`, WHEN 服务启动时 `WORKSPACE_ROOT` 指向一个不存在的路径且 `create_if_missing=false`, THE 应用 SHALL 以 `Startup_Failure` 终止启动。
7. WHILE `Workspace_Config.backend` IN `local_filesystem`, IF 服务启动时 `WORKSPACE_ROOT` 指向一个不存在的路径且 `create_if_missing=true`, THEN THE 应用 SHALL 创建该目录（含父级）并将其规范化为绝对路径作为 `Local_Filesystem_Workspace` 的宿主根。
8. WHILE `Workspace_Config.backend` IN `local_filesystem`, WHEN 服务启动时 `WORKSPACE_ROOT` 指向一个已存在但**不是目录**的路径（文件、socket、设备等）, THE 应用 SHALL 以 `Startup_Failure` 终止启动。
9. WHILE `Workspace_Config.backend` IN `local_filesystem`, WHEN 服务启动时 `WORKSPACE_ROOT` 指向一个进程不可读或不可写的目录, THE 应用 SHALL 以 `Startup_Failure` 终止启动并在错误消息中指明缺失的权限位。
10. WHILE `Workspace_Config.backend` IN `local_filesystem` 且 `follow_symlinks=false`, WHEN `Local_Filesystem_Workspace` 将 `Workspace_Path` 映射为宿主物理路径并发现路径中任一环节是符号链接, THE 后端 SHALL 把该请求判定为 `Workspace_Confinement_Violation`，不做 realpath 解引用。
11. WHILE `Workspace_Config.backend` IN `local_filesystem` 且 `follow_symlinks=true`, WHEN 对目标做 realpath 解引用后的宿主路径落在 Workspace_Root 之外, THE 后端 SHALL 判定为 `Workspace_Confinement_Violation`。
12. THE `Workspace_Root` SHALL 在进程生命周期内不可变，即使 `Workspace_Config` 被声明为 `hot_reload=True`，`backend` 与 `root` 字段的变更也 SHALL 被忽略或以启动后配置校验器拒绝。
13. THE `config.properties` SHALL 新增 `WORKSPACE_BACKEND`、`WORKSPACE_ROOT`、`WORKSPACE_FOLLOW_SYMLINKS`、`WORKSPACE_CREATE_IF_MISSING` 四个键，并在配置注释中说明它们的含义、默认值以及"本期 `WORKSPACE_BACKEND` 仅接受 `local_filesystem`"的限制。

### 需求 6：受控工具全部接入 Workspace 抽象

**用户故事：** 作为 Agent 的调用方，我希望无论 LLM 以何种路径形态调用 6 个受控工具，I/O 都通过注入的 `Workspace` 完成，工具自身不直接接触宿主文件系统或任何后端 SDK。

#### 验收标准

1. FOR ALL `Guarded_Filesystem_Tool`（`ReadFileTool`、`WriteFileTool`、`EditFileTool`、`ListDirTool`）, THE 工具 SHALL 通过构造参数接收 `Workspace` 实例，并在 `execute(...)` 中仅通过该实例完成路径解析与 I/O；工具 SHALL 不得直接 import `os`、`pathlib`、`open`、`common/tools/common_tools.py` 中未迁移为后端内部实现的公共函数，也不得直接调用任何对象存储 SDK。
2. WHEN `Guarded_Filesystem_Tool` 接收到的路径被 `Workspace_Policy` 判定为 `Workspace_Confinement_Violation`, THE 工具 SHALL 抛出 `ToolExecutionError`，其 `tool_name` 设为当前工具名，`message` 为中文可读文案，形如"路径 {Workspace_Path} 超出工作区边界，请改用工作区内的路径"。
3. WHEN `Guarded_Filesystem_Tool` 接收到的路径通过校验, THE 工具 SHALL 使用 `Resolved_Workspace_Path` 而非原始 `Requested_Path` 作为后续操作参数，以避免 TOCTOU（Time-of-Check-to-Time-of-Use）再次解析不一致。
4. WHEN `ListDirTool` 的路径参数为空字符串、`"."` 或 `"/"`（在逻辑路径语义下均等价于工作区根）, THE 工具 SHALL 将其解释为"列出工作区根"；返回条目的路径 SHALL 以 `Workspace_Path` 形式呈现，不得泄露宿主物理路径。
5. FOR ALL `Guarded_Filesystem_Tool` 的工具 schema 描述（`description` / `parameters.description`）, THE 工具 SHALL 在描述中显式说明"路径相对于工作区根解析，使用 POSIX 正斜杠分隔符"，以引导 LLM 正确使用。
6. WHILE `Workspace.capabilities.local_materialization` IN `true`, THE `Guarded_Exec_Tool`（`ShellExecTool` / `PythonExecTool`）SHALL 通过 `Workspace` 获取子进程 `cwd` 对应的宿主目录（本期即 `Local_Filesystem_Workspace` 暴露的"从 `Workspace_Path` 解析得到宿主 cwd"的受限能力），并将子进程的 `cwd` 锁定为工作区根或其子目录。
7. WHILE `Workspace.capabilities.local_materialization` IN `false`, WHEN `Guarded_Exec_Tool` 被调用, THE 工具 SHALL 直接以 `ToolExecutionError` 拒绝执行，`message` 为中文可读文案（形如"当前工作区后端不支持本地命令执行"）；本期不强制实现"临时物化"或"远端执行"降级策略，但在需求层明确此行为，以避免未来 OSS 后端上线时出现未定义行为。
8. THE `Guarded_Exec_Tool` SHALL **不承担**对子进程内部文件访问的运行时阻断（这是操作系统进程边界的问题），但 THE 文档 SHALL 明确此边界，并建议通过 OS 层面的用户/容器权限收敛来兜底。
9. THE `ShellExecTool.parameters` schema SHALL 要么移除 `working_dir` 字段，要么保留但在 `execute` 中由 `Workspace` 强制校验到工作区之内；传入的 `working_dir` 若触发 `Workspace_Confinement_Violation`，THE 工具 SHALL 抛出 `ToolExecutionError`。
10. THE `PythonExecTool` 的 AST 静态分析 SHALL 保持既有行为不变（`BLOCKED_CALLS`、`allowed_modules` 等不受 Workspace 影响），即 Workspace 是**附加**约束而非替代沙箱机制。
11. FOR ALL 子进程创建调用, THE `Guarded_Exec_Tool` SHALL 确保 `cwd` 参数在 `subprocess` 调用之前经过 `Workspace` 校验，即使值源于模块级默认也要经过校验一次，以防配置回归。
12. WHEN `Guarded_Exec_Tool` 启动子进程, THE 工具 SHALL 不以任何方式向子进程环境变量中注入除子进程 `cwd` 之外的宿主绝对路径或敏感信息；既有的环境变量剥离规则（`API_KEY` / `PASSWORD` / `SECRET` / `TOKEN`）SHALL 保持有效。

### 需求 7：相对路径与默认路径的一致语义

**用户故事：** 作为 LLM Agent 的提示词作者，我希望 `read_file("notes.md")` 这类相对路径调用有可预期、与后端无关的语义，始终解析到工作区根下。

#### 验收标准

1. FOR ALL `Guarded_Filesystem_Tool` 与 `Guarded_Exec_Tool` 的输入路径, WHEN 输入路径是相对形式, THE 工具 SHALL 通过 `Workspace` 将其锚定到工作区根，而不是 `os.getcwd()` 或任何宿主环境变量。
2. WHEN `ListDirTool` 的路径参数未传（若 schema 后续支持可选）或为空字符串, THE 工具 SHALL 默认列出工作区根。
3. THE Agent 的 `system_prompt`（`ChatConfig.system_prompt`）和 6 个受控工具的 `description` SHALL 一致地描述"所有路径以工作区根为基准、使用 POSIX 正斜杠"，避免 LLM 与实现端产生语义分歧。
4. WHEN 任一 `Guarded_Filesystem_Tool` 返回的成功消息中包含路径（如 `WriteFileTool` 的 "成功写入文件 {file_path}，共 N 字节"）, THE 工具 SHALL 返回 `Workspace_Path` 形式的逻辑路径，不得返回宿主绝对路径片段或 `bucket+key`。

### 需求 8：越界与后端错误的可观测性

**用户故事：** 作为平台安全运维，我希望每一次 `Workspace_Confinement_Violation` 及严重的后端错误都能在日志中被识别并可关联到调用链，以便事后审计 Agent 行为、调整提示词或工具权限。

#### 验收标准

1. WHEN `Workspace_Policy` 或 `Workspace_Backend` 判定发生 `Workspace_Confinement_Violation`, THE 基础设施层 SHALL 以 `logger.warning`（或更高级别）输出一条结构化日志，至少包含字段：`tool_name`、`requested_path`、`resolved_workspace_path`（若能解析出）、`workspace_root`、`workspace_backend_kind`、`violation_reason`（例如 `symlink_escape` / `absolute_outside` / `nul_byte` / `unc_path` / `cross_drive` / `backslash`）。
2. WHEN `Workspace_Backend` 抛出 `Workspace_Io_Error` 或 `Workspace_Unsupported_Operation_Error`, THE 基础设施层 SHALL 以 `logger.warning` 或 `logger.error` 输出结构化日志，至少包含 `tool_name`、`workspace_backend_kind`、`operation`（如 `read` / `write` / `edit` / `list_dir`）、`workspace_path`、`underlying_error_class`。
3. THE 违规与错误日志 SHALL **不得**包含 `API_KEY`、`PASSWORD`、`SECRET`、`TOKEN`、`CREDENTIAL` 等任何敏感字段；若路径字符串自身含有疑似敏感子串（如路径中嵌入的 `token=xxx`），实现 SHALL 对日志中的路径做最小脱敏或截断。
4. WHEN 日志框架启用了 OpenTelemetry 关联（`OTEL_LOG_CORRELATION=true`）, THE 违规日志 SHALL 自动携带当前请求的 `trace_id` / `span_id`，以便在链路中定位到具体的 ReAct 轮次。
5. THE 违规与后端错误 SHALL NOT 直接终止 ReAct Agent Loop，按现有约定继续作为 ToolMessage 回传给 LLM，允许其自我纠错后重试。
6. THE 返回给 LLM 的 `ToolExecutionError.message` SHALL 不包含 `Workspace_Root` 的宿主绝对路径、`bucket+key` 或其他部署信息；这些信息仅出现在服务端日志中。

### 需求 9：DI 容器装配与现有架构兼容

**用户故事：** 作为后端开发者，我希望 Workspace 抽象与 `Local_Filesystem_Workspace` 实现的引入不破坏现有 DDD 分层、DI 容器装配顺序以及 `ScopedToolRegistry` 权限模型，以便最小改动并可单元测试。

#### 验收标准

1. THE `Workspace_Config`、`Workspace`（Port）的绑定以及 `Local_Filesystem_Workspace`（Adapter）的注册 SHALL 通过 `application/container_config.py` 的 `configure_container()` 完成，遵循既有 `Scope.SINGLETON` 与 `register_async_resource` 的使用模式。
2. THE Workspace 启动期校验（后端合法性、目录存在性、权限、类型等）SHALL 作为一个 `register_async_resource` 或等价的启动钩子在 `_create_tool_registry` **之前**执行，以便 `Startup_Failure` 能触发容器的回滚清理语义。
3. THE `Guarded_Filesystem_Tool` 与 `Guarded_Exec_Tool` 的实例化 SHALL 在 `_create_tool_registry()` 中接收 `Workspace`（不是具体的 `Local_Filesystem_Workspace`）作为构造参数注入，不得在工具内部通过全局 import 隐式获取具体后端，以便测试可替换。
4. THE `ScopedToolRegistry` 的权限模型 SHALL 继续生效：Workspace 校验位于工具 `execute` 内部，发生在 `ToolPermissionDeniedError` 之后；即"工具先被授权可调用，才会进入 Workspace 校验"。
5. FOR ALL 对 `domain/workspace/` 的改动, THE 改动 SHALL 仅新增类型 / Port / 纯函数 / 领域错误，不得 import `infrastructure/`、FastAPI 或任何外部存储 SDK；允许使用 `pathlib.PurePosixPath` 这种不触发 I/O 的纯路径类型用于逻辑路径归一化。
6. THE `Local_Filesystem_Workspace` 的代码位置 SHALL 为 `infrastructure/workspace/local_filesystem/`（或等价的基础设施子目录）；`infrastructure/workspace/oss/` 作为未来 OSS 后端的占位位置 SHALL 在文档中明确说明，本期不要求该目录实际存在，也不要求为其放置任何代码。
7. THE 新增单元测试 SHALL 位于 `epsilon-boot/test/` 下，按 DDD 分层镜像组织（`test/domain/workspace/`、`test/infrastructure/workspace/local_filesystem/`、`test/infrastructure/tools/...`、`test/application/`），至少覆盖：逻辑路径归一化（相对路径锚定、`..` 归一、反斜杠、NUL 字节、Windows 盘符跨盘、UNC 路径）、符号链接开关两种状态、`Workspace_Capabilities` 查询、工具层在非本地物化后端下的 `Guarded_Exec_Tool` 拒绝行为（可用 mock 后端模拟 `local_materialization=false`）、`ShellExecTool.working_dir` 越界、`PythonExecTool` 子进程 cwd 越界；属性测试可选（遵循仓库 `_property.py` 命名约定）。
8. THE 所有新增 Python 模块、类、函数的 docstring SHALL 使用中文，遵循 `docs/development.md` 的代码规范。

### 需求 10：向后兼容与迁移

**用户故事：** 作为已部署本服务的运维方，我希望接入 Workspace 抽象之后，既有的 `config.properties` 在补齐 `WORKSPACE_*` 后即可继续运行，不需要重写 Agent 提示词或客户端 API。

#### 验收标准

1. WHEN `config.properties` 已声明合法的 `WORKSPACE_BACKEND=local_filesystem` 与 `WORKSPACE_ROOT` 且 Agent 使用工作区内的相对路径调用工具, THE 系统的对外 HTTP API 行为（`/api/chat`、`/api/task/execute`、`/v1/models`）SHALL 与引入 Workspace 之前保持一致（同样的请求返回同结构的响应）。
2. THE `config.properties` 模板 SHALL 在新增配置块时给出 `WORKSPACE_BACKEND=local_filesystem` 与空或占位的 `WORKSPACE_ROOT`，并配合启动期校验，使得升级服务而未正确配置 Workspace 的用户获得清晰的启动失败消息。
3. THE 旧有的 `SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 配置 SHALL 继续保留语义，但被 `Workspace` 二次校验；若其配置值对应的 `Workspace_Path` 落在工作区之外，应用 SHALL 以 `Startup_Failure` 终止启动，给出"请将 `SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 设置到工作区内，或留空使用默认"的提示。
4. THE 现有的 `epsilon-client/` 前端 SHALL 不需要任何修改（前端不直接访问宿主文件系统）。
5. THE 既有的 `common/tools/common_tools.py` 公共函数（`read_file` / `write_file` / `edit_file` / `tree`）SHALL 在引入 `Local_Filesystem_Workspace` 后，要么被迁移为该后端的内部实现细节（不再作为跨层公共入口），要么保留时在 docstring 中明确标注"仅供 `Local_Filesystem_Workspace` 内部使用，外部调用方必须经由 `Workspace` 抽象"，以避免两条入口产生边界不一致。

## 留给 design.md 的开放问题

以下问题不在验收条目中做硬性约束，交由设计阶段决策，但必须在 design.md 中给出明确答案：

1. `Workspace` Port 的具体 Python 形态：`typing.Protocol` vs `abc.ABC`；是否区分 `WorkspaceReader` / `WorkspaceWriter` 等细分接口，还是合为一个大接口；本期不在需求层规定。
2. `Workspace_Path` 的具体类型：裸字符串 + 纯函数校验 vs 强类型包装（`NewType` / 小类）；以及它与 `pathlib.PurePosixPath` 的关系。
3. `read` / `write` 接口的返回/参数类型：`bytes` 与 `str` 的划分，编码参数如何表达，二进制文件（如图片）是否本期就支持；以及未来流式变体的命名与签名。
4. `edit` 操作与 `Workspace_Capabilities.supports_atomic_write` 的耦合：是否在 `Local_Filesystem_Workspace` 中以"先写临时文件再 rename"实现原子写；以及当 `supports_atomic_write=false` 时的降级策略（例如 OSS 后端）。
5. `Local_Filesystem_Workspace` 中 `follow_symlinks=false` 下的检测算法：是在逐段 lstat 还是使用 `os.path.realpath` 后再比较；如何处理不同操作系统的大小写折叠（Windows / macOS 默认大小写不敏感）。
6. `Guarded_Exec_Tool` 在未来 OSS 后端下的"临时物化"或"远端执行"策略是否需要一个显式的能力位（例如 `supports_exec_materialization`），以便与 `local_materialization` 解耦；本期只需要保证 `Local_Filesystem_Workspace` 的行为清晰。
7. `Workspace_Config.follow_symlinks` 对 OSS 后端而言是否应被忽略或报错：OSS 本身没有符号链接概念，配置字段对该后端是否该保留、默认值如何。
8. 如何在 DI 容器中优雅表达"本期只允许 `local_filesystem` 后端"：是在配置校验阶段拒绝其他值，还是在 `configure_container()` 的后端工厂注册表中直接不注册 `oss` 条目。
9. 现有 `common/tools/common_tools.py` 的迁移方式（完全内联到 `Local_Filesystem_Workspace` vs 保留为内部工具函数）对现有测试的影响范围与改动成本。
10. 工具 schema 描述的具体中文文案模板，以及是否需要把"POSIX 正斜杠"、"相对工作区根"的说明提升到 `system_prompt` 的公共前缀以减少每个工具 description 的重复。
