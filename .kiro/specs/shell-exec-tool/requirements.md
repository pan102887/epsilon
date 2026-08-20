# 需求文档

## 简介

为 LLM Agent 实现 Shell 命令执行工具（ShellExecTool），使 Agent 具备在受控环境中执行 Shell 命令的能力。该工具基于 Python 标准库 `asyncio.create_subprocess_exec` 实现异步子进程执行，支持任意 Shell 命令的执行并捕获 stdout/stderr 输出。

ShellExecTool 内置多层安全防护机制：
- 执行超时限制（可配置，默认 30 秒），防止长时间运行的命令占用资源
- 环境变量清理，移除包含敏感信息（如 API_KEY、PASSWORD、SECRET、TOKEN）的环境变量，防止命令通过 `env` / `printenv` / `Get-ChildItem Env:` 泄露凭据
- 工作目录隔离，所有命令在系统临时目录下的 `agent_exec/` 子目录中执行，防止对项目文件的意外修改
- stdout/stderr 输出大小限制（可配置），防止大量输出消耗内存

工具支持跨平台运行，运行时通过 `sys.platform` 自动检测操作系统并选择对应的 shell：
- Linux/macOS → `bash -c <command>`
- Windows → `powershell -Command <command>`

工具实现不绑定特定部署环境（裸机、Docker 容器、云主机等均适用），仅依赖操作系统提供的 shell（bash 或 PowerShell）和 Python 标准库 asyncio 子进程能力。安全防护通过进程级隔离（环境变量清理、工作目录隔离、超时 kill）实现，不依赖容器或沙箱机制。

遵循项目 DDD 架构规范：领域层定义 `Tool` 基类和 `ToolExecutionError` 异常，基础设施层提供 `ShellExecTool` 具体实现和 `ShellExecConfig` 配置类，应用层负责工具条件注册编排，配置通过 `config.properties` + `PropertiesBaseSettings` 统一管理。

## 术语表

- **ShellExecTool**：Shell 命令执行工具，基础设施层的 Tool 实现，封装 asyncio 子进程执行能力，为 LLM Agent 提供 Shell 命令执行能力
- **ShellExecConfig**：Shell 执行工具配置类，继承 PropertiesBaseSettings，从 config.properties 加载以 `SHELL_EXEC_` 为前缀的配置项
- **Tool**：工具抽象基类，定义工具的统一接口规范（name、description、parameters、execute），定义于 `domain/agent/tools.py`
- **ToolRegistry**：工具注册表，集中管理所有已注册的 Tool 实例，定义于 `domain/agent/tools.py`
- **ToolExecutionError**：工具执行异常，定义于 `domain/agent/exceptions.py`，用于标准化工具层的错误处理
- **config.properties**：项目配置文件，存放所有配置项，位于项目根目录
- **Sensitive_Env_Pattern**：敏感环境变量匹配模式，用于识别包含 API_KEY、PASSWORD、SECRET、TOKEN、CREDENTIAL 等关键词的环境变量名称，匹配时不区分大小写
- **Working_Directory**：工作目录，Shell 命令执行时的当前工作目录，默认为系统临时目录下的 `agent_exec/` 子目录（通过 `tempfile.gettempdir()` 获取跨平台临时目录），执行前自动创建（如不存在）
- **Shell_Resolver**：Shell 解析器，运行时通过 `sys.platform` 检测操作系统，自动选择对应的 shell 执行方式（Linux/macOS 使用 `bash -c`，Windows 使用 `powershell -Command`）

## 需求

### 需求 1：Shell 执行工具配置管理

**用户故事：** 作为开发者，我希望通过 config.properties 集中管理 Shell 执行工具的配置，以便统一维护超时、输出大小上限和启用状态。

#### 验收标准

1. THE ShellExecConfig SHALL 从 config.properties 读取 `SHELL_EXEC_TIMEOUT` 配置项作为命令执行超时秒数，默认值为 30
2. THE ShellExecConfig SHALL 从 config.properties 读取 `SHELL_EXEC_MAX_OUTPUT_SIZE` 配置项作为 stdout/stderr 合并后的输出大小上限（字节），默认值为 51200（50KB）
3. THE ShellExecConfig SHALL 从 config.properties 读取 `SHELL_EXEC_ENABLED` 配置项作为工具启用开关，默认值为 false
4. THE ShellExecConfig SHALL 从 config.properties 读取 `SHELL_EXEC_WORKING_DIR` 配置项作为命令执行的工作目录路径，默认值为系统临时目录下的 `agent_exec` 子目录（即 `os.path.join(tempfile.gettempdir(), "agent_exec")`）
5. IF `SHELL_EXEC_ENABLED` 配置项为 false，THEN THE ShellExecTool SHALL 在工具注册阶段记录日志并跳过注册

### 需求 2：Shell 命令执行

**用户故事：** 作为 LLM Agent，我希望能够执行 Shell 命令，以便完成文件处理、系统信息查询、脚本运行等操作任务。

#### 验收标准

