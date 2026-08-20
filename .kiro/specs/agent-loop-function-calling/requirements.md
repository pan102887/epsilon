# 需求文档：Agent Loop / Function Calling

## 简介

在现有聊天服务中引入 Agent Loop（工具调用循环）能力。当 LLM 返回 `tool_calls` 时，
系统自动执行对应工具、将结果回传给 LLM，循环往复直到 LLM 返回纯文本回复或达到最大迭代次数。

项目已具备 `Tool` / `ToolRegistry` 抽象体系（`domain/agent/tools.py`）、
`ToolCallRequest` 值对象（`domain/model_access/value_objects.py`）、
`ChatRequest.tools` 字段以及 `OpenAICompatibleAdapter` 中的 tool_calls 解析逻辑。
本次需求在此基础上补齐消息模型扩展、消息序列化适配、DI 容器注册和 Agent Loop 编排，
使端到端的 function calling 流程完整可用。

依赖顺序：
- ①②③ 互相独立可并行：ChatRequest tools 字段（已完成）、AssistantMessage/ToolMessage 扩展、消息序列化适配
- ④ 依赖 ①：ModelAccessPort 适配器已支持 function calling（已完成）
- ⑤ 独立：ToolRegistry 注册到 DI 容器
- ⑥ 依赖全部：Agent Loop 本体（在 ChatServiceAdapter 中实现循环）

## 术语表

- **Agent_Loop**: 工具调用循环，指 LLM 返回 tool_calls 后系统自动执行工具、回传结果、再次调用 LLM 的迭代过程，直到 LLM 返回纯文本回复或达到最大迭代次数。
- **Tool**: 工具抽象基类（`domain/agent/tools.py`），定义名称、描述、参数 schema 和执行逻辑。
- **ToolRegistry**: 工具注册表（`domain/agent/tools.py`），集中管理已注册的 Tool 实例，支持按名称查找、schema 生成和委托执行。
- **ToolCallRequest**: 工具调用请求值对象（`domain/model_access/value_objects.py`），包含 id、name、arguments 三个字段，由 LLM 响应解析得到。
- **AssistantMessage**: AI 助手回复消息（`domain/chat/context.py`），role 固定为 "assistant"。本次扩展新增 `tool_calls` 字段。
- **ToolMessage**: 工具调用结果消息（`domain/chat/context.py`），role 固定为 "tool"。本次扩展新增 `tool_call_id` 字段。
- **ChatRequest**: 对话请求值对象（`domain/model_access/value_objects.py`），已包含 `tools` 字段。
- **LLMResponse**: 对话响应值对象（`domain/model_access/value_objects.py`），已包含 `tool_calls` 字段。
- **Chat_Service_Adapter**: 聊天服务适配器（`infrastructure/chat/chat_service_adapter.py`），编排对话流程，本次在此实现 Agent Loop。
- **ModelAccessPort**: 统一模型接入端口（`domain/model_access/ports.py`），已支持 function calling。
- **DI_Container**: 依赖注入容器（`common/container.py`），管理 Port → Adapter 绑定。
- **max_tool_rounds**: Agent Loop 最大迭代轮次，防止无限循环。
- **Serialize**: 将 BaseMessage 对象列表转换为 LLM API 所需的字典列表格式，需包含 tool_calls 和 tool_call_id 等扩展字段。

## 需求

### 需求 1：AssistantMessage 扩展 tool_calls 字段

**用户故事：** 作为开发者，我希望 AssistantMessage 能携带 LLM 返回的 tool_calls 信息，以便在对话上下文中完整记录助手的工具调用意图。

#### 验收标准

1. THE AssistantMessage SHALL 新增可选的 `tool_calls` 字段，类型为 `list[ToolCallRequest]`，默认为空列表。
2. WHEN `tool_calls` 非空时，THE AssistantMessage 的 `to_dict` 方法 SHALL 在输出字典中包含 `tool_calls` 键，值为 ToolCallRequest 列表的序列化形式（每个元素包含 id、name、arguments）。
3. WHEN `tool_calls` 为空列表时，THE AssistantMessage 的 `to_dict` 方法 SHALL 不包含 `tool_calls` 键，确保与现有序列化格式向后兼容。
4. THE BaseMessage 的 `from_dict` 工厂方法 SHALL 在 role 为 "assistant" 且字典中包含 `tool_calls` 键时，正确还原 `tool_calls` 字段为 `list[ToolCallRequest]`。
5. THE BaseMessage 的 `from_dict` 工厂方法 SHALL 在 role 为 "assistant" 且字典中不包含 `tool_calls` 键时，将 `tool_calls` 设为空列表，确保向后兼容已持久化的数据。
6. FOR ALL 携带 tool_calls 的 AssistantMessage 对象，执行 `to_dict` 后再 `from_dict` SHALL 产生与原始对象等价的 AssistantMessage（往返一致性）。

