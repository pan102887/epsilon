# Requirements Document

## Introduction

本特性为文件系统 Agent 实现 ReadFile 工具，位于 `infrastructure/tools/filesystem/read_file_tool.py`。该工具继承 `domain/agent/tools.py` 中的 `Tool` 抽象基类，提供文件内容读取能力，返回带行号的文件内容，并支持通过 `offset`（起始行号）和 `limit`（读取行数）参数对大文件进行分页读取。

工具底层复用 `common/tools/common_tools.py` 中已有的 `read_file()` 工具函数，将其适配为符合 Tool 抽象接口的标准工具实现，可注册到 `ToolRegistry` 供 LLM Agent 调用。

## Glossary

- **ReadFileTool**: 继承自 `Tool` 抽象基类的具体工具实现，提供文件内容读取功能
- **Tool**: `domain/agent/tools.py` 中定义的工具抽象基类，规定了 name、description、parameters、execute 等接口
- **ToolRegistry**: 工具注册表，管理所有已注册 Tool 实例
- **offset**: 起始行号参数（从 1 开始），指定从文件的第几行开始读取
- **limit**: 读取行数参数，指定从 offset 开始最多读取多少行
- **ToolExecutionError**: 工具执行异常，当文件读取过程中发生错误时抛出
- **ToolParameterValidationError**: 工具参数校验异常，当参数不符合 schema 约束时抛出
- **ToolCallRequest**: LLM 返回的工具调用请求值对象，包含 id、name 和 arguments 字段

## Requirements

### Requirement 1: ReadFileTool 基本定义

**User Story:** 作为 AI Agent 开发者，我希望有一个 ReadFileTool 工具实现，以便 LLM Agent 能够读取文件内容并返回带行号的结果。

#### Acceptance Criteria

1. THE ReadFileTool SHALL 继承自 `Tool` 抽象基类，位于 `infrastructure/tools/filesystem/read_file_tool.py` 模块中
2. THE ReadFileTool 的 `name` 属性 SHALL 返回 `"read_file"`
3. THE ReadFileTool 的 `description` 属性 SHALL 返回描述文件读取功能的中文字符串
4. THE ReadFileTool 的 `parameters` 属性 SHALL 返回符合 JSON Schema 规范的参数描述字典，包含 `file_path`、`offset` 和 `limit` 三个参数定义
5. THE ReadFileTool 的 `parameters` 中 `file_path` SHALL 声明为 `"type": "string"`，且列入 `"required"` 列表
6. THE ReadFileTool 的 `parameters` 中 `offset` SHALL 声明为 `"type": "integer"`，默认值为 1，表示起始行号（从 1 开始）
7. THE ReadFileTool 的 `parameters` 中 `limit` SHALL 声明为 `"type": "integer"`，默认值为 200，表示最多读取的行数

### Requirement 2: 文件内容读取与分页

**User Story:** 作为 AI Agent 开发者，我希望 ReadFileTool 能按 offset 和 limit 分页读取文件内容，以便 LLM 能够逐段浏览大文件而不超出上下文窗口限制。

#### Acceptance Criteria

1. WHEN `execute` 方法被调用且仅提供 `file_path` 参数时，THE ReadFileTool SHALL 从第 1 行开始读取，最多返回 200 行带行号的文件内容
2. WHEN `execute` 方法被调用且提供 `offset` 参数时，THE ReadFileTool SHALL 从第 `offset` 行开始读取文件内容
3. WHEN `execute` 方法被调用且提供 `limit` 参数时，THE ReadFileTool SHALL 最多返回 `limit` 行文件内容
4. WHEN `execute` 方法被调用且同时提供 `offset` 和 `limit` 参数时，THE ReadFileTool SHALL 从第 `offset` 行开始，最多返回 `limit` 行带行号的文件内容
5. THE ReadFileTool 返回的每一行 SHALL 包含行号前缀，格式与 `common/tools/common_tools.py` 中 `read_file()` 函数的输出格式一致

### Requirement 3: 错误处理

**User Story:** 作为 AI Agent 开发者，我希望 ReadFileTool 能优雅地处理各种文件读取错误，以便 LLM 能收到清晰的错误信息并据此调整行为。

#### Acceptance Criteria

1. IF 指定的 `file_path` 对应的文件不存在，THEN THE ReadFileTool SHALL 抛出 `ToolExecutionError`，错误信息中包含文件路径
2. IF 指定的 `file_path` 对应的路径是目录而非文件，THEN THE ReadFileTool SHALL 抛出 `ToolExecutionError`，错误信息中包含路径信息
3. IF 指定的文件无法以 UTF-8 编码读取（如二进制文件），THEN THE ReadFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明可能是二进制文件
4. IF 读取文件时发生权限不足错误，THEN THE ReadFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明权限不足

### Requirement 4: 参数校验

**User Story:** 作为 AI Agent 开发者，我希望 ReadFileTool 能校验 offset 和 limit 参数的合法性，以便防止无效的分页请求。

#### Acceptance Criteria

1. IF `offset` 参数值小于 1，THEN THE ReadFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明 offset 必须大于等于 1
2. IF `limit` 参数值小于 1，THEN THE ReadFileTool SHALL 抛出 `ToolExecutionError`，错误信息中说明 limit 必须大于等于 1

### Requirement 5: 与 Tool 基类流水线集成

**User Story:** 作为 AI Agent 开发者，我希望 ReadFileTool 能通过 `Tool.run()` 方法处理 `ToolCallRequest`，以便与 ToolRegistry 和 LLM 调用链路无缝集成。

#### Acceptance Criteria

1. WHEN ReadFileTool 通过 `ToolRegistry.register()` 注册后，THE ToolRegistry SHALL 能通过 `execute(ToolCallRequest)` 方法调用 ReadFileTool
2. WHEN `ToolCallRequest.arguments` 中未提供 `offset` 参数时，THE ReadFileTool SHALL 使用默认值 1
3. WHEN `ToolCallRequest.arguments` 中未提供 `limit` 参数时，THE ReadFileTool SHALL 使用默认值 200
4. THE ReadFileTool 的 `to_schema()` 方法 SHALL 返回符合 OpenAI function calling 格式的 schema 字典，其中 `"name"` 为 `"read_file"`

### Requirement 6: 分页读取的幂等性与一致性

**User Story:** 作为 AI Agent 开发者，我希望对同一文件使用相同的 offset 和 limit 参数多次读取时，返回结果保持一致，以便 LLM 能可靠地拼接分页结果。

#### Acceptance Criteria

1. FOR ALL 有效的 offset 和 limit 组合，对同一未修改文件连续两次调用 ReadFileTool SHALL 返回相同的结果（幂等性）
2. FOR ALL 有效的分页参数，将文件按 limit 大小分页读取后拼接的内容 SHALL 与一次性读取整个文件的内容等价（分页一致性）
