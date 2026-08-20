# Requirements Document

## Introduction

为系统新增 Agent 间通信机制，使多个命名 Agent 实例能够相互委派任务，实现从"单 Agent 执行"到"多 Agent 协作"的关键跳跃。

当前系统已具备完整的单 Agent 执行能力：AgentPort 定义 Agent 执行协议，ReActAgentAdapter 实现 ReAct 循环，TaskAgentPort 提供面向任务的 Agent 入口，ToolRegistry 管理工具实例。但系统中只有一个全局 Agent 实例，无法实现"Agent A 委派子任务给 Agent B"的协作模式。

本特性通过以下手段实现 Agent 间通信：
1. 定义 AgentRegistry（领域层），集中管理命名 Agent 实例，类似 ToolRegistry 管理 Tool 实例的模式。每个命名 Agent 绑定独立的 AgentConfig（system_prompt、tool_names、model 等），代表一个具有特定职责的专业 Agent。
2. 实现 DelegateToAgentTool（基础设施层），作为 Tool 子类注册到 ToolRegistry。当 LLM 决定委派子任务时，通过 function calling 调用此工具，DelegateToAgentTool 从 AgentRegistry 查找目标 Agent，构造 Task 并调用 TaskAgentPort.execute() 执行。
3. 引入递归深度限制机制，通过 Task 值对象携带当前委派深度，在 DelegateToAgentTool 执行前校验深度是否超限，防止 Agent A → B → A 的无限循环。
4. 采用上下文隔离策略，子 Agent 不继承父 Agent 的对话历史，每次委派创建独立的 ConversationContext，确保子 Agent 执行环境干净。
5. 在 DI 容器中注册 AgentRegistry 和 DelegateToAgentTool，通过 config.properties 配置递归深度上限和命名 Agent 定义。

## Glossary

- **AgentRegistry**: Agent 注册表，领域层组件，集中管理命名 Agent 实例。每个命名 Agent 由唯一名称标识，绑定独立的 AgentConfig（system_prompt、tool_names、model 等）。类似 ToolRegistry 管理 Tool 实例的模式。
- **AgentRegistryPort**: Agent 注册表端口协议（Protocol），定义 AgentRegistry 的领域层接口，包括注册、查找、列举命名 Agent 的能力。
- **DelegateToAgentTool**: 委派工具，继承 Tool ABC 的具体工具实现，注册到 ToolRegistry。接收 agent_name 和 task_goal 参数，从 AgentRegistry 查找目标 Agent，构造 Task 调用 TaskAgentPort.execute()，将 TaskResult 返回给调用方 Agent。
- **NamedAgentConfig**: 命名 Agent 配置值对象（frozen dataclass），封装一个命名 Agent 的完整定义，包括名称、描述、system_prompt、tool_names、model 等字段。
- **AgentRegistry**: Agent 注册表，领域层组件，集中管理 NamedAgentConfig 实例。
- **delegation_depth**: 委派深度，表示当前 Agent 执行处于第几层委派。根 Agent 的 delegation_depth 为 0，每次委派子任务时 depth + 1。
- **max_delegation_depth**: 最大委派深度，通过 config.properties 配置，DelegateToAgentTool 在执行前校验 delegation_depth 是否超限。
- **AgentNotFoundError**: Agent 未找到异常，当 DelegateToAgentTool 在 AgentRegistry 中查找不存在的 Agent 名称时抛出。
- **DelegationDepthExceededError**: 委派深度超限异常，当 delegation_depth 达到 max_delegation_depth 时抛出。
- **TaskAgentPort**: 面向任务的 Agent 端口协议，定义 execute(task: Task) -> TaskResult 接口，由 TaskAgentAdapter 实现。
- **Task**: 任务值对象，封装一次 Agent 执行的完整任务定义，包含 goal、input_data、constraints、tool_names 等字段。
- **TaskResult**: 任务执行结果值对象，包含 content、status、trace 等字段。
- **ToolRegistry**: 工具注册表，管理所有已注册的 Tool 实例。
- **AgentConfig**: Agent 执行配置值对象，封装单次 Agent 执行所需的全部配置参数。
- **ConversationContext**: 对话上下文对象，管理消息列表，Agent 执行过程中被原地修改。

## Requirements

### Requirement 1: AgentRegistryPort 端口定义

**User Story:** 作为领域层开发者，我希望有一个 Protocol 定义的 Agent 注册表端口，以便在领域层声明 Agent 注册、查找和列举能力，遵循六边形架构的依赖方向。

#### Acceptance Criteria

1. THE AgentRegistryPort SHALL 定义 register(config: NamedAgentConfig) 方法，用于注册一个命名 Agent 配置。
2. THE AgentRegistryPort SHALL 定义 get(name: str) -> NamedAgentConfig | None 方法，用于按名称查找已注册的命名 Agent 配置。
3. THE AgentRegistryPort SHALL 定义 has(name: str) -> bool 方法，用于判断指定名称的 Agent 是否已注册。
4. THE AgentRegistryPort SHALL 定义 list_names() -> list[str] 方法，用于返回所有已注册 Agent 的名称列表。
5. THE AgentRegistryPort SHALL 使用 Python Protocol 定义，遵循项目六边形架构的端口定义规范。

