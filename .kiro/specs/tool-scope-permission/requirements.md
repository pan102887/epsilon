# Requirements Document

## Introduction

为 ToolRegistry 增加工具作用域（Scoped View）和执行层权限校验功能。

当前系统中，ToolRegistry 以单例模式管理所有已注册工具。AgentConfig.tool_schemas 字段控制 LLM 能"看到"哪些工具（schema 层面），但 ReActAgentAdapter 在执行工具时直接调用 `self._tool_registry.execute(tool_call)`，未校验 tool_call.name 是否在当前 AgentConfig 允许的工具集合内。这意味着 LLM 若幻觉出一个不在 schema 里的工具名，ToolRegistry 仍然会执行它，构成安全隐患。

本特性通过以下手段解决该问题：
1. ToolRegistry 新增 `create_scoped_view(tool_names)` 方法，返回一个只暴露指定工具子集的轻量视图对象（ScopedToolRegistry），不改变单例模式。
2. ReActAgentAdapter 在执行工具前校验 tool_call.name 是否在当前 AgentConfig 允许的工具集合内，拒绝未授权的工具调用。
3. AgentConfig 新增 `allowed_tool_names: frozenset[str]` 字段，显式声明允许的工具名称集合，使权限校验更清晰。
4. TaskAgentAdapter 支持通过 Task 定义或配置指定工具子集，而非始终获取全量工具 schema。
5. 保持 ToolRegistry 单例不变，通过工厂方法创建 scoped view，支持多 Agent 编排场景。

## Glossary

- **ToolRegistry**: 工具注册表，领域层单例，集中管理所有已注册的 Tool 实例，提供注册、查找、移除和执行能力。
- **ScopedToolRegistry**: 工具作用域视图，ToolRegistry 的轻量包装器，仅暴露指定工具子集的 schema 和执行能力，不持有独立的工具存储。
- **Tool**: 工具抽象基类，定义工具的统一接口规范（名称、描述、参数 schema、执行逻辑）。
- **AgentConfig**: Agent 执行配置值对象（frozen dataclass），封装单次 Agent 执行所需的全部配置参数。
- **ReActAgentAdapter**: ReAct 模式 Agent 适配器，实现 AgentPort 协议，封装"推理→行动→观察"循环逻辑。
- **TaskAgentAdapter**: 面向任务的 Agent 适配器，实现 TaskAgentPort 协议，将 Task 转换为 AgentConfig 委托 AgentPort 执行。
- **ChatServiceAdapter**: 聊天服务适配器（编排层），实现 ChatServicePort，负责对话流程编排和执行路径选择。
- **ToolCallRequest**: LLM 返回的工具调用请求值对象，包含 id、name 和 arguments 字段。
- **ToolNotFoundError**: 工具未找到异常（错误码 60002），当请求的工具名称未在注册表中时抛出。
- **ToolPermissionDeniedError**: 工具权限拒绝异常（新增，错误码 60004），当请求的工具名称不在当前允许的工具集合内时抛出。
- **allowed_tool_names**: AgentConfig 中新增的 frozenset[str] 字段，显式声明当前 Agent 执行允许调用的工具名称集合。

## Requirements

### Requirement 1: ToolRegistry 支持按名称子集过滤 get_schemas

**User Story:** 作为编排层开发者，我希望 ToolRegistry 能按工具名称子集返回 schema 列表，以便为不同 Agent 提供不同的工具视图。

#### Acceptance Criteria

1. WHEN tool_names 参数为 None 时，THE ToolRegistry.get_schemas() SHALL 返回所有已注册工具的 schema 列表（向后兼容行为）。
2. WHEN tool_names 参数为非空 set[str] 时，THE ToolRegistry.get_schemas() SHALL 仅返回名称在 tool_names 集合中的工具的 schema 列表。
3. WHEN tool_names 参数包含未注册的工具名称时，THE ToolRegistry.get_schemas() SHALL 静默忽略未注册的名称，仅返回已注册且在 tool_names 中的工具 schema。
4. WHEN tool_names 参数为空 set 时，THE ToolRegistry.get_schemas() SHALL 返回空列表。

### Requirement 2: ToolRegistry 支持创建作用域视图

**User Story:** 作为多 Agent 编排开发者，我希望能从 ToolRegistry 单例创建只暴露指定工具子集的视图对象，以便每个 Agent 拿到自己的工具作用域而不影响全局注册表。

#### Acceptance Criteria

1. WHEN create_scoped_view(tool_names) 被调用时，THE ToolRegistry SHALL 返回一个 ScopedToolRegistry 实例，该实例仅暴露 tool_names 指定的工具子集。
2. THE ScopedToolRegistry SHALL 提供与 ToolRegistry 相同的 get_schemas() 和 execute() 接口。
3. WHEN ScopedToolRegistry.get_schemas() 被调用时，THE ScopedToolRegistry SHALL 仅返回作用域内工具的 schema 列表。
4. WHEN ScopedToolRegistry.execute(request) 被调用且 request.name 在作用域内时，THE ScopedToolRegistry SHALL 委托底层 ToolRegistry 执行该工具调用。
5. WHEN ScopedToolRegistry.execute(request) 被调用且 request.name 不在作用域内时，THE ScopedToolRegistry SHALL 抛出 ToolPermissionDeniedError 异常。
6. THE ScopedToolRegistry SHALL 不持有独立的工具存储，仅持有对底层 ToolRegistry 的引用和允许的工具名称集合。
7. WHEN 底层 ToolRegistry 注册新工具后，已创建的 ScopedToolRegistry 的作用域 SHALL 保持不变（创建时快照语义）。