### 需求 2：ToolMessage 扩展 tool_call_id 字段

**用户故事：** 作为开发者，我希望 ToolMessage 能携带 tool_call_id，以便 LLM 能将工具执行结果与对应的调用请求正确关联。

#### 验收标准

1. THE ToolMessage SHALL 新增必填的 `tool_call_id` 字段，类型为 `str`。
2. THE ToolMessage 的 `to_dict` 方法 SHALL 在输出字典中包含 `tool_call_id` 键。
3. THE BaseMessage 的 `from_dict` 工厂方法 SHALL 在 role 为 "tool" 时从字典中读取 `tool_call_id` 字段进行还原。
4. THE BaseMessage 的 `from_dict` 工厂方法 SHALL 在 role 为 "tool" 且字典中不包含 `tool_call_id` 键时，将 `tool_call_id` 设为空字符串，确保向后兼容已持久化的旧格式数据。
5. FOR ALL 携带 tool_call_id 的 ToolMessage 对象，执行 `to_dict` 后再 `from_dict` SHALL 产生与原始对象等价的 ToolMessage（往返一致性）。
6. THE ConversationContext SHALL 提供 `add_tool_result` 方法的更新签名，接受 `tool_call_id` 参数，以便在添加工具结果时关联调用标识。


### 需求 3：消息序列化适配（支持 Function Calling 字段）

**用户故事：** 作为开发者，我希望消息序列化逻辑能正确处理 tool_calls 和 tool_call_id 等扩展字段，以便发送给 LLM 的消息格式符合 OpenAI function calling 协议。

#### 验收标准

1. WHEN 序列化携带 `tool_calls` 的 AssistantMessage 时，THE 序列化逻辑 SHALL 输出符合 OpenAI API 格式的字典，包含 `role`、`content`（可为 None 或空字符串）和 `tool_calls` 列表，其中每个 tool_call 包含 `id`、`type`（固定为 "function"）和 `function`（含 `name`、`arguments`）。
2. WHEN 序列化 ToolMessage 时，THE 序列化逻辑 SHALL 输出包含 `role`（值为 "tool"）、`content`、`tool_call_id` 的字典。
3. WHEN 序列化不携带 tool_calls 的 AssistantMessage 时，THE 序列化逻辑 SHALL 输出仅包含 `role` 和 `content` 的字典，与现有行为保持一致。
4. WHEN 序列化 SystemMessage 或 UserMessage 时，THE 序列化逻辑 SHALL 输出仅包含 `role` 和 `content` 的字典，与现有行为保持一致。
5. FOR ALL 有效的 BaseMessage 子类实例，序列化后的字典 SHALL 能被 OpenAI Chat Completions API 接受。

### 需求 4：ToolRegistry 注册到 DI 容器

**用户故事：** 作为开发者，我希望 ToolRegistry 通过 DI 容器统一管理，以便 ChatServiceAdapter 和其他组件能通过依赖注入获取已注册工具的注册表实例。

#### 验收标准

1. THE DI_Container 的 `configure_container` 函数 SHALL 注册 ToolRegistry 的工厂函数，创建 ToolRegistry 实例并注册所有可用工具。
2. THE ToolRegistry 工厂函数 SHALL 实例化项目中已有的具体 Tool 实现（如文件系统工具等），并逐一注册到 ToolRegistry 中。
3. THE ToolRegistry SHALL 以 Singleton 作用域注册到 DI_Container，确保全局共享同一实例。
4. WHEN ChatServiceAdapter 需要 ToolRegistry 时，THE DI_Container SHALL 能正确解析并注入 ToolRegistry 实例。
5. THE ToolRegistry 的工厂函数 SHALL 记录日志，输出已注册工具的数量和名称列表。

### 需求 5：Agent Loop 配置

**用户故事：** 作为开发者，我希望 Agent Loop 的行为参数可通过配置文件控制，以便在不修改代码的情况下调整循环策略。

#### 验收标准

1. THE 配置文件（config.properties） SHALL 支持 `CHAT_MAX_TOOL_ROUNDS` 配置项，指定 Agent Loop 的最大迭代轮次，默认值为 10。
2. THE 配置文件 SHALL 支持 `CHAT_TOOL_CALLING_ENABLED` 配置项，控制是否启用 function calling 功能，默认值为 true。
3. WHEN `CHAT_TOOL_CALLING_ENABLED` 为 false 时，THE Chat_Service_Adapter SHALL 不向 LLM 传递 tools 参数，退化为普通对话模式。
4. WHEN `CHAT_MAX_TOOL_ROUNDS` 配置为正整数时，THE Agent_Loop SHALL 使用该值作为最大迭代轮次。
5. IF `CHAT_MAX_TOOL_ROUNDS` 配置为 0 或负数，THEN THE Agent_Loop SHALL 使用默认值 10。

