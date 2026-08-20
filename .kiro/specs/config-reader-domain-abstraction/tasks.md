# 实现计划：配置读取领域抽象

## 概述

将 `common/configuration/` 中的配置读取功能重构为 DDD 六边形架构。按照领域层定义 → 基础设施层迁移 → 工具层改造 → DI 注册 → 向后兼容 → 测试验证的顺序，逐步完成迁移。每个步骤构建在前一步之上，确保增量可验证。

## 任务

- [x] 1. 创建领域层配置模块（Port 接口与领域异常）
  - [x] 1.1 创建 `domain/configuration/exceptions.py`，定义 `ConfigurationError` 领域异常
    - 从 `common/configuration/config_reader.py` 迁移 `ConfigurationError` 类到领域层
    - 包含模块级 docstring 说明职责
    - _需求: 1.4_
  - [x] 1.2 创建 `domain/configuration/ports.py`，定义 `ConfigReaderPort` Protocol 接口
    - 使用 `typing.Protocol` 定义，声明 `get`、`get_required`、`get_int`、`get_float`、`get_bool`、`get_list`、`get_all`、`get_by_prefix`、`has` 方法签名
    - 仅使用 Python 标准库类型（`str`、`int`、`float`、`bool`、`list`、`dict`）
    - 方法签名与现有 `ConfigReader` 完全一致
    - _需求: 1.1, 1.2, 1.3_
  - [x] 1.3 创建 `domain/configuration/__init__.py`，导出 `ConfigReaderPort` 和 `ConfigurationError`
    - _需求: 1.2, 1.4_

- [x] 2. 迁移基础设施层配置适配器
  - [x] 2.1 创建 `infrastructure/configuration/properties_file_config_adapter.py`
    - 将 `common/configuration/config_reader.py` 中的 `ConfigReader` 类复制并重命名为 `PropertiesFileConfigAdapter`
    - 将 `ConfigurationError` 的导入改为从 `domain.configuration.exceptions` 导入
    - 保留全部现有功能：Properties 解析、环境变量覆盖、自动重载、线程安全读写锁、多文件单例管理
    - 保留 `get_config_reader()` 和 `get_config()` 便捷函数（返回类型更新为 `PropertiesFileConfigAdapter`）
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - [x] 2.2 创建 `infrastructure/configuration/__init__.py`，导出 `PropertiesFileConfigAdapter`
    - _需求: 2.1_

- [x] 3. 检查点 - 确保领域层和基础设施层模块可正常导入
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 4. 改造配置工具模块依赖
  - [x] 4.1 修改 `common/configuration/configuration_utils.py`，将依赖从 `ConfigReader` 改为 `ConfigReaderPort`
    - 将 `from .config_reader import ConfigReader` 改为 `from domain.configuration.ports import ConfigReaderPort`
    - `_reader` 类型从 `ConfigReader | None` 改为 `ConfigReaderPort | None`
    - `init_config_data_source` 参数类型改为 `ConfigReaderPort`
    - `_get_config_reader` 的 fallback 逻辑：当 `_reader` 为 None 时，优先通过 DI 容器 `container.resolve(ConfigReaderPort)` 获取实例，容器解析失败时 fallback 到 `PropertiesFileConfigAdapter.get_instance()`
    - _需求: 3.1, 3.2, 3.3, 3.4_

- [x] 5. 更新向后兼容导出与 DI 容器注册
  - [x] 5.1 修改 `common/configuration/config_reader.py`，改为从基础设施层和领域层重新导出的别名模块
    - 导入 `PropertiesFileConfigAdapter` 并设置 `ConfigReader = PropertiesFileConfigAdapter`
    - 导入 `ConfigurationError` 从 `domain.configuration.exceptions`
    - 导入 `get_config_reader` 和 `get_config` 从 `infrastructure.configuration.properties_file_config_adapter`
    - 确保 `from common.configuration.config_reader import ConfigReader` 路径仍可用
    - _需求: 5.1, 5.4_
  - [x] 5.2 修改 `common/configuration/__init__.py`，更新导出以包含新增符号
    - 导出 `ConfigReader`（别名）、`ConfigReaderPort`、`ConfigurationError`、`PropertiesFileConfigAdapter`、`init_config_data_source`、`configuration_properties`、`Value`、`get_config_reader`、`get_config`
    - _需求: 5.1_
  - [x] 5.3 修改 `common/__init__.py`，同步更新顶层导出
    - 继续导出所有配置相关公开符号，确保 `from common import ConfigReader` 等路径不变
    - _需求: 5.2_
  - [x] 5.4 修改 `application/container_config.py`，注册 `ConfigReaderPort → PropertiesFileConfigAdapter` 绑定
    - 在其他 Port 绑定之前注册，使用 `Scope.SINGLETON`
    - 工厂函数使用 `PropertiesFileConfigAdapter.get_instance()`
    - _需求: 4.1, 4.2, 4.3_

- [x] 6. 检查点 - 确保向后兼容性和 DI 注册正确
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 7. 迁移测试并新增属性测试
  - [x] 7.1 更新 `test/infrastructure/configuration/config_reader_test.py` 的导入路径和测试内容
    - 更新导入路径，从 `infrastructure.configuration.properties_file_config_adapter` 导入 `PropertiesFileConfigAdapter`
    - 将测试中的 `ConfigReader` 引用替换为 `PropertiesFileConfigAdapter`
    - 新增 Protocol 一致性测试：验证 `PropertiesFileConfigAdapter` 满足 `ConfigReaderPort`
    - 新增向后兼容导出测试：验证 `from common.configuration import ConfigReader` 等路径可用
    - 新增 DI 容器注册测试：验证 `container.resolve(ConfigReaderPort)` 返回正确实例
    - _需求: 6.1, 5.1, 5.2, 5.3, 4.1_
  - [x] 7.2 编写属性测试：配置读写往返一致性
    - **属性 1：配置读写往返一致性**
    - 使用 Hypothesis 生成随机有效键值对，写入临时 Properties 文件，通过 `PropertiesFileConfigAdapter` 读取验证一致性
    - 标签：`Feature: config-reader-domain-abstraction, Property 1: 配置读写往返一致性`
    - **验证需求: 6.3**
  - [x] 7.3 编写属性测试：环境变量优先级
    - **属性 2：环境变量优先级**
    - 使用 Hypothesis 生成随机键值对，同时设置文件值和环境变量值，验证 `get` 返回环境变量值
    - 标签：`Feature: config-reader-domain-abstraction, Property 2: 环境变量优先级`
    - **验证需求: 2.6**
  - [-] 7.4 编写属性测试：自动重载一致性
    - **属性 3：自动重载一致性**
    - 使用 Hypothesis 生成随机键值对，写入文件后修改，验证下次读取返回新值
    - 标签：`Feature: config-reader-domain-abstraction, Property 3: 自动重载一致性`
    - **验证需求: 2.5**
  - [x] 7.5 更新 `test/infrastructure/configuration/config_reader_property_test.py` 的导入路径
    - 将现有并发属性测试中的 `ConfigReader` 导入改为从 `infrastructure.configuration.properties_file_config_adapter` 导入 `PropertiesFileConfigAdapter`
    - 保持现有并发测试逻辑不变，仅更新类名引用
    - _需求: 6.2_

- [x] 8. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的任务为可选任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 检查点确保增量验证，及时发现问题
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
