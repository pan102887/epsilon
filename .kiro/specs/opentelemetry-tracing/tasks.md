# 实施计划：OpenTelemetry 链路追踪

## 概述

核心实现（OtelConfig、otel_setup、DI 容器集成、FastAPI 集成、依赖声明）已全部完成。本实施计划聚焦于：创建完整的测试套件（属性测试 + 单元测试），以及验证日志格式中 trace_id/span_id 占位符的正确性。

## Tasks

- [x] 1. 创建测试基础设施和配置属性测试
  - [x] 1.1 创建测试目录和 `__init__.py`
    - 创建 `test/infrastructure/telemetry/__init__.py`
    - _需求: 无（测试基础设施）_

  - [x] 1.2 实现 OtelConfig 配置属性测试
    - 创建 `test/infrastructure/telemetry/test_otel_config.py`
    - 测试所有字段的默认值正确性（enabled=False, service_name="epsilon-boot", service_version="0.1.0", environment="development", exporter_endpoint="", exporter_insecure=True, traces_sampler="parentbased_traceidratio", traces_sampler_arg=1.0, log_correlation=True, instrument_*=True）
    - 测试通过环境变量覆盖各字段值
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_

  - [x] 1.3 编写属性测试：配置环境变量加载往返一致性
    - **Property 1: 配置环境变量加载往返一致性**
    - 对于任意合法的配置字段名和对应的字符串值，设置为 `OTEL_` 前缀环境变量后创建 OtelConfig 实例，读取该字段值应与设置值（经类型转换后）一致
    - 使用 hypothesis 生成随机字符串/布尔/浮点值，`@settings(max_examples=100)`
    - 测试文件：`test/infrastructure/telemetry/test_otel_config.py`
    - **验证: 需求 1.1**

- [x] 2. 实现 Resource 构建和采样器选择测试
  - [x] 2.1 实现 `_build_resource()` 单元测试
    - 创建 `test/infrastructure/telemetry/test_otel_setup.py`
    - 验证 Resource 包含 service.name、service.version、deployment.environment 属性
    - 使用 monkeypatch 模拟 otel_config 的值
    - _需求: 2.3_

  - [x] 2.2 编写属性测试：Resource 包含配置的服务元数据
    - **Property 2: Resource 包含配置的服务元数据**
    - 对于任意 service_name、service_version 和 environment 字符串值，`_build_resource()` 构建的 Resource 应包含对应属性且值一致
    - 使用 hypothesis 生成随机字符串，`@settings(max_examples=100)`
    - 测试文件：`test/infrastructure/telemetry/test_otel_setup.py`
    - **验证: 需求 2.3**

  - [x] 2.3 实现 `_build_sampler()` 单元测试
    - 测试 always_on → ALWAYS_ON、always_off → ALWAYS_OFF、traceidratio → TraceIdRatioBased、parentbased_traceidratio → ParentBasedTraceIdRatio
    - 测试大小写不敏感（如 "ALWAYS_ON" 也应匹配）
    - _需求: 3.1, 3.2, 3.3, 3.4_

  - [x] 2.4 编写属性测试：采样器选择映射正确性
    - **Property 3: 采样器选择映射正确性**
    - 对于任意已知采样器名称和合法采样比例值（0.0~1.0），`_build_sampler()` 应返回正确的采样器类型实例
    - 使用 hypothesis 从已知名称中随机选择 + 随机浮点比例，`@settings(max_examples=100)`
    - 测试文件：`test/infrastructure/telemetry/test_otel_setup.py`
    - **验证: 需求 3.1, 3.2, 3.3, 3.4**

  - [x] 2.5 编写属性测试：未知采样器名称默认回退
    - **Property 4: 未知采样器名称默认回退**
    - 对于任意不属于已知采样器名称集合的字符串，`_build_sampler()` 应返回 ParentBasedTraceIdRatio 实例
    - 使用 hypothesis 生成随机字符串并过滤已知名称，`@settings(max_examples=100)`
    - 测试文件：`test/infrastructure/telemetry/test_otel_setup.py`
    - **验证: 需求 3.5**

