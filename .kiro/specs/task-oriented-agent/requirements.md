# 需求文档：面向任务的 Agent 入口重构

## 简介

将当前面向"对话"的 Agent 入口模型重构为面向"任务"的模型。当前系统的入口是 `ChatRequestVO(session_id, message, stream, model)`，这是一个面向对话的设计——调用方只能发送一条消息，Agent 的行为完全由系统提示词和对话历史隐式决定。重构后，Agent 的入口将变为结构化的 `Task` 值对象，包含目标描述、输入数据、约束条件和期望输出格式，使 Agent 的行为由显式的任务定义驱动。同时定义 `TaskResult` 值对象，包含执行结果、状态（成功/失败/需要人工介入）和执行轨迹，使调用方能够获得结构化的执行反馈。

本次重构涉及三个层面的变更：
1. 领域层：定义 `Task`、`TaskResult`、`TaskStatus` 值对象和 `TaskAgentPort` 端口协议
2. 基础设施层：实现 `TaskAgentAdapter`，将 `Task` 转换为 Agent 可执行的上下文并委托现有 `AgentPort` 执行
3. 应用层：新增面向任务的 API 端点，接收 `Task` 请求并返回 `TaskResult` 响应

现有的面向对话的 `ChatServicePort` / `ChatRequestVO` 路径保持不变，两套入口并行存在。

## 术语表

- **Task**：任务值对象，定义在 `domain/task/value_objects.py` 中，封装一次 Agent 执行的完整任务定义，包含目标描述（goal）、输入数据（input_data）、约束条件（constraints）和期望输出格式（output_format）。
- **TaskResult**：任务执行结果值对象，定义在 `domain/task/value_objects.py` 中，封装 Agent 执行任务后的结构化结果，包含执行结果内容（content）、执行状态（status）、执行轨迹（trace）和 token 用量（usage）。
- **TaskStatus**：任务执行状态枚举，定义在 `domain/task/value_objects.py` 中，包含 SUCCESS（成功）、FAILED（失败）和 HUMAN_INTERVENTION_REQUIRED（需要人工介入）三种状态。
- **TaskAgentPort**：面向任务的 Agent 端口协议，定义在 `domain/task/ports.py` 中，描述"接收 Task、自主执行、返回 TaskResult"的统一接口。
- **TaskAgentAdapter**：面向任务的 Agent 适配器，定义在 `infrastructure/task/task_agent_adapter.py` 中，实现 `TaskAgentPort` 协议，将 Task 转换为 ConversationContext 和 AgentConfig 后委托现有 AgentPort 执行。
- **TraceEntry**：执行轨迹条目值对象，定义在 `domain/task/value_objects.py` 中，记录 Agent 执行过程中的单步操作（工具调用、LLM 推理等）。
- **AgentPort**：已有的 Agent 端口协议，定义在 `domain/agent/ports.py` 中，封装 Agent Loop 的执行逻辑。
- **ToolRegistry**：已有的工具注册表，管理已注册的 Tool 实例。
- **ModelRegistryPort**：已有的模型注册中心端口，管理提供商注册和模型路由。
- **ConversationContext**：已有的对话上下文值对象，管理对话消息列表。

## 需求

### 需求 1：定义 TaskStatus 枚举

**用户故事：** 作为开发者，我希望有一个明确的枚举类型来表示任务执行的三种状态，以便调用方能够根据状态进行分支处理。

#### 验收标准

1. THE TaskStatus SHALL 使用 Python `enum.Enum` 定义，包含三个成员：SUCCESS（值为 "success"）、FAILED（值为 "failed"）、HUMAN_INTERVENTION_REQUIRED（值为 "human_intervention_required"）
2. THE TaskStatus SHALL 定义在 `domain/task/value_objects.py` 模块中

### 需求 2：定义 Task 值对象

**用户故事：** 作为开发者，我希望有一个结构化的任务值对象来替代当前的 `ChatRequestVO`，以便 Agent 的行为由显式的任务定义驱动，而非隐式的对话消息。

#### 验收标准

1. THE Task SHALL 使用 frozen dataclass 定义，包含以下字段：goal（str，任务目标描述）、input_data（dict[str, Any]，输入数据，默认空字典）、constraints（list[str]，约束条件列表，默认空列表）、output_format（str | None，期望输出格式描述，默认 None）、model（str | None，可选模型名称，默认 None）、session_id（str | None，可选会话标识，默认 None，用于关联对话上下文）
2. WHEN goal 为空字符串或纯空白字符时，THE Task SHALL 在 `__post_init__` 中抛出 ValueError
3. THE Task SHALL 定义在 `domain/task/value_objects.py` 模块中

