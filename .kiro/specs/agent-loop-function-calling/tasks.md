# 实现计划：Agent Loop / Function Calling

## 概述

基于已有的 Tool/ToolRegistry 抽象体系和 ChatRequest.tools 字段，补齐消息模型扩展、
消息序列化适配、DI 容器注册和 Agent Loop 编排，使端到端的 function calling 流程完整可用。

依赖顺序：①④ 已完成 → ②③ 并行 → ⑤ 独立 → ⑥ 依赖全部。
实现语言：Python（与项目一致）。

## 任务

- [x] 1. AssistantMessage 扩展 tool_calls 字段
  - [x] 1.1 修改 `domain/chat/context.py` 中的 AssistantMessage，新增可选 `tool_calls: list[ToolCallRequest]` 字段（默认空列表）
    - 导入 `ToolCallRequest`（来自 `domain/model_access/value_objects`）
    - 重写 `to_dict()`：当 `tool_calls` 非空时输出 `tool_calls` 键（每个元素序列化为 `{"id": ..., "name": ..., "arguments": ...}`），为空时不包含该键
    - 修改 `from_dict()` 工厂方法：role 为 "assistant" 时，若字典含 `tool_calls` 键则还原为 `list[ToolCallRequest]`，否则设为空列表
    - _需求：1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 1.2 编写 AssistantMessage tool_calls 往返一致性属性测试
    - 在 `test/domain/chat/` 下新增或扩展测试文件
    - 使用 Hypothesis 生成携带随机 tool_calls 的 AssistantMessage，验证 `to_dict()` → `from_dict()` 往返一致
    - _需求：1.6_

- [x] 2. ToolMessage 扩展 tool_call_id 字段
  - [x] 2.1 修改 `domain/chat/context.py` 中的 ToolMessage，新增必填 `tool_call_id: str` 字段
    - 重写 `to_dict()`：输出中包含 `tool_call_id` 键
    - 修改 `from_dict()` 工厂方法：role 为 "tool" 时读取 `tool_call_id`，缺失时设为空字符串（向后兼容）
    - 更新 `ConversationContext.add_tool_result` 方法签名，新增 `tool_call_id` 参数
    - _需求：2.1, 2.2, 2.3, 2.4, 2.6_

  - [x] 2.2 编写 ToolMessage tool_call_id 往返一致性属性测试
    - 使用 Hypothesis 生成携带随机 tool_call_id 的 ToolMessage，验证 `to_dict()` → `from_dict()` 往返一致
    - 验证旧格式（无 tool_call_id）反序列化时 tool_call_id 为空字符串
    - _需求：2.4, 2.5_


- [x] 3. 消息序列化适配（支持 Function Calling 字段）
  - [x] 3.1 重构 `ChatServiceAdapter._serialize_messages()` 方法，支持 tool_calls 和 tool_call_id 序列化
    - AssistantMessage 携带 tool_calls 时：输出 `{"role": "assistant", "content": ..., "tool_calls": [{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}]}`
    - ToolMessage：输出 `{"role": "tool", "content": ..., "tool_call_id": ...}`
    - 不携带 tool_calls 的 AssistantMessage、SystemMessage、UserMessage：保持现有 `{"role": ..., "content": ...}` 格式不变
    - 更新方法返回类型注解为 `list[dict[str, Any]]`
    - _需求：3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 编写消息序列化属性测试
    - 使用 Hypothesis 生成包含各类消息（含 tool_calls、tool_call_id）的列表，验证序列化输出格式符合 OpenAI API 规范
    - 验证 SystemMessage/UserMessage 序列化后仅含 role 和 content
    - _需求：3.3, 3.4, 3.5_

- [x] 4. 检查点 - 消息模型与序列化
  - 确保所有测试通过（`uv run pytest test/domain/chat/ -v`），如有问题请询问用户。

