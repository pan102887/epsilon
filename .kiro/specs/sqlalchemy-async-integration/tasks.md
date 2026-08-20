# Implementation Plan: SQLAlchemy Async Integration

## Overview

按照项目现有 DDD 六边形架构和 Redis 集成模式，逐步引入 SQLAlchemy 2.0 AsyncIO 作为 MySQL 数据库访问层。每个任务增量构建，从依赖安装、配置类、引擎/会话工厂、领域端口、适配器、健康检查到 DI 容器集成，最终完成端到端接入。

## Tasks

- [x] 1. 安装依赖包
  - 在 `epsilon-boot/` 目录下执行 `uv add "sqlalchemy[asyncio]>=2.0"` 和 `uv add aiomysql`
  - 验证 `pyproject.toml` 的 `[project.dependencies]` 中包含 `sqlalchemy` 和 `aiomysql` 条目
  - _Requirements: 8.1, 8.2, 8.3_

- [x] 2. 创建数据库基础设施模块
  - [x] 2.1 创建 DatabaseConfig 配置类
    - 创建 `src/infrastructure/database/__init__.py`
    - 创建 `src/infrastructure/database/database_config.py`，使用 `@configuration_properties(prefix="db")` 装饰器
    - 定义 host、port、username、password、database、pool_size、max_overflow、pool_recycle、echo、health_check_timeout 字段及默认值
    - 创建模块级 `database_config` 单例实例，与 `redis_config` 模式一致
    - _Requirements: 1.1, 1.2, 1.3_

  - [ ]* 2.2 编写 DatabaseConfig 配置绑定属性测试
    - **Property 1: 配置绑定 round-trip**
    - **Validates: Requirements 1.1**
    - 创建 `test/infrastructure/database/test_database_config_property.py`
    - 使用 Hypothesis 生成随机配置键值对，写入临时 config.properties，验证 DatabaseConfig 读取一致性

  - [ ]* 2.3 编写 DatabaseConfig 默认值单元测试
    - 创建 `test/infrastructure/database/test_database_config.py`
    - 验证所有字段的默认值符合 Requirement 1.2 的规定
    - _Requirements: 1.2_

- [-] 3. 实现引擎与会话工厂
  - [x] 3.1 创建 engine.py 模块
    - 创建 `src/infrastructure/database/engine.py`
    - 实现 `create_db_engine(config: DatabaseConfig) -> AsyncEngine` 函数，构建 `mysql+aiomysql://` 连接字符串并传递 pool_size、max_overflow、pool_recycle、echo 参数
    - 实现 `create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]` 函数，配置 `expire_on_commit=False`
    - 定义模块级变量 `_engine` 和 `_session_factory`，供 container_config.py 的初始化/清理回调使用
    - 实现 `async _init_db()` 回调：创建引擎和会话工厂，通过 `SELECT 1` 验证连通性，失败时抛出异常
    - 实现 `async _cleanup_db()` 回调：调用 `engine.dispose()` 释放连接池
    - 提供 `get_session_factory()` 访问器函数
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4_

  - [ ]* 3.2 编写引擎创建参数正确性属性测试
    - **Property 2: 引擎创建参数正确性**
    - **Validates: Requirements 2.1, 2.2**
    - 创建 `test/infrastructure/database/test_engine_property.py`
    - 使用 Hypothesis 生成随机 DatabaseConfig 参数，mock `create_async_engine`，验证 URL 格式和参数传递

  - [ ]* 3.3 编写初始化失败异常传播属性测试
    - **Property 3: 初始化失败异常传播**
    - **Validates: Requirements 3.3**
    - 在 `test/infrastructure/database/test_engine_property.py` 中追加
    - 生成随机数据库异常，验证 `_init_db` 回调将异常向上传播

  - [ ]* 3.4 编写引擎与会话工厂单元测试
    - 创建 `test/infrastructure/database/test_engine.py`
    - 验证 `expire_on_commit=False` 配置（Requirement 2.3）
    - 验证 `SELECT 1` 连通性验证调用（Requirement 3.2）
    - 验证 `engine.dispose()` 清理调用（Requirement 3.4）
    - _Requirements: 2.3, 3.2, 3.4_

- [x] 4. Checkpoint - 确保基础设施层编译通过
  - Ensure all tests pass, ask the user if questions arise.

