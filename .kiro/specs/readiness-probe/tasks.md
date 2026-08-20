# Implementation Plan: Readiness Probe（就绪探针）

## Overview

基于 DDD 分层架构，为 epsilon-boot 应用新增 Readiness Probe 能力。实现顺序遵循依赖方向：先 Domain 层（值对象、Port、聚合器），再 Infrastructure 层（Redis 适配器），最后 Application 层（路由、DI 注册）。所有代码 docstring 使用中文。

## Tasks

- [x] 1. 创建 Domain 层健康检查值对象和 Port 接口
  - [x] 1.1 创建 `src/domain/health/__init__.py` 和 `src/domain/health/value_objects.py`
    - 新建 `src/domain/health/` 目录
    - 定义 `HealthStatus` 枚举（UP / DOWN）
    - 定义 `HealthCheckResult` 冻结数据类（name, status, reason），包含 `to_dict()` 方法
    - 定义 `ReadinessResult` 冻结数据类（status, checks），包含 `to_dict()` 方法
    - _Requirements: 1.4, 1.5, 2.3_

  - [x] 1.2 创建 `src/domain/health/ports.py`
    - 使用 Python Protocol 定义 `HealthCheckPort`，声明异步 `check()` 方法，返回 `HealthCheckResult`
    - 不引用任何基础设施层模块
    - _Requirements: 2.1, 2.2_

  - [x] 1.3 编写值对象属性测试 `test/domain/health/test_value_objects_property.py`
    - **Property 3: HealthCheckResult 序列化包含必要字段**
    - **Property 4: ReadinessResult 序列化往返一致性**
    - 创建 `test/domain/health/__init__.py`
    - 使用 Hypothesis 生成随机 HealthCheckResult 和 ReadinessResult
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5**

- [x] 2. 实现 Domain 层就绪状态聚合器
  - [x] 2.1 创建 `src/domain/health/aggregator.py`
    - 实现 `ReadinessAggregator` 类，接收 `list[HealthCheckPort]`
    - 实现 `check_readiness()` 异步方法，依次执行每个 check 并聚合结果
    - 全部 UP → 整体 UP，任一 DOWN → 整体 DOWN
    - 无论整体状态如何，返回所有逐项检查结果
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 2.2 编写聚合器属性测试 `test/domain/health/test_aggregator_property.py`
    - **Property 1: 聚合状态等价于全部 UP**
    - **Property 2: 聚合结果包含所有检查项**
    - 使用 Hypothesis 生成随机长度的 HealthCheckResult 列表和 mock HealthCheckPort
    - **Validates: Requirements 1.2, 1.3, 1.4, 4.1, 4.2, 4.3, 4.4**

  - [x] 2.3 编写聚合器单元测试 `test/domain/health/test_aggregator.py`
    - 测试空检查列表聚合返回 UP
    - 测试全部 UP 场景
    - 测试存在 DOWN 场景
    - _Requirements: 4.2, 4.3, 4.4_

- [x] 3. Checkpoint - 确保 Domain 层测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. 实现 Infrastructure 层 Redis 健康检查适配器
  - [x] 4.1 创建 `src/infrastructure/health/__init__.py` 和 `src/infrastructure/health/redis_health_check_adapter.py`
    - 实现 `RedisHealthCheckAdapter` 类，接收 `redis.asyncio.Redis` 客户端
    - 通过 `asyncio.wait_for` 包裹 Redis `PING` 命令，超时 3 秒
    - 捕获 `TimeoutError`、`RedisError` 和通用 `Exception`，均返回 DOWN 状态并携带 reason
    - PING 成功返回 UP 状态
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 4.2 编写 Redis 适配器属性测试 `test/infrastructure/health/test_redis_health_check_property.py`
    - **Property 5: Redis 异常产生 DOWN 结果并携带原因**
    - 创建 `test/infrastructure/health/__init__.py`
    - 使用 Hypothesis 生成随机异常消息，mock Redis 客户端抛出异常
    - **Validates: Requirements 3.3, 3.4**

  - [x] 4.3 编写 Redis 适配器单元测试 `test/infrastructure/health/test_redis_health_check.py`
    - 测试 Redis PING 成功返回 UP
    - 测试 Redis PING 超时返回 DOWN（3 秒内）
    - 测试 Redis 连接异常返回 DOWN 并携带 reason
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 5. Checkpoint - 确保 Infrastructure 层测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. 集成 Application 层路由和 DI 容器
  - [x] 6.1 修改 `src/application/container_config.py`，注册 ReadinessAggregator
    - 新增 `_create_readiness_aggregator()` 工厂函数，内部创建 `RedisHealthCheckAdapter` 并组装 `ReadinessAggregator`
    - 在 `configure_container()` 中注册 `ReadinessAggregator` 为 Singleton
    - 复用已有的 `_redis_client` 模块级变量
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 6.2 修改 `src/application/routers/health.py`，新增 `GET /readiness` 端点
    - 通过 `Depends(inject(ReadinessAggregator))` 注入聚合器
    - 调用 `aggregator.check_readiness()` 获取结果
    - 全部 UP 返回 HTTP 200，存在 DOWN 返回 HTTP 503
    - 响应体格式：`{"status": "UP/DOWN", "checks": {...}}`
    - 保持现有 `/health.json` 和 `/prometheus` 端点不变
    - _Requirements: 1.1, 1.2, 1.3, 6.1, 6.2_

  - [x] 6.3 编写路由单元测试 `test/application/routers/test_health.py`
    - 创建 `test/application/__init__.py` 和 `test/application/routers/__init__.py`
    - 测试 GET /readiness 返回 200（全部 UP）
    - 测试 GET /readiness 返回 503（存在 DOWN）
    - 测试 GET /health.json 保持不变
    - _Requirements: 1.1, 1.2, 1.3, 6.1, 6.2_

  - [x] 6.4 编写路由属性测试 `test/application/routers/test_health_property.py`
    - **Property 6: 存活探针始终返回 UP**
    - 使用 FastAPI TestClient 多次请求验证 /health.json 始终返回 {"status": "UP"}
    - **Validates: Requirements 6.1, 6.2**

- [x] 7. Final checkpoint - 确保所有测试通过
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- 依赖管理使用 `uv`，如需新增测试依赖（如 `fakeredis`），在 `epsilon-boot/` 目录下执行 `uv add --dev <package>`
- 所有代码 docstring 使用中文
- Domain 层不引用 Infrastructure 层或 Application 层的任何模块
- Property tests 使用 Hypothesis 库（项目已包含 `hypothesis>=6.82.0`）
