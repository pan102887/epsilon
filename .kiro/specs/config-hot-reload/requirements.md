# 需求文档：配置热更新（Config Hot Reload）

## 简介

为项目的配置管理机制增加热更新能力，类似 Spring Boot 的 `@RefreshScope`。通过在配置类上声明 `hot_reload = True`，使配置实例在运行时自动感知配置文件变更并重新加载，对调用方完全透明。对于 `hot_reload = False` 的配置类，行为与现有模式完全一致，零额外开销。

核心机制基于代理模式（Proxy）：工厂函数返回一个代理对象而非真实配置实例，代理对象在属性访问时检查配置文件的修改时间（mtime），若文件已变更则重新实例化底层配置对象。

## 术语表

- **PropertiesBaseSettings**：项目配置基类，继承自 pydantic-settings 的 `BaseSettings`，位于 `common/configuration/configuration_utils.py`。
- **ConfigProxy**：配置代理对象，通过 `__getattr__` 将属性访问透明转发到底层真实配置实例。
- **Config_Source_File**：配置源文件，指 `.env` 和 `config.properties` 两个文件。
- **mtime**：文件的最后修改时间戳（`os.path.getmtime` 返回值），用于判断配置文件是否发生变更。
- **Cached_Instance**：ConfigProxy 内部缓存的真实配置实例，在文件未变更时直接复用。
- **Config_Factory**：配置工厂函数，根据配置类的 `hot_reload` 标志决定返回 ConfigProxy 或普通实例。

## 需求

### 需求 1：配置类热更新声明

**用户故事：** 作为开发者，我希望在配置类上通过 `hot_reload` 类变量声明该配置是否支持热更新，以便框架自动决定是否启用热更新机制。

#### 验收标准

1. THE PropertiesBaseSettings SHALL 提供 `hot_reload` 类变量，默认值为 `False`。
2. WHEN 子类将 `hot_reload` 设置为 `True` 时，THE Config_Factory SHALL 返回 ConfigProxy 代理对象。
3. WHEN 子类将 `hot_reload` 保持为 `False`（默认值）时，THE Config_Factory SHALL 返回普通配置实例。
4. THE `hot_reload` 类变量 SHALL 仅接受布尔值 `True` 或 `False`。

### 需求 2：ConfigProxy 代理对象

**用户故事：** 作为开发者，我希望代理对象对调用方完全透明，使用 `config.host` 这样的属性访问方式不需要任何改动。

#### 验收标准

1. THE ConfigProxy SHALL 通过 `__getattr__` 方法将所有属性访问透明转发到 Cached_Instance。
2. WHEN 调用方访问代理对象的属性时，THE ConfigProxy SHALL 返回与直接访问真实配置实例相同的值。
3. THE ConfigProxy SHALL 支持 `isinstance` 检查，使 `isinstance(proxy, ConfigClass)` 返回 `True`。
4. THE ConfigProxy SHALL 支持 `repr` 和 `str` 操作，输出与真实配置实例一致的结果。
5. THE ConfigProxy SHALL 禁止对属性进行赋值操作，保持与 `frozen=True` 一致的不可变语义。
6. WHEN 调用方对 ConfigProxy 执行属性赋值时，THE ConfigProxy SHALL 抛出 `AttributeError`。

### 需求 3：基于文件 mtime 的变更检测

**用户故事：** 作为开发者，我希望配置热更新基于文件修改时间检测变更，以便在配置文件被修改后自动加载最新值。

#### 验收标准

1. WHEN 调用方访问 ConfigProxy 的属性时，THE ConfigProxy SHALL 检查所有 Config_Source_File 的 mtime。
2. WHEN 任一 Config_Source_File 的 mtime 与上次记录的 mtime 不同时，THE ConfigProxy SHALL 重新实例化底层配置对象并更新 Cached_Instance。
3. WHEN 所有 Config_Source_File 的 mtime 与上次记录的 mtime 相同时，THE ConfigProxy SHALL 直接返回 Cached_Instance 的属性值，不执行重新实例化。
4. WHEN Config_Source_File 不存在时，THE ConfigProxy SHALL 将该文件的 mtime 视为 0.0，不抛出异常。
5. IF 读取 Config_Source_File 的 mtime 发生 OSError 时，THEN THE ConfigProxy SHALL 将该文件的 mtime 视为 0.0 并记录警告日志。

### 需求 4：线程安全

**用户故事：** 作为开发者，我希望 ConfigProxy 在 FastAPI 异步环境中是线程安全的，以便多个并发请求可以安全地访问配置。

#### 验收标准

1. THE ConfigProxy SHALL 使用 `threading.Lock` 保护 Cached_Instance 的读取和更新操作。
2. WHEN 多个线程同时触发配置刷新时，THE ConfigProxy SHALL 确保仅执行一次配置重新实例化。
3. WHEN 配置未变更时，THE ConfigProxy SHALL 在锁外完成 mtime 检查，仅在检测到变更时获取锁，以减少锁竞争。

### 需求 5：Config_Factory 工厂函数

**用户故事：** 作为开发者，我希望有一个统一的工厂函数来创建配置实例，以便根据 `hot_reload` 标志自动选择返回代理对象或普通实例。

#### 验收标准

1. THE Config_Factory SHALL 接受一个 PropertiesBaseSettings 子类作为参数。
2. WHEN 传入的配置类 `hot_reload` 为 `True` 时，THE Config_Factory SHALL 返回该配置类对应的 ConfigProxy 实例。
3. WHEN 传入的配置类 `hot_reload` 为 `False` 时，THE Config_Factory SHALL 直接返回该配置类的普通实例。
4. THE Config_Factory SHALL 位于 `common/configuration` 模块中，与 PropertiesBaseSettings 同层。

### 需求 6：零开销保证

**用户故事：** 作为开发者，我希望 `hot_reload=False` 的配置类不引入任何额外开销，以便现有配置的性能不受影响。

#### 验收标准

1. WHEN 配置类 `hot_reload` 为 `False` 时，THE Config_Factory SHALL 返回配置类的直接实例，不包装任何代理层。
2. WHEN 配置类 `hot_reload` 为 `False` 时，THE Config_Factory 返回的实例 SHALL 与当前 `ConfigClass()` 直接实例化的行为完全一致。

### 需求 7：现有调用方兼容性

**用户故事：** 作为开发者，我希望启用热更新后不需要修改任何现有的配置使用代码，以便平滑迁移。

#### 验收标准

1. THE ConfigProxy SHALL 支持现有的 `config.field_name` 属性访问模式，调用方代码无需修改。
2. WHEN 模块级单例从 `ConfigClass()` 改为 `Config_Factory(ConfigClass)` 后，THE 所有通过 `from module import config_instance` 导入并使用 `config_instance.field_name` 的调用方 SHALL 无需任何代码变更即可正常工作。
3. THE ConfigProxy SHALL 支持配置对象被用作函数参数传递的场景。

### 需求 8：配置刷新失败处理

**用户故事：** 作为开发者，我希望配置刷新失败时系统能继续使用旧配置运行，以便不会因为配置文件临时异常导致服务中断。

#### 验收标准

1. IF 重新实例化配置对象时发生异常（如配置文件格式错误），THEN THE ConfigProxy SHALL 保留当前 Cached_Instance 不变，继续使用旧配置。
2. IF 重新实例化配置对象失败，THEN THE ConfigProxy SHALL 记录包含异常详情的错误日志。
3. IF 重新实例化配置对象失败，THEN THE ConfigProxy SHALL 更新已记录的 mtime 为当前值，避免在后续每次属性访问时重复尝试失败的刷新。
