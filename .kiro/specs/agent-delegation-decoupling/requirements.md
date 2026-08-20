# Requirements Document

## Introduction

本需求文档描述对 Agent 委派机制的架构重构，目标是消除当前 `ToolRegistry → DelegateToAgentTool → TaskAgentPort → AgentPort → ToolRegistry` 的循环依赖链。

当前实现通过 `container_config.py` 中的 lambda 延迟解析和 `DelegateToAgentTool` 中的 `Union[TaskAgentPort, Callable[[], Awaitable[TaskAgentPort]]]` 类型签名来缓解循环依赖，但该方案存在以下问题：
1. 循环依赖仅靠运行时延迟解析维持，缺乏架构层面的约束
2. 基础设施层的组装细节（lazy factory）泄漏到工具接口签名中
3. 模块级直接导入 `TaskAgentPort` 即可触发真实的循环导入错误

重构方向：在领域层引入 `DelegationPort` 协议，将委派能力抽象为领域端口。`DelegateToAgentTool` 仅依赖该端口，基础设施层提供适配器桥接 `DelegationPort` 到 `TaskAgentPort`，从而在架构层面彻底打断循环。

## Glossary

- **DelegationPort**: 领域层定义的委派能力协议（Protocol），抽象"将子任务委派给命名 Agent 执行并返回结果"的能力边界
- **DelegationAdapter**: 基础设施层实现的 DelegationPort 适配器，内部桥接 AgentRegistryPort 和 TaskAgentPort 完成实际委派
- **DelegateToAgentTool**: 基础设施层的 Agent 委派工具，继承 Tool ABC，负责校验委派深度、构造 Task 并通过 DelegationPort 执行委派
- **TaskAgentPort**: 领域层定义的面向任务的 Agent 端口协议，接收 Task 值对象并返回 TaskResult
- **TaskAgentAdapter**: 基础设施层实现的 TaskAgentPort 适配器，将 Task 转换为 ConversationContext + AgentConfig 委托 AgentPort 执行
- **AgentPort**: 领域层定义的 Agent 端口协议，封装 Agent Loop 的执行能力
- **ToolRegistry**: 领域层的工具注册表，集中管理所有已注册的 Tool 实例
- **AgentRegistryPort**: 领域层定义的 Agent 注册表端口协议，管理命名 Agent 配置的注册和查找
- **DelegationResult**: 领域层定义的委派结果值对象，封装委派执行的结果内容和成功/失败状态
- **Container**: 应用层的依赖注入容器，负责 Port → Adapter 绑定和异步资源生命周期管理

## Requirements

### Requirement 1: 定义领域层 DelegationPort 协议

**User Story:** 作为架构维护者，我希望在领域层定义一个 DelegationPort 协议来抽象委派能力，以便将委派的业务语义与基础设施实现解耦。

#### Acceptance Criteria

1. THE DelegationPort SHALL 定义为 Python Protocol 类，位于 `domain/agent/ports.py` 模块中
2. THE DelegationPort SHALL 声明一个异步方法 `delegate`，接收 agent_name（str）、task_goal（str）和可选的 input_data（dict）参数，返回 DelegationResult 值对象
3. THE DelegationPort SHALL 不依赖 TaskAgentPort、AgentPort、ToolRegistry 或任何基础设施层模块
4. THE DelegationPort 的 `delegate` 方法 SHALL 包含中文 docstring，说明参数含义、返回值语义和异常场景

### Requirement 2: 定义领域层 DelegationResult 值对象

**User Story:** 作为架构维护者，我希望定义一个 DelegationResult 值对象来封装委派结果，以便领域层能以统一的结构表达委派执行的成功或失败。

#### Acceptance Criteria

1. THE DelegationResult SHALL 定义为不可变的值对象（使用 dataclass frozen=True 或等效机制），位于领域层的 agent 值对象模块中
2. THE DelegationResult SHALL 包含 content（str）字段表示结果内容，以及 success（bool）字段表示执行是否成功
3. THE DelegationResult SHALL 不依赖任何基础设施层模块

### Requirement 3: 实现基础设施层 DelegationAdapter

**User Story:** 作为架构维护者，我希望在基础设施层实现 DelegationAdapter 来桥接 DelegationPort 到 TaskAgentPort，以便在不引入循环依赖的前提下完成实际的委派执行。

#### Acceptance Criteria

