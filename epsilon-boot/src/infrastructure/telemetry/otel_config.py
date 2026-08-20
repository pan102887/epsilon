"""OpenTelemetry 配置模块。

基于 pydantic-settings，从 .env 文件和环境变量加载以 ``OTEL_`` 为前缀的配置项。
控制 OpenTelemetry SDK 的启用状态、服务标识、导出端点和采样策略等。

当 ``enabled=False`` 时，整个 OTel SDK 不会初始化，应用零开销运行。
当 ``enabled=True`` 但未配置 ``exporter_endpoint`` 时，使用控制台导出器，
方便本地开发时在终端查看 trace 输出。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class OtelConfig(PropertiesBaseSettings):
    """OpenTelemetry 配置，对应环境变量前缀 ``OTEL_``。

    Attributes:
        enabled: 是否启用 OpenTelemetry，对应 ``OTEL_ENABLED``，默认 ``False``。
            设为 False 时不初始化任何 OTel 组件，对性能零影响。
        service_name: 服务名称，对应 ``OTEL_SERVICE_NAME``，
            默认 ``"epsilon-boot"``。用于在链路追踪系统中标识本服务。
        service_version: 服务版本号，对应 ``OTEL_SERVICE_VERSION``，默认 ``"0.1.0"``。
        environment: 部署环境标识，对应 ``OTEL_ENVIRONMENT``，默认 ``"development"``。
            常见值：development / testing / staging / production。
        exporter_endpoint: OTLP 导出端点地址，对应 ``OTEL_EXPORTER_ENDPOINT``，默认空字符串。
            为空时使用控制台导出器（ConsoleSpanExporter），适合本地开发调试。
            配置后使用 OTLP gRPC 导出器，将 trace 数据发送到 Collector / Jaeger 等后端。
            示例：``http://localhost:4317``（gRPC）。
        exporter_insecure: OTLP gRPC 连接是否使用非安全模式（不启用 TLS），
            对应 ``OTEL_EXPORTER_INSECURE``，默认 ``True``。
            本地开发和集群内部通信通常设为 True，公网传输应设为 False。
        traces_sampler: 采样策略，对应 ``OTEL_TRACES_SAMPLER``，
            默认 ``"parentbased_traceidratio"``。
            可选值：
            - ``"always_on"``：全量采样，适合开发和低流量环境
            - ``"always_off"``：关闭采样
            - ``"traceidratio"``：按比例采样
            - ``"parentbased_traceidratio"``：基于父 span 的比例采样（推荐生产使用）
        traces_sampler_arg: 采样比例参数，对应 ``OTEL_TRACES_SAMPLER_ARG``，
            默认 ``1.0``（100% 采样）。取值范围 0.0 ~ 1.0。
            生产环境建议设为 0.1（10%）或更低，避免 trace 数据量过大。
        log_correlation: 是否在日志中注入 trace_id 和 span_id，
            对应 ``OTEL_LOG_CORRELATION``，默认 ``True``。
            启用后通过 logging instrumentation 自动在日志记录中附加链路上下文。
        instrument_fastapi: 是否自动埋点 FastAPI，对应 ``OTEL_INSTRUMENT_FASTAPI``，
            默认 ``True``。
        instrument_httpx: 是否自动埋点 httpx HTTP 客户端，
            对应 ``OTEL_INSTRUMENT_HTTPX``，默认 ``True``。
        instrument_redis: 是否自动埋点 Redis 操作，
            对应 ``OTEL_INSTRUMENT_REDIS``，默认 ``True``。
        instrument_sqlalchemy: 是否自动埋点 SQLAlchemy 数据库操作，
            对应 ``OTEL_INSTRUMENT_SQLALCHEMY``，默认 ``True``。
    """

    model_config = SettingsConfigDict(env_prefix="OTEL_")

    enabled: bool = False
    service_name: str = "epsilon-boot"
    service_version: str = "0.1.0"
    environment: str = "development"

    exporter_endpoint: str = ""
    exporter_insecure: bool = True

    traces_sampler: str = "parentbased_traceidratio"
    traces_sampler_arg: float = 1.0

    log_correlation: bool = True

    instrument_fastapi: bool = True
    instrument_httpx: bool = True
    instrument_redis: bool = True
    instrument_sqlalchemy: bool = True


otel_config = create_config(OtelConfig)
"""全局 OpenTelemetry 配置实例，通过工厂函数创建，支持热更新。"""
