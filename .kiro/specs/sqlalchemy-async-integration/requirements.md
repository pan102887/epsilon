# Requirements Document

## Introduction

在 infrastructure 层引入 SQLAlchemy 2.0 AsyncIO 作为 MySQL 数据库访问的基础支撑。本功能遵循项目 DDD 六边形架构，在 domain 层通过 Protocol 定义 Repository 端口接口，在 infrastructure 层通过 SQLAlchemy 2.0 AsyncSession 提供适配器实现。数据库连接池的生命周期由现有 DI 容器统一管理，配置项通过 `config.properties` 的 `@configuration_properties` 机制读取。

## Glossary

- **Database_Engine**: SQLAlchemy 2.0 `create_async_engine` 创建的异步数据库引擎实例，管理底层连接池
- **Async_Session_Factory**: SQLAlchemy 2.0 `async_sessionmaker` 创建的异步会话工厂，用于生产 `AsyncSession` 实例
- **AsyncSession**: SQLAlchemy 2.0 异步会话对象，提供数据库 CRUD 操作的工作单元
- **Database_Config**: 数据库连接配置类，通过 `@configuration_properties(prefix="db")` 绑定 `config.properties` 中的数据库配置项
- **Session_Provider**: 提供 AsyncSession 实例的异步上下文管理器或工厂函数，供 Repository Adapter 使用
- **Repository_Port**: 在 domain 层通过 Python Protocol 定义的仓储抽象接口
- **Repository_Adapter**: 在 infrastructure 层基于 SQLAlchemy AsyncSession 实现 Repository_Port 的具体适配器
- **DI_Container**: 项目现有的轻量级依赖注入容器（`common.container.Container`）
- **Health_Check_Adapter**: 数据库健康检查适配器，实现 `HealthCheckPort` 协议，检测 MySQL 连通性
- **Base_Model**: SQLAlchemy 2.0 `DeclarativeBase` 的子类，作为所有 ORM 映射模型的基类

## Requirements

### Requirement 1: 数据库连接配置

**User Story:** 作为开发者，我希望通过 config.properties 配置 MySQL 连接参数，以便在不同环境中灵活切换数据库配置。

#### Acceptance Criteria

1. THE Database_Config SHALL 通过 `@configuration_properties(prefix="db")` 装饰器绑定以下配置项：`db.host`、`db.port`、`db.username`、`db.password`、`db.database`、`db.pool_size`、`db.max_overflow`、`db.pool_recycle`、`db.echo`
2. THE Database_Config SHALL 为每个配置项提供合理的默认值：host 默认 `localhost`、port 默认 `3306`、username 默认 `root`、password 默认空字符串、database 默认空字符串、pool_size 默认 `5`、max_overflow 默认 `10`、pool_recycle 默认 `3600`、echo 默认 `false`
3. THE Database_Config SHALL 支持配置热刷新，与项目现有 `@configuration_properties` 机制保持一致

### Requirement 2: 异步引擎与会话工厂创建

**User Story:** 作为开发者，我希望系统能基于配置自动创建 SQLAlchemy 2.0 异步引擎和会话工厂，以便 Repository 层获取数据库会话。

#### Acceptance Criteria

1. WHEN Database_Config 配置就绪时, THE Database_Engine SHALL 使用 `create_async_engine` 创建异步引擎，连接字符串格式为 `mysql+aiomysql://{username}:{password}@{host}:{port}/{database}`
2. THE Database_Engine SHALL 将 Database_Config 中的 pool_size、max_overflow、pool_recycle、echo 参数传递给 `create_async_engine`
3. WHEN Database_Engine 创建完成后, THE Async_Session_Factory SHALL 使用 `async_sessionmaker` 绑定该引擎，配置 `expire_on_commit=False`
4. THE Async_Session_Factory SHALL 作为模块级单例存在，供 Session_Provider 使用

### Requirement 3: 数据库连接生命周期管理

**User Story:** 作为开发者，我希望数据库连接池的初始化和关闭由 DI 容器统一管理，以便与 FastAPI lifespan 集成。

#### Acceptance Criteria

