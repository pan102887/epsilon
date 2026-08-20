# Implementation Plan: EditFileTool

## Overview

在 `common/tools/common_tools.py` 中新增 `edit_file()` 工具函数（两阶段匹配：精确 → 回退），然后在 `infrastructure/tools/filesystem/edit_file_tool.py` 中实现 `EditFileTool(Tool)` 适配器，最后通过属性测试和单元测试验证正确性。采用自底向上的顺序：先实现 common 层函数 → 再实现 Tool 适配器 → 编写属性测试 → 补充单元测试。

## Tasks

- [x] 1. 实现 edit_file() 工具函数
  - [x] 1.1 在 `common/tools/common_tools.py` 中新增 `edit_file(file_path, old_str, new_str)` 函数
    - 前置校验：old_str 为空字符串时返回 `"错误：old_str 不能为空"`
    - 文件校验：不存在返回 `"错误：文件不存在 - {file_path}"`，是目录返回 `"错误：路径是目录而非文件 - {file_path}"`
    - 精确匹配：使用 `str.find(old_str)` 查找第一次出现，找到则用 `str.replace(old_str, new_str, 1)` 替换
    - 回退匹配：精确匹配失败时，将 old_str 和文件内容按行分割，每行 strip() 后逐行比较，找到第一个匹配位置后替换对应原始行范围为 new_str
    - 写回文件：UTF-8 编码写入，成功返回字节数（int）
    - 权限不足时抛出 PermissionError（与 read_file/write_file 一致）
    - 中文 docstring，包含 Args、Returns、Raises 说明
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 5.1, 5.2, 5.3, 5.4_

- [x] 2. 实现 EditFileTool 类并导出
  - [x] 2.1 在 `infrastructure/tools/filesystem/edit_file_tool.py` 中实现 `EditFileTool(Tool)` 类
    - `name` 属性返回 `"edit_file"`
    - `description` 属性返回英文功能描述（如 "Edit a file by replacing the first occurrence of a specified text string with new content. Supports fallback matching that ignores leading/trailing whitespace differences."）
    - `parameters` 属性返回 JSON Schema，包含 `file_path`、`old_str`、`new_str`（均为 required, string），所有 description 字段使用英文
    - `execute(file_path, old_str, new_str)` 异步方法：调用 `edit_file()` → 捕获 `PermissionError` 转换为 `ToolExecutionError` → 检测 `"错误："` 前缀转换为 `ToolExecutionError` → 成功返回 `"成功编辑文件 {file_path}，共 {bytes} 字节"`
    - 模块级 docstring 和类/方法 docstring 使用中文
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 2.2 更新 `infrastructure/tools/filesystem/__init__.py`，导出 `EditFileTool`
    - 在 import 和 `__all__` 中添加 EditFileTool
    - _Requirements: 8.1_

- [x] 3. Checkpoint - 确认核心实现可导入
  - 确保 `from infrastructure.tools.filesystem import EditFileTool` 可正常导入，`EditFileTool().to_schema()` 返回正确的 OpenAI function calling 格式 schema。如有问题请向用户确认。

