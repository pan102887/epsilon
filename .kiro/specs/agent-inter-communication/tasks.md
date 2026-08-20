# 实施计划：Agent 间通信机制

## 概述

为系统引入 Agent 间通信能力，使多个命名 Agent 实例能够通过工具调用机制相互委派任务。实施按"领域层值对象与异常 → 领域层端口 → Task 扩展 → 基础设施层适配器与工具 → DI 容器注册与配置"的顺序递进，每步构建在前一步基础上，确保增量可验证。

## Tasks

- [x] 1. 新增 NamedAgentConfig 值对象
  - [x] 1.1 在 `domain/agent/value_objects.py` 中新增 `NamedAgentConfig` frozen dataclass
    - 包含 name(str)、description(str)、system_prompt(str)、tool_names(frozenset[str] | None = None)、model(str | None = None) 字段
    - `__post_init__` 校验 name 和 description 不为空或纯空白字符，否则抛出 ValueError
    - 添加中文 docstring
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 1.2 编写属性测试：NamedAgentConfig 空白字段校验
    - **Property 1: NamedAgentConfig 空白字段校验**
    - 生成随机空白字符串（含空串），验证以其作为 name 或 description 构造时抛出 ValueError；使用非空白 name 和 description 构造应成功
    - 测试文件：`test/domain/agent/test_named_agent_config_properties.py`
    - **验证: 需求 2.7, 2.8**

- [x] 2. 新增 Agent 异常类型
  - [x] 2.1 在 `domain/agent/exceptions.py` 中新增 `AgentNotFoundError` 和 `DelegationDepthExceededError`
    - `AgentNotFoundError` 继承 BizException，错误码 60010，构造函数接收 agent_name(str) 和 registered_names(list[str])，message 包含 agent_name 和已注册列表，保存 agent_name 属性
    - `DelegationDepthExceededError` 继承 BizException，错误码 60011，构造函数接收 current_depth(int)、max_depth(int)、target_agent(str)，message 包含三者信息，保存 current_depth 和 max_depth 属性
    - 添加中文 docstring
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 2.2 编写属性测试：异常消息包含标识信息
    - **Property 5: 异常消息包含标识信息**
    - 生成随机 agent_name 和 registered_names，验证 AgentNotFoundError.message 包含 agent_name 和所有 registered_names 中的名称
    - 生成随机 current_depth、max_depth、target_agent，验证 DelegationDepthExceededError.message 包含三者的字符串表示
    - 测试文件：`test/domain/agent/test_agent_exceptions_properties.py`
    - **验证: 需求 5.3, 5.6**

  - [x] 2.3 编写单元测试：异常错误码和继承关系
    - 验证 AgentNotFoundError 错误码为 60010，isinstance(error, BizException) 为 True
    - 验证 DelegationDepthExceededError 错误码为 60011，isinstance(error, BizException) 为 True
    - 测试文件：`test/domain/agent/test_agent_exceptions_properties.py`
    - _需求: 5.1, 5.4_

- [x] 3. 扩展 Task 值对象新增 delegation_depth 字段
  - [x] 3.1 修改 `domain/task/value_objects.py` 中 `Task` frozen dataclass
    - 新增 `delegation_depth: int = 0` 字段
    - 在 `__post_init__` 中校验 delegation_depth < 0 时抛出 ValueError
    - 更新中文 docstring
    - _需求: 4.1, 4.2, 4.3, 4.4_

  - [x] 3.2 编写属性测试：Task 负数 delegation_depth 校验
    - **Property 4: Task 负数 delegation_depth 校验**
    - 生成随机负整数，验证 Task(goal="test", delegation_depth=d) 抛出 ValueError
    - 测试文件：`test/domain/task/test_task_delegation_depth_properties.py`
    - **验证: 需求 4.3**

  - [x] 3.3 编写单元测试：Task delegation_depth 默认值
    - 验证不传 delegation_depth 时默认为 0
    - 验证传入非负整数时正常构造
    - 测试文件：`test/domain/task/test_task_delegation_depth_properties.py`
    - _需求: 4.2_