1. WHEN DI_Container 启动时, THE Database_Engine SHALL 通过 `register_async_resource` 注册异步初始化回调，执行引擎创建和连通性验证
2. WHEN DI_Container 启动时, THE Database_Engine 的初始化回调 SHALL 通过执行 `SELECT 1` 验证数据库连通性
3. IF 数据库连通性验证失败, THEN THE Database_Engine 的初始化回调 SHALL 抛出异常，触发 DI_Container 的 fail-fast 回滚机制
4. WHEN DI_Container 停止时, THE Database_Engine SHALL 通过注册的清理回调调用 `engine.dispose()` 释放所有连接池资源
5. THE Database_Engine 的异步资源注册 SHALL 在 Redis 资源注册之后执行，确保资源初始化顺序正确

### Requirement 4: 异步会话提供机制

**User Story:** 作为开发者，我希望有一个统一的会话提供机制，以便 Repository Adapter 安全地获取和释放数据库会话。

#### Acceptance Criteria

1. THE Session_Provider SHALL 提供异步上下文管理器，在进入时创建 AsyncSession，在退出时自动关闭 AsyncSession
2. WHEN 上下文管理器正常退出时, THE Session_Provider SHALL 自动提交事务
3. IF 上下文管理器内发生异常, THEN THE Session_Provider SHALL 自动回滚事务并重新抛出异常
4. THE Session_Provider SHALL 注册到 DI_Container，供 Repository_Adapter 通过依赖注入获取

### Requirement 5: ORM 基类定义

**User Story:** 作为开发者，我希望有一个统一的 ORM 基类，以便所有数据库模型继承并获得一致的映射行为。

#### Acceptance Criteria

1. THE Base_Model SHALL 继承自 SQLAlchemy 2.0 的 `DeclarativeBase`
2. THE Base_Model SHALL 放置在 infrastructure 层的数据库模块中，作为所有 ORM 映射模型的基类
3. THE Base_Model SHALL 支持 SQLAlchemy 2.0 的 `Mapped` 和 `mapped_column` 类型注解风格

### Requirement 6: 数据库健康检查

**User Story:** 作为开发者，我希望就绪探针能检测 MySQL 连通性，以便 Kubernetes 能感知数据库不可用的情况。

#### Acceptance Criteria

1. THE Health_Check_Adapter SHALL 实现 `HealthCheckPort` 协议，name 固定为 `"mysql"`
2. WHEN 执行健康检查时, THE Health_Check_Adapter SHALL 通过 AsyncSession 执行 `SELECT 1` 验证数据库连通性
3. WHEN `SELECT 1` 在配置的超时时间内成功返回时, THE Health_Check_Adapter SHALL 返回 `HealthCheckResult(name="mysql", status=HealthStatus.UP)`
4. IF `SELECT 1` 超时, THEN THE Health_Check_Adapter SHALL 返回 `HealthCheckResult(name="mysql", status=HealthStatus.DOWN, reason=<超时描述>)`
5. IF 数据库连接异常, THEN THE Health_Check_Adapter SHALL 返回 `HealthCheckResult(name="mysql", status=HealthStatus.DOWN, reason=<异常描述>)`
6. THE Health_Check_Adapter SHALL 被注入到 ReadinessAggregator 的检查列表中

### Requirement 7: DI 容器集成

**User Story:** 作为开发者，我希望 SQLAlchemy 相关组件通过 DI 容器注册和解析，以便与现有架构保持一致。

#### Acceptance Criteria

1. THE DI_Container SHALL 在 `container_config.py` 中注册 Database_Engine 的异步资源生命周期（初始化和清理回调）
2. THE DI_Container SHALL 注册 Session_Provider 的 Port → Adapter 绑定
3. THE DI_Container SHALL 确保 Database_Engine 异步资源在 Session_Provider 注册之前完成初始化
4. WHEN 新增 Repository_Port 时, THE DI_Container SHALL 支持在 `container_config.py` 中添加对应的 Port → Adapter 绑定

### Requirement 8: 依赖包管理

**User Story:** 作为开发者，我希望通过 UV 包管理工具安装 SQLAlchemy 和 MySQL 异步驱动，以便项目能正确使用异步数据库访问。

#### Acceptance Criteria

1. THE 项目 SHALL 通过 `uv add` 命令添加 `sqlalchemy[asyncio]>=2.0` 依赖
2. THE 项目 SHALL 通过 `uv add` 命令添加 `aiomysql` 作为 MySQL 异步驱动依赖
3. THE 项目的 `pyproject.toml` SHALL 在 `[project.dependencies]` 中包含 `sqlalchemy` 和 `aiomysql` 条目
