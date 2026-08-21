"""OpenTelemetry SDK 初始化模块测试。

覆盖 ``_build_resource()``、``_build_sampler()``、``_build_exporter()``、
``instrument_fastapi_app()``、``_instrument_components()`` 的单元测试与属性测试，
以及 ``init_telemetry()`` / ``shutdown_telemetry()`` 生命周期管理的单元测试。
验证 Resource 元数据构建、采样器选择映射、导出器构建逻辑、
组件埋点开关控制、故障隔离和 SDK 初始化/关闭流程的正确性。
"""

import importlib
import io
import logging
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBasedTraceIdRatio,
    TraceIdRatioBased,
)

from infrastructure.telemetry.otel_setup import (
    build_exporter as _build_exporter,
)
from infrastructure.telemetry.otel_setup import (
    build_resource as _build_resource,
)
from infrastructure.telemetry.otel_setup import (
    build_sampler as _build_sampler,
)
from infrastructure.telemetry.otel_setup import (
    init_telemetry,
    instrument_fastapi_app,
    set_tracer_provider,
    shutdown_telemetry,
    tracer_provider,
)
from infrastructure.telemetry.otel_setup import (
    instrument_components as _instrument_components,
)

# ---------------------------------------------------------------------------
# 公共策略：安全文本（排除 NUL 字符和代理字符，去除首尾空白）
# ---------------------------------------------------------------------------
_safe_text = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
).filter(lambda s: s.strip() == s and len(s.strip()) > 0)

# 已知采样器名称集合
_KNOWN_SAMPLER_NAMES = {"always_on", "always_off", "traceidratio", "parentbased_traceidratio"}


class _LoggingInstrumentor(Protocol):
    """测试使用的 LoggingInstrumentor 最小接口。"""

    def instrument(self, *, set_logging_format: bool) -> None: ...

    def uninstrument(self) -> None: ...


# ===================================================================
# Task 2.1: _build_resource() 单元测试
# ===================================================================
class TestBuildResource:
    """``_build_resource()`` 单元测试。

    验证构建的 Resource 包含正确的 service.name、service.version
    和 deployment.environment 属性。
    """

    def test_resource_contains_service_name(self) -> None:
        """验证 Resource 包含配置的 service.name 属性。"""
        mock_config = MagicMock()
        mock_config.service_name = "my-test-service"
        mock_config.service_version = "2.0.0"
        mock_config.environment = "production"

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            resource = _build_resource()

        assert resource.attributes["service.name"] == "my-test-service"

    def test_resource_contains_service_version(self) -> None:
        """验证 Resource 包含配置的 service.version 属性。"""
        mock_config = MagicMock()
        mock_config.service_name = "my-test-service"
        mock_config.service_version = "3.1.4"
        mock_config.environment = "staging"

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            resource = _build_resource()

        assert resource.attributes["service.version"] == "3.1.4"

    def test_resource_contains_deployment_environment(self) -> None:
        """验证 Resource 包含配置的 deployment.environment 属性。"""
        mock_config = MagicMock()
        mock_config.service_name = "svc"
        mock_config.service_version = "1.0.0"
        mock_config.environment = "testing"

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            resource = _build_resource()

        assert resource.attributes["deployment.environment"] == "testing"

    def test_resource_contains_all_attributes(self) -> None:
        """验证 Resource 同时包含所有三个服务元数据属性。"""
        mock_config = MagicMock()
        mock_config.service_name = "full-service"
        mock_config.service_version = "5.0.0"
        mock_config.environment = "production"

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            resource = _build_resource()

        assert resource.attributes["service.name"] == "full-service"
        assert resource.attributes["service.version"] == "5.0.0"
        assert resource.attributes["deployment.environment"] == "production"


# ===================================================================
# Task 2.2: Property 2 - Resource 包含配置的服务元数据
# ===================================================================
# Feature: opentelemetry-tracing, Property 2: Resource 包含配置的服务元数据
class TestBuildResourceProperty:
    """属性测试：对于任意 service_name、service_version 和 environment 字符串值，
    ``_build_resource()`` 构建的 Resource 应包含对应属性且值一致。

    **Validates: Requirements 2.3**
    """

    @given(
        service_name=_safe_text,
        service_version=_safe_text,
        environment=_safe_text,
    )
    @settings(max_examples=100)
    def test_resource_attributes_match_config(
        self, service_name: str, service_version: str, environment: str
    ) -> None:
        """对于任意合法字符串，Resource 属性应与配置值一致。

        **Validates: Requirements 2.3**
        """
        mock_config = MagicMock()
        mock_config.service_name = service_name
        mock_config.service_version = service_version
        mock_config.environment = environment

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            resource = _build_resource()

        assert resource.attributes["service.name"] == service_name
        assert resource.attributes["service.version"] == service_version
        assert resource.attributes["deployment.environment"] == environment


