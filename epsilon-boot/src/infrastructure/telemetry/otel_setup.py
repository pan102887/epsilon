"""OpenTelemetry SDK 初始化与关闭模块。

负责根据 ``OtelConfig`` 配置初始化 TracerProvider、SpanExporter、采样器，
以及对 FastAPI、httpx、Redis、SQLAlchemy、logging 等组件的自动埋点。

初始化流程：
1. 读取 ``otel_config`` 判断是否启用 OTel
2. 构建 Resource（服务名、版本、环境等元数据）
3. 根据配置选择采样器（always_on / traceidratio / parentbased 等）
4. 根据 ``exporter_endpoint`` 选择导出器（Console 或 OTLP gRPC）
5. 创建 TracerProvider 并设为全局 provider
6. 按配置逐一启用各组件的自动埋点

关闭流程：
调用 ``TracerProvider.shutdown()`` 刷新并关闭所有 SpanProcessor，
确保缓冲区中的 span 数据被完整导出。

本模块在 DI 容器中注册为异步资源，通过 ``container_config.py`` 的
``register_async_resource("telemetry", init_telemetry, shutdown_telemetry)``
嵌入 FastAPI 的 lifespan 生命周期。
"""

import importlib
import logging
from typing import Any, Protocol, cast

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBasedTraceIdRatio,
    Sampler,
    TraceIdRatioBased,
)

from .otel_config import otel_config

logger = logging.getLogger(__name__)


class _Instrumentor(Protocol):
    """可选 OpenTelemetry 自动埋点器的最小接口。"""

    def instrument(self, **kwargs: Any) -> None: ...


class _FastAPIInstrumentor(Protocol):
    """FastAPI 自动埋点器的类级调用接口。"""

    @staticmethod
    def instrument_app(app: Any) -> None: ...


def _instrumentor(module_name: str, class_name: str) -> _Instrumentor:
    """动态加载可选埋点包，避免把无类型信息依赖带入核心模块。"""
    module = importlib.import_module(module_name)
    instrumentor_type = cast(type[_Instrumentor], getattr(module, class_name))
    return instrumentor_type()

_tracer_provider: TracerProvider | None = None
"""模块级 TracerProvider 实例，由 ``init_telemetry()`` 创建，``shutdown_telemetry()`` 释放。"""


def tracer_provider() -> TracerProvider | None:
    """返回当前模块持有的 TracerProvider。"""
    return _tracer_provider


def set_tracer_provider(provider: TracerProvider | None) -> None:
    """替换模块持有的 TracerProvider，供生命周期装配和测试隔离使用。"""
    global _tracer_provider
    _tracer_provider = provider


def _build_resource() -> Resource:
    """构建 OpenTelemetry Resource，描述产生遥测数据的服务实体。

    Resource 包含服务名、版本和部署环境等元数据，
    会附加到所有导出的 span 上，用于在后端系统中标识和过滤。

    Returns:
        包含服务元数据的 Resource 实例。
    """
    return Resource.create(
        {
            SERVICE_NAME: otel_config.service_name,
            SERVICE_VERSION: otel_config.service_version,
            "deployment.environment": otel_config.environment,
        }
    )


def build_resource() -> Resource:
    """构建描述当前服务的 OpenTelemetry Resource。"""
    return _build_resource()


def _build_sampler() -> Sampler:
    """根据配置构建采样器。

    采样器决定哪些请求会被记录为 trace。支持以下策略：

    - ``always_on``：全量采样，每个请求都记录。适合开发和低流量环境。
    - ``always_off``：关闭采样，不记录任何 trace。
    - ``traceidratio``：按 trace_id 哈希值的比例采样，与父 span 无关。
    - ``parentbased_traceidratio``（默认）：如果父 span 已被采样则继续采样，
      否则按比例决定。这是生产环境推荐的策略，能保证同一条链路的完整性。

    Returns:
        根据配置创建的 Sampler 实例。
    """
    sampler_name = otel_config.traces_sampler.lower()
    ratio = otel_config.traces_sampler_arg

    if sampler_name == "always_on":
        return ALWAYS_ON
    elif sampler_name == "always_off":
        return ALWAYS_OFF
    elif sampler_name == "traceidratio":
        return TraceIdRatioBased(ratio)
    else:
        # 默认使用 parentbased_traceidratio
        return ParentBasedTraceIdRatio(ratio)


def build_sampler() -> Sampler:
    """根据当前配置构建采样器。"""
    return _build_sampler()


def _build_exporter() -> SpanExporter:
    """根据配置构建 Span 导出器。

    - ``exporter_endpoint`` 为空时：使用 ``ConsoleSpanExporter``，
      将 span 数据输出到标准输出，适合本地开发调试。
    - ``exporter_endpoint`` 非空时：使用 ``OTLPSpanExporter``（gRPC 协议），
      将 span 数据发送到 OpenTelemetry Collector 或兼容的后端（如 Jaeger、Tempo）。

    Returns:
        配置好的 SpanExporter 实例。
    """
    endpoint = otel_config.exporter_endpoint
    if not endpoint:
        logger.info("OTLP endpoint 未配置，使用 ConsoleSpanExporter（仅本地调试）")
        return ConsoleSpanExporter()

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    logger.info("使用 OTLP gRPC 导出器，endpoint=%s", endpoint)
    return OTLPSpanExporter(
        endpoint=endpoint,
        insecure=otel_config.exporter_insecure,
    )


