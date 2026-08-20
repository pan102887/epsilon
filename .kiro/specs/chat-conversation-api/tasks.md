# 实施计划：聊天对话接口（Chat Conversation API）

## 概述

为系统新增聊天对话 HTTP 接口，支持同步响应和流式响应（SSE）两种模式。实施按"领域值对象 → 端口定义 → 基础设施实现 → 路由端点 → DI 注册与路由挂载 → 配置 → 测试"的顺序递进，确保每一步都可增量验证。

## Tasks

- [x] 1. 定义领域层值对象和端口接口
  - [x] 1.1 在 `domain/conversation/value_objects.py` 中创建 ChatRequest_VO 和 ChatResponse_VO
    - 创建新文件 `src/domain/conversation/value_objects.py`
    - 定义 `ChatRequest_VO` 为 frozen dataclass，包含 `session_id: str`、`message: str`、`stream: bool = False`
    - 在 `__post_init__` 中验证：`session_id` 非空、`message` 非空且非纯空白字符，不合法时抛出 `ValueError`
    - 定义 `ChatResponse_VO` 为 frozen dataclass，包含 `session_id: str`、`reply: str`、`model: str`、`usage: dict[str, int]`
    - 添加中文 docstring，说明每个字段的含义和验证规则
    - _需求: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.2 编写属性测试：空白消息拒绝
    - **Property 1: 空白消息拒绝**
    - 测试文件：`test/domain/conversation/test_chat_value_objects.py`
    - 使用 hypothesis 生成纯空白字符串（空字符串、空格、制表符、换行符等），验证构造 ChatRequest_VO 时抛出 ValueError
    - 同时生成空字符串验证 session_id 为空时抛出 ValueError
    - `@settings(max_examples=100)`
    - **验证: 需求 1.3, 1.4**

  - [x] 1.3 在 `domain/conversation/ports.py` 中追加 Chat_Service_Port 定义
    - 在现有 `ports.py` 文件末尾追加 `Chat_Service_Port` Protocol 类
    - 定义 `chat(self, request: ChatRequest_VO) -> ChatResponse_VO` 异步方法
    - 定义 `stream_chat(self, request: ChatRequest_VO) -> AsyncIterator[StreamingChunk]` 异步方法
    - 定义 `clear_session(self, session_id: str) -> None` 异步方法
    - 添加中文 docstring，说明每个方法的职责、参数和返回值
    - _需求: 2.1, 2.2, 2.3, 2.4_

- [x] 2. Checkpoint - 确保领域层定义正确
  - 确保所有测试通过，ask the user if questions arise.

- [x] 3. 实现 ChatConfig 配置和 ChatServiceAdapter
  - [x] 3.1 创建 `infrastructure/chat/chat_config.py` 配置类
    - 创建目录 `src/infrastructure/chat/` 及 `__init__.py`
    - 定义 `ChatConfig` 继承 `PropertiesBaseSettings`，使用 `env_prefix="CHAT_"`
    - 包含 `system_prompt: str = "你是一个有用的 AI 助手。"` 字段
    - 在 `config.properties` 中添加 `CHAT_SYSTEM_PROMPT=你是一个有用的 AI 助手。` 配置项
    - _需求: 8.1_

  - [x] 3.2 创建 `infrastructure/chat/chat_service_adapter.py` 实现 ChatServiceAdapter
    - 构造函数接收 `session_store: SessionContextStorePort`、`model_access: ModelAccessPort`、`system_prompt: str`
    - 实现 `_ensure_system_prompt(context: ConversationContext, system_prompt: str)` 私有方法：检查上下文中是否已有 system 消息，若无则添加配置的系统提示词
    - 实现 `chat(request: ChatRequest_VO) -> ChatResponse_VO`：加载上下文 → 注入 system prompt → 追加用户消息 → 调用 model_access.chat() → 追加助手回复 → 保存上下文 → 返回 ChatResponse_VO
    - 实现 `stream_chat(request: ChatRequest_VO) -> AsyncIterator[StreamingChunk]`：加载上下文 → 注入 system prompt → 追加用户消息 → 调用 model_access.stream() → 逐个 yield StreamingChunk → 最后一个分片时拼接完整回复并保存上下文
    - 实现 `clear_session(session_id: str) -> None`：调用 session_store.delete(session_id)
    - ModelAccessError 不捕获，向上传播
    - 添加中文 docstring
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 8.2, 8.3_

  - [x] 3.3 编写属性测试：系统提示词注入幂等性
    - **Property 5: 系统提示词注入幂等性**
    - 测试文件：`test/infrastructure/chat/test_chat_service_adapter.py`
    - 使用 hypothesis 生成随机 ConversationContext 和随机 system_prompt 字符串
    - 验证：无 system 消息时恰好新增一条；已有 system 消息时数量不变；对结果再次执行不改变任何内容（幂等性）
    - `@settings(max_examples=100)`
    - **验证: 需求 8.2, 8.3**

  - [ ]* 3.4 编写属性测试：同步对话后消息数量恰好增加 2
    - **Property 3: 同步对话后消息数量恰好增加 2**
    - 测试文件：`test/infrastructure/chat/test_chat_service_adapter.py`
    - 使用 hypothesis 生成随机 ChatRequest_VO 和随机初始 ConversationContext，mock ModelAccessPort 返回随机 ChatResponse
    - 验证 chat() 成功后 ConversationContext 的 message_count 恰好比调用前增加 2
    - `@settings(max_examples=100)`
    - **验证: 需求 10.2, 3.2, 3.4**

  - [ ]* 3.5 编写属性测试：同步响应字段完整性
    - **Property 8: 同步响应字段完整性**
    - 测试文件：`test/infrastructure/chat/test_chat_service_adapter.py`
    - 使用 hypothesis 生成随机 ChatRequest_VO，mock ModelAccessPort 返回随机 ChatResponse
    - 验证 chat() 返回的 ChatResponse_VO 包含非空的 session_id（与请求一致）、reply、model 字段，以及包含 token 用量信息的 usage 字典
    - `@settings(max_examples=100)`
    - **验证: 需求 3.5**

  - [ ]* 3.6 编写属性测试：流式分片拼接等于保存的助手回复
    - **Property 6: 流式分片拼接等于保存的助手回复**
    - 测试文件：`test/infrastructure/chat/test_chat_service_adapter.py`
    - 使用 hypothesis 生成随机 StreamingChunk 列表，mock ModelAccessPort stream 返回
    - 验证所有 delta_content 拼接结果等于最终保存到 ConversationContext 中的 assistant 消息内容
    - `@settings(max_examples=100)`
    - **验证: 需求 4.4, 4.5**