### Requirement 3: 新增 ToolPermissionDeniedError 异常

**User Story:** 作为 Agent 开发者，我希望当工具调用被权限校验拒绝时能收到明确的异常类型，以便区分"工具不存在"和"工具未授权"两种错误场景。

#### Acceptance Criteria

1. THE ToolPermissionDeniedError SHALL 继承自 ToolExecutionError 基类，使用错误码 60004。
2. THE ToolPermissionDeniedError SHALL 包含 tool_name 属性，标识被拒绝的工具名称。
3. THE ToolPermissionDeniedError SHALL 包含 allowed_tools 属性，标识当前允许的工具名称集合。
4. THE ToolPermissionDeniedError 的 message SHALL 包含被拒绝的工具名称和当前允许的工具列表信息。

### Requirement 4: AgentConfig 新增 allowed_tool_names 字段

**User Story:** 作为 Agent 开发者，我希望 AgentConfig 显式声明允许的工具名称集合，以便执行层进行权限校验时有明确的依据。

#### Acceptance Criteria

1. THE AgentConfig SHALL 包含 allowed_tool_names 字段，类型为 frozenset[str]。
2. WHEN allowed_tool_names 未显式传入时，THE AgentConfig SHALL 从 tool_schemas 列表中自动提取工具名称作为默认值。
3. THE AgentConfig.allowed_tool_names 的自动提取逻辑 SHALL 从每个 schema 字典的 `["function"]["name"]` 路径读取工具名称。
4. WHEN allowed_tool_names 显式传入时，THE AgentConfig SHALL 使用传入的值，不执行自动提取。
5. THE AgentConfig.allowed_tool_names SHALL 为 frozenset 类型，确保不可变性。

### Requirement 5: ReActAgentAdapter 执行前权限校验

**User Story:** 作为系统安全负责人，我希望 ReActAgentAdapter 在执行工具前校验工具名称是否在允许集合内，以防止 LLM 幻觉调用未授权的工具。

#### Acceptance Criteria

1. WHEN ReActAgentAdapter 接收到 LLM 返回的 tool_call 时，THE ReActAgentAdapter SHALL 在调用 ToolRegistry.execute() 之前校验 tool_call.name 是否在 AgentConfig.allowed_tool_names 中。
2. WHEN tool_call.name 在 AgentConfig.allowed_tool_names 中时，THE ReActAgentAdapter SHALL 正常执行该工具调用。
3. WHEN tool_call.name 不在 AgentConfig.allowed_tool_names 中时，THE ReActAgentAdapter SHALL 将 ToolPermissionDeniedError 的错误信息作为 ToolMessage 的 content 追加到上下文，Agent Loop 继续运行。
4. THE ReActAgentAdapter 的权限校验逻辑 SHALL 同时应用于 run() 同步模式和 run_streaming() 流式模式。
5. WHEN 权限校验拒绝工具调用时，THE ReActAgentAdapter SHALL 记录 WARNING 级别日志，包含被拒绝的工具名称和当前允许的工具集合。

### Requirement 6: ChatServiceAdapter 传递 allowed_tool_names

**User Story:** 作为编排层开发者，我希望 ChatServiceAdapter 在构造 AgentConfig 时正确传递工具名称集合，以确保权限校验链路完整。

#### Acceptance Criteria

1. WHEN ChatServiceAdapter 构造 AgentConfig 时，THE ChatServiceAdapter SHALL 将 tool_schemas 中的工具名称提取为 allowed_tool_names 传入 AgentConfig（或依赖 AgentConfig 的自动提取默认值）。
2. THE ChatServiceAdapter 构造的 AgentConfig.allowed_tool_names SHALL 与 AgentConfig.tool_schemas 中的工具名称集合一致。

### Requirement 7: TaskAgentAdapter 支持工具子集配置

**User Story:** 作为任务编排开发者，我希望 TaskAgentAdapter 能根据 Task 定义指定工具子集，而非始终使用全量工具，以便不同任务使用不同的工具集合。

#### Acceptance Criteria

1. WHEN Task 值对象包含 tool_names 字段且不为 None 时，THE TaskAgentAdapter SHALL 使用 ToolRegistry.get_schemas(tool_names=task.tool_names) 获取工具子集 schema。
2. WHEN Task 值对象的 tool_names 字段为 None 时，THE TaskAgentAdapter SHALL 使用 ToolRegistry.get_schemas() 获取全量工具 schema（向后兼容行为）。
3. THE TaskAgentAdapter 构造的 AgentConfig.allowed_tool_names SHALL 与实际传入的 tool_schemas 中的工具名称集合一致。

### Requirement 8: Task 值对象扩展 tool_names 字段

**User Story:** 作为任务定义者，我希望 Task 值对象支持指定工具名称子集，以便在任务级别控制可用工具范围。

#### Acceptance Criteria

1. THE Task 值对象 SHALL 包含 tool_names 字段，类型为 frozenset[str] | None。
2. WHEN tool_names 未传入时，THE Task.tool_names SHALL 默认为 None，表示使用全量工具。
3. THE Task.tool_names SHALL 为 frozenset 类型或 None，确保不可变性。
