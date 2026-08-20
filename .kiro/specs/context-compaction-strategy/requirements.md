# 需求文档：上下文压缩策略（Context Compaction Strategy）

## 简介

将 `ConversationContext.get_messages()` 中内嵌的滑动窗口裁剪逻辑抽取为独立的上下文压缩策略，遵循六边形架构的 Port/Adapter 模式。通过在领域层定义 `ContextCompactionPort` 协议，在基础设施层提供可替换的策略实现，使 `ConversationContext` 回归纯粹的消息容器职责，并由 `ChatServiceAdapter` 在调用模型前执行压缩。

此重构为后续引入多种压缩策略（Token 截断、LLM 摘要压缩、Embedding + RAG 检索、混合策略等）奠定扩展基础，同时通过 DI 容器实现策略的配置化切换。

## 术语表

- **ConversationContext**: 对话上下文值对象，管理对话消息列表，支持序列化/反序列化。重构后仅负责消息的存储与访问，不包含裁剪逻辑。
- **Message**: 对话消息值对象，包含角色（role）、内容（content）、可选工具名称和扩展元数据。
- **Context_Compaction_Port**: 领域层上下文压缩端口接口（Protocol），定义将完整消息列表压缩为适合发送给模型的消息列表的标准操作。
- **Sliding_Window_Compaction_Adapter**: 基础设施层滑动窗口压缩适配器，保留所有 system 消息和最近 N 条非 system 消息，复现当前 `ConversationContext.get_messages()` 的裁剪行为。
- **Chat_Service_Adapter**: 基础设施层聊天服务适配器，编排对话流程，重构后在调用模型前通过 Context_Compaction_Port 执行消息压缩。
- **ModelAccessPort**: 已有的统一模型接入端口，接收消息列表并调用底层 LLM。
- **DI_Container**: 依赖注入容器，管理 Port 到 Adapter 的绑定关系。
- **max_messages**: 滑动窗口策略中非 system 消息的最大保留数量。

## 需求

### 需求 1：上下文压缩端口定义

**用户故事：** 作为开发者，我希望在领域层定义上下文压缩的端口接口，以便将压缩策略与消息存储解耦，支持未来替换不同的压缩实现。

#### 验收标准

1. THE Context_Compaction_Port SHALL 使用 Python Protocol 定义，位于 `domain/chat/ports.py` 模块中。
2. THE Context_Compaction_Port SHALL 定义 `compact` 方法，接收 `list[Message]` 类型的完整消息列表，返回 `list[Message]` 类型的压缩后消息列表。
3. THE Context_Compaction_Port 的 `compact` 方法 SHALL 保证返回列表中的每个 Message 对象与输入列表中的对应 Message 对象引用相同或内容等价。
4. WHEN `compact` 方法接收空消息列表时，THE Context_Compaction_Port SHALL 返回空列表。

### 需求 2：ConversationContext 职责简化

**用户故事：** 作为开发者，我希望 ConversationContext 仅负责消息的存储与访问，不包含任何裁剪逻辑，以便遵循单一职责原则。

#### 验收标准

1. THE ConversationContext 的 `get_messages` 方法 SHALL 返回完整的消息列表，不执行任何裁剪或过滤操作。
2. THE ConversationContext SHALL 移除 `_max_messages` 属性及其相关的构造参数。
3. THE ConversationContext 的 `get_messages` 方法 SHALL 返回 `list[Message]` 类型，而非 `list[dict[str, str]]` 类型，将序列化职责留给调用方。
4. THE ConversationContext 的 `to_dict` 方法 SHALL 不再包含 `max_messages` 字段。
5. THE ConversationContext 的 `from_dict` 方法 SHALL 兼容包含和不包含 `max_messages` 字段的字典数据，确保向后兼容已持久化的会话数据。
6. FOR ALL 有效的 ConversationContext 对象，执行 `to_dict` 后再 `from_dict` SHALL 产生与原始对象消息列表等价的 ConversationContext（往返一致性）。

### 需求 3：滑动窗口压缩适配器实现

**用户故事：** 作为开发者，我希望有一个滑动窗口压缩适配器来复现当前的裁剪行为，以便重构后系统行为保持不变。

#### 验收标准

