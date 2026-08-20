# Requirements Document

## Introduction

本特性为文件系统 Agent 实现 EditFile 工具，位于 `infrastructure/tools/filesystem/edit_file_tool.py`。该工具继承 `domain/agent/tools.py` 中的 `Tool` 抽象基类，提供文件内容编辑能力，支持通过替换指定文本片段来修改文件内容，并在精确匹配失败时自动尝试回退匹配策略（如忽略空白差异）。

工具底层需在 `common/tools/common_tools.py` 中新增 `edit_file()` 工具函数（与已有的 `read_file()` 和 `write_file()` 对称），EditFileTool 作为基础设施层适配器将其适配为符合 Tool 抽象接口的标准工具实现，可注册到 `ToolRegistry` 供 LLM Agent 调用。

## Glossary

- **EditFileTool**: 继承自 `Tool` 抽象基类的具体工具实现，提供文件内容编辑功能（文本替换）
- **Tool**: `domain/agent/tools.py` 中定义的工具抽象基类，规定了 name、description、parameters、execute 等接口
- **ToolRegistry**: 工具注册表，管理所有已注册 Tool 实例
- **edit_file**: `common/tools/common_tools.py` 中待新增的文件编辑工具函数
- **file_path**: 目标文件路径参数，指定要编辑的文件
- **old_str**: 要被替换的原始文本片段
- **new_str**: 用于替换的新文本片段
- **Exact_Match**: 精确匹配策略，要求 old_str 与文件中的文本完全一致（包括空白字符）
- **Fallback_Match**: 回退匹配策略，当精确匹配失败时，忽略每行的前导/尾随空白进行匹配
- **ToolExecutionError**: 工具执行异常，当文件编辑过程中发生错误时抛出
- **ToolParameterValidationError**: 工具参数校验异常，当参数不符合 schema 约束时抛出
- **ToolCallRequest**: LLM 返回的工具调用请求值对象，包含 id、name 和 arguments 字段

## Requirements

### Requirement 1: edit_file 工具函数

**User Story:** 作为 AI Agent 开发者，我希望在 common 层有一个 `edit_file()` 工具函数，以便 EditFileTool 能复用统一的文件编辑逻辑，与 `read_file()` 和 `write_file()` 形成对称设计。

#### Acceptance Criteria

1. THE edit_file 函数 SHALL 位于 `common/tools/common_tools.py` 模块中，接受 `file_path`（str）、`old_str`（str）和 `new_str`（str）三个参数
2. WHEN edit_file 被调用且 file_path 指向的文件中包含 old_str 时，THE edit_file 函数 SHALL 将第一次出现的 old_str 替换为 new_str，并将结果写回文件
3. THE edit_file 函数 SHALL 仅替换第一次出现的 old_str，文件中后续出现的相同文本保持不变
4. WHEN 精确匹配（Exact_Match）未找到 old_str 时，THE edit_file 函数 SHALL 自动尝试 Fallback_Match 策略：将 old_str 和文件内容的每一行都去除前导和尾随空白后进行比较
5. WHEN Fallback_Match 成功匹配时，THE edit_file 函数 SHALL 替换文件中对应的原始文本（保留文件中匹配区域之外的原始缩进），并将 new_str 写入匹配位置
6. IF file_path 指向的文件不存在，THEN THE edit_file 函数 SHALL 返回以 `"错误："` 开头的字符串，说明文件不存在
7. IF file_path 指向一个已存在的目录路径，THEN THE edit_file 函数 SHALL 返回以 `"错误："` 开头的字符串，说明路径是目录而非文件
8. IF old_str 在文件中既无法通过 Exact_Match 也无法通过 Fallback_Match 找到，THEN THE edit_file 函数 SHALL 返回以 `"错误："` 开头的字符串，说明未找到匹配的文本
9. WHEN edit_file 成功替换文本后，THE edit_file 函数 SHALL 返回写入的字节数（整数）
10. THE edit_file 函数 SHALL 使用 UTF-8 编码读取和写入文件内容
11. IF 读写过程中发生权限不足错误，THEN THE edit_file 函数 SHALL 抛出 PermissionError（与 read_file 和 write_file 的行为模式一致）

### Requirement 2: EditFileTool 基本定义

**User Story:** 作为 AI Agent 开发者，我希望有一个 EditFileTool 工具实现，以便 LLM Agent 能够通过文本替换来编辑文件。

#### Acceptance Criteria

1. THE EditFileTool SHALL 继承自 `Tool` 抽象基类，位于 `infrastructure/tools/filesystem/edit_file_tool.py` 模块中
2. THE EditFileTool 的 `name` 属性 SHALL 返回 `"edit_file"`
3. THE EditFileTool 的 `description` 属性 SHALL 返回描述文件编辑功能的英文字符串
4. THE EditFileTool 的 `parameters` 属性 SHALL 返回符合 JSON Schema 规范的参数描述字典，包含 `file_path`、`old_str` 和 `new_str` 三个参数定义，所有描述文本使用英文
5. THE EditFileTool 的 `parameters` 中 `file_path` SHALL 声明为 `"type": "string"`，且列入 `"required"` 列表
6. THE EditFileTool 的 `parameters` 中 `old_str` SHALL 声明为 `"type": "string"`，且列入 `"required"` 列表
7. THE EditFileTool 的 `parameters` 中 `new_str` SHALL 声明为 `"type": "string"`，且列入 `"required"` 列表