### 需求 3：定义 TraceEntry 值对象

**用户故事：** 作为开发者，我希望能够记录 Agent 执行过程中的每一步操作，以便在任务完成后审查执行轨迹、排查问题。

#### 验收标准

1. THE TraceEntry SHALL 使用 frozen dataclass 定义，包含以下字段：step（int，步骤序号）、action（str，操作类型，如 "tool_call"、"llm_response"）、detail（str，操作详情）、timestamp_ms（float，时间戳，毫秒）
2. THE TraceEntry SHALL 定义在 `domain/task/value_objects.py` 模块中

### 需求 4：定义 TaskResult 值对象

**用户故事：** 作为开发者，我希望 Agent 执行任务后返回结构化的结果，包含执行状态和执行轨迹，以便调用方能够判断任务是否成功并审查执行过程。

#### 验收标准

1. THE TaskResult SHALL 使用 frozen dataclass 定义，包含以下字段：content（str，执行结果内容）、status（TaskStatus，执行状态）、model（str，实际使用的模型名称）、usage（dict[str, int]，token 用量，默认空字典）、trace（list[TraceEntry]，执行轨迹，默认空列表）、latency_ms（float，总执行耗时毫秒，默认 0.0）
2. THE TaskResult SHALL 定义在 `domain/task/value_objects.py` 模块中，与 Task、TaskStatus、TraceEntry 同模块

### 需求 5：定义 TaskAgentPort 端口协议

**用户故事：** 作为开发者，我希望有一个面向任务的 Agent 端口协议，使编排层可以面向接口编程，将任务提交给 Agent 执行并获取结构化结果。

#### 验收标准

1. THE TaskAgentPort SHALL 使用 Python Protocol 定义，包含 `execute` 异步方法
2. THE TaskAgentPort 的 `execute` 方法 SHALL 接收 Task 作为唯一参数，返回 TaskResult
3. THE TaskAgentPort SHALL 定义在 `domain/task/ports.py` 模块中
4. THE TaskAgentPort 的 `execute` 方法 SHALL 支持有 session_id 和无 session_id 两种场景：有 session_id 时加载已有对话上下文，无 session_id 时创建新的空上下文

### 需求 6：实现 TaskAgentAdapter

**用户故事：** 作为开发者，我希望有一个适配器将 Task 转换为现有 Agent 基础设施可执行的格式，以便复用已有的 AgentPort 和 ToolRegistry，避免重复实现 Agent Loop。

#### 验收标准

1. THE TaskAgentAdapter SHALL 实现 TaskAgentPort 协议，定义在 `infrastructure/task/task_agent_adapter.py` 模块中
2. THE TaskAgentAdapter SHALL 通过构造函数接收 AgentPort、ToolRegistry、ModelRegistryPort、ContextCompactionPort 和 SessionContextStorePort 作为依赖
3. WHEN `execute` 方法被调用时，THE TaskAgentAdapter SHALL 执行以下转换流程：根据 Task 的 goal、constraints 和 output_format 构造系统提示词 → 从 ToolRegistry 获取工具 schema → 构造 AgentConfig → 根据 Task.model 通过 ModelRegistryPort 解析 ModelAccessPort → 委托 AgentPort.run() 执行
4. WHEN Task 的 session_id 不为 None 时，THE TaskAgentAdapter SHALL 通过 SessionContextStorePort 加载已有对话上下文，并在执行完成后保存更新的上下文
5. WHEN Task 的 session_id 为 None 时，THE TaskAgentAdapter SHALL 创建新的空 ConversationContext，执行完成后不保存上下文
6. WHEN AgentPort.run() 执行成功（返回 AgentResult）时，THE TaskAgentAdapter SHALL 将 AgentResult 转换为 TaskResult，status 设为 TaskStatus.SUCCESS
7. WHEN AgentPort.run() 执行过程中抛出异常时，THE TaskAgentAdapter SHALL 捕获异常，将异常信息作为 TaskResult 的 content，status 设为 TaskStatus.FAILED
8. THE TaskAgentAdapter SHALL 从 AgentPort 执行过程中的 ConversationContext 提取执行轨迹（AssistantMessage 中的 tool_calls 和 ToolMessage），转换为 TraceEntry 列表填入 TaskResult.trace
9. THE TaskAgentAdapter SHALL 从 `config.properties` 读取 `task.agent.max_rounds` 配置项作为 Agent Loop 最大迭代轮次，默认值为 10