- [x] 4. Checkpoint - 确保基础设施层实现通过测试
  - 确保所有测试通过，ask the user if questions arise。

- [x] 5. 实现 Chat_Router 路由端点
  - [x] 5.1 创建 `application/routers/chat.py` 路由模块
    - 定义 `ChatRequestBody(BaseModel)` 和 `ChatResponseBody(BaseModel)` Pydantic 模型用于 HTTP 请求/响应
    - 实现 `POST /api/chat` 端点：根据 request body 中的 `stream` 字段分发到同步或流式处理
    - 同步模式：调用 Chat_Service.chat()，将 ChatResponse_VO 转换为 ChatResponseBody 返回 JSON
    - 流式模式：调用 Chat_Service.stream_chat()，使用 `sse-starlette` 的 `EventSourceResponse` 返回 SSE 流
    - SSE 事件 data 字段使用 JSON 格式 `{"delta_content": "...", "finished": false}`，最后发送 `[DONE]` 标记
    - 实现 `DELETE /api/chat/sessions/{session_id}` 端点：调用 Chat_Service.clear_session()，返回 `{"code": 0, "message": "会话已清除"}`
    - 使用 `Depends(inject(Chat_Service_Port))` 模式注入聊天服务依赖
    - ChatRequestBody 到 ChatRequest_VO 的转换在 Router 层完成，转换时 ValueError 由 FastAPI 异常处理器捕获返回 400
    - 添加中文 docstring
    - _需求: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3_

  - [x] 5.2 编写属性测试：SSE 序列化格式正确性
    - **Property 7: SSE 序列化格式正确性**
    - 测试文件：`test/application/routers/test_chat_router.py`
    - 使用 hypothesis 生成随机 StreamingChunk 对象，将其序列化为 SSE 事件的 data 字段
    - 验证 data 字段是合法 JSON 字符串，解析后包含 `delta_content`（字符串类型）和 `finished`（布尔类型）
    - `@settings(max_examples=100)`
    - **验证: 需求 6.2, 6.4**

  - [x] 5.3 编写属性测试：无效请求返回 400
    - **Property 9: 无效请求返回 400**
    - 测试文件：`test/application/routers/test_chat_router.py`
    - 使用 hypothesis 生成缺少必填字段或字段值非法的请求体（如缺少 session_id、message 为空等）
    - 使用 httpx.AsyncClient + TestClient 发送请求，验证返回 HTTP 400 状态码
    - `@settings(max_examples=100)`
    - **验证: 需求 5.4**

- [x] 6. DI 容器注册与路由挂载
  - [x] 6.1 在 `container_config.py` 中注册 Chat_Service_Port 绑定
    - 添加 `_create_chat_service()` 工厂函数：通过容器解析 `SessionContextStorePort` 和 `ModelAccessPort`，读取 `ChatConfig` 的 `system_prompt`，创建 `ChatServiceAdapter` 实例
    - 在 `configure_container()` 中添加 `container.register(Chat_Service_Port, _create_chat_service, Scope.SINGLETON)` 绑定
    - _需求: 9.1, 9.3_

  - [x] 6.2 在 `routers/__init__.py` 和 `server_app.py` 中挂载 Chat_Router
    - 在 `routers/__init__.py` 中导出 `chat_router`
    - 在 `server_app.py` 中添加 `app.include_router(chat_router)`
    - _需求: 9.2_

- [x] 7. Checkpoint - 确保 DI 注册和路由挂载正确
  - 确保所有测试通过，ask the user if questions arise。

- [x] 8. ConversationContext 属性测试
  - [x]* 8.1 编写属性测试：ConversationContext 序列化往返一致性
    - **Property 2: ConversationContext 序列化往返一致性**
    - 测试文件：`test/domain/conversation/test_conversation_context_props.py`
    - 使用 hypothesis 生成随机 ConversationContext（包含任意数量的 system、user、assistant、tool 消息，随机 max_messages）
    - 验证 `ConversationContext.from_dict(context.to_dict())` 产生与原始对象等价的 ConversationContext
    - `@settings(max_examples=100)`
    - **验证: 需求 10.1**

  - [x] 8.2 编写属性测试：窗口裁剪保留 system 消息
    - **Property 4: 窗口裁剪保留 system 消息**
    - 测试文件：`test/domain/conversation/test_conversation_context_props.py`
    - 使用 hypothesis 生成随机 ConversationContext（消息数量从 0 到 200，随机 max_messages）
    - 验证 get_messages() 后：所有 system 消息均被保留，非 system 消息数量不超过 max_messages
    - `@settings(max_examples=100)`
    - **验证: 需求 10.3**

- [x] 9. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise。

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 测试运行命令：`cd epsilon-boot && uv run pytest`
- Checkpoint 任务确保增量验证
- 项目使用 UV 包管理工具，所有命令在 `epsilon-boot/` 目录下执行