1. THE Sliding_Window_Compaction_Adapter SHALL 实现 Context_Compaction_Port 协议，位于 `infrastructure/chat/` 模块中。
2. THE Sliding_Window_Compaction_Adapter SHALL 通过构造参数接收 `max_messages` 配置值，指定非 system 消息的最大保留数量。
3. WHEN 非 system 消息数量超过 `max_messages` 时，THE Sliding_Window_Compaction_Adapter SHALL 保留所有 system 消息，并仅保留最近的 `max_messages` 条非 system 消息。
4. WHEN 非 system 消息数量未超过 `max_messages` 时，THE Sliding_Window_Compaction_Adapter SHALL 返回与输入相同的完整消息列表。
5. THE Sliding_Window_Compaction_Adapter 返回的消息列表 SHALL 保持 system 消息在前、非 system 消息在后的顺序，与重构前 `ConversationContext.get_messages()` 的行为一致。
6. FOR ALL 仅包含 system 消息的输入列表，THE Sliding_Window_Compaction_Adapter SHALL 原样返回全部 system 消息。

### 需求 4：ChatServiceAdapter 集成压缩策略

**用户故事：** 作为开发者，我希望 ChatServiceAdapter 在调用模型前通过 Context_Compaction_Port 执行消息压缩，以便将压缩时机集中在编排层管理。

#### 验收标准

1. THE Chat_Service_Adapter SHALL 通过构造参数接收 Context_Compaction_Port 依赖。
2. WHEN 执行同步对话（chat 方法）时，THE Chat_Service_Adapter SHALL 在构建 ModelAccessPort 请求前，调用 Context_Compaction_Port 的 `compact` 方法对 ConversationContext 的完整消息列表进行压缩。
3. WHEN 执行流式对话（stream_chat 方法）时，THE Chat_Service_Adapter SHALL 在构建 ModelAccessPort 请求前，调用 Context_Compaction_Port 的 `compact` 方法对 ConversationContext 的完整消息列表进行压缩。
4. THE Chat_Service_Adapter SHALL 将压缩后的消息列表（而非完整消息列表）传递给 ModelAccessPort。
5. THE Chat_Service_Adapter SHALL 将完整的消息列表（包含用户消息和助手回复，未经压缩）保存到 SessionContextStorePort，确保对话历史的完整性。
6. THE Chat_Service_Adapter 的 `_ensure_system_prompt` 方法 SHALL 适配 `get_messages` 返回 `list[Message]` 类型的变更。

### 需求 5：DI 容器注册与配置化切换

**用户故事：** 作为开发者，我希望通过 DI 容器管理压缩策略的绑定，并支持通过配置切换策略，以便在不修改代码的情况下调整压缩行为。

#### 验收标准

1. THE DI_Container SHALL 注册 Context_Compaction_Port 到 Sliding_Window_Compaction_Adapter 的绑定。
2. THE Chat_Service_Adapter 的工厂函数 SHALL 从 DI_Container 解析 Context_Compaction_Port 依赖，并注入到 Chat_Service_Adapter 构造函数中。
3. THE Sliding_Window_Compaction_Adapter SHALL 从配置中读取 `max_messages` 参数值，默认值为 50（与重构前保持一致）。
4. WHEN 配置中指定了不同的 `max_messages` 值时，THE Sliding_Window_Compaction_Adapter SHALL 使用配置指定的值。

### 需求 6：消息序列化适配

**用户故事：** 作为开发者，我希望在 ChatServiceAdapter 中将 Message 对象列表转换为 ModelAccessPort 所需的字典列表格式，以便保持与模型接入层的兼容性。

#### 验收标准

1. WHEN 将压缩后的消息列表传递给 ModelAccessPort 时，THE Chat_Service_Adapter SHALL 将 `list[Message]` 转换为 `list[dict[str, str]]` 格式（包含 role 和 content 字段）。
2. THE 消息序列化逻辑 SHALL 与重构前 `ConversationContext.get_messages()` 返回的字典格式保持一致。
3. FOR ALL 有效的 Message 对象，序列化为字典后 SHALL 包含且仅包含 `role` 和 `content` 两个键（用于模型调用场景）。

### 需求 7：行为等价性保证

**用户故事：** 作为开发者，我希望重构后系统的外部行为与重构前完全一致，以便确保重构不引入功能回归。

#### 验收标准

1. FOR ALL 有效的对话场景，重构后 Chat_Service_Adapter 发送给 ModelAccessPort 的消息列表 SHALL 与重构前 `ConversationContext.get_messages()` 返回的结果等价。
2. FOR ALL 有效的 ConversationContext 对象，重构后通过 SessionContextStorePort 保存再加载 SHALL 产生与原始对象消息列表等价的 ConversationContext（往返一致性）。
3. WHEN 非 system 消息数量为 0 时，THE Sliding_Window_Compaction_Adapter SHALL 仅返回 system 消息列表。
4. WHEN 消息列表中包含 tool 角色消息时，THE Sliding_Window_Compaction_Adapter SHALL 将 tool 消息视为非 system 消息参与滑动窗口裁剪。
