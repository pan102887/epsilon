# 需求文档：Agent 抽象层

## 简介

将 `ChatServiceAdapter` 中内嵌的 Agent Loop 逻辑（`_run_agent_loop` 和 `_run_agent_loop_streaming`）抽取为独立的 Agent 抽象层。在 `domain/agent/` 中定义 `AgentPort` Protocol 和 `AgentConfig` 值对象，在 `infrastructure/agent/` 中实现 `ReActAgentAdapter`，最终将 `ChatServiceAdapter` 转变为持有 `AgentPort` 的编排层，不再直接执行 Agent Loop。

## 术语表

- **AgentPort**：Agent 端口协议，定义在 `domain/agent/ports.py` 中，描述"接收任务、自主执行、返回结果"的统一接口，支持同步和流式两种执行模式。
- **AgentConfig**：Agent 配置值对象，定义在 `domain/agent/value_objects.py` 中，封装 Agent 运行所需的配置参数（系统提示词、可用工具集、模型选择、最大迭代轮次等）。
- **ReActAgentAdapter**：ReAct 模式 Agent 适配器，定义在 `infrastructure/agent/react_agent_adapter.py` 中，实现 `AgentPort` 协议，封装"推理→行动→观察"循环逻辑。
- **ChatServiceAdapter**：聊天服务适配器，重构后作为编排层，持有 `AgentPort` 实例，将 Agent Loop 执行委托给 `AgentPort`。
- **ToolRegistry**：工具注册表，管理已注册的 Tool 实例，提供工具 schema 查询和工具执行能力。
- **ConversationContext**：对话上下文值对象，管理对话消息列表。
- **ModelAccessPort**：统一模型接入端口，定义与 LLM 交互的标准操作。
- **ModelRegistryPort**：模型注册中心端口，管理提供商注册和模型路由。
- **ContextCompactionPort**：上下文压缩端口，将完整消息列表压缩为适合发送给模型的消息列表。
- **AgentResult**：Agent 执行结果值对象，封装 Agent 同步执行的返回数据（回复内容、模型名称、token 用量、延迟信息）。

## 需求

### 需求 1：定义 AgentConfig 值对象

**用户故事：** 作为开发者，我希望有一个不可变的配置值对象来封装 Agent 运行所需的全部参数，以便在创建 Agent 实例时传入统一的配置。

#### 验收标准

1. THE AgentConfig SHALL 使用 frozen dataclass 定义，包含以下字段：system_prompt（str）、tool_schemas（list[dict]）、model（str | None）、max_rounds（int）
2. WHEN max_rounds 的值小于等于 0 时，THE AgentConfig SHALL 在 __post_init__ 中抛出 ValueError
3. THE AgentConfig SHALL 定义在 `domain/agent/value_objects.py` 模块中

### 需求 2：定义 AgentPort Protocol

**用户故事：** 作为开发者，我希望有一个统一的 Agent 端口协议，以便编排层可以面向接口编程，不依赖具体的 Agent 实现。

#### 验收标准

1. THE AgentPort SHALL 使用 Python Protocol 定义，包含 `run` 异步方法和 `run_streaming` 方法
2. THE AgentPort 的 `run` 方法 SHALL 接收 ConversationContext、AgentConfig 和 ModelAccessPort 三个参数，返回 AgentResult
3. THE AgentPort 的 `run_streaming` 方法 SHALL 接收 ConversationContext、AgentConfig 和 ModelAccessPort 三个参数，返回 AsyncIterator[StreamingChunk]
4. THE AgentPort SHALL 定义在 `domain/agent/ports.py` 模块中
5. THE AgentPort 的 `run` 方法和 `run_streaming` 方法 SHALL 在执行过程中原地修改传入的 ConversationContext（追加 AssistantMessage 和 ToolMessage），与当前 `_run_agent_loop` 的行为一致

### 需求 3：定义 AgentResult 值对象

**用户故事：** 作为开发者，我希望 Agent 同步执行的返回结果有一个明确的值对象类型，以便调用方获取结构化的执行结果。

#### 验收标准

1. THE AgentResult SHALL 使用 frozen dataclass 定义，包含以下字段：content（str）、model（str）、usage（dict[str, int]）、latency_ms（float）
2. THE AgentResult SHALL 定义在 `domain/agent/value_objects.py` 模块中，与 AgentConfig 同模块

### 需求 4：实现 ReActAgentAdapter

**用户故事：** 作为开发者，我希望将现有的 Agent Loop 逻辑封装为独立的 ReActAgentAdapter，以便 Agent 执行逻辑与聊天编排逻辑解耦。

