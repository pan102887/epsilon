# 实施计划：工具作用域与权限校验

## 概述

为 ToolRegistry 增加工具作用域（Scoped View）和执行层权限校验功能。实施按"领域层异常与值对象 → ToolRegistry 扩展 → ScopedToolRegistry → AgentConfig 扩展 → ReActAgentAdapter 权限校验 → Task 扩展与 TaskAgentAdapter 集成"的顺序递进，每步构建在前一步基础上，确保增量可验证。

## Tasks

- [x] 1. 新增 ToolPermissionDeniedError 异常
  - [x] 1.1 在 `domain/agent/exceptions.py` 中新增 `ToolPermissionDeniedError` 类
    - 继承自 `ToolExecutionError`，错误码 60004
    - 构造函数接收 `tool_name: str` 和 `allowed_tools: frozenset[str]`
    - `message` 包含被拒绝的工具名称和允许的工具列表信息
    - `allowed_tools` 属性保存传入的 frozenset
    - 添加中文 docstring
    - _需求: 3.1, 3.2, 3.3, 3.4_

  - [x] 1.2 编写属性测试：ToolPermissionDeniedError 构造完整性
    - **Property 4: ToolPermissionDeniedError 构造完整性**
    - 生成随机 tool_name 和 allowed_tools frozenset，验证 code=60004、tool_name 和 allowed_tools 属性正确、message 包含所有工具名称
    - 测试文件：`test/domain/agent/test_tool_scope_properties.py`
    - **验证: 需求 3.2, 3.3, 3.4**

  - [x] 1.3 编写单元测试：ToolPermissionDeniedError 继承关系和边界条件
    - 验证 isinstance(error, ToolExecutionError) 为 True
    - 验证 code=60004
    - 验证 allowed_tools 为空 frozenset 时 message 包含 "(空)"
    - 测试文件：`test/domain/agent/test_tool_scope_unit.py`
    - _需求: 3.1, 3.2, 3.4_

- [x] 2. 扩展 ToolRegistry.get_schemas() 支持按名称子集过滤
  - [x] 2.1 修改 `domain/agent/tools.py` 中 `ToolRegistry.get_schemas()` 方法
    - 新增可选参数 `tool_names: set[str] | None = None`
    - None 时返回全量 schema（向后兼容）
    - 非空 set 时按名称过滤，未注册名称静默忽略
    - 空 set 时返回空列表
    - 更新中文 docstring
    - _需求: 1.1, 1.2, 1.3, 1.4_

  - [x] 2.2 编写属性测试：get_schemas 按名称子集过滤
    - **Property 1: get_schemas 按名称子集过滤**
    - 生成随机 ToolRegistry 和 tool_names 参数，验证 None 返回全量、set 返回过滤结果、空 set 返回空列表
    - 测试文件：`test/domain/agent/test_tool_scope_properties.py`
    - **验证: 需求 1.1, 1.2, 1.3, 1.4**

  - [x] 2.3 编写单元测试：get_schemas 边界条件
    - 验证空 set 返回空列表
    - 验证全部未注册名称返回空列表
    - 测试文件：`test/domain/agent/test_tool_scope_unit.py`
    - _需求: 1.3, 1.4_

- [x] 3. 实现 ScopedToolRegistry 和 create_scoped_view 工厂方法
  - [x] 3.1 在 `domain/agent/tools.py` 中新增 `ScopedToolRegistry` 类
    - 持有 `_registry: ToolRegistry` 引用和 `_allowed_names: frozenset[str]`
    - `get_schemas()` 委托底层 ToolRegistry.get_schemas(tool_names=self._allowed_names)
    - `execute(request)` 先校验 request.name 是否在 _allowed_names 中，不在则抛出 ToolPermissionDeniedError，在则委托底层 ToolRegistry.execute(request)
    - 添加中文 docstring
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 3.2 在 `domain/agent/tools.py` 的 `ToolRegistry` 中新增 `create_scoped_view(tool_names: frozenset[str]) -> ScopedToolRegistry` 方法
    - 返回 ScopedToolRegistry 实例
    - 添加中文 docstring
    - _需求: 2.1, 2.7_

  - [x] 3.3 编写属性测试：ScopedToolRegistry get_schemas 作用域隔离与快照语义
    - **Property 2: ScopedToolRegistry get_schemas 作用域隔离与快照语义**
    - 验证 get_schemas() 仅返回作用域内工具 schema；创建后注册新工具不影响已创建视图
    - 测试文件：`test/domain/agent/test_tool_scope_properties.py`
    - **验证: 需求 2.3, 2.7**

  - [x] 3.4 编写属性测试：ScopedToolRegistry execute 权限控制
    - **Property 3: ScopedToolRegistry execute 权限控制**
    - 验证作用域内工具正常执行、作用域外工具抛出 ToolPermissionDeniedError
    - 测试文件：`test/domain/agent/test_tool_scope_properties.py`
    - **验证: 需求 2.4, 2.5**

  - [x] 3.5 编写单元测试：ScopedToolRegistry 创建返回正确类型
    - 验证 create_scoped_view 返回 ScopedToolRegistry 实例
    - 测试文件：`test/domain/agent/test_tool_scope_unit.py`
    - _需求: 2.1_