### 需求 6：Agent Loop 同步对话编排

**用户故事：** 作为开发者，我希望 ChatServiceAdapter 的同步对话方法能自动执行工具调用循环，以便用户发送一条消息后系统能自主完成多轮工具调用并返回最终回复。

#### 验收标准

1. WHEN LLM 响应的 `LLMResponse.tool_calls` 非空时，THE Chat_Service_Adapter SHALL 进入 Agent Loop，依次执行以下步骤：将携带 tool_calls 的 AssistantMessage 追加到上下文、通过 ToolRegistry 执行每个工具调用、将每个工具执行结果作为 ToolMessage 追加到上下文、压缩上下文后再次调用 LLM。
2. WHEN LLM 响应的 `LLMResponse.tool_calls` 为空时，THE Chat_Service_Adapter SHALL 将 LLM 的文本回复作为 AssistantMessage 追加到上下文并返回，与现有行为一致。
3. THE Agent_Loop SHALL 在每轮迭代中将 ToolRegistry 的 schemas 通过 `ChatRequest.tools` 传递给 LLM。
4. WHEN Agent_Loop 达到 `max_tool_rounds` 最大迭代轮次时，THE Chat_Service_Adapter SHALL 停止循环，将最后一轮 LLM 的回复内容作为最终结果返回。
5. THE Chat_Service_Adapter SHALL 在 Agent Loop 结束后将完整的未压缩上下文（包含所有 AssistantMessage 和 ToolMessage）保存到 SessionContextStorePort，确保对话历史完整性。
6. IF 工具执行过程中发生异常，THEN THE Chat_Service_Adapter SHALL 将异常信息作为 ToolMessage 的 content 回传给 LLM，由 LLM 决定后续处理方式，Agent Loop 继续运行。
7. THE Chat_Service_Adapter 的 `chat` 方法 SHALL 返回的 ChatResponseVO 中包含最终的文本回复、实际使用的模型名称和累计的 token 用量。

### 需求 7：Agent Loop 流式对话编排

**用户故事：** 作为开发者，我希望流式对话模式下也能支持 Agent Loop，以便在实时展示生成过程的同时完成多轮工具调用。

#### 验收标准

1. WHILE Agent_Loop 处于工具调用迭代中，THE Chat_Service_Adapter 的 `stream_chat` 方法 SHALL 不产出中间轮次的流式分片，仅在最终轮次（LLM 返回纯文本回复）时产出流式分片。
2. WHEN 最终轮次 LLM 返回纯文本回复时，THE Chat_Service_Adapter SHALL 以流式方式逐个产出 StreamingChunk 分片，与现有流式行为一致。
3. THE Chat_Service_Adapter SHALL 在流式 Agent Loop 结束后将完整的未压缩上下文保存到 SessionContextStorePort。
4. WHEN Agent_Loop 达到 `max_tool_rounds` 最大迭代轮次时，THE Chat_Service_Adapter SHALL 停止循环，将最后一轮的回复以流式方式产出。
5. IF 工具执行过程中发生异常，THEN THE Chat_Service_Adapter SHALL 将异常信息作为 ToolMessage 的 content 回传给 LLM，Agent Loop 继续运行。

### 需求 8：Agent Loop 上下文完整性保证

**用户故事：** 作为开发者，我希望 Agent Loop 过程中产生的所有消息都被完整记录在对话上下文中，以便后续对话能引用之前的工具调用历史。

#### 验收标准

1. FOR ALL Agent Loop 执行过程中产生的 AssistantMessage（含 tool_calls）和 ToolMessage，THE ConversationContext SHALL 完整记录，不遗漏任何中间轮次的消息。
2. FOR ALL 经过 Agent Loop 的 ConversationContext 对象，执行 `to_dict` 后再 `from_dict` SHALL 产生与原始对象消息列表等价的 ConversationContext（往返一致性），包括 tool_calls 和 tool_call_id 字段。
3. THE ContextCompactionPort 的实现 SHALL 将 ToolMessage 视为非 system 消息参与压缩，与现有行为一致。
4. WHEN 加载包含 tool_calls 历史的会话上下文时，THE SessionContextStorePort 的实现 SHALL 正确还原所有 AssistantMessage 的 tool_calls 字段和 ToolMessage 的 tool_call_id 字段。