1. THE ShellExecTool SHALL 继承 `Tool` 抽象基类，实现 `name`、`description`、`parameters`、`execute` 四个抽象成员
2. THE ShellExecTool 的 `name` 属性 SHALL 返回 `"shell_exec"`
3. THE ShellExecTool 的 `parameters` 属性 SHALL 声明以下参数：一个必填参数 `command`（类型 string，待执行的 Shell 命令字符串）；两个可选参数 `timeout`（类型 integer，单次执行超时秒数，默认取自配置）和 `working_dir`（类型 string，工作目录路径，默认取自配置）
4. WHEN Agent 调用 `execute` 方法并传入有效的 `command` 参数时，THE ShellExecTool SHALL 根据运行时操作系统自动选择 shell 执行方式：Linux/macOS 使用 `asyncio.create_subprocess_exec("bash", "-c", command)`，Windows 使用 `asyncio.create_subprocess_exec("powershell", "-Command", command)`，并返回包含退出码、stdout 和 stderr 的格式化结果
5. THE ShellExecTool SHALL 将 shell 选择逻辑封装为独立函数（如 `get_shell_command`），接收命令字符串，返回适用于当前平台的可执行参数列表，便于单元测试和复用
6. THE ShellExecTool SHALL 在执行命令前确保工作目录存在，若目录不存在则自动创建
7. IF `execute` 过程中发生任何异常（子进程创建失败、权限不足、shell 不可用等），THEN THE ShellExecTool SHALL 将异常包装为 ToolExecutionError 并抛出，错误信息中包含原始异常描述

### 需求 3：执行超时防护

**用户故事：** 作为系统安全负责人，我希望 Shell 命令执行有超时限制，以防止长时间运行的命令占用系统资源。

#### 验收标准

1. THE ShellExecTool SHALL 在子进程执行时设置超时限制，超时时长由 `timeout` 参数或配置默认值决定
2. IF 子进程执行时间超过超时限制，THEN THE ShellExecTool SHALL 终止子进程并抛出 ToolExecutionError，错误信息中包含超时秒数和被执行的命令摘要
3. WHEN 子进程因超时被终止时，THE ShellExecTool SHALL 调用 `process.kill()` 终止子进程（跨平台兼容：Linux/macOS 发送 SIGKILL，Windows 调用 TerminateProcess），然后收集已产生的输出

### 需求 4：环境变量安全清理

**用户故事：** 作为系统安全负责人，我希望 Shell 命令执行时移除敏感环境变量，以防止 Agent 通过 `env` 或 `printenv` 命令泄露 API 密钥、数据库密码等凭据。

#### 验收标准

1. THE ShellExecTool SHALL 在创建子进程时传入经过清理的环境变量副本，移除所有名称中包含敏感关键词的环境变量
2. THE ShellExecTool 的敏感关键词列表 SHALL 包含以下模式（不区分大小写匹配）：KEY、SECRET、PASSWORD、TOKEN、CREDENTIAL
3. THE ShellExecTool SHALL 保留非敏感的系统环境变量，确保命令能正常执行。保留列表按平台区分：Linux/macOS 保留 PATH、HOME、LANG、USER、SHELL、TERM；Windows 保留 Path、USERPROFILE、USERNAME、SystemRoot、TEMP、TMP、PATHEXT、COMSPEC
4. THE ShellExecTool 的环境变量清理逻辑 SHALL 封装为独立函数，便于单元测试和复用

### 需求 5：工作目录隔离

**用户故事：** 作为系统安全负责人，我希望 Shell 命令在隔离的工作目录中执行，以防止对项目文件的意外修改。

#### 验收标准

1. THE ShellExecTool SHALL 将子进程的工作目录设置为配置指定的路径（默认为系统临时目录下的 `agent_exec` 子目录），与项目源码目录隔离
2. WHEN 配置的工作目录不存在时，THE ShellExecTool SHALL 在执行命令前自动创建该目录（包括必要的父目录）
3. THE ShellExecTool 的 `execute` 方法 SHALL 支持通过 `working_dir` 参数覆盖默认工作目录

### 需求 6：输出大小限制

**用户故事：** 作为系统安全负责人，我希望 Shell 命令的输出有大小限制，以防止大量输出消耗内存或超出 LLM 上下文窗口。

#### 验收标准

1. WHEN stdout 和 stderr 的合并输出大小超过配置的上限（SHELL_EXEC_MAX_OUTPUT_SIZE）时，THE ShellExecTool SHALL 截断输出内容并在末尾附加提示信息 "[输出已截断，原始大小: XXX bytes]"
2. THE ShellExecTool 返回的结果 SHALL 包含退出码（exit_code）、stdout 内容和 stderr 内容，格式化为结构清晰的文本，便于 LLM 解析

### 需求 7：工具注册集成

**用户故事：** 作为开发者，我希望 ShellExecTool 能按条件自动注册到 ToolRegistry，以便 Agent 在对话中直接使用 Shell 执行能力。

#### 验收标准

1. THE ShellExecTool SHALL 在 `_create_tool_registry()` 函数中完成注册，注册位置位于 HttpRequestTool 条件注册之后
2. WHEN `SHELL_EXEC_ENABLED` 配置为 true 时，THE `_create_tool_registry()` 函数 SHALL 实例化 ShellExecTool 并注册到 ToolRegistry
3. IF `SHELL_EXEC_ENABLED` 配置为 false，THEN THE `_create_tool_registry()` 函数 SHALL 记录日志并跳过 ShellExecTool 注册，不影响其他工具的正常注册

### 需求 8：DDD 架构合规

**用户故事：** 作为开发者，我希望 ShellExecTool 遵循项目 DDD 架构规范，以便保持代码结构一致性。

#### 验收标准

1. THE ShellExecTool 实现文件 SHALL 位于 `infrastructure/tools/shell_exec/` 包下，遵循现有工具包的组织方式
2. THE ShellExecTool 模块 SHALL 包含中文 docstring，说明模块职责、类作用和方法功能
3. THE ShellExecTool SHALL 仅依赖 `domain/agent/tools.py` 中的 `Tool` 基类和 `domain/agent/exceptions.py` 中的 `ToolExecutionError`，不在领域层引入基础设施依赖
