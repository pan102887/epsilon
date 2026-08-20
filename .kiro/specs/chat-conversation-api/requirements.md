# 需求文档：聊天对话接口（Chat Conversation API）

## 简介

为系统新增聊天对话 HTTP 接口，允许用户通过 API 与 AI 模型进行多轮对话交互。接口支持同步响应和流式响应（SSE）两种模式，基于现有的 `ConversationContext` 管理对话历史，通过 `SessionContextStorePort` 持久化会话状态，并通过 `ModelAccessPort` 调用底层 LLM 模型。

本功能遵循项目 DDD 分层架构：在 `domain/conversation` 限界上下文中定义聊天服务端口，在 `infrastructure` 层提供适配器实现，在 `application/routers` 中暴露 FastAPI 路由端点。

## 术语表

- **Chat_Router**: 应用层聊天路由模块，定义 FastAPI HTTP 端点，负责接收请求、调用领域服务并返回响应。
- **Chat_Service**: 领域层聊天服务，编排对话流程（加载上下文 → 追加用户消息 → 调用模型 → 保存上下文 → 返回结果）。
- **Chat_Service_Port**: 领域层聊天服务端口接口（Protocol），定义聊天服务的标准操作。
- **ConversationContext**: 已有的对话上下文值对象，管理消息列表并提供窗口裁剪策略。
- **SessionContextStorePort**: 已有的会话上下文存储端口，支持保存、加载和删除操作（Redis 实现）。
- **ModelAccessPort**: 已有的统一模型接入端口，支持同步对话（chat）和流式对话（stream）。
- **ChatRequest_VO**: 聊天请求值对象，封装用户发送的对话消息和会话标识。
- **ChatResponse_VO**: 聊天响应值对象，封装模型返回的回复内容和元数据。
- **SSE**: Server-Sent Events，服务器推送事件协议，用于流式响应场景。
- **Session_ID**: 会话唯一标识符，用于关联同一用户的多轮对话上下文。

## 需求

### 需求 1：聊天请求与响应值对象定义

**用户故事：** 作为开发者，我希望有明确定义的聊天请求和响应值对象，以便在各层之间传递结构化的对话数据。

#### 验收标准

1. THE ChatRequest_VO SHALL 包含以下字段：session_id（会话标识，字符串类型）、message（用户消息内容，字符串类型）、stream（是否使用流式响应，布尔类型，默认 false）。
2. THE ChatResponse_VO SHALL 包含以下字段：session_id（会话标识）、reply（模型回复内容）、model（实际使用的模型名称）、usage（token 用量信息）。
3. WHEN ChatRequest_VO 的 message 字段为空字符串或仅包含空白字符时，THE ChatRequest_VO SHALL 在验证阶段拒绝该请求。
4. WHEN ChatRequest_VO 的 session_id 字段为空时，THE ChatRequest_VO SHALL 在验证阶段拒绝该请求。

### 需求 2：聊天服务端口定义

**用户故事：** 作为开发者，我希望在领域层定义聊天服务的端口接口，以便遵循六边形架构原则，将业务逻辑与具体实现解耦。

#### 验收标准

1. THE Chat_Service_Port SHALL 使用 Python Protocol 定义，位于 `domain/conversation` 模块中。
2. THE Chat_Service_Port SHALL 定义 `chat` 异步方法，接收 ChatRequest_VO 并返回 ChatResponse_VO。
3. THE Chat_Service_Port SHALL 定义 `stream_chat` 异步方法，接收 ChatRequest_VO 并返回 AsyncIterator，逐个产出流式响应分片。
4. THE Chat_Service_Port SHALL 定义 `clear_session` 异步方法，接收 session_id 并清除对应的对话上下文。

### 需求 3：聊天服务实现（同步对话）

**用户故事：** 作为用户，我希望发送一条消息后获得完整的 AI 回复，以便在不需要实时展示生成过程的场景下使用。

#### 验收标准

1. WHEN 收到同步聊天请求时，THE Chat_Service SHALL 通过 SessionContextStorePort 加载该 session_id 对应的 ConversationContext。
2. WHEN ConversationContext 加载完成后，THE Chat_Service SHALL 将用户消息追加到 ConversationContext 中。
3. WHEN 用户消息追加完成后，THE Chat_Service SHALL 通过 ModelAccessPort 的 chat 方法发送对话请求并获取完整响应。
4. WHEN 模型响应返回后，THE Chat_Service SHALL 将助手回复追加到 ConversationContext 中，并通过 SessionContextStorePort 保存更新后的上下文。
5. WHEN 对话流程完成后，THE Chat_Service SHALL 返回包含回复内容、模型名称和 token 用量的 ChatResponse_VO。
6. IF 模型调用过程中发生 ModelAccessError，THEN THE Chat_Service SHALL 将异常向上传播，由统一异常处理器处理。

### 需求 4：聊天服务实现（流式对话）

**用户故事：** 作为用户，我希望在发送消息后实时看到 AI 逐字生成回复的过程，以便获得更好的交互体验。

#### 验收标准

