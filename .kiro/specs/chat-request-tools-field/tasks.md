# Implementation Plan: ChatRequest tools 字段

## Overview

为 `ChatRequest` 值对象新增 `tools: list[dict[str, Any]] | None = None` 字段，并修改 `OpenAICompatibleAdapter._build_params()` 在 tools 非空时将其传递给 OpenAI SDK。变更涉及两个文件，采用自底向上的顺序：先修改领域层值对象 → 再修改基础设施层适配器 → 编写属性测试 → 补充单元测试。

## Tasks

- [x] 1. 修改 ChatRequest 值对象
  - [x] 1.1 在 `domain/model_access/value_objects.py` 的 `ChatRequest` 中新增 `tools` 字段
    - 类型为 `list[dict[str, Any]] | None`，默认值为 `None`
    - 字段位置在 `thinking` 之后、`extra_params` 之前
    - 更新类的 docstring，在 Attributes 部分添加 `tools` 字段说明
    - `__post_init__` 无需新增校验逻辑
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 2. 修改 OpenAICompatibleAdapter._build_params()
  - [x] 2.1 在 `infrastructure/model_access/openai_compatible_adapter.py` 的 `_build_params` 方法中新增 tools 传递逻辑
    - 在 `extra_params` 处理之前，添加 `if request.tools: params["tools"] = request.tools`
    - 更新方法的 docstring，说明 tools 参数的传递条件
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 3. 编写属性测试
  - [x] 3.1 创建 `test/domain/model_access/test_chat_request_tools_property.py`，编写属性测试：Tools field preservation（Property 1）
    - 生成随机 tools 值（None、空列表、非空 schema 列表），构造 ChatRequest，验证 `request.tools` 等于输入值
    - 标签：`# Feature: chat-request-tools-field, Property 1: Tools field preservation`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4**

  - [x] 3.2 编写属性测试：_build_params includes tools if and only if truthy（Property 2）
    - 生成随机 tools 值，构造 ChatRequest，调用 `_build_params`，验证：truthy tools 时 params 含 `"tools"` 键且值一致，falsy tools 时 params 不含 `"tools"` 键
    - 需要构造 `OpenAICompatibleAdapter` 实例（mock `ProviderConfig`）
    - 标签：`# Feature: chat-request-tools-field, Property 2: _build_params includes tools if and only if truthy`
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [x] 3.3 编写属性测试：Frozen immutability of tools field（Property 3）
    - 生成随机 tools 值，构造 ChatRequest，尝试赋值 `request.tools = ...`，验证抛出 `FrozenInstanceError`
    - 标签：`# Feature: chat-request-tools-field, Property 3: Frozen immutability of tools field`
    - **Validates: Requirements 1.5**

  - [x] 3.4 编写属性测试：ToolRegistry.get_schemas() to ChatRequest.tools compatibility（Property 4）
    - 生成随机数量的 mock Tool 实例注册到 ToolRegistry，调用 `get_schemas()` 传入 ChatRequest.tools，验证 `request.tools` 等于 `get_schemas()` 输出
    - 标签：`# Feature: chat-request-tools-field, Property 4: ToolRegistry.get_schemas() to ChatRequest.tools compatibility`
    - **Validates: Requirements 3.1, 3.2**

- [x] 4. 编写单元测试
  - [x] 4.1 创建 `test/domain/model_access/test_chat_request_tools_unit.py`，编写值对象测试
    - 验证不传 tools 时 `request.tools is None`
    - 验证 `tools=[]` 时 `request.tools == []`
    - 验证传入具体 schema 列表时值一致
    - 验证向后兼容：不传 tools 的 ChatRequest 行为与变更前一致（messages、model 等字段不受影响）
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 4.2 编写 _build_params 单元测试
    - 验证 tools=None 时返回的 dict 无 `"tools"` 键
    - 验证 tools=[] 时返回的 dict 无 `"tools"` 键
    - 验证 tools 非空时返回的 dict 含 `"tools"` 键且值正确
    - 需要 mock `ProviderConfig` 构造 `OpenAICompatibleAdapter`
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 5. Checkpoint - 运行所有测试确保通过
  - 运行 `uv run pytest test/domain/model_access/test_chat_request_tools_property.py test/domain/model_access/test_chat_request_tools_unit.py -v` 确保所有测试通过

## Notes

- 本次变更仅涉及两个源文件的小幅修改，无需新增模块或依赖
- 属性测试使用 `@settings(max_examples=100, deadline=2000)` 配置
- 属性测试函数需包含注释标签：`# Feature: chat-request-tools-field, Property {N}: {title}`
- Property 2 测试需要 mock ProviderConfig，可参考项目中已有的 adapter 测试模式
- 所有 docstring 使用中文，与项目整体风格保持一致
