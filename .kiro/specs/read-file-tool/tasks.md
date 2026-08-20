# Implementation Plan: ReadFileTool

## Overview

在 `infrastructure/tools/filesystem/` 基础设施层实现 ReadFileTool，继承 `domain/agent/tools.py` 中的 `Tool` 抽象基类，复用 `common/tools/common_tools.py` 中的 `read_file()` 函数，提供带分页能力的文件内容读取工具。采用自底向上的顺序：先实现工具 → 再编写属性测试 → 最后补充单元测试和集成测试。

## Tasks

- [x] 1. 实现 ReadFileTool 类
  - [x] 1.1 在 `infrastructure/tools/filesystem/read_file_tool.py` 中实现 `ReadFileTool(Tool)` 类
    - `name` 属性返回 `"read_file"`
    - `description` 属性返回中文功能描述
    - `parameters` 属性返回 JSON Schema，包含 `file_path`（required, string）、`offset`（integer, default 1）、`limit`（integer, default 200）
    - `execute(file_path, offset=1, limit=200)` 异步方法：校验 offset >= 1 和 limit >= 1 → 计算 end_line = offset + limit - 1 → 调用 `read_file(file_path, offset, end_line)` → 检测 `"错误："` 前缀转换为 `ToolExecutionError` → 捕获 `PermissionError` 转换为 `ToolExecutionError`
    - 模块级 docstring 和类/方法 docstring 使用中文
    - _Requirements: 1.1-1.7, 2.1-2.5, 3.1-3.4, 4.1-4.2, 5.4_

  - [x] 1.2 更新 `infrastructure/tools/filesystem/__init__.py`，导出 `ReadFileTool`
    - _Requirements: 1.1_

- [x] 2. 编写属性测试
  - [x] 2.1 创建 `test/infrastructure/tools/filesystem/__init__.py`

  - [x] 2.2 编写属性测试：Delegation consistency（Property 1）
    - 生成随机文本文件（1~500 行），随机 offset 和 limit
    - 验证 `execute(file_path, offset, limit)` 输出与 `read_file(file_path, offset, offset + limit - 1)` 一致
    - 标签：`# Feature: read-file-tool, Property 1: Delegation consistency`
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5**

  - [x] 2.3 编写属性测试：Invalid parameter rejection（Property 2）
    - 生成 offset < 1 或 limit < 1 的随机整数
    - 验证抛出 `ToolExecutionError`，错误信息包含参数名
    - 标签：`# Feature: read-file-tool, Property 2: Invalid parameter rejection`
    - **Validates: Requirements 4.1, 4.2**

  - [x] 2.4 编写属性测试：Read idempotence（Property 3）
    - 生成随机文本文件，随机 offset 和 limit
    - 验证连续两次调用返回相同结果
    - 标签：`# Feature: read-file-tool, Property 3: Read idempotence`
    - **Validates: Requirements 6.1**

  - [x] 2.5 编写属性测试：Pagination consistency（Property 4）
    - 生成随机文本文件，随机 page_size
    - 按 page_size 分页读取并拼接，与一次性全量读取比较
    - 标签：`# Feature: read-file-tool, Property 4: Pagination consistency`
    - **Validates: Requirements 6.2**

  - [x] 2.6 编写属性测试：Non-existent file raises error（Property 5）
    - 生成不存在的文件路径
    - 验证抛出 `ToolExecutionError`，错误信息包含文件路径
    - 标签：`# Feature: read-file-tool, Property 5: Non-existent file raises error`
    - **Validates: Requirements 3.1**

- [x] 3. 编写单元测试
  - [x] 3.1 编写基本定义和 schema 验证测试
    - 验证 `name == "read_file"`、`description` 非空中文
    - 验证 `parameters` schema 结构正确（type, properties, required）
    - 验证 `to_schema()` 返回 OpenAI function calling 格式
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 5.4_

  - [x] 3.2 编写默认参数和 ToolRegistry 集成测试
    - 验证仅传 file_path 时使用 offset=1, limit=200
    - 验证通过 ToolRegistry.register() 注册后可通过 ToolCallRequest 调用
    - 验证 ToolCallRequest.arguments 中缺少 offset/limit 时使用默认值
    - _Requirements: 2.1, 5.1, 5.2, 5.3_

  - [x] 3.3 编写错误处理测试
    - 验证目录路径抛出 ToolExecutionError
    - 验证二进制文件抛出 ToolExecutionError
    - 验证权限不足抛出 ToolExecutionError
    - 验证 offset 超出文件行数返回空内容
    - _Requirements: 3.2, 3.3, 3.4_

- [x] 4. Checkpoint - 运行所有测试确保通过
  - 运行 `uv run pytest test/infrastructure/tools/filesystem/ -v` 确保所有测试通过

## Notes

- 所有测试文件放在 `test/infrastructure/tools/filesystem/` 目录下
- 属性测试使用 `@settings(max_examples=100, deadline=2000)` 配置
- 属性测试函数需包含注释标签：`# Feature: read-file-tool, Property {N}: {title}`
- 测试中使用 `tmp_path` fixture 创建临时文件
- ReadFileTool 的 execute 方法虽然是 async，但底层 read_file() 是同步 I/O，这在当前项目中是可接受的