- [x] 4. 编写属性测试
  - [x] 4.1 创建 `test/infrastructure/tools/filesystem/test_edit_file_tool_property.py`，编写属性测试：Delegation consistency（Property 1）
    - 生成随机文本文件（1~100 行），从文件内容中随机选取连续行作为 old_str，生成随机 new_str
    - 分别通过 EditFileTool.execute 和 edit_file() 执行，验证文件内容一致；验证 edit_file 返回 int 时 execute 返回成功字符串，返回错误字符串时 execute 抛出 ToolExecutionError
    - 标签：`# Feature: edit-file-tool, Property 1: Delegation consistency`
    - **Property 1: Delegation consistency**
    - **Validates: Requirements 3.1, 3.3**

  - [x] 4.2 编写属性测试：Edit-then-read round trip（Property 2）
    - 生成包含 old_str 的随机文件，执行 edit_file 后用 read_file 读回，验证内容包含 new_str
    - 标签：`# Feature: edit-file-tool, Property 2: Edit-then-read round trip`
    - **Property 2: Edit-then-read round trip**
    - **Validates: Requirements 1.2, 7.1**

  - [x] 4.3 编写属性测试：Self-replacement idempotence（Property 3）
    - 生成包含 old_str 的随机文件，执行 edit_file(file_path, old_str, old_str)，验证文件内容与原始内容字节一致
    - 标签：`# Feature: edit-file-tool, Property 3: Self-replacement idempotence`
    - **Property 3: Self-replacement idempotence**
    - **Validates: Requirements 7.2**

  - [x] 4.4 编写属性测试：Only-first-occurrence replacement（Property 4）
    - 生成包含 old_str 至少两次的随机文件，执行 edit_file 后验证 old_str 仍存在（第二次及后续出现未被替换），且出现次数恰好减少 1（当 new_str 不包含 old_str 时）
    - 标签：`# Feature: edit-file-tool, Property 4: Only-first-occurrence replacement`
    - **Property 4: Only-first-occurrence replacement**
    - **Validates: Requirements 1.3, 5.4**

  - [x] 4.5 编写属性测试：Fallback matching succeeds on whitespace differences（Property 5）
    - 生成随机文件，从中选取连续行作为 old_str，对 old_str 每行添加随机前导/尾随空白使精确匹配失败，验证 edit_file 仍返回 int（回退匹配成功）
    - 标签：`# Feature: edit-file-tool, Property 5: Fallback matching succeeds on whitespace differences`
    - **Property 5: Fallback matching succeeds on whitespace differences**
    - **Validates: Requirements 1.4, 1.5, 3.2, 5.1, 5.2**

  - [x] 4.6 编写属性测试：Non-existent file raises error（Property 6）
    - 生成不存在的文件路径，验证 EditFileTool.execute 抛出 ToolExecutionError，错误信息包含文件路径
    - 标签：`# Feature: edit-file-tool, Property 6: Non-existent file raises error`
    - **Property 6: Non-existent file raises error**
    - **Validates: Requirements 1.6, 4.1**

  - [x] 4.7 编写属性测试：No-match raises error（Property 7）
    - 生成随机文件，使用 UUID 等保证不在文件中出现的随机文本作为 old_str，验证 EditFileTool.execute 抛出 ToolExecutionError
    - 标签：`# Feature: edit-file-tool, Property 7: No-match raises error`
    - **Property 7: No-match raises error**
    - **Validates: Requirements 1.8, 4.3**

  - [x] 4.8 编写属性测试：Missing required params raises validation error（Property 8）
    - 随机选择 {file_path, old_str, new_str} 的子集（缺少至少一个必填参数），通过 Tool.run(ToolCallRequest) 调用，验证抛出 ToolParameterValidationError
    - 标签：`# Feature: edit-file-tool, Property 8: Missing required params raises validation error`
    - **Property 8: Missing required params raises validation error**
    - **Validates: Requirements 6.3**

- [x] 5. 编写单元测试
  - [x] 5.1 创建 `test/infrastructure/tools/filesystem/test_edit_file_tool.py`，编写基本定义和 schema 验证测试
    - 验证 `name == "edit_file"`、`description` 为非空英文字符串
    - 验证 `parameters` schema 结构正确（type, properties, required 包含 file_path/old_str/new_str）
    - 验证所有 parameter description 字段为英文
    - 验证 `to_schema()` 返回 OpenAI function calling 格式，name 为 "edit_file"
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 6.2_

  - [x] 5.2 编写 ToolRegistry 集成和错误处理测试
    - 验证通过 ToolRegistry.register() 注册后可通过 ToolCallRequest 调用
    - 验证目录路径抛出 ToolExecutionError
    - 验证权限不足抛出 ToolExecutionError
    - 验证 old_str 为空字符串抛出 ToolExecutionError
    - 验证 new_str 为空字符串时成功删除 old_str 对应文本
    - 验证多次出现仅替换第一次（具体示例）
    - 验证 Fallback 匹配具体示例（缩进不同但内容相同时匹配成功）
    - 验证 UTF-8 非 ASCII 内容（中文/emoji）的编辑正确性
    - _Requirements: 3.4, 4.1, 4.2, 4.3, 4.4, 5.3, 6.1, 6.3_

- [x] 6. Final checkpoint - 运行所有测试确保通过
  - 运行 `uv run pytest test/infrastructure/tools/filesystem/ -v` 确保所有测试通过，如有问题请向用户确认。

## Notes

- 所有测试文件放在 `test/infrastructure/tools/filesystem/` 目录下
- 属性测试使用 `@settings(max_examples=100, deadline=2000)` 配置
- 属性测试函数需包含注释标签：`# Feature: edit-file-tool, Property {N}: {title}`
- 测试中使用 `tmp_path` fixture 或 `tempfile.TemporaryDirectory` 创建临时文件
- 工具的 `description` 和 `parameters` 中的 description 字段必须使用英文，代码 docstring 保持中文
- Tasks marked with `*` are optional and can be skipped for faster MVP
