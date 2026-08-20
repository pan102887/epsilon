# 需求文档：配置读取领域抽象

## 简介

将当前 `common/configuration/` 中的配置读取功能重构为符合 DDD 六边形架构的设计。在领域层（domain）定义配置读取的 Port 接口（使用 Python Protocol），将具体的 Properties 文件读取实现下沉到基础设施层（infrastructure）作为 Adapter，并通过依赖注入容器管理 Port → Adapter 的绑定。

当前 `ConfigReader` 类直接位于 `common/configuration/` 模块中，被 `configuration_utils.py`（Value 描述符、configuration_properties 装饰器）和多个基础设施配置模块（redis_config、gateway_config 等）直接引用。重构后，领域层仅依赖 Protocol 接口，具体实现由基础设施层提供。

## 术语表

- **ConfigReaderPort**: 领域层定义的配置读取端口接口，使用 Python Protocol 声明，定义配置读取的抽象行为契约
- **PropertiesFileConfigAdapter**: 基础设施层的配置读取适配器，实现 ConfigReaderPort，负责从 Java Properties 格式文件读取配置
- **ConfigurationError**: 配置相关的领域异常，表示配置缺失、类型转换失败等错误
- **Value 描述符**: 字段级别的配置注入工具，类似 Spring @Value 注解，通过 ConfigReaderPort 读取配置值
- **configuration_properties 装饰器**: 类级别的配置绑定工具，类似 Spring @ConfigurationProperties，按前缀批量绑定配置到类字段
- **DI 容器**: 依赖注入容器（`common/container.py` 中的 Container），管理 Port → Adapter 的绑定关系

## 需求

### 需求 1：定义配置读取领域端口接口

**用户故事：** 作为开发者，我希望在领域层定义配置读取的抽象接口，以便领域层不依赖具体的配置文件读取实现。

#### 验收标准

1. THE ConfigReaderPort SHALL 使用 Python Protocol 定义，声明以下方法签名：get（获取字符串配置值）、get_required（获取必需配置值）、get_int（获取整数配置值）、get_float（获取浮点数配置值）、get_bool（获取布尔配置值）、get_list（获取列表配置值）、get_all（获取所有配置项）、get_by_prefix（按前缀获取配置项）、has（检查配置项是否存在）
2. THE ConfigReaderPort SHALL 位于 `domain/configuration/ports.py` 模块中
3. THE ConfigReaderPort SHALL 仅使用 Python 标准库类型作为方法参数和返回值类型，不引用任何基础设施层或第三方库的类型
4. THE ConfigurationError SHALL 作为领域异常定义在 `domain/configuration/exceptions.py` 模块中，供 Port 接口和 Adapter 实现共同使用

### 需求 2：实现基础设施层配置读取适配器

**用户故事：** 作为开发者，我希望将当前的 Properties 文件配置读取实现迁移到基础设施层，以便遵循六边形架构的依赖方向。

#### 验收标准

1. THE PropertiesFileConfigAdapter SHALL 位于 `infrastructure/configuration/` 模块中，实现 ConfigReaderPort 定义的所有方法
2. THE PropertiesFileConfigAdapter SHALL 保留当前 ConfigReader 的所有功能，包括：Java Properties 格式文件解析、环境变量覆盖、自动重新加载、线程安全读写锁、多配置文件单例管理
3. THE PropertiesFileConfigAdapter SHALL 在配置文件不存在时创建空配置而不抛出异常
4. IF 配置文件存在但无法读取（如权限不足、编码错误），THEN THE PropertiesFileConfigAdapter SHALL 抛出 ConfigurationError
5. WHEN 自动重新加载功能开启且配置文件已修改时，THE PropertiesFileConfigAdapter SHALL 在下次读取操作时自动重新加载配置文件内容
6. WHEN 获取配置值时，THE PropertiesFileConfigAdapter SHALL 优先检查环境变量（键名转为大写，点号和连字符替换为下划线），环境变量存在时返回环境变量值

### 需求 3：迁移配置工具模块依赖

**用户故事：** 作为开发者，我希望 Value 描述符和 configuration_properties 装饰器通过 Port 接口读取配置，以便这些工具不直接依赖具体实现。

#### 验收标准

1. THE Value 描述符 SHALL 通过 ConfigReaderPort 接口读取配置值，不直接引用 PropertiesFileConfigAdapter
2. THE configuration_properties 装饰器 SHALL 通过 ConfigReaderPort 接口读取配置值，不直接引用 PropertiesFileConfigAdapter
3. THE init_config_data_source 函数 SHALL 接受 ConfigReaderPort 类型的参数，用于设置全局配置数据源
4. WHEN init_config_data_source 未被调用时，THE Value 描述符和 configuration_properties 装饰器 SHALL 通过 DI 容器解析 ConfigReaderPort 获取配置读取器实例

### 需求 4：注册 Port → Adapter 绑定到 DI 容器

**用户故事：** 作为开发者，我希望通过 DI 容器管理配置读取的 Port → Adapter 绑定，以便在应用启动时自动完成依赖注入。

#### 验收标准

1. WHEN 应用启动时，THE container_config 模块 SHALL 将 ConfigReaderPort 绑定到 PropertiesFileConfigAdapter 实例
2. THE ConfigReaderPort 的 Adapter 绑定 SHALL 注册为 Singleton 作用域，确保全局共享同一个配置读取器实例
3. THE ConfigReaderPort 的 Adapter 绑定 SHALL 在其他 Port → Adapter 绑定之前完成注册，因为其他组件的配置类（如 redis_config、gateway_config）依赖配置读取功能

### 需求 5：保持向后兼容性

**用户故事：** 作为开发者，我希望重构后现有的配置使用方式保持兼容，以便不需要修改大量业务代码。

#### 验收标准

1. THE `common/configuration/__init__.py` SHALL 继续导出 ConfigReader（作为 PropertiesFileConfigAdapter 的别名）、ConfigurationError、init_config_data_source、configuration_properties、Value，确保现有 import 路径不变
2. THE `common/__init__.py` SHALL 继续导出与配置相关的所有公开符号，确保顶层 import 路径不变
3. THE configuration_properties 装饰器 SHALL 保持现有的使用方式不变，现有的 redis_config、gateway_config、server_config、logging_config 等配置类无需修改
4. THE get_config_reader 和 get_config 便捷函数 SHALL 继续可用，返回符合 ConfigReaderPort 接口的实例

### 需求 6：测试迁移与验证

**用户故事：** 作为开发者，我希望现有的配置读取测试迁移到基础设施层测试目录，并验证重构后的功能正确性。

#### 验收标准

1. THE 现有的 `test/infrastructure/configuration/config_reader_test.py` 中的单元测试 SHALL 验证 PropertiesFileConfigAdapter 的所有功能，包括字符串读取、类型转换、前缀查询、自动重新加载、环境变量覆盖和单例管理
2. THE 现有的 `test/infrastructure/configuration/config_reader_property_test.py` 中的属性测试 SHALL 验证 PropertiesFileConfigAdapter 在并发读取场景下的线程安全性
3. FOR ALL 有效的配置键值对，写入配置文件后通过 PropertiesFileConfigAdapter 读取 SHALL 返回与写入值一致的结果（往返一致性）
