# 需求文档：OpenTelemetry 链路追踪（OpenTelemetry Tracing）

## 简介

为项目引入 OpenTelemetry 分布式链路追踪能力，实现对 HTTP 请求、数据库操作、Redis 访问、外部 HTTP 调用等关键路径的自动埋点和 trace 数据采集。通过 OTLP 协议将 trace 数据导出到 Collector 或兼容后端（如 Jaeger、Tempo），支持在日志中注入 trace_id/span_id 实现日志与链路的关联。

整体设计遵循项目 DDD 架构规范：配置和初始化逻辑位于 `infrastructure/telemetry/` 层，通过 DI 容器的 lifespan 机制管理生命周期，对业务代码零侵入。支持通过环境变量灵活控制各组件的埋点开关、采样策略和导出目标，未启用时对应用性能零开销。

## 术语表

- **TracerProvider**：OpenTelemetry SDK 的核心组件，负责创建 Tracer 实例并管理 span 的生命周期和导出流程。
- **Span**：链路追踪的基本单元，表示一次操作（如 HTTP 请求、数据库查询），包含操作名称、起止时间、属性和状态等信息。
- **SpanExporter**：负责将 span 数据发送到外部后端的组件。本项目支持 ConsoleSpanExporter（控制台输出）和 OTLPSpanExporter（gRPC 协议导出）。
- **BatchSpanProcessor**：批量处理 span 的处理器，将 span 缓存后批量发送给 SpanExporter，减少导出频率，提升性能。
- **Sampler**：采样器，决定哪些请求的 trace 数据会被记录。支持全量采样、关闭采样、按比例采样和基于父 span 的比例采样。
- **Resource**：描述产生遥测数据的服务实体的元数据集合，包含服务名、版本、部署环境等信息。
- **Auto_Instrumentation**：自动埋点，通过 monkey-patch 或回调注入的方式，在不修改业务代码的前提下为框架和库添加 span 创建逻辑。
- **OTel_Config**：OpenTelemetry 配置类，基于 pydantic-settings，从环境变量加载以 `OTEL_` 为前缀的配置项。
- **Log_Correlation**：日志关联，在日志记录中自动注入当前 trace_id 和 span_id，实现日志与链路追踪数据的关联查询。
- **DI_Container**：依赖注入容器，管理异步资源的生命周期，通过 FastAPI 的 lifespan 机制控制初始化和清理顺序。

## 需求

### 需求 1：OTel 配置管理

**用户故事：** 作为运维人员，我希望通过环境变量灵活控制 OpenTelemetry 的启用状态和各项参数，以便在不同环境（开发、测试、生产）中使用不同的配置。

#### 验收标准

1. THE OTel_Config SHALL 从以 `OTEL_` 为前缀的环境变量加载配置项。
2. THE OTel_Config SHALL 提供 `enabled` 配置项，默认值为 `False`，控制 OpenTelemetry SDK 的整体启用状态。
3. THE OTel_Config SHALL 提供 `service_name` 配置项，默认值为 `"epsilon-boot"`，用于在链路追踪系统中标识本服务。
4. THE OTel_Config SHALL 提供 `service_version` 和 `environment` 配置项，分别默认为 `"0.1.0"` 和 `"development"`。
5. THE OTel_Config SHALL 提供 `exporter_endpoint` 配置项，默认为空字符串，为空时使用控制台导出器。
6. THE OTel_Config SHALL 提供 `exporter_insecure` 配置项，默认为 `True`，控制 OTLP gRPC 连接是否使用非安全模式。
7. THE OTel_Config SHALL 提供 `traces_sampler` 和 `traces_sampler_arg` 配置项，分别默认为 `"parentbased_traceidratio"` 和 `1.0`。
8. THE OTel_Config SHALL 提供独立的布尔开关控制各组件的自动埋点：`instrument_fastapi`、`instrument_httpx`、`instrument_redis`、`instrument_sqlalchemy`，默认均为 `True`。
9. THE OTel_Config SHALL 提供 `log_correlation` 配置项，默认为 `True`，控制是否在日志中注入 trace_id 和 span_id。

### 需求 2：TracerProvider 初始化与生命周期管理