#### 验收标准

1. THE ReActAgentAdapter SHALL 实现 AgentPort 协议，定义在 `infrastructure/agent/react_agent_adapter.py` 模块中
2. THE ReActAgentAdapter SHALL 通过构造函数接收 ToolRegistry 和 ContextCompactionPort 作为依赖
3. WHEN `run` 方法被调用时，THE ReActAgentAdapter SHALL 执行与当前 `ChatServiceAdapter._run_agent_loop` 等价的循环逻辑：压缩上下文 → 序列化消息 → 调用 LLM → 检查 tool_calls → 执行工具 → 追加消息到上下文 → 重复直到获得纯文本回复或达到最大轮次
4. WHEN `run_streaming` 方法被调用时，THE ReActAgentAdapter SHALL 执行与当前 `ChatServiceAdapter._run_agent_loop_streaming` 等价的流式循环逻辑
5. WHEN 工具执行过程中发生异常时，THE ReActAgentAdapter SHALL 将异常信息作为 ToolMessage 的 content 回传给 LLM，循环继续运行
6. THE ReActAgentAdapter SHALL 在 `run` 方法中累计所有轮次的 token 用量，并通过 AgentResult 返回
7. THE ReActAgentAdapter SHALL 包含消息序列化逻辑（将 BaseMessage 列表序列化为 LLM API 所需的字典列表格式），该逻辑从 `ChatServiceAdapter._serialize_messages` 提取

### 需求 5：重构 ChatServiceAdapter 为编排层

**用户故事：** 作为开发者，我希望 ChatServiceAdapter 仅负责对话编排（上下文加载、系统提示词注入、用户消息追加、Agent 调用委托、上下文保存），不再直接包含 Agent Loop 执行逻辑。

#### 验收标准

1. THE ChatServiceAdapter SHALL 通过构造函数接收 AgentPort 实例作为依赖，替代直接持有 ToolRegistry 和 max_tool_rounds
2. WHEN tool_calling_enabled 为 True 且有已注册工具时，THE ChatServiceAdapter 的 `chat` 方法 SHALL 将 Agent Loop 执行委托给 AgentPort 的 `run` 方法，而非直接执行循环逻辑
3. WHEN tool_calling_enabled 为 True 且有已注册工具时，THE ChatServiceAdapter 的 `stream_chat` 方法 SHALL 将流式 Agent Loop 执行委托给 AgentPort 的 `run_streaming` 方法
4. WHEN tool_calling_enabled 为 False 或无已注册工具时，THE ChatServiceAdapter SHALL 保持当前的直接 LLM 调用行为不变
5. THE ChatServiceAdapter SHALL 继续负责上下文加载、系统提示词注入、用户消息追加、最终助手回复追加和上下文保存的编排职责
6. THE ChatServiceAdapter SHALL 继续实现 ChatServicePort 协议，对外接口保持不变

### 需求 6：更新依赖注入容器配置

**用户故事：** 作为开发者，我希望 DI 容器正确注册 AgentPort → ReActAgentAdapter 的绑定，并更新 ChatServiceAdapter 的创建逻辑以注入 AgentPort。

#### 验收标准

1. THE container_config 模块 SHALL 新增 AgentPort → ReActAgentAdapter 的 Singleton 绑定注册
2. THE container_config 模块中 `_create_chat_service` 工厂函数 SHALL 通过容器解析 AgentPort 实例，并注入到 ChatServiceAdapter 的构造函数中
3. THE ChatServiceAdapter 的构造函数 SHALL 不再直接接收 ToolRegistry 和 max_tool_rounds 参数（这些参数转移到 AgentConfig 或 ReActAgentAdapter 中）
4. WHEN 应用启动时，THE 容器 SHALL 按正确的依赖顺序初始化 ToolRegistry → ReActAgentAdapter → ChatServiceAdapter

### 需求 7：保持行为向后兼容

**用户故事：** 作为开发者，我希望重构后的系统在功能行为上与重构前完全一致，不引入任何行为变更。

#### 验收标准

1. THE 重构后的 `chat` 方法 SHALL 产生与重构前完全相同的 ChatResponseVO 输出（相同输入下）
2. THE 重构后的 `stream_chat` 方法 SHALL 产生与重构前完全相同的 StreamingChunk 序列（相同输入下）
3. THE 重构后的系统 SHALL 保持对话上下文的完整性：保存到 SessionContextStorePort 的始终是包含所有消息的完整未压缩上下文
4. THE 重构后的系统 SHALL 保持工具执行异常处理行为：异常信息作为 ToolMessage 回传给 LLM，循环继续运行
