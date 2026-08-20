# 实施计划：Shell Exec Tool

## 概述

为 LLM Agent 实现基于 asyncio 的 Shell 命令执行工具。实施按"配置模块 → 辅助函数（Shell 选择 + 环境变量清理）→ 工具实现 → 包导出 → 容器注册 → 配置文件 → 测试"的顺序递进，每步构建在前一步基础上，确保增量可验证。

## Tasks

- [x] 1. 创建 ShellExecConfig 配置类
  - [x] 1.1 新建 `infrastructure/tools/shell_exec/shell_exec_config.py`
    - 继承 `PropertiesBaseSettings`，使用 `SettingsConfigDict(env_prefix="SHELL_EXEC_")` 前缀
    - 定义 `timeout: int = 30`（对应 `SHELL_EXEC_TIMEOUT`）
    - 定义 `max_output_size: int = 51200`（对应 `SHELL_EXEC_MAX_OUTPUT_SIZE`）
    - 定义 `enabled: bool = False`（对应 `SHELL_EXEC_ENABLED`，默认 false，安全优先）
    - 定义 `working_dir: str = ""`（对应 `SHELL_EXEC_WORKING_DIR`，空字符串时运行时回退为 `os.path.join(tempfile.gettempdir(), "agent_exec")`）
    - 使用 `create_config` 工厂函数创建模块级 `shell_exec_config` 实例
    - 添加中文 docstring
    - _需求: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.2 编写属性测试：配置读取正确性
    - **Property 1: 配置读取正确性**
    - 生成随机 timeout 整数值、max_output_size 整数值、enabled 布尔值和 working_dir 字符串值，通过 monkeypatch 写入环境变量，验证 ShellExecConfig 读取后字段值一致；未设置时默认值分别为 30、51200、False 和空字符串
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_config.py`
    - **验证: 需求 1.1, 1.2, 1.3, 1.4**

- [x] 2. 实现辅助函数与 ShellExecTool 工具类
  - [x] 2.1 新建 `infrastructure/tools/shell_exec/shell_exec_tool.py`，实现 `get_shell_command` 函数
    - 定义模块级函数 `get_shell_command(command: str) -> list[str]`
    - 通过 `sys.platform` 检测操作系统：Linux/macOS 返回 `["bash", "-c", command]`，Windows 返回 `["powershell", "-Command", command]`
    - 添加中文 docstring
    - _需求: 2.4, 2.5_

  - [x] 2.2 编写属性测试：Shell 选择正确性
    - **Property 3: Shell 选择正确性**
    - 生成随机非空命令字符串和平台标识（linux/darwin/win32），Mock `sys.platform`，验证 `get_shell_command` 返回正确的参数列表，且最后一个元素始终等于原始 command 字符串
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_tool.py`
    - **验证: 需求 2.4**

  - [x] 2.3 在 `shell_exec_tool.py` 中实现 `sanitize_env` 函数
    - 定义敏感关键词列表 `_SENSITIVE_KEYWORDS`：KEY、SECRET、PASSWORD、TOKEN、CREDENTIAL
    - 定义平台保留变量集合 `_UNIX_PRESERVED_VARS` 和 `_WIN_PRESERVED_VARS`
    - 实现 `sanitize_env() -> dict[str, str]` 函数：复制当前环境变量，移除包含敏感关键词的变量（不区分大小写），但保留平台特定的系统必要变量
    - 添加中文 docstring
    - _需求: 4.1, 4.2, 4.3, 4.4_

  - [x] 2.4 编写属性测试：环境变量清理正确性
    - **Property 4: 环境变量清理正确性**
    - 生成随机环境变量名值对（含敏感/非敏感/保留变量），Mock `os.environ` 和 `sys.platform`，验证：敏感且非保留的变量被移除、保留列表中的变量始终保留（即使名称包含敏感关键词）、非敏感非保留变量也被保留
    - 测试文件：`test/infrastructure/tools/shell_exec/test_sanitize_env.py`
    - **验证: 需求 4.1, 4.2, 4.3**


  - [x] 2.5 在 `shell_exec_tool.py` 中实现 ShellExecTool 类
    - 继承 `Tool` 抽象基类，实现 `name`、`description`、`parameters`、`execute` 四个抽象成员
    - `name` 返回 `"shell_exec"`
    - 构造函数接收 `timeout: int = 30`、`max_output_size: int = 51200` 和 `working_dir: str | None = None`（None 时回退为 `os.path.join(tempfile.gettempdir(), "agent_exec")`）
    - `parameters` 返回 JSON Schema：必填 `command`（string），可选 `timeout`（integer）和 `working_dir`（string）
    - `execute` 流程：提取参数 → 确保工作目录存在 → `get_shell_command(command)` → `sanitize_env()` → `asyncio.create_subprocess_exec` → `asyncio.wait_for(process.communicate(), timeout)` → 超时时 `process.kill()` 并抛出 ToolExecutionError → 合并输出并检查大小 → 截断（如超限）→ 返回格式化结果
    - 异常时包装为 `ToolExecutionError`（`tool_name="shell_exec"`），错误信息包含原始异常描述
    - 添加中文 docstring
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 3.1, 3.2, 3.3, 5.1, 5.2, 5.3, 6.1, 6.2, 8.1, 8.2, 8.3_

  - [x] 2.6 编写属性测试：输出截断正确性
    - **Property 5: 输出截断正确性**
    - 生成随机 max_output_size 正整数和超过/未超过该大小的 stdout+stderr 内容，验证截断行为和提示信息格式 `"[输出已截断，原始大小: XXX bytes]"`；未超限时输出内容保持不变
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_tool.py`
    - **验证: 需求 6.1**

  - [x] 2.7 编写属性测试：结果格式正确性
    - **Property 6: 结果格式正确性**
    - 生成随机退出码（整数）、stdout 内容和 stderr 内容，验证格式化结果包含 `"Exit Code: {退出码}"`、`"[stdout]"` 和 `"[stderr]"` 标记，且内容分别出现在对应标记之后
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_tool.py`
    - **验证: 需求 6.2**

  - [x] 2.8 编写属性测试：异常包装正确性
    - **Property 7: 异常包装正确性**
    - 生成随机异常类型（OSError、FileNotFoundError、PermissionError）和消息，Mock subprocess 抛出异常，验证包装后的 ToolExecutionError 保留原始信息且 `tool_name` 为 `"shell_exec"`
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_tool.py`
    - **验证: 需求 2.7**

  - [x] 2.9 编写属性测试：超时终止正确性
    - **Property 8: 超时终止正确性**
    - 使用小超时值和 Mock 的长时间运行子进程，验证超时后抛出 ToolExecutionError 且消息包含超时秒数的字符串表示
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_tool.py`
    - **验证: 需求 3.1, 3.2**

  - [x] 2.10 编写单元测试：ShellExecTool 接口合规与边界情况
    - 验证继承 `Tool`、`name` 返回 `"shell_exec"`、`parameters` schema 结构正确（含 required=["command"]）
    - 验证工作目录不存在时自动创建（使用临时目录）
    - 验证 execute 的 working_dir 参数覆盖默认值
    - 验证超时时 process.kill() 被调用（Mock subprocess）
    - 验证默认配置值（timeout=30, max_output_size=51200, enabled=false）
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_tool.py`
    - _需求: 2.1, 2.2, 2.3, 2.6, 3.3, 5.2, 5.3_

- [x] 3. Checkpoint - 确保工具实现测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 4. 包导出与工具注册集成
  - [x] 4.1 新建 `infrastructure/tools/shell_exec/__init__.py`
    - 导出 `ShellExecTool`，定义 `__all__ = ["ShellExecTool"]`
    - _需求: 8.1_

  - [x] 4.2 在 `config.properties` 中追加 Shell 命令执行工具配置项
    - 新增 `SHELL_EXEC_TIMEOUT=30`
    - 新增 `SHELL_EXEC_MAX_OUTPUT_SIZE=51200`
    - 新增 `SHELL_EXEC_ENABLED=false`
    - _需求: 1.1, 1.2, 1.3_

  - [x] 4.3 修改 `application/container_config.py` 的 `_create_tool_registry()` 函数
    - 在 HttpRequestTool 条件注册之后、DelegateToAgentTool 条件注册之前，添加 ShellExecTool 条件注册逻辑
    - 读取 `shell_exec_config.enabled`，为 True 时实例化 `ShellExecTool(timeout=..., max_output_size=..., working_dir=... or None)` 并注册到 ToolRegistry
    - `enabled` 为 False 时记录 `logger.info` 并跳过注册
    - 导入失败时记录 `logger.debug` 并跳过注册
    - _需求: 1.5, 7.1, 7.2, 7.3_

  - [x] 4.4 编写属性测试：条件注册正确性
    - **Property 2: 条件注册正确性**
    - 生成随机 enabled 布尔值，验证 enabled=True 时 ToolRegistry 包含 `"shell_exec"` 工具，enabled=False 时不包含且其他工具不受影响
    - 测试文件：`test/infrastructure/tools/shell_exec/test_shell_exec_config.py`
    - **验证: 需求 1.5, 7.2, 7.3**

- [x] 5. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 Hypothesis 库，建议 `@settings(max_examples=100, deadline=5000)`
- 测试运行命令：`cd epsilon-boot && uv run pytest test/infrastructure/tools/shell_exec/ -v`
- 变更范围集中在 `infrastructure/tools/shell_exec/`、`application/container_config.py`、`config.properties` 三处
- ShellExecTool 仅依赖领域层的 `Tool` 基类和 `ToolExecutionError`，不在领域层引入基础设施依赖
- 项目无需添加新依赖，仅使用 Python 标准库 `asyncio`、`os`、`sys`、`tempfile` 和已有的领域层接口