**用户故事：** 作为开发者，我希望 OpenTelemetry SDK 的初始化和关闭与 FastAPI 应用的生命周期绑定，以便确保 trace 数据的完整采集和可靠导出。

#### 验收标准

1. WHEN 应用启动且 `enabled` 为 `True` 时，THE TracerProvider SHALL 在 DI_Container 的 lifespan 启动阶段完成初始化。
2. WHEN 应用启动且 `enabled` 为 `False` 时，THE TracerProvider SHALL 跳过初始化，不创建任何 OTel 组件。
3. THE TracerProvider SHALL 使用包含 `service_name`、`service_version` 和 `deployment.environment` 的 Resource 进行初始化。
4. THE TracerProvider SHALL 使用 BatchSpanProcessor 处理 span 数据，确保批量导出以减少性能开销。
5. WHEN 应用关闭时，THE TracerProvider SHALL 调用 `shutdown()` 方法刷新缓冲区中尚未导出的 span 数据，确保数据不丢失。
6. THE TracerProvider 的初始化 SHALL 在 DI_Container 中排在所有业务资源之前，确保后续资源初始化过程中产生的 span 也能被追踪。
7. THE TracerProvider 的关闭 SHALL 在 DI_Container 中排在所有业务资源之后（通过 LIFO 清理机制），确保业务资源清理过程中产生的 span 也能被导出。

### 需求 3：采样策略

**用户故事：** 作为运维人员，我希望能够灵活配置采样策略，以便在开发环境全量采样方便调试，在生产环境按比例采样控制数据量。

#### 验收标准

1. WHEN `traces_sampler` 为 `"always_on"` 时，THE Sampler SHALL 对所有请求进行采样。
2. WHEN `traces_sampler` 为 `"always_off"` 时，THE Sampler SHALL 不对任何请求进行采样。
3. WHEN `traces_sampler` 为 `"traceidratio"` 时，THE Sampler SHALL 按 `traces_sampler_arg` 指定的比例（0.0 ~ 1.0）进行采样。
4. WHEN `traces_sampler` 为 `"parentbased_traceidratio"` 时，THE Sampler SHALL 在父 span 已被采样时继续采样，否则按 `traces_sampler_arg` 比例决定。
5. WHEN `traces_sampler` 的值不匹配以上任何策略时，THE Sampler SHALL 默认使用 `"parentbased_traceidratio"` 策略。

### 需求 4：Span 导出器

**用户故事：** 作为开发者，我希望在本地开发时 trace 数据输出到控制台方便调试，在部署环境通过 OTLP 协议发送到后端系统。

#### 验收标准

1. WHEN `exporter_endpoint` 为空字符串时，THE SpanExporter SHALL 使用 ConsoleSpanExporter 将 span 数据输出到标准输出。
2. WHEN `exporter_endpoint` 配置了有效地址时，THE SpanExporter SHALL 使用 OTLPSpanExporter 通过 gRPC 协议将 span 数据发送到指定端点。
3. WHEN 使用 OTLPSpanExporter 时，THE SpanExporter SHALL 根据 `exporter_insecure` 配置决定是否启用 TLS。

### 需求 5：FastAPI 自动埋点

**用户故事：** 作为开发者，我希望每个 HTTP 请求自动创建 span，包含请求方法、路径和状态码等信息，无需在业务代码中手动埋点。

#### 验收标准

1. WHEN `enabled` 为 `True` 且 `instrument_fastapi` 为 `True` 时，THE Auto_Instrumentation SHALL 对 FastAPI 应用实例执行自动埋点。
2. WHEN FastAPI 自动埋点启用后，THE Auto_Instrumentation SHALL 为每个 HTTP 请求自动创建一个 span，包含 HTTP 方法、路径和响应状态码。
3. THE FastAPI 自动埋点 SHALL 在中间件注册之前执行，确保埋点覆盖所有请求处理流程。
4. IF FastAPI 自动埋点过程中发生异常，THEN THE Auto_Instrumentation SHALL 记录警告日志并继续应用启动，不影响服务可用性。

### 需求 6：httpx HTTP 客户端自动埋点

**用户故事：** 作为开发者，我希望所有通过 httpx 发出的外部 HTTP 请求自动创建子 span，以便在链路中追踪外部调用的耗时和状态。

#### 验收标准