- [x] 3. 实现导出器构建和生命周期测试
  - [x] 3.1 实现 `_build_exporter()` 单元测试
    - 测试空 endpoint 返回 ConsoleSpanExporter
    - 测试非空 endpoint 返回 OTLPSpanExporter（使用 mock 避免实际 gRPC 连接）
    - _需求: 4.1, 4.2, 4.3_

  - [x] 3.2 编写属性测试：非空 endpoint 产生 OTLP 导出器
    - **Property 5: 非空 endpoint 产生 OTLP 导出器**
    - 对于任意非空 exporter_endpoint 字符串和任意 exporter_insecure 布尔值，`_build_exporter()` 应返回 OTLPSpanExporter 实例
    - 使用 hypothesis 生成随机非空字符串 + 随机布尔值，`@settings(max_examples=100)`
    - 测试文件：`test/infrastructure/telemetry/test_otel_setup.py`
    - **验证: 需求 4.2, 4.3**

  - [x] 3.3 实现 `init_telemetry()` 和 `shutdown_telemetry()` 单元测试
    - 测试 enabled=False 时 init_telemetry 不创建 TracerProvider
    - 测试 enabled=True 时 init_telemetry 创建 TracerProvider 并设为全局 provider
    - 测试 TracerProvider 使用 BatchSpanProcessor
    - 测试 shutdown_telemetry 调用 provider.shutdown()
    - 测试 shutdown_telemetry 在 provider 为 None 时安全返回
    - _需求: 2.1, 2.2, 2.4, 2.5, 11.1, 11.2, 11.3_

- [x] 4. Checkpoint - 确保配置和核心逻辑测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 5. 实现组件埋点和容错测试
  - [x] 5.1 实现各组件埋点开关的单元测试
    - 测试 instrument_fastapi_app：enabled=True + instrument_fastapi=True 时执行埋点
    - 测试 instrument_fastapi_app：enabled=False 或 instrument_fastapi=False 时跳过
    - 测试 _instrument_components 中各组件开关独立控制（httpx、Redis、SQLAlchemy、logging）
    - 使用 unittest.mock.patch 模拟各 Instrumentor
    - _需求: 5.1, 5.4, 6.1, 6.3, 7.1, 7.3, 8.1, 8.3, 9.1, 9.4_

  - [x] 5.2 编写属性测试：组件埋点故障隔离
    - **Property 6: 组件埋点故障隔离**
    - 对于任意组件子集（httpx、Redis、SQLAlchemy、logging 中的任意组合），若该子集中的组件在埋点过程中抛出异常，则不在该子集中的其他组件仍应被正常埋点
    - 使用 hypothesis 随机选择失败组件子集，通过 mock side_effect 注入异常，`@settings(max_examples=100)`
    - 测试文件：`test/infrastructure/telemetry/test_otel_setup.py`
    - **验证: 需求 10.2, 5.4, 6.3, 7.3, 8.3, 9.4**

- [x] 6. 验证日志关联配置
  - [x] 6.1 验证 `main.py` 日志格式包含 trace_id/span_id 占位符
    - 确认 `logging.basicConfig` 的 format 包含 `%(otelTraceID)s` 和 `%(otelSpanID)s`
    - 编写单元测试验证 OTel 未启用时日志中 otelTraceID/otelSpanID 输出为默认值（空字符串或 0），不影响日志正常输出
    - 测试文件：`test/infrastructure/telemetry/test_otel_setup.py`
    - _需求: 9.1, 9.2, 9.3_

- [x] 7. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试验证通用正确性属性（6 个 Property），单元测试验证具体示例和边界情况
- 核心实现代码（OtelConfig、otel_setup、DI 容器集成、FastAPI 集成）已全部完成，本计划仅覆盖测试和日志格式验证
- 测试运行命令：`cd epsilon-boot && uv run pytest test/infrastructure/telemetry/ -v`
- Checkpoint 任务确保增量验证
