# Implementation Plan: Python Script Sandbox (PythonExecTool)

## Overview

基于 requirements.md 和 design.md，将 PythonExecTool 的实现拆分为增量式编码任务。每个任务构建在前一个任务之上，从配置层开始，经过 AST 分析、核心工具类，到 ToolRegistry 集成，最后完成端到端验证。所有代码位于 `epsilon-boot/src/infrastructure/tools/python_exec/` 下，测试位于 `epsilon-boot/test/infrastructure/tools/python_exec/` 下。

## Tasks

- [x] 1. 创建 PythonExecConfig 配置类与 config.properties 配置项
  - [x] 1.1 在 `config.properties` 中添加 `PYTHON_EXEC_` 前缀的配置项
    - 添加 `PYTHON_EXEC_ENABLED=false`、`PYTHON_EXEC_TIMEOUT=30`、`PYTHON_EXEC_MAX_OUTPUT_SIZE=51200`、`PYTHON_EXEC_MAX_MEMORY_MB=256`、`PYTHON_EXEC_WORKING_DIR=`、`PYTHON_EXEC_ALLOWED_MODULES=`
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 1.2 创建 `python_exec_config.py` 配置类
    - 在 `src/infrastructure/tools/python_exec/python_exec_config.py` 中实现 `PythonExecConfig`，继承 `PropertiesBaseSettings`，`env_prefix="PYTHON_EXEC_"`
    - 包含字段：`enabled`(bool, False)、`timeout`(int, 30)、`max_output_size`(int, 51200)、`max_memory_mb`(int, 256)、`working_dir`(str, "")、`allowed_modules`(str, "")
    - 实现 `get_allowed_modules()` 方法：将 `allowed_modules` 逗号分隔解析后与 `DEFAULT_ALLOWED_MODULES` 合并，返回 `frozenset[str]`
    - 创建模块级实例 `python_exec_config = create_config(PythonExecConfig)`
    - _Requirements: 3.1, 3.2, 8.1, 8.2, 8.3, 10.2_

  - [x] 1.3 编写 PythonExecConfig 单元测试
    - 在 `test/infrastructure/tools/python_exec/test_python_exec_config.py` 中验证默认值、`env_prefix`、`get_allowed_modules()` 合并逻辑
    - _Requirements: 8.1, 8.3, 3.1, 3.2_

  - [x] 1.4 编写配置模块合并属性测试
    - **Property 7: 配置模块合并**
    - 使用 Hypothesis 生成随机合法 Python 标识符列表，验证 `get_allowed_modules()` 返回 `DEFAULT_ALLOWED_MODULES` 与输入列表的并集
    - **Validates: Requirements 3.2**

- [x] 2. 实现 AST 静态分析函数
  - [x] 2.1 在 `python_exec_tool.py` 中实现 `AnalysisResult` 数据类和 `analyze_code` 函数
    - 定义 `DEFAULT_ALLOWED_MODULES` 和 `BLOCKED_CALLS` 常量
    - 实现 `AnalysisResult` frozen dataclass（`ok: bool`, `reason: str = ""`）
    - 实现 `analyze_code(code, allowed_modules, blocked_calls) -> AnalysisResult`：
      - `ast.parse` 解析代码，捕获 `SyntaxError` 返回描述性错误
      - 遍历 AST 检查 `Import`/`ImportFrom` 节点，验证顶层模块名在白名单中
      - 拒绝相对导入（`ImportFrom.level > 0`）
      - 检查 `Call` 节点中的函数名是否在黑名单中
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.3, 3.4, 10.3_

  - [x] 2.2 编写非白名单导入被拒绝属性测试
    - **Property 3: 非白名单导入被拒绝**
    - 使用 Hypothesis 生成随机合法 Python 标识符（过滤掉白名单中的），验证 `analyze_code` 拒绝包含该模块导入的代码
    - **Validates: Requirements 2.2, 3.3, 3.4**

  - [x] 2.3 编写危险函数调用被拒绝属性测试
    - **Property 4: 危险函数调用被拒绝**
    - 从 `BLOCKED_CALLS` 中随机选择函数名，验证 `analyze_code` 拒绝包含该调用的代码
    - **Validates: Requirements 2.3**

  - [x] 2.4 编写语法错误产生描述性错误属性测试
    - **Property 5: 语法错误产生描述性错误**
    - 使用 Hypothesis 生成随机无效 Python 代码，验证 `analyze_code` 返回 `ok=False` 且 reason 非空
    - **Validates: Requirements 2.4**

  - [x] 2.5 编写 AST 解析往返属性测试
    - **Property 6: AST 解析往返**
    - 使用 Hypothesis 生成随机有效 Python 表达式，验证 `ast.parse → ast.unparse → ast.parse` 产生结构等价的 AST
    - **Validates: Requirements 2.6**

