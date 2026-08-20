# Implementation Plan: 面向任务的 Agent 入口重构

## Overview

在现有面向对话的 Agent 入口基础上，新增面向任务的入口。采用自底向上的顺序：先建领域层值对象（TaskStatus、Task、TraceEntry、TaskResult）→ 定义 TaskAgentPort Protocol → 实现 TaskAgentAdapter（含系统提示词生成和轨迹提取）→ 新增 API 端点 → 更新 DI 容器和路由注册。每步紧跟属性测试和单元测试验证正确性。现有对话入口保持不变。

## Tasks

- [x] 1. 创建领域层值对象和端口
  - [x] 1.1 创建 `domain/task/__init__.py` 和 `domain/task/value_objects.py`，实现 TaskStatus、Task、TraceEntry、TaskResult
    - `TaskStatus`：Enum，成员 SUCCESS("success")、FAILED("failed")、HUMAN_INTERVENTION_REQUIRED("human_intervention_required")
    - `Task`：frozen dataclass，字段 goal(str)、input_data(dict[str, Any], 默认{})、constraints(list[str], 默认[])、output_format(str|None, 默认None)、model(str|None, 默认None)、session_id(str|None, 默认None)；`__post_init__` 中校验 goal 非空非纯空白
    - `TraceEntry`：frozen dataclass，字段 step(int)、action(str)、detail(str)、timestamp_ms(float)
    - `TaskResult`：frozen dataclass，字段 content(str)、status(TaskStatus)、model(str)、usage(dict[str,int], 默认{})、trace(list[TraceEntry], 默认[])、latency_ms(float, 默认0.0)
    - 模块级 docstring 和类 docstring 使用中文
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 3.1, 3.2, 4.1, 4.2_

  - [x] 1.2 创建 `domain/task/ports.py`，定义 TaskAgentPort Protocol
    - `execute(self, task: Task) -> TaskResult`：异步方法
    - 支持有 session_id 和无 session_id 两种场景
    - 模块级 docstring 和方法 docstring 使用中文
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 1.3 编写属性测试：值对象构造与不可变性
    - **Property 1: Value object construction and immutability**
    - 文件：`test/domain/task/test_task_value_objects_property.py`
    - 使用 Hypothesis 生成随机合法字段，验证 Task、TraceEntry、TaskResult 构造成功且字段值保留
    - 验证三者均为 frozen：赋值属性时抛出 `FrozenInstanceError`
    - **Validates: Requirements 2.1, 3.1, 4.1**

  - [x] 1.4 编写属性测试：Task goal 空白校验
    - **Property 2: Task goal whitespace validation**
    - 文件：`test/domain/task/test_task_value_objects_property.py`
    - 使用 Hypothesis 生成纯空白字符串（含空字符串），验证构造时抛出 ValueError
    - 使用 Hypothesis 生成含至少一个非空白字符的字符串，验证构造成功
    - **Validates: Requirements 2.2**

  - [x] 1.5 编写单元测试：值对象边界情况
    - 文件：`test/domain/task/test_task_value_objects_unit.py`
    - 测试 TaskStatus 三个成员及其字符串值
    - 测试 Task 基本构造、默认值正确、goal="" 抛出 ValueError、goal="   " 抛出 ValueError
    - 测试 TraceEntry 基本构造
    - 测试 TaskResult 基本构造、默认值正确
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 4.1_

