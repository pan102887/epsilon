# Requirements Document

## Introduction

为 ChatRequest 值对象新增 `tools` 字段，使调用方能够将 ToolRegistry.get_schemas() 返回的工具 schema 列表传递给 LLM，让模型在对话中知道有哪些工具可用并能发起 tool_calls。当前 ChatRequest 不携带工具信息，OpenAICompatibleAdapter._build_params() 也不向 OpenAI SDK 传递 `tools` 参数，导致 LLM 无法感知可用工具。

## Glossary

- **ChatRequest**: 领域层值对象（frozen dataclass），封装一次 LLM 对话调用所需的全部参数，位于 `domain/model_access/value_objects.py`
- **OpenAICompatibleAdapter**: 基础设施层适配器，将 ChatRequest 转换为 OpenAI SDK 调用参数并发起请求，位于 `infrastructure/model_access/openai_compatible_adapter.py`
- **ToolRegistry**: 工具注册表，集中管理已注册的 Tool 实例，其 get_schemas() 方法返回所有工具的 OpenAI function calling 格式 schema 列表
- **tools 字段**: ChatRequest 上新增的可选字段，类型为 `list[dict[str, Any]] | None`，用于携带工具 schema 列表
- **_build_params**: OpenAICompatibleAdapter 的私有方法，负责将 ChatRequest 转换为 OpenAI SDK `chat.completions.create()` 所需的参数字典

## Requirements

### Requirement 1: ChatRequest 新增 tools 字段

**User Story:** As a 应用层开发者, I want ChatRequest 支持携带工具 schema 列表, so that 调用 LLM 时能告知模型有哪些工具可用。

#### Acceptance Criteria

1. THE ChatRequest SHALL 包含一个名为 `tools` 的可选字段，类型为 `list[dict[str, Any]] | None`，默认值为 `None`
2. WHEN `tools` 字段为 `None` 时, THE ChatRequest SHALL 正常构造，行为与新增字段前一致
3. WHEN `tools` 字段为非空列表时, THE ChatRequest SHALL 正常构造并保留该列表的值
4. WHEN `tools` 字段为空列表 `[]` 时, THE ChatRequest SHALL 正常构造并保留空列表的值
5. THE ChatRequest SHALL 保持 frozen dataclass 语义，`tools` 字段构造后不可修改

### Requirement 2: OpenAICompatibleAdapter 传递 tools 参数

**User Story:** As a 应用层开发者, I want OpenAICompatibleAdapter 在构建 SDK 调用参数时将 tools 传递给 OpenAI API, so that LLM 能感知可用工具并在需要时发起 tool_calls。

#### Acceptance Criteria

1. WHEN ChatRequest 的 `tools` 字段为非空列表时, THE OpenAICompatibleAdapter 的 _build_params 方法 SHALL 在返回的参数字典中包含 `"tools"` 键，值为该列表
2. WHEN ChatRequest 的 `tools` 字段为 `None` 时, THE OpenAICompatibleAdapter 的 _build_params 方法 SHALL 在返回的参数字典中不包含 `"tools"` 键
3. WHEN ChatRequest 的 `tools` 字段为空列表 `[]` 时, THE OpenAICompatibleAdapter 的 _build_params 方法 SHALL 在返回的参数字典中不包含 `"tools"` 键

### Requirement 3: 调用方集成 ToolRegistry.get_schemas()

**User Story:** As a 应用层开发者, I want 在构造 ChatRequest 时能将 ToolRegistry.get_schemas() 的结果传入 tools 字段, so that LLM 对话请求自动携带所有已注册工具的 schema。

#### Acceptance Criteria

1. THE ChatRequest 的 `tools` 字段 SHALL 接受 ToolRegistry.get_schemas() 返回的 `list[dict[str, Any]]` 类型值
2. WHEN ToolRegistry 中无已注册工具时, THE get_schemas() 方法 SHALL 返回空列表，调用方可将其传入 ChatRequest 的 `tools` 字段或传入 `None`
