# 实现计划：配置热更新（Config Hot Reload）

## 概述

基于代理模式为项目配置管理增加热更新能力。实现分为四个阶段：基类扩展与 ConfigProxy 核心实现、工厂函数与模块导出、现有配置类迁移、集成验证。每个阶段包含对应的属性测试和单元测试子任务。

## 任务

- [x] 1. 扩展 PropertiesBaseSettings 基类，新增 hot_reload 类变量
  - 在 `epsilon-boot/src/common/configuration/configuration_utils.py` 的 `PropertiesBaseSettings` 类中新增 `hot_reload: ClassVar[bool] = False` 类变量
  - 导入 `ClassVar` 类型（从 `typing` 模块）
  - 更新类的 docstring，说明 `hot_reload` 的作用
  - _需求: 1.1, 1.4_

- [x] 2. 实现 ConfigProxy 代理类
  - [x] 2.1 创建 `epsilon-boot/src/common/configuration/config_proxy.py` 模块，实现 `ConfigProxy` 类
    - 实现 `__init__` 方法：接受 `config_class` 参数，使用 `object.__setattr__` 设置内部属性（`_config_class`、`_instance`、`_lock`、`_mtimes`、`_source_files`）
    - 实现 `_get_current_mtimes` 方法：获取所有配置源文件的当前 mtime，文件不存在或 OSError 时返回 `0.0` 并记录警告日志
    - 实现 `_mtimes_changed` 方法：比较当前 mtime 与缓存的 mtime
    - 实现 `_refresh` 方法：双重检查锁定模式，锁内重新实例化配置对象，失败时保留旧实例并更新 mtime、记录错误日志
    - 实现 `__getattr__` 方法：检查 mtime 变更，必要时刷新，然后转发属性访问到 `_instance`
    - 实现 `__setattr__` 方法：对非内部属性抛出 `AttributeError`
    - 实现 `__repr__` 和 `__str__` 方法：委托给 `_instance`
    - 通过 `__class__` 属性伪装实现 `isinstance` 检查支持
    - 使用 `Generic[T]` 泛型支持类型提示
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 8.1, 8.2, 8.3_

  - [x] 2.2 编写属性测试：工厂函数路由正确性
    - **Property 1: 工厂函数路由正确性**
    - **验证: 需求 1.2, 1.3, 5.2, 5.3, 6.1**
    - 在 `epsilon-boot/test/common/configuration/test_config_proxy.py` 中编写
    - 使用 hypothesis 生成随机 `hot_reload` 布尔值，验证 `create_config` 返回类型正确

  - [x] 2.3 编写属性测试：属性访问透明转发
    - **Property 2: 属性访问透明转发**
    - **验证: 需求 2.1, 2.2, 6.2, 7.1**
    - 使用 hypothesis 生成随机配置字段值，验证代理访问与直接访问结果一致

  - [x] 2.4 编写属性测试：代理身份等价性
    - **Property 3: 代理身份等价性**
    - **验证: 需求 2.3, 2.4**
    - 验证 `isinstance(proxy, ConfigClass)` 返回 `True`，`repr` 和 `str` 与直接实例一致

  - [x] 2.5 编写属性测试：不可变语义
    - **Property 4: 不可变语义**
    - **验证: 需求 2.5, 2.6**
    - 使用 hypothesis 生成随机属性名和值，验证赋值操作抛出 `AttributeError`

- [x] 3. 检查点 - 确保 ConfigProxy 核心功能正确
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 4. 实现 create_config 工厂函数并更新模块导出
  - [x] 4.1 在 `epsilon-boot/src/common/configuration/config_proxy.py` 中实现 `create_config` 工厂函数
    - 接受 `type[T]` 参数（`PropertiesBaseSettings` 子类）
    - `hot_reload=True` 时返回 `ConfigProxy[T]`，`hot_reload=False` 时返回 `T()` 直接实例
    - 类型标注返回 `T`，保证调用方获得正确的类型提示
    - _需求: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2_

  - [x] 4.2 更新 `epsilon-boot/src/common/configuration/__init__.py` 模块导出
    - 新增导出 `ConfigProxy` 和 `create_config`
    - 更新 `__all__` 列表
    - _需求: 5.4_

  - [x] 4.3 编写属性测试：基于 mtime 的配置刷新
    - **Property 5: 基于 mtime 的配置刷新**
    - **验证: 需求 3.2, 3.3**
    - 使用 `tmp_path` 创建临时配置文件，修改文件后验证代理返回新值；未修改时验证返回缓存实例

  - [x] 4.4 编写属性测试：刷新失败保留旧配置
    - **Property 6: 刷新失败保留旧配置**
    - **验证: 需求 8.1**
    - 将配置文件修改为非法内容，验证代理继续返回旧配置值

  - [x] 4.5 编写属性测试：失败后 mtime 更新防止重复刷新
    - **Property 7: 失败后 mtime 更新防止重复刷新**
    - **验证: 需求 8.3**
    - 触发失败刷新后，验证后续访问不再尝试重新实例化

  - [x] 4.6 编写单元测试：边界情况和并发安全
    - 测试配置文件不存在时 ConfigProxy 正常工作（需求 3.4）
    - 测试 mtime 读取 OSError 时记录警告日志（需求 3.5）
    - 测试多线程并发刷新仅执行一次实例化（需求 4.2）
    - 测试刷新失败时记录错误日志（需求 8.2）
    - 测试 `isinstance(proxy, ConfigClass)` 返回 `True`（需求 2.3）

- [x] 5. 检查点 - 确保工厂函数和所有属性测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 6. 迁移现有配置类使用 create_config 工厂函数
  - [x] 6.1 迁移 RedisConfig（`epsilon-boot/src/infrastructure/redis/redis_config.py`）
    - 添加 `hot_reload: ClassVar[bool] = True`
    - 将 `redis_config = RedisConfig()` 改为 `redis_config = create_config(RedisConfig)`
    - 导入 `ClassVar` 和 `create_config`
    - _需求: 7.1, 7.2_

  - [x] 6.2 迁移 ModelConfig（`epsilon-boot/src/infrastructure/model_access/model_config.py`）
    - 添加 `hot_reload: ClassVar[bool] = True`
    - 将模块级实例改为使用 `create_config(ModelConfig)`
    - _需求: 7.1, 7.2_

  - [x] 6.3 迁移 GatewayConfig（`epsilon-boot/src/infrastructure/gateway/gateway_config.py`）
    - 添加 `hot_reload: ClassVar[bool] = True`
    - 将模块级实例改为使用 `create_config(GatewayConfig)`
    - _需求: 7.1, 7.2_

  - [x] 6.4 迁移 DatabaseConfig（`epsilon-boot/src/infrastructure/database/database_config.py`）
    - 添加 `hot_reload: ClassVar[bool] = True`
    - 将模块级实例改为使用 `create_config(DatabaseConfig)`
    - _需求: 7.1, 7.2_

- [x] 7. 最终检查点 - 确保所有测试通过且现有功能不受影响
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的任务为可选任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 检查点任务确保增量验证，及时发现问题
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 所有 `uv` 命令需在 `epsilon-boot/` 目录下执行
- 运行测试命令：`uv run pytest test/common/configuration/test_config_proxy.py -v`