1. THE DelegationAdapter SHALL 实现 DelegationPort 协议，位于 `infrastructure/agent/` 目录下
2. THE DelegationAdapter SHALL 在构造时接收 AgentRegistryPort 和 TaskAgentPort 作为直接依赖（非 lazy factory）
3. WHEN DelegationAdapter 的 `delegate` 方法被调用时，THE DelegationAdapter SHALL 通过 AgentRegistryPort 查找目标 Agent 配置，构造 Task 值对象，调用 TaskAgentPort.execute() 执行，并将 TaskResult 转换为 DelegationResult 返回
4. IF 目标 Agent 未在 AgentRegistryPort 中注册，THEN THE DelegationAdapter SHALL 抛出 AgentNotFoundError 异常
5. THE DelegationAdapter SHALL 包含中文模块级 docstring 和类级 docstring，说明其桥接职责

### Requirement 4: 重构 DelegateToAgentTool 依赖 DelegationPort

**User Story:** 作为架构维护者，我希望重构 DelegateToAgentTool 使其依赖 DelegationPort 而非 TaskAgentPort，以便消除工具接口中的基础设施组装细节泄漏。

#### Acceptance Criteria

1. THE DelegateToAgentTool SHALL 在构造时接收 DelegationPort 实例作为依赖，替代当前的 `Union[TaskAgentPort, Callable[[], Awaitable[TaskAgentPort]]]` 参数
2. THE DelegateToAgentTool SHALL 移除 `_get_task_agent()` 延迟解析方法和 `_resolved_task_agent` 缓存字段
3. WHEN DelegateToAgentTool 的 `execute` 方法被调用时，THE DelegateToAgentTool SHALL 校验委派深度后直接调用 DelegationPort.delegate() 完成委派
4. THE DelegateToAgentTool SHALL 保留对 AgentRegistryPort 的依赖，用于生成动态工具描述（已注册 Agent 列表）和委派深度校验
5. THE DelegateToAgentTool SHALL 不再直接或间接依赖 TaskAgentPort

### Requirement 5: 更新容器配置消除循环依赖

**User Story:** 作为架构维护者，我希望更新 container_config.py 中的依赖注册顺序和工厂函数，以便利用 DelegationPort 中间层彻底消除循环依赖链。

#### Acceptance Criteria

1. THE Container SHALL 注册 DelegationPort → DelegationAdapter 的绑定，DelegationAdapter 的工厂函数通过容器解析 AgentRegistryPort 和 TaskAgentPort 作为构造参数
2. THE Container 中 `_create_tool_registry` 工厂函数 SHALL 通过容器解析 DelegationPort 实例，将其传递给 DelegateToAgentTool 的构造函数
3. THE Container SHALL 移除 `_create_tool_registry` 中对 TaskAgentPort 的 lambda 延迟解析逻辑
4. WHEN 容器按注册顺序初始化所有组件时，THE Container SHALL 不产生循环依赖错误

### Requirement 6: 确保依赖方向符合 DDD 六边形架构

**User Story:** 作为架构维护者，我希望重构后的依赖关系严格遵循 DDD 六边形架构的依赖方向规则，以便防止未来的架构退化。

#### Acceptance Criteria

1. THE DelegationPort（领域层）SHALL 不导入任何 infrastructure/ 或 application/ 模块
2. THE DelegationAdapter（基础设施层）SHALL 仅依赖 domain/ 和 common/ 模块
3. THE DelegateToAgentTool（基础设施层）SHALL 仅依赖 domain/ 中定义的 DelegationPort 和 AgentRegistryPort 协议，不依赖 TaskAgentPort
4. WHEN 重构完成后，THE 依赖链 SHALL 变为：ToolRegistry → DelegateToAgentTool → DelegationPort ← DelegationAdapter → TaskAgentPort → AgentPort → ToolRegistry，其中 DelegationPort 作为领域层抽象打断循环

### Requirement 7: 保持现有功能行为不变

**User Story:** 作为开发者，我希望重构后 Agent 委派功能的外部行为与重构前完全一致，以便确保重构不引入功能回归。

#### Acceptance Criteria

1. WHEN DelegateToAgentTool 被调用且委派深度未超限时，THE DelegateToAgentTool SHALL 成功执行委派并返回子 Agent 的执行结果内容
2. WHEN 委派深度超过 max_delegation_depth 时，THE DelegateToAgentTool SHALL 抛出 DelegationDepthExceededError 异常
3. WHEN 目标 Agent 未注册时，THE DelegateToAgentTool SHALL 抛出 AgentNotFoundError 异常
4. WHEN 子 Agent 执行失败时，THE DelegateToAgentTool SHALL 返回包含失败信息的错误字符串
5. THE DelegateToAgentTool 的 name、description、parameters 属性 SHALL 保持与重构前一致的对外接口