1. WHEN 收到流式聊天请求时，THE Chat_Service SHALL 通过 SessionContextStorePort 加载该 session_id 对应的 ConversationContext。
2. WHEN ConversationContext 加载完成后，THE Chat_Service SHALL 将用户消息追加到 ConversationContext 中。
3. WHEN 用户消息追加完成后，THE Chat_Service SHALL 通过 ModelAccessPort 的 stream 方法发起流式对话请求。
4. WHILE 流式响应进行中，THE Chat_Service SHALL 逐个产出 StreamingChunk 给调用方。
5. WHEN 流式响应的最后一个分片（finished 为 True）到达时，THE Chat_Service SHALL 将完整的助手回复拼接后追加到 ConversationContext 中，并通过 SessionContextStorePort 保存更新后的上下文。
6. IF 流式传输过程中发生 ModelAccessError，THEN THE Chat_Service SHALL 停止产出分片并将异常向上传播。

### 需求 5：同步聊天 HTTP 端点

**用户故事：** 作为前端开发者，我希望有一个标准的 HTTP POST 端点用于发送聊天消息并获取完整回复，以便集成到前端应用中。

#### 验收标准

1. THE Chat_Router SHALL 在 `/api/chat` 路径上注册一个 POST 端点，接收 JSON 格式的聊天请求。
2. WHEN 收到有效的聊天请求且 stream 字段为 false 时，THE Chat_Router SHALL 调用 Chat_Service 的 chat 方法并返回 JSON 格式的 ChatResponse_VO。
3. THE Chat_Router SHALL 通过 DI 容器注入 Chat_Service_Port 依赖。
4. WHEN 请求参数校验失败时，THE Chat_Router SHALL 返回 HTTP 400 状态码和参数校验错误详情（由 FastAPI 的 RequestValidationError 处理器统一处理）。

### 需求 6：流式聊天 HTTP 端点（SSE）

**用户故事：** 作为前端开发者，我希望有一个支持 Server-Sent Events 的 HTTP 端点用于接收流式回复，以便在前端实时展示 AI 生成过程。

#### 验收标准

1. WHEN 收到有效的聊天请求且 stream 字段为 true 时，THE Chat_Router SHALL 返回 `text/event-stream` 类型的 SSE 响应。
2. WHILE 流式响应进行中，THE Chat_Router SHALL 将每个 StreamingChunk 序列化为 SSE 格式的 `data:` 事件逐个发送给客户端。
3. WHEN 流式响应的最后一个分片到达时，THE Chat_Router SHALL 发送一个包含 `[DONE]` 标记的 SSE 事件，通知客户端流式传输结束。
4. THE SSE 事件的 data 字段 SHALL 使用 JSON 格式，包含 delta_content（增量内容）和 finished（是否结束）字段。

### 需求 7：会话管理端点

**用户故事：** 作为前端开发者，我希望能够清除指定会话的对话历史，以便用户可以开始新的对话。

#### 验收标准

1. THE Chat_Router SHALL 在 `/api/chat/sessions/{session_id}` 路径上注册一个 DELETE 端点。
2. WHEN 收到有效的会话删除请求时，THE Chat_Router SHALL 调用 Chat_Service 的 clear_session 方法清除对应的对话上下文。
3. WHEN 会话清除成功后，THE Chat_Router SHALL 返回 HTTP 200 状态码和 `{"code": 0, "message": "会话已清除"}` 响应。

### 需求 8：系统提示词配置

**用户故事：** 作为开发者，我希望能够为聊天服务配置默认的系统提示词，以便控制 AI 助手的行为和角色设定。

#### 验收标准

1. THE Chat_Service SHALL 支持通过配置指定默认的系统提示词（system prompt）。
2. WHEN 加载的 ConversationContext 中不包含 system 角色消息时，THE Chat_Service SHALL 自动将配置的系统提示词作为第一条 system 消息添加到上下文中。
3. WHEN 加载的 ConversationContext 中已包含 system 角色消息时，THE Chat_Service SHALL 保留现有的 system 消息，不重复添加。

### 需求 9：DI 容器注册与路由挂载

**用户故事：** 作为开发者，我希望聊天服务的依赖注入和路由注册遵循项目现有模式，以便保持架构一致性。

#### 验收标准

1. THE Container_Config SHALL 注册 Chat_Service_Port 到其具体实现的绑定，使用 SessionContextStorePort 和 ModelAccessPort 作为构造依赖。
2. THE server_app.py SHALL 将 Chat_Router 通过 `app.include_router` 挂载到 FastAPI 应用实例上。
3. THE Chat_Router SHALL 使用 `Depends(inject(Chat_Service_Port))` 模式注入聊天服务依赖，与现有 health router 的依赖注入模式保持一致。

### 需求 10：对话上下文序列化往返一致性

**用户故事：** 作为开发者，我希望对话上下文在保存和加载过程中保持数据一致性，以便多轮对话的历史记录不会丢失或损坏。

#### 验收标准

1. FOR ALL 有效的 ConversationContext 对象，通过 SessionContextStorePort 保存后再加载 SHALL 产生与原始对象等价的 ConversationContext（往返一致性）。
2. THE Chat_Service SHALL 确保每次对话完成后，ConversationContext 中的消息数量恰好增加 2 条（1 条用户消息 + 1 条助手回复）。
3. WHILE ConversationContext 的消息数量超过 max_messages 限制时，THE ConversationContext SHALL 通过窗口裁剪策略保留最近的非 system 消息，确保 system 消息始终保留。