- [x] 3. Checkpoint - 确保配置和 AST 分析测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. 实现 PythonExecTool 核心工具类
  - [x] 4.1 在 `python_exec_tool.py` 中实现 `PythonExecTool` 类
    - 继承 `Tool` ABC，实现 `name`("python_exec")、`description`、`parameters`（required: ["code"], optional: ["timeout"]）、`execute` 方法
    - `execute` 流程：提取参数 → `analyze_code` 静态分析 → 写入临时 `.py` 文件 → 构建子进程（`asyncio.create_subprocess_exec`）→ `asyncio.wait_for` 超时控制 → 输出截断 + 格式化 → 清理临时文件
    - 实现 `_create_memory_limiter` 函数（Linux/macOS 使用 `resource.setrlimit`，Windows 跳过并记录警告）
    - 复用 `shell_exec` 模块的 `sanitize_env` 函数进行环境变量清理
    - AST 分析拒绝时抛出 `ToolExecutionError`，不创建子进程
    - 超时时 kill 子进程并抛出 `ToolExecutionError`
    - 输出截断逻辑与 ShellExecTool 保持一致
    - 临时文件在 `finally` 块中清理
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 9.1, 9.2, 9.3, 9.4, 9.5, 10.3, 10.4, 10.5_

  - [x] 4.2 创建 `__init__.py` 导出 `PythonExecTool`
    - 在 `src/infrastructure/tools/python_exec/__init__.py` 中导出 `PythonExecTool`
    - _Requirements: 10.1_

  - [x] 4.3 编写 stdout/stderr 捕获往返属性测试
    - **Property 1: stdout/stderr 捕获往返**
    - 使用 Hypothesis 生成随机可打印字符串，验证 PythonExecTool 执行 `print(S)` 后结果包含 S
    - **Validates: Requirements 1.2, 1.3**

  - [x] 4.4 编写异常产生非零退出码属性测试
    - **Property 2: 异常产生非零退出码与 traceback**
    - 从异常类型列表中随机选择，验证执行 `raise ExcType()` 后返回非零退出码且 stderr 包含 "Traceback" 和异常类型名
    - **Validates: Requirements 1.4**

  - [x] 4.5 编写输出截断与大小标注属性测试
    - **Property 8: 输出截断与大小标注**
    - 使用 Hypothesis 生成超过 `max_output_size` 的随机字符串，验证输出被截断且包含原始大小标注
    - **Validates: Requirements 6.2, 6.3**

  - [x] 4.6 编写输出格式一致性属性测试
    - **Property 10: 输出格式一致性**
    - 使用 Hypothesis 生成随机有效 Python 代码，验证执行结果匹配 `Exit Code: {int}\n\n[stdout]\n{text}\n\n[stderr]\n{text}` 格式
    - **Validates: Requirements 9.4**

  - [x] 4.7 编写 AST 拒绝触发 ToolExecutionError 属性测试
    - **Property 11: AST 拒绝触发 ToolExecutionError**
    - 生成包含非白名单导入或危险调用的代码，验证 `execute` 抛出 `ToolExecutionError`
    - **Validates: Requirements 9.5**

- [x] 5. Checkpoint - 确保 PythonExecTool 核心功能测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. 集成 ToolRegistry 条件注册
  - [x] 6.1 在 `container_config.py` 的 `_create_tool_registry` 中添加 PythonExecTool 条件注册
    - 参照 ShellExecTool 的注册模式，导入 `python_exec_config`，当 `enabled=True` 时注册 `PythonExecTool`
    - 传入 `timeout`、`max_output_size`、`max_memory_mb`、`working_dir`、`allowed_modules`（通过 `get_allowed_modules()`）参数
    - _Requirements: 8.4, 8.5, 10.1_

  - [x] 6.2 编写条件注册单元测试
    - 验证 `enabled=false` 时不注册、`enabled=true` 时注册成功
    - _Requirements: 8.4, 8.5_

  - [x] 6.3 编写环境变量清理属性测试
    - **Property 9: 环境变量清理排除敏感变量**
    - 使用 Hypothesis 生成包含敏感关键词的环境变量名，验证 `sanitize_env()` 返回的字典不包含这些变量
    - **Validates: Requirements 7.2**

- [x] 7. Final checkpoint - 确保所有测试通过
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from design.md
- Unit tests validate specific examples and edge cases
- 所有属性测试放在 `test/infrastructure/tools/python_exec/test_python_exec_tool_property.py` 中
- 单元测试分别放在 `test_python_exec_config.py` 和 `test_python_exec_tool.py` 中