- [x] 5. Agent Loop 配置
  - [x] 5.1 在 `infrastructure/chat/chat_config.py` 的 ChatConfig 中新增配置字段
    - `max_tool_rounds: int = 10`，对应 `CHAT_MAX_TOOL_ROUNDS`
    - `tool_calling_enabled: bool = True`，对应 `CHAT_TOOL_CALLING_ENABLED`
    - 添加 `__post_init__` 或 validator：当 `max_tool_rounds` ≤ 0 时回退为默认值 10
    - _需求：5.1, 5.2, 5.4, 5.5_

  - [x] 5.2 在 `config.properties` 中添加 Agent Loop 配置项及注释
    - 添加 `CHAT_MAX_TOOL_ROUNDS=10` 和 `CHAT_TOOL_CALLING_ENABLED=true`
    - _需求：5.1, 5.2_

  - [x] 5.3 编写 ChatConfig 配置校验单元测试
    - 验证 max_tool_rounds ≤ 0 时回退为 10
    - 验证 tool_calling_enabled 默认为 true
    - _需求：5.4, 5.5_

- [x] 6. ToolRegistry 注册到 DI 容器
  - [x] 6.1 在 `application/container_config.py` 中添加 ToolRegistry 工厂函数和注册逻辑
    - 创建 `_create_tool_registry()` 工厂函数：实例化 ToolRegistry，导入并注册已有的具体 Tool 实现（filesystem 下的 ReadFileTool、WriteFileTool、EditFileTool、ListDirTool 等）
    - 记录日志：输出已注册工具的数量和名称列表
    - 在 `configure_container()` 中以 Singleton 作用域注册 ToolRegistry
    - _需求：4.1, 4.2, 4.3, 4.5_

  - [x] 6.2 编写 ToolRegistry DI 注册单元测试
    - 验证 `_create_tool_registry()` 返回的 ToolRegistry 包含预期的工具
    - 验证注册为 Singleton 作用域
    - _需求：4.3, 4.4_

- [x] 7. 检查点 - 配置与 DI 注册
  - 确保所有测试通过（`uv run pytest test/ -v`），如有问题请询问用户。


- [x] 8. Agent Loop 同步对话编排
  - [x] 8.1 修改 `ChatServiceAdapter.__init__`，注入 ToolRegistry 和 ChatConfig 中的 agent loop 配置
    - 新增 `_tool_registry: ToolRegistry` 属性
    - 新增 `_max_tool_rounds: int` 和 `_tool_calling_enabled: bool` 属性
    - 更新 `_create_chat_service()` 工厂函数，从容器解析 ToolRegistry 并传入
    - _需求：4.4, 5.3, 6.3_

  - [x] 8.2 在 `ChatServiceAdapter` 中实现 `_run_agent_loop` 私有异步方法（同步模式）
    - 接收 context、chat_request 参数，返回 LLMResponse
    - 循环逻辑：调用 LLM → 检查 tool_calls → 非空则追加 AssistantMessage（含 tool_calls）到 context → 逐个执行工具（通过 ToolRegistry.execute）→ 将每个结果作为 ToolMessage（含 tool_call_id）追加到 context → 压缩上下文 → 重新序列化 → 再次调用 LLM
    - 工具执行异常时：捕获异常，将异常信息作为 ToolMessage.content 回传，循环继续
    - 达到 max_tool_rounds 时停止循环，返回最后一轮 LLM 响应
    - tool_calls 为空时直接返回 LLM 响应
    - 每轮通过 ChatRequest.tools 传递 ToolRegistry.get_schemas()
    - 累计 token 用量
    - _需求：6.1, 6.2, 6.3, 6.4, 6.6, 6.7_

  - [x] 8.3 重构 `ChatServiceAdapter.chat` 方法，集成 Agent Loop
    - tool_calling_enabled 为 true 且 ToolRegistry 有工具时：构建 ChatRequest 时传入 tools，调用 `_run_agent_loop`
    - tool_calling_enabled 为 false 时：保持现有行为不变（不传 tools）
    - Agent Loop 结束后保存完整未压缩上下文到 SessionContextStorePort
    - _需求：5.3, 6.1, 6.2, 6.5, 6.7_

  - [x] 8.4 编写 Agent Loop 同步对话单元测试
    - Mock ModelAccessPort 返回含 tool_calls 的 LLMResponse，验证循环执行
    - Mock ToolRegistry.execute 返回工具结果，验证 ToolMessage 正确追加
    - 验证达到 max_tool_rounds 时停止
    - 验证工具执行异常时异常信息回传给 LLM
    - 验证 tool_calling_enabled=false 时不传 tools
    - _需求：6.1, 6.2, 6.4, 6.6, 5.3_