### Requirement 3: 文件内容编辑执行

**User Story:** 作为 AI Agent 开发者，我希望 EditFileTool 能将文件中指定的文本片段替换为新内容，以便 LLM Agent 能够精确修改项目文件。

#### Acceptance Criteria

1. WHEN `execute` 方法被调用且提供 `file_path`、`old_str` 和 `new_str` 参数时，THE EditFileTool SHALL 将 file_path 指定文件中第一次出现的 old_str 替换为 new_str
2. WHEN old_str 的精确匹配失败时，THE EditFileTool SHALL 自动尝试 Fallback_Match 策略进行匹配和替换
3. THE EditFileTool 成功编辑后 SHALL 返回包含写入字节数的结果字符串
4. WHEN new_str 为空字符串时，THE EditFileTool SHALL 删除 old_str 对应的文本（等效于用空字符串替换）

### Requirement 4: 错误处理

**User Story:** 作为 AI Agent 开发者，我希望 EditFileTool 能优雅地处理各种文件编辑错误，以便 LLM 能收到清晰的错误信息并据此调整行为。

#### Acceptance Criteria

1. IF 指定的 `file_path` 对应的文件不存在，THEN THE EditFileTool SHALL 抛出 `ToolExecutionError`，错误信息中包含文件路径
2. IF 指定的 `file_path` 对应的路径是已存在的目录而非文件，THEN THE EditFileTool SHALL 抛出 `ToolExecutionError`，错误信息中包含路径信息
3. IF old_str 在文件中无法匹配（精确匹配和回退匹配均失败），THEN THE EditFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明未找到匹配文本
4. IF 读写文件时发生权限不足错误，THEN THE EditFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明权限不足
5. IF edit_file 函数返回以 `"错误："` 开头的字符串，THEN THE EditFileTool SHALL 将其转换为 `ToolExecutionError` 抛出

### Requirement 5: 回退匹配策略

**User Story:** 作为 AI Agent 开发者，我希望 EditFileTool 在精确匹配失败时能通过忽略空白差异进行回退匹配，以便 LLM 生成的代码片段即使缩进不完全一致也能成功匹配。

#### Acceptance Criteria

1. WHEN old_str 在文件中无法精确匹配时，THE edit_file 函数 SHALL 将 old_str 按行分割，并将每行去除前导和尾随空白后，与文件内容的每行（同样去除前导和尾随空白后）进行逐行比较
2. WHEN Fallback_Match 找到匹配时，THE edit_file 函数 SHALL 替换文件中从匹配起始行到匹配结束行的原始内容为 new_str
3. IF old_str 为空字符串，THEN THE edit_file 函数 SHALL 返回以 `"错误："` 开头的字符串，说明 old_str 不能为空
4. WHEN Fallback_Match 在文件中找到多个匹配位置时，THE edit_file 函数 SHALL 仅替换第一个匹配位置的内容

### Requirement 6: 与 Tool 基类流水线集成

**User Story:** 作为 AI Agent 开发者，我希望 EditFileTool 能通过 `Tool.run()` 方法处理 `ToolCallRequest`，以便与 ToolRegistry 和 LLM 调用链路无缝集成。

#### Acceptance Criteria

1. WHEN EditFileTool 通过 `ToolRegistry.register()` 注册后，THE ToolRegistry SHALL 能通过 `execute(ToolCallRequest)` 方法调用 EditFileTool
2. THE EditFileTool 的 `to_schema()` 方法 SHALL 返回符合 OpenAI function calling 格式的 schema 字典，其中 `"name"` 为 `"edit_file"`
3. WHEN `ToolCallRequest.arguments` 中缺少 `file_path`、`old_str` 或 `new_str` 参数时，THE Tool 基类的 validate_params 方法 SHALL 抛出 `ToolParameterValidationError`

### Requirement 7: 编辑后读取一致性（Round-Trip）

**User Story:** 作为 AI Agent 开发者，我希望通过 EditFileTool 编辑后的文件内容能通过 ReadFileTool 正确读回，以便确保编辑工具与读取工具之间的数据一致性。

#### Acceptance Criteria

1. FOR ALL 有效的 file_path、old_str 和 new_str，当文件包含 old_str 时，使用 EditFileTool 替换后，使用 `read_file()` 读取同一文件 SHALL 返回包含 new_str 且不包含被替换的 old_str 的内容（仅针对第一次出现）
2. FOR ALL 有效的 file_path 和 old_str，使用 EditFileTool 将 old_str 替换为 old_str 本身 SHALL 产生与原文件相同的内容（自替换幂等性）

### Requirement 8: 模块导出

**User Story:** 作为 AI Agent 开发者，我希望 EditFileTool 能通过 `infrastructure/tools/filesystem` 包导入，以便与 ReadFileTool 和 WriteFileTool 保持一致的导入方式。

#### Acceptance Criteria

1. THE `infrastructure/tools/filesystem/__init__.py` SHALL 导出 `EditFileTool`，使其可通过 `from infrastructure.tools.filesystem import EditFileTool` 导入
