# Requirements Document

## Introduction

本特性为文件系统 Agent 实现 WriteFile 工具，位于 `infrastructure/tools/filesystem/write_file_tool.py`。该工具继承 `domain/agent/tools.py` 中的 `Tool` 抽象基类，提供文件内容写入能力，支持将指定内容写入给定路径的文件，并在父目录不存在时自动创建。

工具底层需在 `common/tools/common_tools.py` 中新增 `write_file()` 工具函数（与已有的 `read_file()` 对称），WriteFileTool 作为基础设施层适配器将其适配为符合 Tool 抽象接口的标准工具实现，可注册到 `ToolRegistry` 供 LLM Agent 调用。

## Glossary

- **WriteFileTool**: 继承自 `Tool` 抽象基类的具体工具实现，提供文件内容写入功能
- **Tool**: `domain/agent/tools.py` 中定义的工具抽象基类，规定了 name、description、parameters、execute 等接口
- **ToolRegistry**: 工具注册表，管理所有已注册 Tool 实例
- **write_file**: `common/tools/common_tools.py` 中待新增的文件写入工具函数
- **file_path**: 目标文件路径参数，指定写入内容的目标文件
- **content**: 写入内容参数，指定要写入文件的文本内容
- **ToolExecutionError**: 工具执行异常，当文件写入过程中发生错误时抛出
- **ToolParameterValidationError**: 工具参数校验异常，当参数不符合 schema 约束时抛出
- **ToolCallRequest**: LLM 返回的工具调用请求值对象，包含 id、name 和 arguments 字段

## Requirements

### Requirement 1: write_file 工具函数

**User Story:** 作为 AI Agent 开发者，我希望在 common 层有一个 `write_file()` 工具函数，以便 WriteFileTool 能复用统一的文件写入逻辑，与 `read_file()` 形成对称设计。

#### Acceptance Criteria

1. THE write_file 函数 SHALL 位于 `common/tools/common_tools.py` 模块中，接受 `file_path`（str）和 `content`（str）两个参数
2. WHEN write_file 被调用且 file_path 的父目录不存在时，THE write_file 函数 SHALL 自动创建所有缺失的父目录
3. WHEN write_file 被调用且 file_path 指向的文件已存在时，THE write_file 函数 SHALL 覆盖该文件的原有内容
4. WHEN write_file 被调用且 file_path 指向的文件不存在时，THE write_file 函数 SHALL 创建该文件并写入内容
5. THE write_file 函数 SHALL 使用 UTF-8 编码写入文件内容
6. WHEN write_file 成功写入文件后，THE write_file 函数 SHALL 返回写入的字节数（整数）
7. IF file_path 指向一个已存在的目录路径，THEN THE write_file 函数 SHALL 返回以 `"错误："` 开头的字符串，说明路径是目录而非文件
8. IF 写入过程中发生权限不足错误，THEN THE write_file 函数 SHALL 抛出 PermissionError（与 read_file 的行为模式一致）

### Requirement 2: WriteFileTool 基本定义

**User Story:** 作为 AI Agent 开发者，我希望有一个 WriteFileTool 工具实现，以便 LLM Agent 能够将内容写入文件。

#### Acceptance Criteria

1. THE WriteFileTool SHALL 继承自 `Tool` 抽象基类，位于 `infrastructure/tools/filesystem/write_file_tool.py` 模块中
2. THE WriteFileTool 的 `name` 属性 SHALL 返回 `"write_file"`
3. THE WriteFileTool 的 `description` 属性 SHALL 返回描述文件写入功能的中文字符串
4. THE WriteFileTool 的 `parameters` 属性 SHALL 返回符合 JSON Schema 规范的参数描述字典，包含 `file_path` 和 `content` 两个参数定义
5. THE WriteFileTool 的 `parameters` 中 `file_path` SHALL 声明为 `"type": "string"`，且列入 `"required"` 列表
6. THE WriteFileTool 的 `parameters` 中 `content` SHALL 声明为 `"type": "string"`，且列入 `"required"` 列表

### Requirement 3: 文件内容写入

**User Story:** 作为 AI Agent 开发者，我希望 WriteFileTool 能将指定内容写入文件并自动创建父目录，以便 LLM Agent 能够创建和修改项目文件。

#### Acceptance Criteria

1. WHEN `execute` 方法被调用且提供 `file_path` 和 `content` 参数时，THE WriteFileTool SHALL 将 content 写入 file_path 指定的文件
2. WHEN file_path 的父目录不存在时，THE WriteFileTool SHALL 自动创建所有缺失的父目录后再写入文件
3. WHEN file_path 指向的文件已存在时，THE WriteFileTool SHALL 覆盖该文件的原有内容
4. THE WriteFileTool 成功写入后 SHALL 返回包含写入字节数的结果字符串
5. WHEN content 为空字符串时，THE WriteFileTool SHALL 创建一个空文件（或清空已有文件内容）

### Requirement 4: 错误处理

**User Story:** 作为 AI Agent 开发者，我希望 WriteFileTool 能优雅地处理各种文件写入错误，以便 LLM 能收到清晰的错误信息并据此调整行为。

#### Acceptance Criteria

1. IF 指定的 `file_path` 对应的路径是已存在的目录而非文件，THEN THE WriteFileTool SHALL 抛出 `ToolExecutionError`，错误信息中包含路径信息
2. IF 写入文件时发生权限不足错误，THEN THE WriteFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明权限不足
3. IF write_file 函数返回以 `"错误："` 开头的字符串，THEN THE WriteFileTool SHALL 将其转换为 `ToolExecutionError` 抛出

### Requirement 5: 与 Tool 基类流水线集成

**User Story:** 作为 AI Agent 开发者，我希望 WriteFileTool 能通过 `Tool.run()` 方法处理 `ToolCallRequest`，以便与 ToolRegistry 和 LLM 调用链路无缝集成。

#### Acceptance Criteria

1. WHEN WriteFileTool 通过 `ToolRegistry.register()` 注册后，THE ToolRegistry SHALL 能通过 `execute(ToolCallRequest)` 方法调用 WriteFileTool
2. THE WriteFileTool 的 `to_schema()` 方法 SHALL 返回符合 OpenAI function calling 格式的 schema 字典，其中 `"name"` 为 `"write_file"`
3. WHEN `ToolCallRequest.arguments` 中缺少 `file_path` 或 `content` 参数时，THE Tool 基类的 validate_params 方法 SHALL 抛出 `ToolParameterValidationError`

### Requirement 6: 写入后读取一致性（Round-Trip）

**User Story:** 作为 AI Agent 开发者，我希望通过 WriteFileTool 写入的内容能通过 ReadFileTool 完整读回，以便确保写入和读取工具之间的数据一致性。

#### Acceptance Criteria

1. FOR ALL 有效的 file_path 和 content，使用 WriteFileTool 写入 content 后，使用 `read_file()` 读取同一文件 SHALL 返回与 content 等价的内容（忽略行号前缀格式）
2. FOR ALL 有效的 file_path 和 content，对同一文件连续两次调用 WriteFileTool 写入相同 content SHALL 产生相同的文件内容（写入幂等性）

### Requirement 7: 模块导出

**User Story:** 作为 AI Agent 开发者，我希望 WriteFileTool 能通过 `infrastructure/tools/filesystem` 包导入，以便与 ReadFileTool 保持一致的导入方式。

#### Acceptance Criteria

1. THE `infrastructure/tools/filesystem/__init__.py` SHALL 导出 `WriteFileTool`，使其可通过 `from infrastructure.tools.filesystem import WriteFileTool` 导入