- [x] 9. Agent Loop 流式对话编排
  - [x] 9.1 在 `ChatServiceAdapter` 中实现 `_run_agent_loop_streaming` 私有异步生成器方法
    - 循环逻辑与同步模式类似，但中间轮次使用同步 `model_access.chat()` 调用（不产出流式分片）
    - 最终轮次（tool_calls 为空）使用 `model_access.stream()` 产出 StreamingChunk
    - 达到 max_tool_rounds 时，最后一轮以流式方式产出
    - 工具执行异常处理与同步模式一致
    - _需求：7.1, 7.2, 7.4, 7.5_

  - [x] 9.2 重构 `ChatServiceAdapter.stream_chat` 方法，集成流式 Agent Loop
    - tool_calling_enabled 为 true 且有工具时：调用 `_run_agent_loop_streaming`
    - tool_calling_enabled 为 false 时：保持现有行为不变
    - 流式结束后保存完整未压缩上下文到 SessionContextStorePort
    - _需求：7.1, 7.2, 7.3_

  - [x] 9.3 编写 Agent Loop 流式对话单元测试
    - Mock ModelAccessPort，验证中间轮次不产出流式分片
    - 验证最终轮次正确产出 StreamingChunk
    - 验证达到 max_tool_rounds 时停止并流式产出
    - _需求：7.1, 7.2, 7.4_

- [x] 10. 检查点 - Agent Loop 核心逻辑
  - 确保所有测试通过（`cd epsilon-boot && uv run pytest test/ -v`），如有问题请询问用户。

- [x] 11. 上下文完整性保证与属性测试补全
  - [x] 11.1 更新 `test/domain/chat/test_compaction_properties.py` 中的 Hypothesis 策略，支持 Agent Loop 消息字段
    - 扩展 `message_st` 策略：AssistantMessage 可携带随机 `tool_calls`（复用 `test_agent_loop_message_properties.py` 中的 `tool_call_request_st` 策略），ToolMessage 携带随机 `tool_call_id`
    - 更新 `message_action_st` 策略：tool 角色动作元组新增 `tool_call_id` 字段
    - 更新 `_add_message` 辅助函数：传递 `tool_call_id` 参数给 `ctx.add_tool_result()`
    - 确保现有 Property 2（消息完整性）和 Property 3（序列化往返一致性）测试自动覆盖 `tool_calls` 和 `tool_call_id` 新字段
    - _需求：8.1, 8.2, 8.4_

  - [x] 11.2 编写 ConversationContext 含 Agent Loop 消息的往返一致性属性测试
    - **Property 5: ConversationContext 含 Agent Loop 消息的往返一致性**
    - **验证：需求 8.2, 8.4**
    - 测试文件：`test/domain/chat/test_agent_loop_message_properties.py`
    - 使用 Hypothesis 生成包含 AssistantMessage（含随机 tool_calls）和 ToolMessage（含随机 tool_call_id）的 ConversationContext
    - 验证 `to_dict()` → `ConversationContext.from_dict()` 后所有 `tool_calls` 和 `tool_call_id` 字段正确还原
    - _需求：8.2_

  - [x] 11.3 编写滑动窗口压缩对 ToolMessage 的处理验证属性测试
    - **Property 6: 滑动窗口压缩将 ToolMessage 视为非 system 消息**
    - **验证：需求 8.3**
    - 测试文件：`test/domain/chat/test_compaction_properties.py`（扩展现有测试）
    - 使用 Hypothesis 生成包含 ToolMessage 的随机消息列表和随机正整数 `max_messages`
    - 验证 `SlidingWindowCompactionAdapter(max_messages).compact(messages)` 中 ToolMessage 不出现在 system 消息组中，而是与 user/assistant 消息一起受 `max_messages` 限制
    - _需求：8.3_

- [x] 12. 最终检查点 - 全量测试
  - 确保所有测试通过（`cd epsilon-boot && uv run pytest test/ -v`），如有问题请询问用户。

## 备注

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 检查点任务确保增量验证，及时发现问题
- ①（ChatRequest.tools 字段）和 ④（ModelAccessPort 适配器 function calling 支持）已完成，不在任务列表中
- 任务 1-2 可并行执行，任务 3 依赖 1-2 的消息模型变更，任务 5-6 独立，任务 8-9 依赖全部前置任务
- 设计文档 Property 1-4 已由已完成的任务 1.2、2.2、3.2、5.3 覆盖；Property 5-6 由任务 11.2、11.3 覆盖