1. WHEN `enabled` 为 `True` 且 `instrument_httpx` 为 `True` 时，THE Auto_Instrumentation SHALL 对 httpx 客户端执行自动埋点。
2. WHEN httpx 自动埋点启用后，THE Auto_Instrumentation SHALL 为每个外部 HTTP 请求自动创建子 span。
3. IF httpx 自动埋点过程中发生异常，THEN THE Auto_Instrumentation SHALL 记录警告日志并跳过，不影响其他组件的埋点。

### 需求 7：Redis 自动埋点

**用户故事：** 作为开发者，我希望所有 Redis 操作自动创建子 span，以便在链路中追踪缓存访问的耗时和命令详情。

#### 验收标准

1. WHEN `enabled` 为 `True` 且 `instrument_redis` 为 `True` 时，THE Auto_Instrumentation SHALL 对 Redis 客户端执行自动埋点。
2. WHEN Redis 自动埋点启用后，THE Auto_Instrumentation SHALL 为每个 Redis 命令自动创建子 span。
3. IF Redis 自动埋点过程中发生异常，THEN THE Auto_Instrumentation SHALL 记录警告日志并跳过，不影响其他组件的埋点。

### 需求 8：SQLAlchemy 数据库自动埋点

**用户故事：** 作为开发者，我希望所有数据库操作自动创建子 span，以便在链路中追踪 SQL 查询的耗时和语句详情。

#### 验收标准

1. WHEN `enabled` 为 `True` 且 `instrument_sqlalchemy` 为 `True` 时，THE Auto_Instrumentation SHALL 对 SQLAlchemy 执行自动埋点。
2. WHEN SQLAlchemy 自动埋点启用后，THE Auto_Instrumentation SHALL 为每个数据库操作自动创建子 span。
3. IF SQLAlchemy 自动埋点过程中发生异常，THEN THE Auto_Instrumentation SHALL 记录警告日志并跳过，不影响其他组件的埋点。

### 需求 9：日志与链路关联

**用户故事：** 作为运维人员，我希望日志中自动包含 trace_id 和 span_id，以便在排查问题时能够从日志快速定位到对应的链路追踪数据。

#### 验收标准

1. WHEN `enabled` 为 `True` 且 `log_correlation` 为 `True` 时，THE Log_Correlation SHALL 通过 logging instrumentation 在日志记录中自动注入 `otelTraceID` 和 `otelSpanID` 字段。
2. THE 应用的日志格式 SHALL 包含 `trace_id=%(otelTraceID)s` 和 `span_id=%(otelSpanID)s` 占位符，展示链路上下文信息。
3. WHEN OpenTelemetry 未启用时，THE 日志格式中的 `otelTraceID` 和 `otelSpanID` SHALL 输出为空字符串或默认值 `0`，不影响日志正常输出。
4. IF logging 自动埋点过程中发生异常，THEN THE Log_Correlation SHALL 记录警告日志并跳过，不影响应用的日志功能。

### 需求 10：组件埋点隔离与容错

**用户故事：** 作为开发者，我希望各组件的自动埋点相互独立，单个组件埋点失败不影响其他组件和应用的正常运行。

#### 验收标准

1. THE Auto_Instrumentation SHALL 对每个组件（FastAPI、httpx、Redis、SQLAlchemy、logging）独立执行埋点操作。
2. IF 任一组件的自动埋点发生异常，THEN THE Auto_Instrumentation SHALL 捕获异常、记录包含异常详情的警告日志，并继续执行后续组件的埋点。
3. IF 任一组件的自动埋点失败，THEN THE Auto_Instrumentation SHALL 确保应用正常启动，不因埋点失败而中断启动流程。

### 需求 11：零开销保证

**用户故事：** 作为运维人员，我希望在 OpenTelemetry 未启用时对应用性能零影响，以便在不需要链路追踪的环境中安全部署。

#### 验收标准

1. WHEN `enabled` 为 `False` 时，THE TracerProvider SHALL 不执行任何初始化操作，不创建 Resource、Sampler、SpanExporter 等组件。
2. WHEN `enabled` 为 `False` 时，THE Auto_Instrumentation SHALL 不对任何组件执行自动埋点。
3. WHEN `enabled` 为 `False` 时，THE 应用 SHALL 不引入 OpenTelemetry SDK 的运行时开销。