### Requirement 2: NamedAgentConfig 值对象

**User Story:** 作为 Agent 编排开发者，我希望有一个不可变的值对象来封装命名 Agent 的完整定义，以便在注册和查找时传递 Agent 的配置信息。

#### Acceptance Criteria

1. THE NamedAgentConfig SHALL 包含 name 字段（str 类型），作为 Agent 的唯一标识名称。
2. THE NamedAgentConfig SHALL 包含 description 字段（str 类型），描述该 Agent 的职责和能力。
3. THE NamedAgentConfig SHALL 包含 system_prompt 字段（str 类型），定义该 Agent 的系统提示词。
4. THE NamedAgentConfig SHALL 包含 tool_names 字段（frozenset[str] | None 类型），指定该 Agent 可用的工具子集，None 表示使用全量工具。
5. THE NamedAgentConfig SHALL 包含 model 字段（str | None 类型），指定该 Agent 使用的模型，None 表示使用系统默认模型。
6. THE NamedAgentConfig SHALL 使用 frozen dataclass 定义，确保不可变性。
7. WHEN name 为空字符串或纯空白字符时，THE NamedAgentConfig SHALL 在 __post_init__ 中抛出 ValueError。
8. WHEN description 为空字符串或纯空白字符时，THE NamedAgentConfig SHALL 在 __post_init__ 中抛出 ValueError。

### Requirement 3: AgentRegistry 基础设施实现

**User Story:** 作为基础设施层开发者，我希望有一个 AgentRegistryPort 的具体实现，以便在运行时管理命名 Agent 实例的注册和查找。

#### Acceptance Criteria

1. THE AgentRegistryAdapter SHALL 实现 AgentRegistryPort 协议的所有方法。
2. WHEN register(config) 被调用时，THE AgentRegistryAdapter SHALL 按 config.name 将其存入内部字典，同名 Agent 重复注册时覆盖。
3. WHEN get(name) 被调用且 name 已注册时，THE AgentRegistryAdapter SHALL 返回对应的 NamedAgentConfig 实例。
4. WHEN get(name) 被调用且 name 未注册时，THE AgentRegistryAdapter SHALL 返回 None。
5. WHEN has(name) 被调用时，THE AgentRegistryAdapter SHALL 返回该名称是否已注册的布尔值。
6. WHEN list_names() 被调用时，THE AgentRegistryAdapter SHALL 返回所有已注册 Agent 名称的列表。

### Requirement 4: Task 值对象扩展 delegation_depth 字段

**User Story:** 作为 Agent 编排开发者，我希望 Task 值对象携带当前委派深度信息，以便 DelegateToAgentTool 在执行前校验深度是否超限。

#### Acceptance Criteria

1. THE Task 值对象 SHALL 包含 delegation_depth 字段，类型为 int，默认值为 0。
2. WHEN delegation_depth 未传入时，THE Task.delegation_depth SHALL 默认为 0，表示根 Agent 执行（无委派）。
3. WHEN delegation_depth 为负数时，THE Task SHALL 在 __post_init__ 中抛出 ValueError。
4. THE Task.delegation_depth SHALL 为 int 类型，确保与 frozen dataclass 的不可变性一致。

### Requirement 5: Agent 异常类型定义

**User Story:** 作为 Agent 开发者，我希望有明确的异常类型来区分 Agent 间通信中的不同错误场景，以便调用方能精确处理错误。

#### Acceptance Criteria

1. THE AgentNotFoundError SHALL 继承自 BizException 基类，使用错误码 60010。
2. THE AgentNotFoundError SHALL 包含 agent_name 属性，标识未找到的 Agent 名称。
3. THE AgentNotFoundError 的 message SHALL 包含未找到的 Agent 名称和当前已注册的 Agent 列表信息。
4. THE DelegationDepthExceededError SHALL 继承自 BizException 基类，使用错误码 60011。
5. THE DelegationDepthExceededError SHALL 包含 current_depth 属性和 max_depth 属性。
6. THE DelegationDepthExceededError 的 message SHALL 包含当前深度、最大深度和目标 Agent 名称信息。

### Requirement 6: DelegateToAgentTool 实现

**User Story:** 作为 Agent 开发者，我希望有一个 Tool 实现能让当前 Agent 将子任务委派给其他命名 Agent 执行，以实现多 Agent 协作。

#### Acceptance Criteria

