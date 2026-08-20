# Implementation Plan: WriteFileTool

## Overview

在 `common/tools/common_tools.py` 中新增 `write_file()` 工具函数，然后在 `infrastructure/tools/filesystem/` 基础设施层实现 WriteFileTool，继承 `domain/agent/tools.py` 中的 `Tool` 抽象基类，复用 `write_file()` 函数，提供文件内容写入工具。采用自底向上的顺序：先实现工具函数 → 实现工具类 → 更新模块导出 → 编写属性测试 → 补充单元测试。

## Tasks

- [x] 1. 实现 write_file 工具函数和 WriteFileTool 类
  - [x] 1.1 在 `common/tools/common_tools.py` 中新增 `write_file()` 函数
    - 接受 `file_path`（str）和 `content`（str）两个参数
    - 检查 `path.is_dir()`，若是目录则返回 `"错误：路径是目录而非文件 - {file_path}"`
    - 使用 `path.parent.mkdir(parents=True, exist_ok=True)` 自动创建父目录
    - 使用 `path.write_text(content, encoding="utf-8")` 写入内容
    - 成功时返回写入字节数（int），失败时返回 `"错误："` 开头的字符串
    - 权限不足时抛出 PermissionError（与 read_file 行为一致）
    - docstring 使用中文
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

  - [x] 1.2 在 `infrastructure/tools/filesystem/write_file_tool.py` 中实现 `WriteFileTool(Tool)` 类
    - `name` 属性返回 `"write_file"`
    - `description` 属性返回中文功能描述
    - `parameters` 属性返回 JSON Schema，包含 `file_path`（required, string）和 `content`（required, string）
    - `execute(file_path, content)` 异步方法：调用 `write_file(file_path, content)` → 检测 `isinstance(result, str)` 且以 `"错误："` 开头转换为 `ToolExecutionError` → 捕获 `PermissionError` 转换为 `ToolExecutionError` → 成功时返回 `"成功写入文件 {file_path}，共 {bytes_written} 字节"`
    - 模块级 docstring 和类/方法 docstring 使用中文
    - _Requirements: 2.1-2.6, 3.1-3.5, 4.1-4.3, 5.1-5.3_

  - [x] 1.3 更新 `infrastructure/tools/filesystem/__init__.py`，导出 `WriteFileTool`
    - 在 `__all__` 列表中添加 `WriteFileTool`
    - _Requirements: 7.1_

- [x] 2. 编写属性测试
  - [x] 2.1 编写属性测试：Write-read round trip（Property 1）
    - **Property 1: Write-read round trip**
    - 生成随机文本内容（含多行、空行、Unicode 字符），写入临时文件后用 `read_file()` 读回
    - 验证读回内容（去除行号前缀后）与原始 content 一致
    - 标签：`# Feature: write-file-tool, Property 1: Write-read round trip`
    - **Validates: Requirements 1.3, 1.4, 1.5, 3.1, 3.3, 6.1**

  - [x] 2.2 编写属性测试：Write idempotence（Property 2）
    - **Property 2: Write idempotence**
    - 生成随机文本内容，对同一文件连续写入两次相同内容
    - 验证两次写入后文件内容一致（通过 `read_file()` 读取比较）
    - 标签：`# Feature: write-file-tool, Property 2: Write idempotence`
    - **Validates: Requirements 6.2**

  - [x] 2.3 编写属性测试：Parent directory auto-creation（Property 3）
    - **Property 3: Parent directory auto-creation**
    - 生成随机嵌套目录路径（父目录不存在），写入内容
    - 验证不抛出异常，且文件存在于指定路径
    - 标签：`# Feature: write-file-tool, Property 3: Parent directory auto-creation`
    - **Validates: Requirements 1.2, 3.2**

  - [x] 2.4 编写属性测试：Directory path rejection（Property 4）
    - **Property 4: Directory path rejection**
    - 在 `tmp_path` 下创建随机目录，以该目录路径作为 file_path 调用 execute
    - 验证抛出 `ToolExecutionError`，错误信息包含路径信息
    - 标签：`# Feature: write-file-tool, Property 4: Directory path rejection`
    - **Validates: Requirements 1.7, 4.1, 4.3**

  - [x] 2.5 编写属性测试：Byte count correctness（Property 5）
    - **Property 5: Byte count correctness**
    - 生成随机文本内容，调用 `write_file()` 获取返回的字节数
    - 验证返回值等于 `len(content.encode("utf-8"))`
    - 标签：`# Feature: write-file-tool, Property 5: Byte count correctness`
    - **Validates: Requirements 1.6, 3.4**

- [x] 3. 编写单元测试
  - [x] 3.1 编写基本定义和 schema 验证测试
    - 验证 `name == "write_file"`、`description` 非空中文
    - 验证 `parameters` schema 结构正确（type, properties, required 包含 file_path 和 content）
    - 验证 `to_schema()` 返回 OpenAI function calling 格式，name 为 `"write_file"`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 5.2_

  - [x] 3.2 编写 ToolRegistry 集成和功能测试
    - 验证通过 ToolRegistry.register() 注册后可通过 ToolCallRequest 调用
    - 验证空内容写入（content 为空字符串）创建空文件（0 字节）
    - _Requirements: 3.5, 5.1, 5.3_

  - [x] 3.3 编写错误处理测试
    - 验证目录路径抛出 ToolExecutionError
    - 验证权限不足抛出 ToolExecutionError
    - 验证缺少 file_path 或 content 参数时抛出 ToolParameterValidationError
    - _Requirements: 4.1, 4.2, 4.3, 5.3_

- [x] 4. Checkpoint - 运行所有测试确保通过
  - 运行 `uv run pytest test/infrastructure/tools/filesystem/ -v` 确保所有测试通过，ask the user if questions arise.

## Notes

- 所有测试文件放在 `test/infrastructure/tools/filesystem/` 目录下
- 属性测试使用 `@settings(max_examples=100, deadline=2000)` 配置
- 属性测试函数需包含注释标签：`# Feature: write-file-tool, Property {N}: {title}`
- 测试中使用 `tmp_path` fixture 创建临时文件
- WriteFileTool 的 execute 方法虽然是 async，但底层 write_file() 是同步 I/O，这在当前项目中是可接受的
- Tasks marked with `*` are optional and can be skipped for faster MVP
- 工作目录：`epsilon-boot/`