# ===================================================================
# Task 2.3: _build_sampler() 单元测试
# ===================================================================
class TestBuildSampler:
    """``_build_sampler()`` 单元测试。

    验证各采样器名称到采样器类型的映射正确性，包括大小写不敏感。
    """

    def test_always_on(self) -> None:
        """验证 'always_on' 返回 ALWAYS_ON 单例。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "always_on"
        mock_config.traces_sampler_arg = 1.0

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert sampler is ALWAYS_ON

    def test_always_off(self) -> None:
        """验证 'always_off' 返回 ALWAYS_OFF 单例。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "always_off"
        mock_config.traces_sampler_arg = 1.0

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert sampler is ALWAYS_OFF

    def test_traceidratio(self) -> None:
        """验证 'traceidratio' 返回 TraceIdRatioBased 实例。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "traceidratio"
        mock_config.traces_sampler_arg = 0.5

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert isinstance(sampler, TraceIdRatioBased)

    def test_parentbased_traceidratio(self) -> None:
        """验证 'parentbased_traceidratio' 返回 ParentBasedTraceIdRatio 实例。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "parentbased_traceidratio"
        mock_config.traces_sampler_arg = 0.8

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert isinstance(sampler, ParentBasedTraceIdRatio)

    def test_case_insensitive_always_on(self) -> None:
        """验证大写 'ALWAYS_ON' 也能正确匹配。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "ALWAYS_ON"
        mock_config.traces_sampler_arg = 1.0

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert sampler is ALWAYS_ON

    def test_case_insensitive_always_off(self) -> None:
        """验证混合大小写 'Always_Off' 也能正确匹配。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "Always_Off"
        mock_config.traces_sampler_arg = 1.0

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert sampler is ALWAYS_OFF

    def test_case_insensitive_traceidratio(self) -> None:
        """验证大写 'TRACEIDRATIO' 也能正确匹配。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "TRACEIDRATIO"
        mock_config.traces_sampler_arg = 0.3

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert isinstance(sampler, TraceIdRatioBased)

    def test_unknown_sampler_defaults_to_parentbased(self) -> None:
        """验证未知采样器名称默认回退到 ParentBasedTraceIdRatio。"""
        mock_config = MagicMock()
        mock_config.traces_sampler = "some_unknown_sampler"
        mock_config.traces_sampler_arg = 0.5

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert isinstance(sampler, ParentBasedTraceIdRatio)


# ===================================================================
# Task 2.4: Property 3 - 采样器选择映射正确性
# ===================================================================
# Feature: opentelemetry-tracing, Property 3: 采样器选择映射正确性
class TestBuildSamplerMappingProperty:
    """属性测试：对于任意已知采样器名称和合法采样比例值（0.0~1.0），
    ``_build_sampler()`` 应返回与该名称对应的正确采样器类型实例。

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """

    # 已知采样器名称与期望类型的映射
    _SAMPLER_TYPE_MAP: ClassVar[dict[str, tuple[str, Any]]] = {
        "always_on": ("singleton", ALWAYS_ON),
        "always_off": ("singleton", ALWAYS_OFF),
        "traceidratio": ("class", TraceIdRatioBased),
        "parentbased_traceidratio": ("class", ParentBasedTraceIdRatio),
    }

    @given(
        sampler_name=st.sampled_from(sorted(_KNOWN_SAMPLER_NAMES)),
        ratio=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_known_sampler_returns_correct_type(self, sampler_name: str, ratio: float) -> None:
        """对于任意已知采样器名称和合法比例，应返回正确的采样器类型。

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        """
        mock_config = MagicMock()
        mock_config.traces_sampler = sampler_name
        mock_config.traces_sampler_arg = ratio

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        check_kind, expected = self._SAMPLER_TYPE_MAP[sampler_name]
        if check_kind == "singleton":
            assert sampler is expected, (
                f"采样器 '{sampler_name}' 应返回单例 {expected!r}，实际为 {sampler!r}"
            )
        else:
            assert isinstance(sampler, expected), (
                f"采样器 '{sampler_name}' 应返回 {expected.__name__} 实例，"
                f"实际类型为 {type(sampler).__name__}"
            )


# ===================================================================
# Task 2.5: Property 4 - 未知采样器名称默认回退
# ===================================================================
# Feature: opentelemetry-tracing, Property 4: 未知采样器名称默认回退
class TestBuildSamplerFallbackProperty:
    """属性测试：对于任意不属于已知采样器名称集合的字符串，
    ``_build_sampler()`` 应返回 ``ParentBasedTraceIdRatio`` 实例。

    **Validates: Requirements 3.5**
    """

    @given(
        sampler_name=_safe_text.filter(lambda s: s.lower() not in _KNOWN_SAMPLER_NAMES),
        ratio=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_unknown_sampler_falls_back_to_parentbased(
        self, sampler_name: str, ratio: float
    ) -> None:
        """对于任意未知采样器名称，应默认回退到 ParentBasedTraceIdRatio。

        **Validates: Requirements 3.5**
        """
        mock_config = MagicMock()
        mock_config.traces_sampler = sampler_name
        mock_config.traces_sampler_arg = ratio

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            sampler = _build_sampler()

        assert isinstance(sampler, ParentBasedTraceIdRatio), (
            f"未知采样器名称 '{sampler_name}' 应回退到 ParentBasedTraceIdRatio，"
            f"实际类型为 {type(sampler).__name__}"
        )


# ===================================================================
# Task 3.1: _build_exporter() 单元测试
# ===================================================================
class TestBuildExporter:
    """``_build_exporter()`` 单元测试。

    验证空 endpoint 返回 ConsoleSpanExporter，
    非空 endpoint 返回 OTLPSpanExporter 并传递正确的参数。

    需求: 4.1, 4.2, 4.3
    """

    def test_empty_endpoint_returns_console_exporter(self) -> None:
        """验证 exporter_endpoint 为空时返回 ConsoleSpanExporter。"""
        mock_config = MagicMock()
        mock_config.exporter_endpoint = ""

        with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
            exporter = _build_exporter()

        assert isinstance(exporter, ConsoleSpanExporter)

    def test_nonempty_endpoint_returns_otlp_exporter(self) -> None:
        """验证 exporter_endpoint 非空时返回 OTLPSpanExporter，并传递正确参数。"""
        mock_config = MagicMock()
        mock_config.exporter_endpoint = "http://localhost:4317"
        mock_config.exporter_insecure = True

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch(
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
            ) as mock_otlp,
        ):
            mock_otlp.return_value = mock_otlp
            _build_exporter()
            mock_otlp.assert_called_once_with(endpoint="http://localhost:4317", insecure=True)


# ===================================================================
# Task 3.2: Property 5 - 非空 endpoint 产生 OTLP 导出器
# ===================================================================
# Feature: opentelemetry-tracing, Property 5: 非空 endpoint 产生 OTLP 导出器
class TestBuildExporterProperty:
    """属性测试：对于任意非空 exporter_endpoint 字符串和任意 exporter_insecure 布尔值，
    ``_build_exporter()`` 应返回 OTLPSpanExporter 实例，且参数与输入一致。

    **Validates: Requirements 4.2, 4.3**
    """

    @given(
        endpoint=_safe_text,
        insecure=st.booleans(),
    )
    @settings(max_examples=100)
    def test_nonempty_endpoint_produces_otlp_exporter(self, endpoint: str, insecure: bool) -> None:
        """对于任意非空 endpoint 和任意 insecure 布尔值，应调用 OTLPSpanExporter 构造。

        **Validates: Requirements 4.2, 4.3**
        """
        mock_config = MagicMock()
        mock_config.exporter_endpoint = endpoint
        mock_config.exporter_insecure = insecure

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch(
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter"
            ) as mock_otlp_cls,
        ):
            mock_instance = MagicMock()
            mock_otlp_cls.return_value = mock_instance
            _build_exporter()
            mock_otlp_cls.assert_called_once_with(endpoint=endpoint, insecure=insecure)


# ===================================================================
# Task 3.3: init_telemetry() 和 shutdown_telemetry() 单元测试
# ===================================================================
class TestInitShutdownTelemetry:
    """``init_telemetry()`` 和 ``shutdown_telemetry()`` 单元测试。

    验证 SDK 初始化和关闭的生命周期管理逻辑：
    - enabled=False 时跳过初始化
    - enabled=True 时创建 TracerProvider 并设为全局 provider
    - TracerProvider 使用 BatchSpanProcessor
    - shutdown 调用 provider.shutdown()
    - shutdown 在 provider 为 None 时安全返回

    需求: 2.1, 2.2, 2.4, 2.5, 11.1, 11.2, 11.3
    """

    async def test_init_skips_when_disabled(self) -> None:
        """验证 enabled=False 时 init_telemetry 不创建 TracerProvider。"""
        mock_config = MagicMock()
        mock_config.enabled = False

        # 确保初始状态为 None
        set_tracer_provider(None)

        try:
            with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
                await init_telemetry()

            assert tracer_provider() is None
        finally:
            set_tracer_provider(None)

    async def test_init_creates_tracer_provider_when_enabled(self) -> None:
        """验证 enabled=True 时 init_telemetry 创建 TracerProvider 并设为全局 provider。"""
        mock_config = MagicMock()
        mock_config.enabled = True
        mock_config.service_name = "test"
        mock_config.service_version = "1.0"
        mock_config.environment = "test"
        mock_config.traces_sampler = "always_on"
        mock_config.traces_sampler_arg = 1.0
        mock_config.exporter_endpoint = ""
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        try:
            with (
                patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
                patch("infrastructure.telemetry.otel_setup.trace") as mock_trace,
            ):
                await init_telemetry()

                assert tracer_provider() is not None
                mock_trace.set_tracer_provider.assert_called_once()
        finally:
            provider = tracer_provider()
            if provider is not None:
                provider.shutdown()
                set_tracer_provider(None)

    async def test_init_uses_batch_span_processor(self) -> None:
        """验证 TracerProvider 使用 BatchSpanProcessor 处理 span 数据。"""
        mock_config = MagicMock()
        mock_config.enabled = True
        mock_config.service_name = "test"
        mock_config.service_version = "1.0"
        mock_config.environment = "test"
        mock_config.traces_sampler = "always_on"
        mock_config.traces_sampler_arg = 1.0
        mock_config.exporter_endpoint = ""
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        try:
            with (
                patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
                patch("infrastructure.telemetry.otel_setup.trace"),
                patch("infrastructure.telemetry.otel_setup.BatchSpanProcessor") as mock_bsp,
            ):
                await init_telemetry()

                mock_bsp.assert_called_once()
        finally:
            provider = tracer_provider()
            if provider is not None:
                provider.shutdown()
                set_tracer_provider(None)

    async def test_shutdown_calls_provider_shutdown(self) -> None:
        """验证 shutdown_telemetry 调用 provider.shutdown() 并释放引用。"""
        mock_provider = MagicMock()
        set_tracer_provider(mock_provider)

        try:
            await shutdown_telemetry()

            mock_provider.shutdown.assert_called_once()
            assert tracer_provider() is None
        finally:
            set_tracer_provider(None)

    async def test_shutdown_safe_when_provider_is_none(self) -> None:
        """验证 shutdown_telemetry 在 provider 为 None 时安全返回，不抛出异常。"""
        set_tracer_provider(None)

        # 不应抛出任何异常
        await shutdown_telemetry()

        assert tracer_provider() is None


# ===================================================================
# Task 5.1: 各组件埋点开关的单元测试
# ===================================================================
class TestInstrumentFastAPIApp:
    """``instrument_fastapi_app()`` 单元测试。

    验证 FastAPI 自动埋点的双开关控制逻辑：
    - enabled=True 且 instrument_fastapi=True 时执行埋点
    - enabled=False 或 instrument_fastapi=False 时跳过

    需求: 5.1, 5.4
    """

    def test_instruments_when_both_enabled(self) -> None:
        """验证 enabled=True 且 instrument_fastapi=True 时执行 FastAPI 埋点。"""
        mock_config = MagicMock()
        mock_config.enabled = True
        mock_config.instrument_fastapi = True
        mock_app = MagicMock()

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor") as mock_instrumentor,
        ):
            instrument_fastapi_app(mock_app)
            mock_instrumentor.instrument_app.assert_called_once_with(mock_app)

    def test_skips_when_disabled(self) -> None:
        """验证 enabled=False 时跳过 FastAPI 埋点。"""
        mock_config = MagicMock()
        mock_config.enabled = False
        mock_config.instrument_fastapi = True
        mock_app = MagicMock()

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor") as mock_instrumentor,
        ):
            instrument_fastapi_app(mock_app)
            mock_instrumentor.instrument_app.assert_not_called()

    def test_skips_when_instrument_fastapi_false(self) -> None:
        """验证 enabled=True 但 instrument_fastapi=False 时跳过 FastAPI 埋点。"""
        mock_config = MagicMock()
        mock_config.enabled = True
        mock_config.instrument_fastapi = False
        mock_app = MagicMock()

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor") as mock_instrumentor,
        ):
            instrument_fastapi_app(mock_app)
            mock_instrumentor.instrument_app.assert_not_called()


class TestInstrumentComponents:
    """``_instrument_components()`` 单元测试。

    验证各组件开关独立控制对应的自动埋点行为：
    - 开关为 True 时，对应 Instrumentor 的 instrument() 被调用
    - 开关为 False 时，对应 Instrumentor 不被调用

    需求: 5.4, 6.1, 6.3, 7.1, 7.3, 8.1, 8.3, 9.1, 9.4
    """

    def test_httpx_enabled(self) -> None:
        """验证 instrument_httpx=True 时 HTTPXClientInstrumentor 被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = True
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.httpx.HTTPXClientInstrumentor") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            _instrument_components()
            mock_instance.instrument.assert_called()

    def test_httpx_disabled(self) -> None:
        """验证 instrument_httpx=False 时 HTTPXClientInstrumentor 不被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.httpx.HTTPXClientInstrumentor") as mock_cls,
        ):
            _instrument_components()
            mock_cls.assert_not_called()

    def test_redis_enabled(self) -> None:
        """验证 instrument_redis=True 时 RedisInstrumentor 被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = True
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.redis.RedisInstrumentor") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            _instrument_components()
            mock_instance.instrument.assert_called()

    def test_redis_disabled(self) -> None:
        """验证 instrument_redis=False 时 RedisInstrumentor 不被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.redis.RedisInstrumentor") as mock_cls,
        ):
            _instrument_components()
            mock_cls.assert_not_called()

    def test_sqlalchemy_enabled(self) -> None:
        """验证 instrument_sqlalchemy=True 时 SQLAlchemyInstrumentor 被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = True
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.sqlalchemy.SQLAlchemyInstrumentor") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            _instrument_components()
            mock_instance.instrument.assert_called()

    def test_sqlalchemy_disabled(self) -> None:
        """验证 instrument_sqlalchemy=False 时 SQLAlchemyInstrumentor 不被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.sqlalchemy.SQLAlchemyInstrumentor") as mock_cls,
        ):
            _instrument_components()
            mock_cls.assert_not_called()

    def test_logging_enabled(self) -> None:
        """验证 log_correlation=True 时 LoggingInstrumentor 被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = True

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.logging.LoggingInstrumentor") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            _instrument_components()
            mock_instance.instrument.assert_called()

    def test_logging_disabled(self) -> None:
        """验证 log_correlation=False 时 LoggingInstrumentor 不被调用。"""
        mock_config = MagicMock()
        mock_config.instrument_httpx = False
        mock_config.instrument_redis = False
        mock_config.instrument_sqlalchemy = False
        mock_config.log_correlation = False

        with (
            patch("infrastructure.telemetry.otel_setup.otel_config", mock_config),
            patch("opentelemetry.instrumentation.logging.LoggingInstrumentor") as mock_cls,
        ):
            _instrument_components()
            mock_cls.assert_not_called()


