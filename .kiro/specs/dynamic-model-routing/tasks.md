# 实现计划：动态模型路由

## 概述

将 `ChatServiceAdapter` 的依赖从固定的 `ModelAccessPort` 实例改为 `ModelRegistryPort` 实例，实现请求级别的动态模型路由。变更集中在两个文件：`chat_service_adapter.py`（构造函数 + 内部路由逻辑）和 `container_config.py`（DI 注入配置）。

## 任务

- [x] 1. 修改 ChatServiceAdapter 构造函数和新增 `_resolve_model_access` 方法
  - [x] 1.1 修改 `ChatServiceAdapter.__init__` 签名，将 `model_access: ModelAccessPort` 参数替换为 `model_registry: ModelRegistryPort`，更新实例属性 `self._model_registry`，移除 `self._model_access`
    - 文件：`epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
    - 更新 import 语句，新增 `ModelRegistryPort` 导入
    - 更新类 docstring 中的 `_model_access` 属性说明为 `_model_registry`
    - _需求：1.1, 1.2, 1.3_

  - [x] 1.2 新增私有方法 `_resolve_model_access(self, model: str | None) -> tuple[ModelAccessPort, str]`
    - 当 `model` 不为 None 时，直接调用 `self._model_registry.get_adapter_for_model(model)` 返回 `(adapter, model)`
    - 当 `model` 为 None 时，先调用 `self._model_registry.get_default_model()` 获取默认模型名称，再获取适配器
    - 异常由 `ModelRegistryPort` 实现（`ProviderRegistry`）抛出，直接向上传播
    - _需求：2.1, 2.2, 2.3_

- [x] 2. 修改 ChatServiceAdapter 的对话方法使用动态路由
  - [x] 2.1 修改 `chat()` 方法，在调用 LLM 前通过 `_resolve_model_access(request.model)` 获取适配器
    - 替换 `self._model_access.chat(chat_request)` 为使用动态获取的 `model_access` 实例
    - 非 Agent Loop 路径和 Agent Loop 路径均需使用动态适配器
    - _需求：3.1, 3.2, 3.3_

  - [x] 2.2 修改 `stream_chat()` 方法，在调用 LLM 前通过 `_resolve_model_access(request.model)` 获取适配器
    - 替换 `self._model_access.stream(chat_request)` 为使用动态获取的 `model_access` 实例
    - 非 Agent Loop 路径和 Agent Loop 路径均需使用动态适配器
    - _需求：4.1, 4.2_

  - [x] 2.3 修改 `_run_agent_loop()` 和 `_run_agent_loop_streaming()` 方法，接收 `model_access: ModelAccessPort` 参数
    - 将方法签名新增 `model_access` 参数，替换内部 `self._model_access` 引用
    - 确保所有轮次使用同一个 `model_access` 实例
    - _需求：5.1, 5.2_

  - [x] 2.4 编写属性测试：Property 1 - 指定模型的动态路由
    - **Property 1: 指定模型的动态路由**
    - 生成随机模型名称，构建 mock `ModelRegistryPort`，验证 `_resolve_model_access` 使用指定模型调用 `get_adapter_for_model`
    - 文件：`epsilon-boot/test/infrastructure/chat/test_dynamic_model_routing_properties.py`
    - **验证：需求 1.2, 2.1, 3.1, 4.1**

  - [x] 2.5 编写属性测试：Property 2 - 未指定模型时回退到默认模型
    - **Property 2: 未指定模型时回退到默认模型**
    - 生成 `model=None` 的请求，验证先调用 `get_default_model()` 再调用 `get_adapter_for_model()`
    - 文件：`epsilon-boot/test/infrastructure/chat/test_dynamic_model_routing_properties.py`
    - **验证：需求 2.2, 3.2, 4.2**

  - [x] 2.6 编写属性测试：Property 3 - 未注册模型的错误传播
    - **Property 3: 未注册模型的错误传播**
    - 生成不在注册中心的随机模型名称，验证抛出 `ModelAccessError`
    - 文件：`epsilon-boot/test/infrastructure/chat/test_dynamic_model_routing_properties.py`
    - **验证：需求 2.3**

- [x] 3. 检查点 - 确保 ChatServiceAdapter 变更正确
  - 确保所有测试通过，ask the user if questions arise.

- [x] 4. 修改 DI 容器配置
  - [x] 4.1 修改 `_create_chat_service()` 工厂函数，将 `ModelAccessPort` 解析替换为 `ModelRegistryPort` 解析
    - 文件：`epsilon-boot/src/application/container_config.py`
    - 将 `model_access = await container.resolve(ModelAccessPort)` 改为 `model_registry = await container.resolve(ModelRegistryPort)`
    - 将 `ChatServiceAdapter` 构造参数 `model_access=model_access` 改为 `model_registry=model_registry`
    - 保留 `container.register(ModelAccessPort, ...)` 注册不变
    - _需求：6.1, 6.2, 6.3_

  - [x] 4.2 编写单元测试验证容器配置变更
    - 验证 `_create_chat_service()` 注入 `ModelRegistryPort` 而非 `ModelAccessPort`
    - 验证 `ModelAccessPort` 注册仍然存在
    - 文件：`epsilon-boot/test/application/test_container_config.py`
    - _需求：6.1, 6.2, 6.3_

- [x] 5. 编写剩余属性测试
  - [x] 5.1 编写属性测试：Property 4 - 响应中的模型名称准确性
    - **Property 4: 响应中的模型名称准确性**
    - 生成随机模型名称和对应的 mock `LLMResponse`，验证 `ChatResponseVO.model` 与 `LLMResponse.model` 一致
    - 文件：`epsilon-boot/test/infrastructure/chat/test_dynamic_model_routing_properties.py`
    - **验证：需求 3.3**

  - [x] 5.2 编写属性测试：Property 5 - Agent Loop 中适配器一致性
    - **Property 5: Agent Loop 中适配器一致性**
    - 生成多轮工具调用场景，验证所有轮次使用同一个由 `_resolve_model_access` 返回的适配器实例
    - 文件：`epsilon-boot/test/infrastructure/chat/test_dynamic_model_routing_properties.py`
    - **验证：需求 5.1, 5.2**

  - [x] 5.3 编写属性测试：Property 6 - Round-Robin 负载均衡保持
    - **Property 6: Round-Robin 负载均衡保持**
    - 注册多个提供商到同一模型，通过 `_resolve_model_access` 连续请求验证轮询分布
    - 文件：`epsilon-boot/test/infrastructure/chat/test_dynamic_model_routing_properties.py`
    - **验证：需求 7.1**

- [x] 6. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 Hypothesis 库，每个属性测试至少运行 100 次迭代
- 测试运行命令：在 `epsilon-boot/` 目录下执行 `uv run pytest`
- 变更范围小且集中：仅涉及 `chat_service_adapter.py` 和 `container_config.py` 两个文件的修改
