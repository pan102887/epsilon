·# Implementation Plan: Agent 抽象层

## Overview

将 `ChatServiceAdapter` 中内嵌的 Agent Loop 逻辑抽取为独立的 Agent 抽象层。采用自底向上的顺序：先建值对象（AgentConfig、AgentResult）→ 定义 AgentPort Protocol → 实现 ReActAgentAdapter → 重构 ChatServiceAdapter → 更新 DI 容器配置。每步紧跟属性测试和单元测试验证行为等价性。

## Tasks

- [x] 1. 创建 Agent 值对象
  - [x] 1.1 创建 `domain/agent/value_objects.py`，实现 `AgentConfig` 和 `AgentResult`
    - `AgentConfig`：frozen dataclass，字段 `system_prompt: str`、`tool_schemas: list[dict[str, Any]]`、`model: str | None`、`max_rounds: int`
    - `AgentConfig.__post_init__`：当 `max_rounds <= 0` 时抛出 `ValueError`
    - `AgentResult`：frozen dataclass，字段 `content: str`、`model: str`、`usage: dict[str, int]`（默认 `{}`）、`latency_ms: float`（默认 `0.0`）
    - 模块级 docstring 和类 docstring 使用中文
    - _Requirements: 1.1, 1.2, 1.3, 3.1, 3.2_

  - [x] 1.2 编写属性测试：值对象构造与不可变性
    - **Property 1: Value object construction and immutability**
    - 文件：`test/domain/agent/test_agent_value_objects_property.py`
    - 使用 Hypothesis 生成随机 system_prompt、tool_schemas、model、max_rounds（>0），验证 AgentConfig 构造成功且字段值保留
    - 使用 Hypothesis 生成随机 content、model、usage、latency_ms，验证 AgentResult 构造成功且字段值保留
    - 验证两者均为 frozen：赋值属性时抛出 `FrozenInstanceError`
    - **Validates: Requirements 1.1, 3.1**

  - [x] 1.3 编写属性测试：AgentConfig max_rounds 校验
    - **Property 2: AgentConfig max_rounds validation**
    - 文件：`test/domain/agent/test_agent_value_objects_property.py`
    - 使用 Hypothesis 生成 max_rounds <= 0 的整数，验证构造时抛出 `ValueError`
    - 使用 Hypothesis 生成 max_rounds > 0 的整数，验证构造成功
    - **Validates: Requirements 1.2**

  - [x] 1.4 编写单元测试：值对象边界情况
    - 文件：`test/domain/agent/test_agent_value_objects_unit.py`
    - 测试 AgentConfig 基本构造、max_rounds=0 抛出 ValueError、max_rounds=-1 抛出 ValueError
    - 测试 AgentResult 基本构造、默认值正确（usage 为空 dict、latency_ms 为 0.0）
    - _Requirements: 1.1, 1.2, 3.1_