- [x] 4. Checkpoint - 确保领域层测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 5. 扩展 AgentConfig 新增 allowed_tool_names 字段
  - [x] 5.1 修改 `domain/agent/value_objects.py` 中 `AgentConfig`
    - 新增 `allowed_tool_names: frozenset[str] = field(default=frozenset())` 字段
    - 在 `__post_init__` 中：当 allowed_tool_names 为空且 tool_schemas 非空时，从 tool_schemas 的 `["function"]["name"]` 自动提取
    - 使用 `object.__setattr__` 绕过 frozen 限制设置默认值
    - 更新中文 docstring
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 5.2 编写属性测试：AgentConfig allowed_tool_names 自动提取
    - **Property 5: AgentConfig allowed_tool_names 自动提取**
    - 生成随机 tool_schemas，验证未显式传入时自动提取的 allowed_tool_names 等于 tool_schemas 中所有 function.name 的 frozenset
    - 测试文件：`test/domain/agent/test_tool_scope_properties.py`
    - **验证: 需求 4.2, 4.3**

  - [x] 5.3 编写属性测试：AgentConfig allowed_tool_names 显式覆盖
    - **Property 6: AgentConfig allowed_tool_names 显式覆盖**
    - 生成随机 tool_schemas 和显式 allowed_tool_names，验证显式传入时使用传入值
    - 测试文件：`test/domain/agent/test_tool_scope_properties.py`
    - **验证: 需求 4.4, 4.5**

- [x] 6. ReActAgentAdapter 执行前权限校验
  - [x] 6.1 修改 `infrastructure/agent/react_agent_adapter.py` 的 `run()` 方法
    - 在工具执行循环中，执行 `self._tool_registry.execute(tool_call)` 之前校验 `tool_call.name in config.allowed_tool_names`
    - 不在允许集合内时：构造 ToolPermissionDeniedError，记录 WARNING 日志，将 str(error) 作为 ToolMessage content 追加到上下文
    - 在允许集合内时：正常执行工具
    - 导入 ToolPermissionDeniedError
    - _需求: 5.1, 5.2, 5.3, 5.5_

  - [x] 6.2 修改 `infrastructure/agent/react_agent_adapter.py` 的 `run_streaming()` 方法
    - 应用与 run() 相同的权限校验逻辑
    - _需求: 5.4_

  - [x] 6.3 编写属性测试：ReActAgentAdapter 权限校验
    - **Property 7: ReActAgentAdapter 权限校验**
    - Mock LLM 返回 tool_calls，验证允许的工具被执行、未允许的工具不被执行且上下文中追加包含错误信息的 ToolMessage
    - 测试文件：`test/infrastructure/agent/test_react_agent_permission_properties.py`
    - **验证: 需求 5.1, 5.2, 5.3, 5.4**

  - [x] 6.4 编写单元测试：权限拒绝记录 WARNING 日志
    - 验证权限拒绝时记录 WARNING 级别日志，包含被拒绝的工具名称和允许的工具集合
    - 测试文件：`test/infrastructure/agent/test_react_agent_permission_unit.py`
    - _需求: 5.5_

- [x] 7. Checkpoint - 确保权限校验测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 8. Task 值对象扩展和 TaskAgentAdapter 工具子集支持
  - [x] 8.1 修改 `domain/task/value_objects.py` 中 `Task` 值对象
    - 新增 `tool_names: frozenset[str] | None = None` 字段
    - 更新中文 docstring
    - _需求: 8.1, 8.2, 8.3_

  - [x] 8.2 修改 `infrastructure/task/task_agent_adapter.py` 的 `execute()` 方法
    - 当 `task.tool_names is not None` 时，调用 `self._tool_registry.get_schemas(tool_names=task.tool_names)` 获取工具子集 schema
    - 当 `task.tool_names is None` 时，调用 `self._tool_registry.get_schemas()` 获取全量 schema
    - AgentConfig 的 allowed_tool_names 由 `__post_init__` 自动从 tool_schemas 提取
    - _需求: 7.1, 7.2, 7.3_

  - [x] 8.3 编写属性测试：TaskAgentAdapter 工具子集路由
    - **Property 8: TaskAgentAdapter 工具子集路由**
    - 验证 task.tool_names 不为 None 时 AgentConfig.tool_schemas 仅包含子集；为 None 时包含全量
    - 测试文件：`test/infrastructure/task/test_task_agent_tool_subset_properties.py`
    - **验证: 需求 7.1, 7.2**

  - [x] 8.4 编写单元测试：Task 默认 tool_names 为 None 和向后兼容
    - 验证不传 tool_names 时默认为 None
    - 验证 ChatServiceAdapter 构造 AgentConfig 不传 allowed_tool_names 时依赖自动提取
    - 测试文件：`test/domain/task/test_task_value_objects_unit.py` 和 `test/infrastructure/chat/test_chat_service_adapter_unit.py`
    - _需求: 6.1, 6.2, 8.2_

- [x] 9. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 Hypothesis 库，每个属性测试至少运行 100 次迭代（`@settings(max_examples=100, deadline=5000)`）
- 测试运行命令：`cd epsilon-boot && uv run pytest test/ -v`
- Checkpoint 任务确保增量验证
- 变更范围集中在 domain/agent/、domain/task/、infrastructure/agent/、infrastructure/task/ 四个目录
