# Implementation Plan: Agent Delegation Decoupling

## Overview

通过引入领域层 `DelegationPort` 协议和 `DelegationResult` 值对象，将 `DelegateToAgentTool` 对 `TaskAgentPort` 的直接依赖替换为对领域层抽象的依赖，从而在架构层面打断循环依赖链。实现顺序按依赖方向从内到外：领域层值对象 → 领域层端口 → 基础设施层适配器 → 工具重构 → 容器配置更新。

## Tasks

- [x] 1. 新增领域层 DelegationResult 值对象和 DelegationPort 协议
  - [x] 1.1 在 `epsilon-boot/src/domain/agent/value_objects.py` 中新增 `DelegationResult` frozen dataclass
    - 包含 `content: str` 和 `success: bool` 两个字段
    - 添加中文 docstring，说明值对象的用途和字段含义
    - _Requirements: 2.1, 2.2, 2.3_
  - [x] 1.2 编写 DelegationResult 属性测试
    - **Property 1: DelegationResult 构造 round-trip 与不可变性**
    - 测试文件：`epsilon-boot/test/domain/agent/test_delegation_result_properties.py`
    - 使用 hypothesis 生成任意 content 字符串和 success 布尔值，验证构造后字段值一致且 frozen 不可变
    - **Validates: Requirements 2.1, 2.2**
  - [x] 1.3 在 `epsilon-boot/src/domain/agent/ports.py` 中新增 `DelegationPort` Protocol 类
    - 声明 `async def delegate(self, agent_name, task_goal, input_data, delegation_depth, max_delegation_depth) -> DelegationResult` 方法
    - 添加中文 docstring，说明参数含义、返回值语义和异常场景
    - 确保不导入任何 infrastructure/ 或 application/ 模块
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Checkpoint - 确保领域层变更正确
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. 新增基础设施层 DelegationAdapter
  - [x] 3.1 创建 `epsilon-boot/src/infrastructure/agent/delegation_adapter.py`
    - 实现 `DelegationAdapter` 类，满足 `DelegationPort` 协议
    - 构造函数接收 `AgentRegistryPort` 和 `TaskAgentPort` 作为直接依赖（非 lazy factory）
    - `delegate` 方法内部：通过 AgentRegistryPort 查找 Agent → 构造 Task → 调用 TaskAgentPort.execute() → 将 TaskResult 转换为 DelegationResult
    - 未找到 Agent 时抛出 `AgentNotFoundError`
    - `input_data` 为 None 时传递空字典给 Task
    - 添加中文模块级 docstring 和类级 docstring
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  - [x] 3.2 编写 DelegationAdapter 属性测试 - TaskResult 转换
    - **Property 2: DelegationAdapter 正确转换 TaskResult 为 DelegationResult**
    - 测试文件：`epsilon-boot/test/infrastructure/agent/test_delegation_adapter_properties.py`
    - 使用 hypothesis 生成任意 TaskResult（SUCCESS/FAILED 状态），验证转换后 content 一致且 success 与 status 对应
    - **Validates: Requirements 3.3, 7.4**
  - [x] 3.3 编写 DelegationAdapter 属性测试 - 未注册 Agent
    - **Property 3: DelegationAdapter 对未注册 Agent 抛出 AgentNotFoundError**
    - 测试文件：`epsilon-boot/test/infrastructure/agent/test_delegation_adapter_properties.py`（同文件追加）
    - 使用 hypothesis 生成任意未注册的 agent_name，验证抛出 AgentNotFoundError 且 agent_name 属性正确
    - **Validates: Requirements 3.4**

- [x] 4. 重构 DelegateToAgentTool 依赖 DelegationPort
  - [x] 4.1 修改 `epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py`
    - 构造函数：将 `task_agent` 参数替换为 `delegation: DelegationPort`
    - 移除 `_task_agent_or_factory`、`_resolved_task_agent` 字段和 `_get_task_agent()` 方法
    - 移除对 `TaskAgentPort`、`Task`、`TaskStatus` 的导入
    - `execute()` 方法：深度校验后直接调用 `self._delegation.delegate(agent_name, task_goal, input_data, next_depth, self._max_delegation_depth)`
    - 根据 `DelegationResult.success` 返回 content 或错误字符串
    - 保留 `_agent_registry` 用于动态描述生成和深度校验
    - 保持 name、description、parameters 属性对外接口不变
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.5_
  - [x] 4.2 编写 DelegateToAgentTool 属性测试 - 深度校验与结果路由
    - **Property 4: DelegateToAgentTool execute 行为（深度校验 + 成功/失败路由）**
    - 测试文件：`epsilon-boot/test/infrastructure/agent/test_delegate_tool_delegation_properties.py`
    - 使用 hypothesis 生成任意 depth 组合，验证超限时抛出 DelegationDepthExceededError，未超限时根据 DelegationResult.success 正确路由返回值
    - **Validates: Requirements 4.3, 7.1, 7.2, 7.4**
  - [x] 4.3 编写 DelegateToAgentTool 属性测试 - description 包含 Agent 名称
    - **Property 5: DelegateToAgentTool description 包含所有已注册 Agent 名称**
    - 测试文件：`epsilon-boot/test/infrastructure/agent/test_delegate_tool_delegation_properties.py`（同文件追加）
    - 使用 hypothesis 生成任意非空 Agent 名称集合，验证 description 包含每个名称；空集合时验证描述指示无可用 Agent
    - **Validates: Requirements 4.4, 7.5**

- [x] 5. Checkpoint - 确保重构后所有测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. 更新容器配置消除循环依赖
  - [x] 6.1 修改 `epsilon-boot/src/application/container_config.py`
    - 新增 `_create_delegation_adapter` 异步工厂函数，通过容器解析 `AgentRegistryPort` 和 `TaskAgentPort` 构造 `DelegationAdapter`
    - 在 `configure_container()` 中注册 `DelegationPort` → `DelegationAdapter` 绑定（位于 TaskAgentPort 之后、ToolRegistry 之前）
    - 修改 `_create_tool_registry` 中 DelegateToAgentTool 的注册逻辑：解析 `DelegationPort` 实例传递给构造函数，移除 lambda 延迟解析 TaskAgentPort 的逻辑
    - 导入 `DelegationPort` 类型
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4_

- [x] 7. Final checkpoint - 确保所有测试通过且依赖方向正确
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- 所有代码须包含中文 docstring，遵循项目现有风格
- 测试使用 pytest + pytest-asyncio + hypothesis，通过 `uv run pytest` 执行