- [x] 2. Checkpoint - 确保领域层值对象和端口测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. 实现 TaskAgentAdapter
  - [x] 3.1 在 `config.properties` 中新增 `TASK_AGENT_MAX_ROUNDS=10` 配置项
    - 在"聊天服务 / Agent Loop 配置"段落下方新增
    - _Requirements: 6.9_

  - [x] 3.2 创建 `infrastructure/task/__init__.py` 和 `infrastructure/task/task_agent_adapter.py`，实现 TaskAgentAdapter
    - 构造函数接收 AgentPort、ToolRegistry、ModelRegistryPort、ContextCompactionPort、SessionContextStorePort、max_rounds(int, 默认10)
    - 实现 `build_system_prompt(task: Task) -> str` 静态方法：将 goal 作为核心指令，input_data 非空时序列化为 JSON 嵌入"输入数据"段落，constraints 非空时作为编号列表嵌入"约束条件"段落，output_format 不为 None 时嵌入"期望输出格式"段落
    - 实现 `_extract_trace(messages, start_index) -> list[TraceEntry]`：遍历新增消息，将 AssistantMessage 中的 tool_calls 转为 action="tool_call" 的 TraceEntry，将 ToolMessage 转为 action="tool_result" 的 TraceEntry，step 从 1 递增
    - 实现 `execute(task: Task) -> TaskResult`：根据 session_id 加载/创建 ConversationContext → build_system_prompt → 构造 AgentConfig → 通过 ModelRegistryPort 解析 ModelAccessPort → 记录执行前消息数量 → 委托 AgentPort.run() → 提取轨迹 → 构造 TaskResult(status=SUCCESS) → 有 session_id 时保存上下文；异常时捕获并返回 TaskResult(status=FAILED, content=str(e))
    - 所有 docstring 使用中文
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 3.3 编写属性测试：系统提示词生成正确性与确定性
    - **Property 3: System prompt generation correctness and determinism**
    - 文件：`test/infrastructure/task/test_task_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机 Task，验证 build_system_prompt 输出包含 goal；input_data 非空时包含 JSON 序列化内容；constraints 非空时包含每条约束；output_format 不为 None 时包含 output_format；两次调用结果相同
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5**

  - [x] 3.4 编写属性测试：Session 上下文加载/保存路由
    - **Property 4: Session context load/save routing**
    - 文件：`test/infrastructure/task/test_task_agent_adapter_property.py`
    - 使用 Hypothesis 生成有 session_id 和无 session_id 的 Task，mock AgentPort.run() 返回成功，验证有 session_id 时调用 load 和 save，无 session_id 时不调用
    - **Validates: Requirements 5.4, 6.4, 6.5**

  - [x] 3.5 编写属性测试：成功执行产生 SUCCESS 结果
    - **Property 5: Successful execution produces SUCCESS result**
    - 文件：`test/infrastructure/task/test_task_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机 Task 和 AgentResult，mock AgentPort.run() 返回该 AgentResult，验证 TaskResult.status == SUCCESS，content/model/usage 匹配
    - **Validates: Requirements 6.3, 6.6**

  - [x] 3.6 编写属性测试：异常处理产生 FAILED 结果
    - **Property 6: Exception handling produces FAILED result**
    - 文件：`test/infrastructure/task/test_task_agent_adapter_property.py`
    - 使用 Hypothesis 生成随机 Task 和异常消息，mock AgentPort.run() 抛出异常，验证 TaskResult.status == FAILED，content == str(exception)，不向上传播异常
    - **Validates: Requirements 6.7**

  - [x] 3.7 编写属性测试：轨迹提取
    - **Property 7: Trace extraction from context messages**
    - 文件：`test/infrastructure/task/test_task_agent_adapter_property.py`
    - 使用 Hypothesis 生成含 tool_calls 的 AssistantMessage 和 ToolMessage 序列，验证 _extract_trace 输出的 TraceEntry 列表中 tool_call 和 tool_result 正确映射，step 从 1 递增
    - **Validates: Requirements 6.8**

  - [x] 3.8 编写单元测试：TaskAgentAdapter 核心场景
    - 文件：`test/infrastructure/task/test_task_agent_adapter_unit.py`
    - 测试 TaskAgentPort Protocol 结构：验证 TaskAgentAdapter 满足 TaskAgentPort Protocol
    - 测试无 session_id 执行成功：mock AgentPort.run() 返回成功，验证不调用 save，TaskResult.status == SUCCESS
    - 测试有 session_id 执行成功：mock AgentPort.run() 返回成功，验证调用 load 和 save
    - 测试异常处理：mock AgentPort.run() 抛出异常，验证返回 FAILED
    - 测试轨迹提取：模拟含 tool_calls 的上下文，验证 TraceEntry 列表正确
    - 测试 build_system_prompt 仅 goal：验证只包含 goal 段落
    - 测试 build_system_prompt 全字段：验证包含所有段落
    - _Requirements: 5.1, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 9.1, 9.2, 9.3, 9.4_

- [x] 4. Checkpoint - 确保 TaskAgentAdapter 所有测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. 新增 API 端点和 DI 注册
  - [x] 5.1 创建 `application/routers/task.py`，实现 POST /api/task/execute 端点
    - 定义 TaskExecuteRequestBody（Pydantic BaseModel）：goal(str, 必填)、input_data(dict, 默认{})、constraints(list[str], 默认[])、output_format(str|None)、model(str|None)、session_id(str|None)
    - 定义 TraceEntryBody（Pydantic BaseModel）：step(int)、action(str)、detail(str)、timestamp_ms(float)
    - 定义 TaskExecuteResponseBody（Pydantic BaseModel）：code(int, 默认0)、content(str)、status(str)、model(str)、usage(dict[str,int])、trace(list[TraceEntryBody])、latency_ms(float)
    - 实现 execute_task 端点：解析请求体 → 构造 Task → 注入 TaskAgentPort → 调用 execute → 转换为响应体；goal 校验失败返回 HTTP 400
    - 所有 docstring 使用中文
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 5.2 更新 `application/container_config.py`，新增 TaskAgentPort → TaskAgentAdapter 的 Singleton 绑定
    - 新增 `_create_task_agent` 工厂函数：通过容器解析 AgentPort、ToolRegistry、ModelRegistryPort、ContextCompactionPort、SessionContextStorePort，从 config.properties 读取 TASK_AGENT_MAX_ROUNDS（默认 10），创建 TaskAgentAdapter 实例
    - 在 `configure_container()` 中 AgentPort 注册之后、ChatServicePort 注册之前新增 TaskAgentPort 注册
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 5.3 更新 `application/server_app.py`，注册 task_router
    - 在 routers 导入中新增 task_router
    - 在 `app.include_router` 列表中新增 task_router
    - _Requirements: 8.4_

  - [x] 5.4 更新 `application/routers/__init__.py`，导出 task_router
    - _Requirements: 8.4_

  - [x] 5.5 编写单元测试：API 端点
    - 文件：`test/application/routers/test_task_router_unit.py`
    - 测试正常请求：mock TaskAgentPort.execute() 返回成功 TaskResult，验证响应体字段正确
    - 测试 goal 为空：验证返回 HTTP 400
    - 测试 goal 为纯空白：验证返回 HTTP 400
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 6. Final checkpoint - 确保所有测试通过，现有对话入口不受影响
  - Ensure all tests pass, ask the user if questions arise.
  - 验证现有 `POST /api/chat` 端点不受影响
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- 所有测试文件需创建对应目录的 `__init__.py`（`test/domain/task/`、`test/infrastructure/task/`、`test/application/routers/`）
- 属性测试使用 `@settings(max_examples=100, deadline=5000)` 配置
- 属性测试函数需包含注释标签：`# Feature: task-oriented-agent, Property {N}: {title}`
- 现有面向对话的 ChatServicePort / ChatRequestVO 路径保持不变，两套入口并行存在
- TaskAgentAdapter 复用已有的 AgentPort、ToolRegistry、ModelRegistryPort 等基础设施
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