1. THE DelegateToAgentTool SHALL 继承 Tool ABC，注册到 ToolRegistry 中。
2. THE DelegateToAgentTool 的 name 属性 SHALL 为 "delegate_to_agent"。
3. THE DelegateToAgentTool 的 parameters SHALL 包含 agent_name（string，必填）和 task_goal（string，必填）两个参数。
4. THE DelegateToAgentTool 的 parameters SHALL 包含 input_data（object，可选）参数，用于传递结构化输入数据给子 Agent。
5. WHEN execute 被调用时，THE DelegateToAgentTool SHALL 从 AgentRegistryPort 按 agent_name 查找目标 Agent 的 NamedAgentConfig。
6. IF agent_name 在 AgentRegistryPort 中未找到，THEN THE DelegateToAgentTool SHALL 抛出 AgentNotFoundError。
7. WHEN 目标 Agent 找到时，THE DelegateToAgentTool SHALL 使用 NamedAgentConfig 的 tool_names 和 model 字段，结合 task_goal 和 input_data 构造 Task 值对象。
8. THE DelegateToAgentTool 构造的 Task SHALL 将 delegation_depth 设置为当前深度 + 1。
9. THE DelegateToAgentTool SHALL 调用 TaskAgentPort.execute(task) 执行子任务，并将 TaskResult.content 作为工具执行结果返回。
10. IF TaskResult.status 为 FAILED，THEN THE DelegateToAgentTool SHALL 返回包含错误信息的结果字符串，Agent Loop 继续运行。

### Requirement 7: 递归深度限制

**User Story:** 作为系统安全负责人，我希望 Agent 间委派有深度限制，以防止 Agent A → B → A 的无限循环导致系统资源耗尽。

#### Acceptance Criteria

1. THE DelegateToAgentTool SHALL 在构造 Task 之前校验 delegation_depth + 1 是否超过 max_delegation_depth。
2. IF delegation_depth + 1 超过 max_delegation_depth，THEN THE DelegateToAgentTool SHALL 抛出 DelegationDepthExceededError。
3. THE max_delegation_depth SHALL 通过 config.properties 配置项 AGENT_MAX_DELEGATION_DEPTH 读取，默认值为 3。
4. WHEN DelegationDepthExceededError 被抛出时，THE DelegateToAgentTool SHALL 记录 WARNING 级别日志，包含当前深度、最大深度和目标 Agent 名称。
5. THE DelegateToAgentTool SHALL 通过构造函数接收 current_delegation_depth 参数，表示当前 Agent 执行所处的委派深度。

### Requirement 8: 上下文隔离策略

**User Story:** 作为 Agent 编排开发者，我希望子 Agent 执行时使用独立的对话上下文，以确保子 Agent 不受父 Agent 对话历史的干扰，执行环境干净可控。

#### Acceptance Criteria

1. WHEN DelegateToAgentTool 构造 Task 时，THE DelegateToAgentTool SHALL 将 session_id 设置为 None，确保 TaskAgentAdapter 创建全新的 ConversationContext。
2. THE 子 Agent 的 ConversationContext SHALL 不包含父 Agent 的任何对话历史消息。
3. THE 子 Agent 的系统提示词 SHALL 来自目标 NamedAgentConfig.system_prompt，与父 Agent 的系统提示词无关。
4. WHEN 子 Agent 执行完成后，THE 子 Agent 的 ConversationContext SHALL 不被持久化（因 session_id 为 None）。

### Requirement 9: DI 容器注册

**User Story:** 作为应用层开发者，我希望 AgentRegistry 和 DelegateToAgentTool 通过 DI 容器管理生命周期，以保持与项目现有依赖注入模式一致。

#### Acceptance Criteria

1. THE container_config.py SHALL 注册 AgentRegistryPort → AgentRegistryAdapter 的绑定，Scope 为 SINGLETON。
2. THE container_config.py SHALL 在 ToolRegistry 初始化时将 DelegateToAgentTool 注册到 ToolRegistry 中。
3. THE DelegateToAgentTool 的构造 SHALL 通过容器解析 AgentRegistryPort 和 TaskAgentPort 依赖。
4. THE DelegateToAgentTool 的 max_delegation_depth SHALL 从 config.properties 的 AGENT_MAX_DELEGATION_DEPTH 配置项读取。
5. THE AgentRegistryAdapter 的初始化 SHALL 支持从配置或代码注册命名 Agent 实例。

### Requirement 10: 配置项定义

**User Story:** 作为运维人员，我希望 Agent 间通信的关键参数可通过配置文件调整，以便在不修改代码的情况下控制系统行为。

#### Acceptance Criteria

1. THE config.properties SHALL 包含 AGENT_MAX_DELEGATION_DEPTH 配置项，类型为整数，默认值为 3。
2. WHEN AGENT_MAX_DELEGATION_DEPTH 配置值小于等于 0 时，THE 系统 SHALL 回退使用默认值 3。
3. THE config.properties SHALL 包含 AGENT_DELEGATE_TOOL_ENABLED 配置项，类型为布尔值，默认值为 true。
4. WHEN AGENT_DELEGATE_TOOL_ENABLED 为 false 时，THE container_config.py SHALL 跳过 DelegateToAgentTool 的注册，ToolRegistry 中不包含委派工具。