- [x] 2. 定义 AgentPort Protocol
  - [x] 2.1 创建 `domain/agent/ports.py`，定义 `AgentPort` Protocol
    - `run(self, context: ConversationContext, config: AgentConfig, model_access: ModelAccessPort) -> AgentResult`：异步方法
    - `run_streaming(self, context: ConversationContext, config: AgentConfig, model_access: ModelAccessPort) -> AsyncIterator[StreamingChunk]`：返回异步迭代器
    - 模块级 docstring 和方法 docstring 使用中文
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 3. 实现 ReActAgentAdapter
  - [x] 3.1 创建 `infrastructure/agent/__init__.py`（空文件）
    - _Requirements: 4.1_

  - [x] 3.2 创建 `infrastructure/agent/react_agent_adapter.py`，实现 `ReActAgentAdapter`
    - 构造函数接收 `tool_registry: ToolRegistry` 和 `compaction: ContextCompactionPort`
    - 迁移 `ChatServiceAdapter._serialize_messages` 静态方法到此类
    - 实现 `run` 方法：等价于原 `ChatServiceAdapter._run_agent_loop`，使用 `config.max_rounds` 控制循环、`config.tool_schemas` 传入 ChatRequest、`config.model` 传入 ChatRequest，累计 token 用量并返回 `AgentResult`
    - 实现 `run_streaming` 方法：等价于原 `ChatServiceAdapter._run_agent_loop_streaming`
    - 工具异常处理：捕获异常，将 `str(e)` 作为 ToolMessage content 追加到上下文
    - 所有 docstring 使用中文
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 3.3 编写属性测试：消息序列化正确性
    - **Property 3: Message serialization correctness**
    - 文件：`test/infrastructure/agent/test_react_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机 BaseMessage 列表（含 AssistantMessage with tool_calls、ToolMessage），验证 `ReActAgentAdapter._serialize_messages` 输出与原 `ChatServiceAdapter._serialize_messages` 完全一致
    - **Validates: Requirements 4.7**

  - [x] 3.4 编写属性测试：Token 用量累计
    - **Property 4: Token usage accumulation**
    - 文件：`test/infrastructure/agent/test_react_agent_adapter_property.py`
    - 使用 Hypothesis 生成多轮 LLMResponse 序列（中间轮含 tool_calls，最终轮不含），mock ModelAccessPort 按序返回，验证 `AgentResult.usage` 等于所有轮次 usage 的逐键累加
    - **Validates: Requirements 4.6**

  - [x] 3.5 编写属性测试：工具异常处理
    - **Property 5: Tool exception handling in Agent Loop**
    - 文件：`test/infrastructure/agent/test_react_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机异常消息，mock 工具执行抛出异常，验证 ToolMessage content 等于 `str(exception)` 且循环继续
    - **Validates: Requirements 4.5, 7.4**

  - [x] 3.6 编写属性测试：Agent run 行为等价性
    - **Property 6: Agent run behavioral equivalence**
    - 文件：`test/infrastructure/agent/test_react_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机 ConversationContext 和 LLMResponse 序列，验证 `ReActAgentAdapter.run()` 产生的 AgentResult 和上下文消息序列与原 `ChatServiceAdapter._run_agent_loop` 行为一致
    - **Validates: Requirements 4.3, 2.5, 7.1**

  - [x] 3.7 编写属性测试：Agent run_streaming 行为等价性
    - **Property 7: Agent run_streaming behavioral equivalence**
    - 文件：`test/infrastructure/agent/test_react_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机 ConversationContext 和 LLMResponse 序列，验证 `ReActAgentAdapter.run_streaming()` 产出的 StreamingChunk 序列与原 `ChatServiceAdapter._run_agent_loop_streaming` 行为一致
    - **Validates: Requirements 4.4, 7.2**

  - [x] 3.8 编写单元测试：ReActAgentAdapter 核心场景
    - 文件：`test/infrastructure/agent/test_react_agent_adapter_unit.py`
    - 测试单轮无工具调用：LLM 直接返回文本，验证 AgentResult 正确
    - 测试多轮工具调用：模拟 2 轮工具调用 + 1 轮文本回复，验证上下文消息序列
    - 测试达到 max_rounds：验证返回最后一轮响应
    - 测试工具异常：模拟工具抛出异常，验证 ToolMessage content 为异常信息
    - 测试 AgentPort Protocol 结构：验证 ReActAgentAdapter 满足 AgentPort Protocol（`isinstance` 检查）
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 4. Checkpoint - 确保 Agent 层所有测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. 重构 ChatServiceAdapter 为编排层
  - [x] 5.1 修改 `infrastructure/chat/chat_service_adapter.py`
    - 构造函数：移除 `tool_registry: ToolRegistry` 和 `max_tool_rounds: int` 参数，新增 `agent: AgentPort` 参数
    - 移除 `_serialize_messages` 静态方法（已迁移到 ReActAgentAdapter）
    - 移除 `_run_agent_loop` 和 `_run_agent_loop_streaming` 方法
    - 修改 `chat` 方法：当 `tool_calling_enabled=True` 且有工具时，构造 `AgentConfig` 并委托 `self._agent.run(context, config, model_access)` 执行，将 `AgentResult` 转换为 `ChatResponseVO`
    - 修改 `stream_chat` 方法：当 `tool_calling_enabled=True` 且有工具时，构造 `AgentConfig` 并委托 `self._agent.run_streaming(context, config, model_access)` 执行
    - 保留 `_ensure_system_prompt`、`_resolve_model_access`、`clear_session` 方法不变
    - 保留直接 LLM 调用路径（`tool_calling_enabled=False` 时），此路径仍需 `_serialize_messages`，从 ReActAgentAdapter 导入使用
    - 更新模块级 docstring 和类 docstring
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 7.1, 7.2, 7.3_

  - [x] 5.2 编写属性测试：ChatServiceAdapter 委托路由
    - **Property 8: ChatServiceAdapter delegation routing**
    - 文件：`test/infrastructure/chat/test_chat_service_adapter_refactor_property.py`
    - 使用 Hypothesis 生成随机 ChatRequestVO，验证 `tool_calling_enabled=True` 且有工具时委托 AgentPort.run()，`tool_calling_enabled=False` 时直接调用 LLM
    - **Validates: Requirements 5.2, 5.3, 5.4**

- [x] 6. 更新依赖注入容器配置
  - [x] 6.1 修改 `application/container_config.py`
    - 新增 `_create_agent` 工厂函数：通过容器解析 `ToolRegistry` 和 `ContextCompactionPort`，创建 `ReActAgentAdapter` 实例
    - 新增 `AgentPort → ReActAgentAdapter` 的 Singleton 绑定注册
    - 修改 `_create_chat_service` 工厂函数：通过容器解析 `AgentPort` 实例，移除 `tool_registry` 和 `max_tool_rounds` 参数，新增 `agent` 参数传入 `ChatServiceAdapter`
    - 确保初始化顺序：ToolRegistry → AgentPort → ChatServicePort
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 7. Final checkpoint - 确保所有测试通过
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- 所有测试文件需创建对应目录的 `__init__.py`（`test/domain/agent/`、`test/infrastructure/agent/`）
- 属性测试使用 `@settings(max_examples=100, deadline=5000)` 配置
- 属性测试函数需包含注释标签：`# Feature: agent-abstraction-layer, Property {N}: {title}`
- 本次重构是纯结构性重构，不改变任何外部可观测行为
- 直接 LLM 调用路径（`tool_calling_enabled=False`）中的消息序列化，从 `ReActAgentAdapter._serialize_messages` 导入使用
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
