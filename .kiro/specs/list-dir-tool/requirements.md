# Requirements Document

## Introduction

实现 ListDirTool，为 LLM Agent 提供目录内容列举能力。该工具继承自 Tool 抽象基类，复用公共层 `common/tools/common_tools.py` 中已有的 `tree()` 函数，将其适配为符合 Tool 接口规范的标准工具实现。支持递归展示嵌套目录结构，并自动忽略常见噪声目录（.git、node_modules、\_\_pycache\_\_、.venv 等）。

## Glossary

- **ListDirTool**: 目录内容列举工具，继承自 Tool 抽象基类，作为基础设施层适配器
- **Tool**: 定义在 `domain/agent/tools.py` 中的工具抽象基类，规定 name、description、parameters、execute 接口
- **tree()**: 定义在 `common/tools/common_tools.py` 中的公共函数，以树形结构展示目录内容
- **ToolExecutionError**: 定义在 `domain/agent/exceptions.py` 中的工具执行异常类
- **ToolRegistry**: 工具注册表，集中管理所有已注册的 Tool 实例
- **噪声目录**: 对 Agent 无意义的常见目录，如 .git、node_modules、\_\_pycache\_\_、.venv、.idea、.hypothesis 等
- **directory_path**: 必填参数，指定要列举的目标目录路径
- **recursive**: 可选布尔参数，控制是否递归展示嵌套子目录结构，默认为 true

## Requirements

### Requirement 1: 目录内容列举

**User Story:** As an LLM Agent, I want to list the contents of a directory in a tree-like format, so that I can understand the project structure and navigate the codebase.

#### Acceptance Criteria

1. WHEN a valid directory_path is provided, THE ListDirTool SHALL return a tree-formatted string showing the directory contents by delegating to the tree() function.
2. WHEN recursive is set to true (or omitted), THE ListDirTool SHALL display the full nested directory structure including all subdirectories and their contents.
3. WHEN recursive is set to false, THE ListDirTool SHALL display only the immediate children (files and directories) of the specified directory without descending into subdirectories.
4. THE ListDirTool SHALL auto-ignore the following noise directories: .git, node_modules, \_\_pycache\_\_, .venv, .idea, .hypothesis, .mypy_cache, .pytest_cache, .tox, .eggs, .svn, .hg.

### Requirement 2: Tool 接口规范

**User Story:** As a ToolRegistry consumer, I want ListDirTool to conform to the Tool abstract interface, so that it can be registered and invoked like any other tool.

#### Acceptance Criteria

1. THE ListDirTool SHALL inherit from the Tool abstract base class and implement all required abstract members: name, description, parameters, execute.
2. THE ListDirTool SHALL return "list_dir" as its tool name.
3. THE ListDirTool SHALL expose a parameters property returning a JSON Schema with directory_path (required, string) and recursive (optional, boolean, default true).
4. THE ListDirTool SHALL be registerable with ToolRegistry and invocable via ToolRegistry.execute().

### Requirement 3: 错误处理

**User Story:** As an LLM Agent, I want clear error messages when directory listing fails, so that I can understand what went wrong and adjust my request.

#### Acceptance Criteria

1. WHEN directory_path points to a non-existent path, THE ListDirTool SHALL raise a ToolExecutionError containing the path in the error message.
2. WHEN directory_path points to a file instead of a directory, THE ListDirTool SHALL raise a ToolExecutionError indicating the path is not a directory.
3. IF a PermissionError occurs during directory listing, THEN THE ListDirTool SHALL raise a ToolExecutionError indicating insufficient permissions.
4. WHEN the tree() function returns a string starting with "错误：", THE ListDirTool SHALL convert the error string into a ToolExecutionError.

### Requirement 4: 非递归模式实现

**User Story:** As an LLM Agent, I want to list only the top-level contents of a directory without recursion, so that I can get a quick overview without overwhelming output for large projects.

#### Acceptance Criteria

1. WHEN recursive is false, THE ListDirTool SHALL list each immediate child entry on a separate line, prefixed with a tree connector (├── or └──).
2. WHEN recursive is false, THE ListDirTool SHALL still apply the noise directory ignore list to filter out unwanted entries.
3. WHEN recursive is false, THE ListDirTool SHALL sort entries with directories first, then files, both in case-insensitive alphabetical order (consistent with tree() behavior).

### Requirement 5: 幂等性与一致性

**User Story:** As a test engineer, I want the tool's output to be deterministic and consistent, so that I can write reliable property-based tests.

#### Acceptance Criteria

1. FOR ALL valid directory_path values, calling ListDirTool.execute() twice with the same parameters on an unchanged directory SHALL produce identical results (idempotence).
2. WHEN recursive is true, THE ListDirTool output SHALL be consistent with directly calling tree(Path(directory_path), ignore=NOISE_SET) on the same directory.
3. FOR ALL directories containing only noise directories, THE ListDirTool SHALL return an empty string or a string with no file/directory entries listed.