### 需求 7：新增面向任务的 API 端点

**用户故事：** 作为 API 调用方，我希望有一个面向任务的 HTTP 端点，以便通过提交结构化的任务定义来驱动 Agent 执行，而非发送对话消息。

#### 验收标准

1. THE 应用层 SHALL 在 `application/routers/task.py` 中新增 `POST /api/task/execute` 端点
2. WHEN 接收到任务执行请求时，THE 端点 SHALL 将 HTTP 请求体转换为 Task 值对象，通过 DI 容器注入 TaskAgentPort，调用 `execute` 方法并将 TaskResult 转换为 HTTP 响应返回
3. THE 端点的请求体 SHALL 包含以下字段：goal（str，必填）、input_data（dict，可选，默认空字典）、constraints（list[str]，可选，默认空列表）、output_format（str，可选）、model（str，可选）、session_id（str，可选）
4. THE 端点的响应体 SHALL 包含以下字段：code（int，业务状态码，0 表示成功）、content（str，执行结果）、status（str，执行状态枚举值）、model（str，实际使用的模型）、usage（dict，token 用量）、trace（list，执行轨迹）、latency_ms（float，总耗时）
5. IF Task 构造时 goal 校验失败，THEN THE 端点 SHALL 返回 HTTP 400 响应，包含错误信息

### 需求 8：更新依赖注入容器配置

**用户故事：** 作为开发者，我希望 DI 容器正确注册 TaskAgentPort → TaskAgentAdapter 的绑定，以便应用层可以通过容器注入获取 TaskAgentPort 实例。

#### 验收标准

1. THE container_config 模块 SHALL 新增 TaskAgentPort → TaskAgentAdapter 的 Singleton 绑定注册
2. THE TaskAgentAdapter 的创建 SHALL 通过容器解析 AgentPort、ToolRegistry、ModelRegistryPort、ContextCompactionPort 和 SessionContextStorePort 实例并注入
3. WHEN 应用启动时，THE 容器 SHALL 在 AgentPort 注册完成后再注册 TaskAgentPort，确保依赖顺序正确
4. THE 应用层 SHALL 在 `server_app.py` 中注册 task router，使 `/api/task/execute` 端点生效

### 需求 9：Task 到系统提示词的转换规则

**用户故事：** 作为开发者，我希望 Task 的结构化字段能够被转换为清晰的系统提示词，以便 LLM 能够理解任务目标、约束条件和期望输出格式。

#### 验收标准

1. THE TaskAgentAdapter SHALL 将 Task.goal 作为系统提示词的核心指令部分
2. WHEN Task.input_data 非空时，THE TaskAgentAdapter SHALL 将 input_data 序列化为 JSON 字符串并嵌入系统提示词的"输入数据"段落
3. WHEN Task.constraints 非空时，THE TaskAgentAdapter SHALL 将每条约束条件作为编号列表项嵌入系统提示词的"约束条件"段落
4. WHEN Task.output_format 不为 None 时，THE TaskAgentAdapter SHALL 将 output_format 嵌入系统提示词的"期望输出格式"段落
5. THE 系统提示词的生成逻辑 SHALL 是确定性的：相同的 Task 输入产生相同的系统提示词输出
6. THE 系统提示词生成方法 SHALL 作为 TaskAgentAdapter 的静态方法或独立纯函数实现，便于单独测试

### 需求 10：保持现有对话入口不变

**用户故事：** 作为开发者，我希望本次重构不影响现有的面向对话的 `ChatServicePort` / `ChatRequestVO` 路径，两套入口并行存在。

#### 验收标准

1. THE 现有的 `POST /api/chat` 端点 SHALL 保持不变，请求和响应格式不变
2. THE 现有的 `ChatServicePort`、`ChatRequestVO`、`ChatResponseVO` SHALL 保持不变
3. THE 现有的 `ChatServiceAdapter` 编排逻辑 SHALL 保持不变
4. THE 新增的 TaskAgentPort 路径 SHALL 复用现有的 AgentPort、ToolRegistry、ModelRegistryPort 等基础设施，不重复实现