# ===================================================================
# Task 5.2: Property 6 - 组件埋点故障隔离
# ===================================================================
# Feature: opentelemetry-tracing, Property 6: 组件埋点故障隔离

# 组件名称列表和对应的 patch 目标
_COMPONENTS = ["httpx", "redis", "sqlalchemy", "logging"]
_COMPONENT_PATCH_TARGETS = {
    "httpx": "opentelemetry.instrumentation.httpx.HTTPXClientInstrumentor",
    "redis": "opentelemetry.instrumentation.redis.RedisInstrumentor",
    "sqlalchemy": "opentelemetry.instrumentation.sqlalchemy.SQLAlchemyInstrumentor",
    "logging": "opentelemetry.instrumentation.logging.LoggingInstrumentor",
}


class TestFaultIsolationProperty:
    """属性测试：组件埋点故障隔离。

    对于任意组件子集（httpx、Redis、SQLAlchemy、logging 中的任意组合），
    若该子集中的组件在埋点过程中抛出异常，则不在该子集中的其他组件
    仍应被正常埋点。

    **Validates: Requirements 10.2, 5.4, 6.3, 7.3, 8.3, 9.4**
    """

    @given(
        failing_components=st.lists(
            st.sampled_from(_COMPONENTS), unique=True, min_size=0, max_size=4
        ),
    )
    @settings(max_examples=100)
    def test_fault_isolation(self, failing_components: list[str]) -> None:
        """对于任意失败组件子集，非失败组件仍应被正常埋点。

        **Validates: Requirements 10.2, 5.4, 6.3, 7.3, 8.3, 9.4**
        """
        mock_config = MagicMock()
        mock_config.instrument_httpx = True
        mock_config.instrument_redis = True
        mock_config.instrument_sqlalchemy = True
        mock_config.log_correlation = True

        patches: dict[str, tuple[Any, MagicMock, MagicMock]] = {}
        for comp, target in _COMPONENT_PATCH_TARGETS.items():
            p = patch(target)
            mock_cls = p.start()
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            if comp in failing_components:
                mock_instance.instrument.side_effect = RuntimeError(f"{comp} failed")
            patches[comp] = (p, mock_cls, mock_instance)

        try:
            with patch("infrastructure.telemetry.otel_setup.otel_config", mock_config):
                _instrument_components()

            # 验证非失败组件仍被正常埋点
            for comp in _COMPONENTS:
                _, mock_cls, mock_instance = patches[comp]
                if comp not in failing_components:
                    mock_instance.instrument.assert_called()
        finally:
            for p, _, _ in patches.values():
                p.stop()


