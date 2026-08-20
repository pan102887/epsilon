# Requirements Document

## Introduction

增强 DI 容器（`common.container.Container`），使其支持通过名称（name）解析依赖，作为现有按类型（type）解析的补充。当同一个 Port 接口存在多个 Adapter 实现时（例如多个 OpenAI Provider），可以通过名称区分不同的实现。

## Glossary

- **Container**: 轻量级依赖注入容器（`common.container.Container`），负责管理 Port → Adapter 的类型映射和依赖解析
- **Registration**: 单个依赖的注册信息，包含 Provider 回调、Scope 和是否异步的标记
- **Registry**: Container 内部的注册表（`_registry`），存储类型/名称到 Registration 的映射
- **Provider**: 创建依赖实例的工厂回调函数，可以是同步或异步的
- **Scope**: 依赖的生命周期范围，包括 Singleton（单例）和 Transient（瞬态）
- **Registry_Key**: 注册表中用于唯一标识一个依赖的键，可以是纯类型 `type`，也可以是 `(type, name)` 元组
- **Named_Dependency**: 通过 `(type, name)` 组合标识的依赖注册，允许同一类型注册多个不同名称的实现
- **inject**: 模块级快捷函数，封装 `Container.get_dependency()` 供 FastAPI `Depends` 使用

## Requirements

### Requirement 1: 带名称的依赖注册

**User Story:** 作为开发者，我希望在注册依赖时可以指定一个可选的名称，以便同一个 Port 接口可以注册多个不同的 Adapter 实现。

#### Acceptance Criteria

1. WHEN `register()` 被调用且未提供 `name` 参数时，THE Container SHALL 以纯类型作为 Registry_Key 注册依赖，行为与当前实现完全一致
2. WHEN `register()` 被调用且提供了 `name` 参数时，THE Container SHALL 以 `(type, name)` 元组作为 Registry_Key 注册依赖
3. WHEN 同一类型和同一名称的组合被重复注册时，THE Container SHALL 覆盖之前的 Registration
4. WHEN 同一类型分别以不同名称注册时，THE Container SHALL 将每个 `(type, name)` 组合视为独立的 Registration
5. WHEN 同一类型同时存在无名称注册和有名称注册时，THE Container SHALL 将无名称注册和有名称注册视为独立的 Registration，互不影响

### Requirement 2: 带名称的依赖解析

**User Story:** 作为开发者，我希望在解析依赖时可以通过名称指定要获取哪个实现，以便在同一 Port 接口有多个 Adapter 时精确选择。

#### Acceptance Criteria

1. WHEN `resolve()` 被调用且未提供 `name` 参数时，THE Container SHALL 按纯类型查找 Registry，行为与当前实现完全一致
2. WHEN `resolve()` 被调用且提供了 `name` 参数时，THE Container SHALL 按 `(type, name)` 元组查找 Registry 并返回对应实例
3. IF 按 `(type, name)` 查找未找到匹配的 Registration，THEN THE Container SHALL 抛出 DependencyNotRegisteredError
4. WHEN 以 Singleton Scope 注册的 Named_Dependency 被多次 resolve 时，THE Container SHALL 返回同一个缓存实例
5. WHEN 以 Transient Scope 注册的 Named_Dependency 被多次 resolve 时，THE Container SHALL 每次返回新创建的实例
6. WHEN 解析 Named_Dependency 时检测到循环依赖，THE Container SHALL 抛出 CircularDependencyError

### Requirement 3: 带名称的 FastAPI 集成

**User Story:** 作为开发者，我希望 `get_dependency()` 和 `inject()` 支持按名称获取依赖，以便在 FastAPI 路由中通过 `Depends` 注入指定名称的实现。

#### Acceptance Criteria

1. WHEN `get_dependency()` 被调用且未提供 `name` 参数时，THE Container SHALL 返回按纯类型解析的依赖提供函数，行为与当前实现完全一致
2. WHEN `get_dependency()` 被调用且提供了 `name` 参数时，THE Container SHALL 返回按 `(type, name)` 解析的依赖提供函数
3. WHEN `inject()` 被调用且未提供 `name` 参数时，THE inject SHALL 返回按纯类型解析的依赖提供函数，行为与当前实现完全一致
4. WHEN `inject()` 被调用且提供了 `name` 参数时，THE inject SHALL 返回按 `(type, name)` 解析的依赖提供函数

### Requirement 4: 向后兼容性

**User Story:** 作为开发者，我希望现有的所有按类型注册和解析的代码无需任何修改即可继续正常工作。

#### Acceptance Criteria

1. THE Container SHALL 保持 `register(abstract_type, provider, scope)` 的现有调用签名不变，`name` 参数为可选且默认值为 None
2. THE Container SHALL 保持 `resolve(abstract_type)` 的现有调用签名不变，`name` 参数为可选且默认值为 None
3. THE Container SHALL 保持 `get_dependency(abstract_type)` 的现有调用签名不变，`name` 参数为可选且默认值为 None
4. THE inject SHALL 保持 `inject(abstract_type)` 的现有调用签名不变，`name` 参数为可选且默认值为 None
5. THE Container SHALL 保持异步资源生命周期管理（`register_async_resource`、`start`、`stop`、`lifespan`）的行为不变

### Requirement 5: 错误信息质量

**User Story:** 作为开发者，我希望在解析命名依赖失败时获得清晰的错误信息，以便快速定位问题。

#### Acceptance Criteria

1. WHEN 解析一个未注册的 Named_Dependency 失败时，THE DependencyNotRegisteredError SHALL 在错误消息中包含请求的类型名称和依赖名称
2. WHEN 解析一个未注册的 Named_Dependency 失败时，THE DependencyNotRegisteredError SHALL 在错误消息中列出该类型下所有已注册的名称