- [-] 5. 定义领域端口与实现适配器
  - [x] 5.1 创建 SessionProviderPort 领域端口
    - 创建 `src/domain/database/__init__.py`
    - 创建 `src/domain/database/ports.py`，定义 `SessionProviderPort(Protocol)` 接口
    - 接口包含 `session()` 异步上下文管理器方法，返回 `AsyncIterator[AsyncSession]`
    - _Requirements: 4.1_

  - [x] 5.2 实现 SessionProviderAdapter 适配器
    - 创建 `src/infrastructure/database/session_provider.py`
    - 实现 `SessionProviderAdapter`，持有 `async_sessionmaker` 引用
    - `session()` 方法：进入时创建 AsyncSession，正常退出时 commit，异常退出时 rollback 并重抛异常，最终 close
    - _Requirements: 4.1, 4.2, 4.3_

  - [ ]* 5.3 编写会话正常退出自动提交属性测试
    - **Property 4: 会话正常退出自动提交**
    - **Validates: Requirements 4.1, 4.2**
    - 创建 `test/infrastructure/database/test_session_provider_property.py`
    - mock AsyncSession，验证正常退出时 commit + close 被调用

  - [ ]* 5.4 编写会话异常退出自动回滚属性测试
    - **Property 5: 会话异常退出自动回滚**
    - **Validates: Requirements 4.1, 4.3**
    - 在 `test/infrastructure/database/test_session_provider_property.py` 中追加
    - 生成随机异常，mock AsyncSession，验证 rollback + close + 异常重抛

  - [ ]* 5.5 编写 SessionProviderAdapter 单元测试
    - 创建 `test/infrastructure/database/test_session_provider.py`
    - 测试正常退出和异常退出的具体场景
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 6. 创建 ORM 基类
  - [x] 6.1 创建 Base 声明基类
    - 创建 `src/infrastructure/database/base.py`
    - 定义 `Base(DeclarativeBase)` 基类
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ]* 6.2 编写 ORM 基类单元测试
    - 创建 `test/infrastructure/database/test_base.py`
    - 验证 Base 继承自 DeclarativeBase（Requirement 5.1）
    - 验证支持 Mapped/mapped_column 类型注解风格（Requirement 5.3）
    - _Requirements: 5.1, 5.3_

- [x] 7. 实现 MySQL 健康检查适配器
  - [x] 7.1 创建 MysqlHealthCheckAdapter
    - 创建 `src/infrastructure/health/mysql_health_check_adapter.py`
    - 实现 `HealthCheckPort` 协议，name 固定为 `"mysql"`
    - 通过 `async_sessionmaker` 创建 AsyncSession 执行 `SELECT 1`
    - 使用 `asyncio.wait_for` 包裹超时控制，超时时长从 `database_config.health_check_timeout` 动态读取
    - 捕获 TimeoutError、SQLAlchemy 异常和通用 Exception，返回 DOWN + reason
    - 与 RedisHealthCheckAdapter 模式保持一致
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 7.2 编写健康检查成功返回 UP 属性测试
    - **Property 6: 健康检查成功返回 UP**
    - **Validates: Requirements 6.1, 6.3**
    - 创建 `test/infrastructure/health/test_mysql_health_check_property.py`
    - mock 成功的 SELECT 1，验证返回 `HealthCheckResult(name="mysql", status=UP)`

  - [ ]* 7.3 编写健康检查异常返回 DOWN 属性测试
    - **Property 7: 健康检查异常返回 DOWN 并携带原因**
    - **Validates: Requirements 6.1, 6.4, 6.5**
    - 在 `test/infrastructure/health/test_mysql_health_check_property.py` 中追加
    - 生成随机异常（超时、连接错误、通用异常），验证返回 DOWN + 非空 reason + 不抛出异常

  - [ ]* 7.4 编写 MysqlHealthCheckAdapter 单元测试
    - 创建 `test/infrastructure/health/test_mysql_health_check.py`
    - 测试 SELECT 1 成功、超时、连接异常的具体场景
    - 验证通过 AsyncSession 执行 SELECT 1（Requirement 6.2）
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 8. Checkpoint - 确保所有组件测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. DI 容器集成与接入
  - [x] 9.1 在 container_config.py 中注册数据库异步资源和 Port 绑定
    - 在 `configure_container()` 中，Redis 和 Gateway 之后注册 `register_async_resource("database", _init_db, _cleanup_db)`
    - 注册 `SessionProviderPort` 的 Port → Adapter 绑定
    - 更新 `_create_readiness_aggregator()` 将 `MysqlHealthCheckAdapter` 加入检查列表
    - 添加必要的 import 和模块级变量
    - _Requirements: 3.1, 3.4, 3.5, 4.4, 6.6, 7.1, 7.2, 7.3, 7.4_

  - [x] 9.2 在 config.properties 中添加数据库配置项模板
    - 添加 `db.host`、`db.port`、`db.username`、`db.password`、`db.database` 等配置项
    - _Requirements: 1.1_

- [x] 10. Final checkpoint - 确保所有测试通过且集成完整
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- 所有代码文件须包含中文 docstring，遵循项目 code-documentation 规范
- 所有 UV 命令须在 `epsilon-boot/` 目录下执行