- [x] 4. Checkpoint - 确保领域层测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 5. 新增 AgentRegistryPort 端口定义
  - [x] 5.1 在 `domain/agent/ports.py` 中追加 `AgentRegistryPort` Protocol
    - 定义 register(config: NamedAgentConfig) -> None 方法
    - 定义 get(name: str) -> NamedAgentConfig | None 方法
    - 定义 has(name: str) -> bool 方法
    - 定义 list_names() -> list[str] 方法
    - 使用 Python Protocol 定义，添加中文 docstring
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 6. 实现 AgentRegistryAdapter 基础设施适配器
  - [x] 6.1 新建 `infrastructure/agent/agent_registry_adapter.py`
    - 实现 AgentRegistryPort 协议，内部使用 dict[str, NamedAgentConfig] 存储
    - register() 按 config.name 存入字典，同名覆盖
    - get() 返回对应 config 或 None
    - has() 返回布尔值
    - list_names() 返回所有已注册名称列表
    - 添加中文 docstring
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 6.2 编写属性测试：AgentRegistry register/get/has 一致性
    - **Property 2: AgentRegistry register/get/has 一致性**
    - 生成随机 NamedAgentConfig 序列注册到 AgentRegistryAdapter，验证对于任意名称 n：曾注册则 get(n) 返回最后一次注册的 config 且 has(n) 为 True；未注册则 get(n) 为 None 且 has(n) 为 False
    - 测试文件：`test/infrastructure/agent/test_agent_registry_properties.py`
    - **验证: 需求 3.2, 3.3, 3.4, 3.5**

  - [x] 6.3 编写属性测试：AgentRegistry list_names 完整性
    - **Property 3: AgentRegistry list_names 完整性**
    - 生成随机 NamedAgentConfig 集合注册到 AgentRegistryAdapter，验证 list_names() 返回的名称集合等于所有已注册 config 的 name 集合（同名覆盖后的去重集合）
    - 测试文件：`test/infrastructure/agent/test_agent_registry_properties.py`
    - **验证: 需求 3.6**

- [x] 7. 实现 DelegateToAgentTool 委派工具
  - [x] 7.1 新建 `infrastructure/agent/delegate_to_agent_tool.py`
    - 继承 Tool ABC，name 为 "delegate_to_agent"
    - 构造函数接收 agent_registry(AgentRegistryPort)、task_agent(TaskAgentPort)、current_delegation_depth(int=0)、max_delegation_depth(int=3)
    - parameters 包含 agent_name(string, 必填)、task_goal(string, 必填)、input_data(object, 可选)
    - description 动态包含已注册 Agent 列表信息
    - execute() 流程：校验深度 → 查找 Agent → 构造 Task(goal, tool_names, model, delegation_depth+1, session_id=None) → 调用 TaskAgentPort.execute(task) → 返回 TaskResult.content 或错误信息
    - 深度超限时抛出 DelegationDepthExceededError 并记录 WARNING 日志
    - Agent 未找到时抛出 AgentNotFoundError
    - TaskResult.status 为 FAILED 时返回包含错误信息的字符串
    - 添加中文 docstring
    - _需求: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 7.1, 7.2, 7.4, 7.5, 8.1, 8.2, 8.3_

  - [x] 7.2 编写属性测试：DelegateToAgentTool 未注册 Agent 抛出 AgentNotFoundError
    - **Property 6: DelegateToAgentTool 未注册 Agent 抛出 AgentNotFoundError**
    - 生成随机未注册名称，验证 execute() 抛出 AgentNotFoundError 且异常的 agent_name 属性等于传入名称
    - 测试文件：`test/infrastructure/agent/test_delegate_tool_properties.py`
    - **验证: 需求 6.6**

  - [x] 7.3 编写属性测试：DelegateToAgentTool 正确构造 Task
    - **Property 7: DelegateToAgentTool 正确构造 Task**
    - 生成随机已注册 NamedAgentConfig 和 current_delegation_depth（在限制范围内），Mock TaskAgentPort.execute 捕获传入的 Task，验证 task.tool_names 等于 config.tool_names、task.model 等于 config.model、task.delegation_depth 等于 current_depth + 1、task.session_id 为 None
    - 测试文件：`test/infrastructure/agent/test_delegate_tool_properties.py`
    - **验证: 需求 6.7, 6.8, 8.1**

  - [x] 7.4 编写属性测试：DelegateToAgentTool 返回值映射
    - **Property 8: DelegateToAgentTool 返回值映射**
    - 生成随机 TaskResult，验证 status=SUCCESS 时返回 content、status=FAILED 时返回包含错误信息的字符串
    - 测试文件：`test/infrastructure/agent/test_delegate_tool_properties.py`
    - **验证: 需求 6.9, 6.10**

  - [x] 7.5 编写属性测试：DelegateToAgentTool 深度超限抛出 DelegationDepthExceededError
    - **Property 9: DelegateToAgentTool 深度超限抛出 DelegationDepthExceededError**
    - 生成随机 current_delegation_depth 和 max_delegation_depth（使 current+1 > max），验证 execute() 抛出 DelegationDepthExceededError 且异常包含正确的 current_depth 和 max_depth
    - 测试文件：`test/infrastructure/agent/test_delegate_tool_properties.py`
    - **验证: 需求 7.2**