def build_exporter() -> SpanExporter:
    """根据当前配置构建 Span 导出器。"""
    return _build_exporter()


def _instrument_components() -> None:
    """根据配置对各组件执行自动埋点。

    自动埋点（auto-instrumentation）通过 monkey-patch 或回调注入的方式，
    在不修改业务代码的前提下为框架和库添加 span 创建逻辑。

    各组件的埋点相互独立，单个组件埋点失败不影响其他组件。
    失败时记录 warning 日志并继续，确保应用正常启动。
    """
    if otel_config.instrument_httpx:
        try:
            _instrumentor(
                "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"
            ).instrument()
            logger.info("OpenTelemetry httpx 自动埋点已启用")
        except Exception:
            logger.warning("httpx 自动埋点失败，跳过", exc_info=True)

    if otel_config.instrument_redis:
        try:
            _instrumentor(
                "opentelemetry.instrumentation.redis", "RedisInstrumentor"
            ).instrument()
            logger.info("OpenTelemetry Redis 自动埋点已启用")
        except Exception:
            logger.warning("Redis 自动埋点失败，跳过", exc_info=True)

    if otel_config.instrument_sqlalchemy:
        try:
            _instrumentor(
                "opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"
            ).instrument()
            logger.info("OpenTelemetry SQLAlchemy 自动埋点已启用")
        except Exception:
            logger.warning("SQLAlchemy 自动埋点失败，跳过", exc_info=True)

    if otel_config.log_correlation:
        try:
            _instrumentor(
                "opentelemetry.instrumentation.logging", "LoggingInstrumentor"
            ).instrument(set_logging_format=False)
            logger.info("OpenTelemetry Logging 关联已启用（trace_id/span_id 注入日志记录）")
        except Exception:
            logger.warning("Logging 自动埋点失败，跳过", exc_info=True)


def instrument_components() -> None:
    """按当前配置启用可选组件自动埋点。"""
    _instrument_components()


def instrument_fastapi_app(app: Any) -> None:
    """对 FastAPI 应用实例执行自动埋点。

    与其他组件不同，FastAPI 的埋点需要传入 app 实例，
    因此单独提供此函数，在 ``server_app.py`` 中创建 app 后调用。

    埋点后，每个 HTTP 请求会自动创建一个 span，包含：
    - HTTP 方法、路径、状态码
    - 请求/响应头（可配置）
    - 异常信息（如果发生）

    Args:
        app: FastAPI 应用实例。
    """
    if not otel_config.enabled or not otel_config.instrument_fastapi:
        return

    try:
        module = importlib.import_module("opentelemetry.instrumentation.fastapi")
        instrumentor_type = cast(
            type[_FastAPIInstrumentor], vars(module)["FastAPIInstrumentor"]
        )
        instrumentor_type.instrument_app(app)
        logger.info("OpenTelemetry FastAPI 自动埋点已启用")
    except Exception:
        logger.warning("FastAPI 自动埋点失败，跳过", exc_info=True)


async def init_telemetry() -> None:
    """初始化 OpenTelemetry SDK。

    作为 DI 容器的异步资源初始化回调，在 FastAPI lifespan 启动阶段调用。
    注册顺序应排在所有业务资源之前，确保后续资源初始化时 OTel 已就绪。

    初始化步骤：
    1. 检查 ``otel_config.enabled``，未启用则直接返回
    2. 构建 Resource（服务元数据）
    3. 构建 Sampler（采样策略）
    4. 构建 SpanExporter（导出目标）
    5. 创建 TracerProvider 并注册为全局 provider
    6. 对 httpx、Redis、SQLAlchemy、logging 执行自动埋点

    注意：FastAPI 的埋点通过 ``instrument_fastapi_app()`` 单独完成，
    因为它需要 app 实例作为参数。
    """
    global _tracer_provider

    if not otel_config.enabled:
        logger.info(f"OpenTelemetry 未启用（OTEL_ENABLED={otel_config.enabled}），跳过初始化")
        return

    resource = _build_resource()
    sampler = _build_sampler()
    exporter = _build_exporter()

    _tracer_provider = TracerProvider(
        resource=resource,
        sampler=sampler,
    )
    _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_tracer_provider)

    logger.info(
        "OpenTelemetry SDK 已初始化: service=%s, version=%s, env=%s, sampler=%s(%.2f)",
        otel_config.service_name,
        otel_config.service_version,
        otel_config.environment,
        otel_config.traces_sampler,
        otel_config.traces_sampler_arg,
    )

    # 对各组件执行自动埋点（FastAPI 除外，需要 app 实例）
    _instrument_components()


async def shutdown_telemetry() -> None:
    """关闭 OpenTelemetry SDK，刷新并释放所有资源。

    作为 DI 容器的异步资源清理回调，在 FastAPI lifespan 关闭阶段调用。
    调用 ``TracerProvider.shutdown()`` 会触发 BatchSpanProcessor 将缓冲区中
    尚未导出的 span 数据强制刷新到导出器，确保数据不丢失。

    关闭顺序应排在所有业务资源之后（由容器的 LIFO 清理机制保证），
    确保业务资源清理过程中产生的 span 也能被正确导出。
    """
    global _tracer_provider

    if _tracer_provider is None:
        return

    _tracer_provider.shutdown()
    _tracer_provider = None
    logger.info("OpenTelemetry SDK 已关闭")