# ===================================================================
# Task 6.1: 验证 main.py 日志格式包含 trace_id/span_id 占位符
# ===================================================================


class TestLogCorrelation:
    """日志关联测试。

    验证 main.py 的日志格式包含 otelTraceID 和 otelSpanID 占位符，
    以及 LoggingInstrumentor 启用但无活跃 span 时日志中这些字段输出为默认值。

    需求: 9.1, 9.2, 9.3
    """

    def test_main_log_format_contains_trace_placeholders(self) -> None:
        """验证 main.py 的日志格式包含 otelTraceID 和 otelSpanID 占位符。

        读取 main.py 文件内容，确认 logging.basicConfig 的 format 字符串
        中包含 ``%(otelTraceID)s`` 和 ``%(otelSpanID)s``。

        **需求: 9.2**
        """
        main_py = Path(__file__).resolve().parent.parent.parent.parent / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "%(otelTraceID)s" in content, "main.py 的日志格式应包含 %(otelTraceID)s 占位符"
        assert "%(otelSpanID)s" in content, "main.py 的日志格式应包含 %(otelSpanID)s 占位符"

    def test_log_output_with_logging_instrumentation_no_active_span(self) -> None:
        """验证 LoggingInstrumentor 启用但无活跃 span 时，
        日志中 otelTraceID/otelSpanID 输出为默认值。

        启用 LoggingInstrumentor（不设置 logging format，仅注入字段），
        使用与 main.py 相同的日志格式创建 logger 并输出一条日志，
        验证日志正常输出且 otelTraceID/otelSpanID 为默认值（"0"）。

        **需求: 9.1, 9.3**
        """
        logging_module = importlib.import_module("opentelemetry.instrumentation.logging")
        instrumentor_type = cast(
            type[_LoggingInstrumentor], vars(logging_module)["LoggingInstrumentor"]
        )
        instrumentor = instrumentor_type()
        instrumentor.instrument(set_logging_format=True)

        try:
            log_format = (
                "%(levelname)s [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"
            )
            test_logger = logging.getLogger("test_log_correlation_default")
            test_logger.setLevel(logging.INFO)
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(logging.Formatter(log_format))
            test_logger.addHandler(handler)

            try:
                test_logger.info("hello")
                output = stream.getvalue()
                assert "hello" in output, "日志输出应包含消息内容"
                # 无活跃 span 时，otelTraceID 和 otelSpanID 应为 "0"
                assert "trace_id=0" in output, (
                    f"无活跃 span 时 otelTraceID 应为 '0'，实际输出: {output}"
                )
                assert "span_id=0" in output, (
                    f"无活跃 span 时 otelSpanID 应为 '0'，实际输出: {output}"
                )
            finally:
                test_logger.removeHandler(handler)
        finally:
            instrumentor.uninstrument()
