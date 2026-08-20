# 实施计划：容器命名依赖（Container Named Dependency）

## 概述

为 DI 容器增加按名称解析依赖的能力。实施按"模型层扩展 → 错误类增强 → 容器核心改造 → FastAPI 集成 → 测试"的顺序递进，每步构建在前一步基础上。先扩展 `RegistryKey` 类型和辅助函数，再修改容器内部数据结构和 API，最后通过属性测试和单元测试验证正确性。

## Tasks

- [x] 1. 扩展数据模型和错误类
  - [x] 1.1 在 `src/common/container_models.py` 中新增 `RegistryKey` 类型别名和 `make_registry_key` 辅助函数
    - 新增 `RegistryKey = type | tuple[type, str]` 类型别名
    - 新增 `make_registry_key(abstract_type: type, name: str | None = None) -> RegistryKey` 函数
    - 添加中文 docstring
    - _需求: 1.1, 1.2_

  - [x] 1.2 增强 `src/common/container_errors.py` 中的 `DependencyNotRegisteredError`
    - `__init__` 新增 keyword-only 参数 `name: str | None = None` 和 `registered_names: list[str] | None = None`
    - 当 `name` 不为 None 时，错误消息格式为：`Type 'X' with name 'y' is not registered. Registered names for 'X': [...]`
    - 当 `name` 为 None 时，保持现有错误消息格式不变
    - 增强 `CircularDependencyError` 支持 `RegistryKey`，命名依赖在链路中显示为 `TypeName(name)` 格式
    - 添加中文 docstring
    - _需求: 5.1, 5.2, 2.3, 2.6_

- [x] 2. 改造 Container 核心注册与解析
  - [x] 2.1 修改 `src/common/container.py` 中 `Container` 的内部数据结构
    - `_registry` 类型从 `dict[type, Registration]` 改为 `dict[RegistryKey, Registration]`
    - `_singletons` 类型从 `dict[type, Any]` 改为 `dict[RegistryKey, Any]`
    - `_resolving` 类型从 `set[type]` 改为 `set[RegistryKey]`
    - 导入 `RegistryKey` 和 `make_registry_key`
    - _需求: 1.1, 1.2, 1.4, 1.5_

  - [x] 2.2 修改 `Container.register()` 方法，新增 keyword-only `name` 参数
    - 签名变为 `register(self, abstract_type, provider, scope=Scope.SINGLETON, *, name: str | None = None)`
    - 使用 `make_registry_key(abstract_type, name)` 构造键
    - 现有无名称调用行为完全不变
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 4.1_

  - [x] 2.3 修改 `Container.resolve()` 方法，新增 keyword-only `name` 参数
    - 签名变为 `resolve(self, abstract_type, *, name: str | None = None)`
    - 使用 `make_registry_key(abstract_type, name)` 构造键
    - 未找到时调用 `_get_registered_names()` 收集该类型下所有已注册名称，传入 `DependencyNotRegisteredError`
    - 循环依赖检测使用 `RegistryKey`
    - Singleton 缓存查找和写入使用 `RegistryKey`
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 4.2_

  - [x] 2.4 新增 `Container._get_registered_names()` 私有方法
    - 遍历 `_registry` 收集指定类型下所有已注册的名称
    - 返回 `list[str]`
    - 添加中文 docstring
    - _需求: 5.2_

  - [x] 2.5 编写属性测试：无名称注册/解析往返（向后兼容）
    - **Property 1: 无名称注册/解析往返**
    - 生成随机 Protocol 类和 Provider，以无名称方式注册后解析，验证返回实例正确且注册表键为纯类型
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 1.1, 2.1, 4.1, 4.2**

  - [x] 2.6 编写属性测试：命名注册/解析往返
    - **Property 2: 命名注册/解析往返**
    - 生成随机类型、名称字符串和 Provider，以 `(type, name)` 方式注册后解析，验证返回实例正确
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 1.2, 2.2**

  - [x] 2.7 编写属性测试：注册独立性
    - **Property 3: 注册独立性**
    - 生成随机类型和多个不同名称，分别注册不同 Provider，验证各自解析结果独立，互不影响
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 1.4, 1.5**

  - [x] 2.8 编写属性测试：重复命名注册覆盖
    - **Property 4: 重复命名注册覆盖**
    - 生成随机类型和名称，以相同 `(type, name)` 注册两次不同 Provider，验证解析返回第二个 Provider 的实例
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 1.3**

  - [x] 2.9 编写属性测试：命名依赖的 Scope 行为
    - **Property 5: 命名依赖的 Scope 行为**
    - 生成随机类型、名称和 Scope，注册后多次解析，验证 Singleton 返回同一实例、Transient 返回不同实例
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 2.4, 2.5**

- [x] 3. Checkpoint - 确保核心注册与解析测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 4. FastAPI 集成层和错误信息测试
  - [x] 4.1 修改 `Container.get_dependency()` 方法，新增 keyword-only `name` 参数
    - 签名变为 `get_dependency(self, abstract_type, *, name: str | None = None)`
    - 内部闭包调用 `self.resolve(abstract_type, name=name)`
    - _需求: 3.1, 3.2, 4.3_

  - [x] 4.2 修改模块级 `inject()` 函数，新增 keyword-only `name` 参数
    - 签名变为 `inject(abstract_type, *, name: str | None = None)`
    - 内部调用 `container.get_dependency(abstract_type, name=name)`
    - _需求: 3.3, 3.4, 4.4_

  - [x] 4.3 编写属性测试：未注册命名依赖的错误信息质量
    - **Property 6: 未注册命名依赖的错误信息质量**
    - 生成随机类型和一组已注册名称，尝试解析未注册名称，验证错误消息包含请求的类型名称、依赖名称和所有已注册名称列表
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 2.3, 5.1, 5.2**

  - [x] 4.4 编写属性测试：FastAPI 集成层正确传递名称
    - **Property 7: FastAPI 集成层正确传递名称**
    - 生成随机类型和可选名称，验证 `get_dependency(type, name=name)` 返回的异步函数调用后等价于 `resolve(type, name=name)` 的结果；`inject(type, name=name)` 等价于 `container.get_dependency(type, name=name)`
    - 测试文件：`test/common/test_container_named.py`
    - **验证: 需求 3.1, 3.2, 3.3, 3.4, 4.3, 4.4**

  - [x] 4.5 编写单元测试：边界条件和集成验证
    - 测试文件：`test/common/test_container_named.py`
    - 循环依赖检测对命名依赖生效（需求 2.6）
    - 异步 Provider 与命名依赖配合正常工作（边界情况）
    - 空字符串名称作为合法名称处理（边界情况）
    - 异步资源生命周期管理不受命名依赖影响（需求 4.5）
    - _需求: 2.6, 4.5_

- [x] 5. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 `hypothesis` 框架，每个属性最少运行 100 次迭代
- 每个测试用例创建新的 `Container()` 实例，避免测试间状态污染
- 测试运行命令：`cd epsilon-boot && uv run pytest test/common/test_container_named.py -v`
- Checkpoint 任务确保增量验证
