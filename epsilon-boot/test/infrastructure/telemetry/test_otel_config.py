"""OtelConfig 配置类单元测试和属性测试。

验证 OtelConfig 的配置加载行为：
- 所有字段的默认值正确性
- 通过 OTEL_ 前缀环境变量覆盖各字段值
- 环境变量加载往返一致性（属性测试）
"""

import os
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from infrastructure.telemetry.otel_config import OtelConfig


def _make_isolated_config(**env_overrides: Any) -> OtelConfig:
    """创建隔离的 OtelConfig 实例，仅使用 init_settings 和 env_settings。

    通过覆盖 settings_customise_sources 排除 .env 和 config.properties 文件源，
    确保测试不受外部文件影响。

    Args:
        **env_overrides: 通过构造参数传入的字段覆盖值。

    Returns:
        隔离的 OtelConfig 实例。
    """

    class _IsolatedOtelConfig(OtelConfig):
        """仅从构造参数和环境变量加载配置的隔离子类。"""

        @classmethod
        def settings_customise_sources(
            cls: type[BaseSettings],
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            return (init_settings, env_settings)

    return _IsolatedOtelConfig(**env_overrides)


# ── 默认值测试 ──


class TestOtelConfigDefaults:
    """OtelConfig 默认值测试，验证所有字段在无外部配置时的默认值。"""

    def test_enabled_default(self):
        """验证 enabled 默认值为 False。"""
        config = _make_isolated_config()
        assert config.enabled is False

    def test_service_name_default(self):
        """验证 service_name 默认值为 'epsilon-boot'。"""
        config = _make_isolated_config()
        assert config.service_name == "epsilon-boot"

    def test_service_version_default(self):
        """验证 service_version 默认值为 '0.1.0'。"""
        config = _make_isolated_config()
        assert config.service_version == "0.1.0"

    def test_environment_default(self):
        """验证 environment 默认值为 'development'。"""
        config = _make_isolated_config()
        assert config.environment == "development"

    def test_exporter_endpoint_default(self):
        """验证 exporter_endpoint 默认值为空字符串。"""
        config = _make_isolated_config()
        assert config.exporter_endpoint == ""

    def test_exporter_insecure_default(self):
        """验证 exporter_insecure 默认值为 True。"""
        config = _make_isolated_config()
        assert config.exporter_insecure is True

    def test_traces_sampler_default(self):
        """验证 traces_sampler 默认值为 'parentbased_traceidratio'。"""
        config = _make_isolated_config()
        assert config.traces_sampler == "parentbased_traceidratio"

    def test_traces_sampler_arg_default(self):
        """验证 traces_sampler_arg 默认值为 1.0。"""
        config = _make_isolated_config()
        assert config.traces_sampler_arg == 1.0

    def test_log_correlation_default(self):
        """验证 log_correlation 默认值为 True。"""
        config = _make_isolated_config()
        assert config.log_correlation is True

    def test_instrument_fastapi_default(self):
        """验证 instrument_fastapi 默认值为 True。"""
        config = _make_isolated_config()
        assert config.instrument_fastapi is True

    def test_instrument_httpx_default(self):
        """验证 instrument_httpx 默认值为 True。"""
        config = _make_isolated_config()
        assert config.instrument_httpx is True

    def test_instrument_redis_default(self):
        """验证 instrument_redis 默认值为 True。"""
        config = _make_isolated_config()
        assert config.instrument_redis is True

    def test_instrument_sqlalchemy_default(self):
        """验证 instrument_sqlalchemy 默认值为 True。"""
        config = _make_isolated_config()
        assert config.instrument_sqlalchemy is True


# ── 环境变量覆盖测试 ──


class TestOtelConfigEnvOverride:
    """OtelConfig 环境变量覆盖测试，验证通过 OTEL_ 前缀环境变量覆盖各字段。"""

    def test_override_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_ENABLED 环境变量覆盖 enabled 字段。"""
        monkeypatch.setenv("OTEL_ENABLED", "true")
        config = _make_isolated_config()
        assert config.enabled is True

    def test_override_service_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_SERVICE_NAME 环境变量覆盖 service_name 字段。"""
        monkeypatch.setenv("OTEL_SERVICE_NAME", "my-custom-service")
        config = _make_isolated_config()
        assert config.service_name == "my-custom-service"

    def test_override_service_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_SERVICE_VERSION 环境变量覆盖 service_version 字段。"""
        monkeypatch.setenv("OTEL_SERVICE_VERSION", "2.0.0")
        config = _make_isolated_config()
        assert config.service_version == "2.0.0"

    def test_override_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_ENVIRONMENT 环境变量覆盖 environment 字段。"""
        monkeypatch.setenv("OTEL_ENVIRONMENT", "production")
        config = _make_isolated_config()
        assert config.environment == "production"

    def test_override_exporter_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_EXPORTER_ENDPOINT 环境变量覆盖 exporter_endpoint 字段。"""
        monkeypatch.setenv("OTEL_EXPORTER_ENDPOINT", "http://localhost:4317")
        config = _make_isolated_config()
        assert config.exporter_endpoint == "http://localhost:4317"

    def test_override_exporter_insecure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_EXPORTER_INSECURE 环境变量覆盖 exporter_insecure 字段。"""
        monkeypatch.setenv("OTEL_EXPORTER_INSECURE", "false")
        config = _make_isolated_config()
        assert config.exporter_insecure is False

    def test_override_traces_sampler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_TRACES_SAMPLER 环境变量覆盖 traces_sampler 字段。"""
        monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
        config = _make_isolated_config()
        assert config.traces_sampler == "always_on"

    def test_override_traces_sampler_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_TRACES_SAMPLER_ARG 环境变量覆盖 traces_sampler_arg 字段。"""
        monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.5")
        config = _make_isolated_config()
        assert config.traces_sampler_arg == 0.5

    def test_override_log_correlation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_LOG_CORRELATION 环境变量覆盖 log_correlation 字段。"""
        monkeypatch.setenv("OTEL_LOG_CORRELATION", "false")
        config = _make_isolated_config()
        assert config.log_correlation is False

    def test_override_instrument_fastapi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_INSTRUMENT_FASTAPI 环境变量覆盖 instrument_fastapi 字段。"""
        monkeypatch.setenv("OTEL_INSTRUMENT_FASTAPI", "false")
        config = _make_isolated_config()
        assert config.instrument_fastapi is False

    def test_override_instrument_httpx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_INSTRUMENT_HTTPX 环境变量覆盖 instrument_httpx 字段。"""
        monkeypatch.setenv("OTEL_INSTRUMENT_HTTPX", "false")
        config = _make_isolated_config()
        assert config.instrument_httpx is False

    def test_override_instrument_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_INSTRUMENT_REDIS 环境变量覆盖 instrument_redis 字段。"""
        monkeypatch.setenv("OTEL_INSTRUMENT_REDIS", "false")
        config = _make_isolated_config()
        assert config.instrument_redis is False

    def test_override_instrument_sqlalchemy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证通过 OTEL_INSTRUMENT_SQLALCHEMY 环境变量覆盖 instrument_sqlalchemy 字段。"""
        monkeypatch.setenv("OTEL_INSTRUMENT_SQLALCHEMY", "false")
        config = _make_isolated_config()
        assert config.instrument_sqlalchemy is False


# ── 属性测试：配置环境变量加载往返一致性 ──


# Feature: opentelemetry-tracing, Property 1: 配置环境变量加载往返一致性

# 字符串字段列表
_STRING_FIELDS = [
    "service_name",
    "service_version",
    "environment",
    "exporter_endpoint",
    "traces_sampler",
]

# 布尔字段列表
_BOOL_FIELDS = [
    "enabled",
    "exporter_insecure",
    "log_correlation",
    "instrument_fastapi",
    "instrument_httpx",
    "instrument_redis",
    "instrument_sqlalchemy",
]

# 安全的字符串策略：非空、无 NUL 字符、无前后空格
_safe_text = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
).filter(lambda s: s.strip() == s and len(s.strip()) > 0)


class TestOtelConfigEnvRoundTrip:
    """属性测试：配置环境变量加载往返一致性。

    验证对于任意合法的配置字段名和对应的字符串值，
    设置为 OTEL_ 前缀环境变量后创建 OtelConfig 实例，
    读取该字段值应与设置值（经类型转换后）一致。

    **Validates: Requirements 1.1**
    """

    @given(
        field_name=st.sampled_from(_STRING_FIELDS),
        value=_safe_text,
    )
    @settings(max_examples=100)
    def test_string_field_round_trip(self, field_name: str, value: str):
        """验证字符串字段的环境变量往返一致性。

        对于任意字符串字段和合法字符串值，通过 OTEL_ 前缀环境变量设置后，
        OtelConfig 实例中该字段的值应与设置值完全一致。
        """
        env_var = f"OTEL_{field_name.upper()}"
        original = os.environ.get(env_var)
        try:
            os.environ[env_var] = value
            config = _make_isolated_config()
            assert getattr(config, field_name) == value
        finally:
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original

    @given(
        field_name=st.sampled_from(_BOOL_FIELDS),
        value=st.booleans(),
    )
    @settings(max_examples=100)
    def test_bool_field_round_trip(self, field_name: str, value: bool):
        """验证布尔字段的环境变量往返一致性。

        对于任意布尔字段和布尔值，通过 OTEL_ 前缀环境变量设置后，
        OtelConfig 实例中该字段的值应与设置值一致。
        """
        env_var = f"OTEL_{field_name.upper()}"
        original = os.environ.get(env_var)
        try:
            os.environ[env_var] = str(value).lower()
            config = _make_isolated_config()
            assert getattr(config, field_name) is value
        finally:
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original

    @given(
        value=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_float_field_round_trip(self, value: float):
        """验证浮点字段 traces_sampler_arg 的环境变量往返一致性。

        对于任意合法采样比例值（0.0~1.0），通过 OTEL_TRACES_SAMPLER_ARG
        环境变量设置后，OtelConfig 实例中该字段的值应与设置值一致。
        """
        env_var = "OTEL_TRACES_SAMPLER_ARG"
        original = os.environ.get(env_var)
        try:
            os.environ[env_var] = str(value)
            config = _make_isolated_config()
            assert config.traces_sampler_arg == pytest.approx(value)
        finally:
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original
