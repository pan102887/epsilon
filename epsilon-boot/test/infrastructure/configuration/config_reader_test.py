"""pydantic-settings 配置加载测试。

验证基于 pydantic-settings 的配置系统核心功能：
- 从环境变量加载配置
- 默认值回退
- 类型自动转换（str、int、float、bool）
- env_prefix 前缀隔离
- ConfigurationError 异常兼容性
"""

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings

# ── 测试用配置类 ──


class _ServerConfig(PropertiesBaseSettings):
    """测试用服务器配置。"""

    model_config = SettingsConfigDict(env_prefix="TEST_SERVER_")

    host: str = "127.0.0.1"
    port: int = 8080
    debug: bool = False
    workers: int = 4
    ratio: float = 0.75


class _EmptyPrefixConfig(PropertiesBaseSettings):
    """无前缀配置，用于测试默认行为。"""

    model_config = SettingsConfigDict(env_prefix="TEST_EMPTY_")

    name: str = "default-app"


# ── 基本功能测试 ──


class TestDefaultValues:
    """默认值测试：未设置环境变量时应返回字段默认值。"""

    def test_string_default(self):
        config = _ServerConfig()
        assert config.host == "127.0.0.1"

    def test_int_default(self):
        config = _ServerConfig()
        assert config.port == 8080

    def test_bool_default(self):
        config = _ServerConfig()
        assert config.debug is False

    def test_float_default(self):
        config = _ServerConfig()
        assert config.ratio == 0.75


class TestEnvOverride:
    """环境变量覆盖测试：环境变量应优先于默认值。"""

    def test_env_overrides_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_HOST", "192.168.1.1")
        config = _ServerConfig()
        assert config.host == "192.168.1.1"

    def test_env_overrides_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_PORT", "9090")
        config = _ServerConfig()
        assert config.port == 9090

    def test_env_overrides_bool_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_DEBUG", "true")
        config = _ServerConfig()
        assert config.debug is True

    def test_env_overrides_bool_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_DEBUG", "false")
        config = _ServerConfig()
        assert config.debug is False

    def test_env_overrides_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_RATIO", "1.5")
        config = _ServerConfig()
        assert config.ratio == 1.5


class TestTypeConversion:
    """类型转换测试：验证 pydantic 自动类型转换。"""

    def test_int_from_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_WORKERS", "16")
        config = _ServerConfig()
        assert config.workers == 16
        assert isinstance(config.workers, int)

    def test_bool_various_true_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("TEST_SERVER_DEBUG", val)
            config = _ServerConfig()
            assert config.debug is True, f"'{val}' should be True"

    def test_bool_various_false_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "False", "FALSE", "0", "no", "off"):
            monkeypatch.setenv("TEST_SERVER_DEBUG", val)
            config = _ServerConfig()
            assert config.debug is False, f"'{val}' should be False"

    def test_invalid_int_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SERVER_PORT", "not_a_number")
        with pytest.raises(ValidationError):
            _ServerConfig()


class TestPrefixIsolation:
    """前缀隔离测试：不同前缀的配置类互不干扰。"""

    def test_different_prefix_no_interference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_SERVER_HOST", "10.0.0.1")
        monkeypatch.setenv("TEST_EMPTY_NAME", "custom-app")

        server = _ServerConfig()
        empty = _EmptyPrefixConfig()

        assert server.host == "10.0.0.1"
        assert empty.name == "custom-app"


class TestConfigurationError:
    """ConfigurationError 异常兼容性测试。"""

    def test_configuration_error_is_exception(self):
        assert issubclass(ConfigurationError, Exception)

    def test_configuration_error_message(self):
        err = ConfigurationError("测试错误")
        assert str(err) == "测试错误"

    def test_configuration_error_can_be_raised_and_caught(self):
        with pytest.raises(ConfigurationError, match="缺少配置"):
            raise ConfigurationError("缺少配置")


class TestImmutability:
    """不可变性测试：pydantic-settings 实例字段值创建后不可修改。"""

    def test_frozen_model_raises_on_assignment(self):
        config = _ServerConfig()
        with pytest.raises(ValidationError):
            config.host = "new-host"