- [x] 8. Checkpoint - 确保基础设施层测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 9. 配置项与 DI 容器注册
  - [x] 9.1 在 `config.properties` 中追加 Agent 间通信配置项
    - 新增 `AGENT_MAX_DELEGATION_DEPTH=3`（最大委派深度，<=0 回退为默认值 3）
    - 新增 `AGENT_DELEGATE_TOOL_ENABLED=true`（是否启用委派工具）
    - _需求: 10.1, 10.2, 10.3_

  - [x] 9.2 修改 `application/container_config.py` 注册 AgentRegistryPort 和 DelegateToAgentTool
    - 注册 AgentRegistryPort → AgentRegistryAdapter，Scope 为 SINGLETON
    - 在 `_create_tool_registry()` 中：当 AGENT_DELEGATE_TOOL_ENABLED=true 时，通过容器解析 AgentRegistryPort 和 TaskAgentPort，读取 AGENT_MAX_DELEGATION_DEPTH（<=0 回退为 3），创建 DelegateToAgentTool 并注册到 ToolRegistry
    - 当 AGENT_DELEGATE_TOOL_ENABLED=false 时跳过注册
    - _需求: 9.1, 9.2, 9.3, 9.4, 9.5, 10.2, 10.4_

  - [x] 9.3 编写属性测试：非正 max_delegation_depth 回退默认值
    - **Property 10: 非正 max_delegation_depth 回退默认值**
    - 生成随机非正整数（<= 0），验证系统回退使用默认值 3
    - 测试文件：`test/application/test_agent_delegation_config_properties.py`
    - **验证: 需求 10.2**

  - [x] 9.4 编写单元测试：AGENT_DELEGATE_TOOL_ENABLED=false 时不注册工具
    - 验证配置为 false 时 ToolRegistry 中不包含 "delegate_to_agent" 工具
    - 测试文件：`test/application/test_agent_delegation_config_properties.py`
    - _需求: 10.4_

- [x] 10. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 Hypothesis 库，每个属性测试至少运行 100 次迭代（`@settings(max_examples=100, deadline=5000)`）
- 测试运行命令：`cd epsilon-boot && uv run pytest test/ -v`
- Checkpoint 任务确保增量验证
- 变更范围集中在 domain/agent/、domain/task/、infrastructure/agent/、infrastructure/task/、application/ 五个目录
