"""PropertiesFileSettingsSource 单元测试。

验证从 config.properties 文件加载配置的自定义设置源：
- properties 文件解析（键值对、注释、空行、冒号分隔符）
- 键名到环境变量风格的转换（点号替换、大写化）
- env_prefix 匹配与字段绑定
- 优先级：环境变量 > config.properties > .env > 默认值
- 文件不存在时的降级行为
"""

from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from common.configuration.configuration_utils import (
    PropertiesBaseSettings,
    PropertiesFileSettingsSource,
    _parse_properties_file,
)

# ── 解析器测试 ──


class TestParsePropertiesFile:
    """_parse_properties_file 解析器测试。"""

    def test_basic_key_value(self, tmp_path):
        """验证基本的 key=value 解析。"""
        f = tmp_path / "test.properties"
        f.write_text("server.host=127.0.0.1\nserver.port=8080\n", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {"server.host": "127.0.0.1", "server.port": "8080"}

    def test_colon_separator(self, tmp_path):
        """验证冒号分隔符 key:value 解析。"""
        f = tmp_path / "test.properties"
        f.write_text("app.name:my-app\n", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {"app.name": "my-app"}

    def test_comments_ignored(self, tmp_path):
        """验证注释行被忽略。"""
        f = tmp_path / "test.properties"
        f.write_text("# comment\n! another comment\nkey=value\n", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {"key": "value"}

    def test_empty_lines_ignored(self, tmp_path):
        """验证空行被忽略。"""
        f = tmp_path / "test.properties"
        f.write_text("\n\nkey=value\n\n", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {"key": "value"}

    def test_whitespace_trimmed(self, tmp_path):
        """验证键值前后空格被去除。"""
        f = tmp_path / "test.properties"
        f.write_text("  spaced.key  =  spaced value  \n", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {"spaced.key": "spaced value"}

    def test_empty_value(self, tmp_path):
        """验证空值解析。"""
        f = tmp_path / "test.properties"
        f.write_text("empty.key=\n", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {"empty.key": ""}

    def test_file_not_exist(self, tmp_path):
        """验证文件不存在时返回空字典。"""
        result = _parse_properties_file(tmp_path / "nonexistent.properties")
        assert result == {}

    def test_empty_file(self, tmp_path):
        """验证空文件返回空字典。"""
        f = tmp_path / "test.properties"
        f.write_text("", encoding="utf-8")
        result = _parse_properties_file(f)
        assert result == {}


# ── 设置源集成测试 ──


class TestPropertiesFileSettingsSource:
    """PropertiesFileSettingsSource 集成测试。"""

    def _make_properties_file(self, tmp_path, content: str) -> Path:
        """创建临时 properties 文件。"""
        f = tmp_path / "config.properties"
        f.write_text(content, encoding="utf-8")
        return f

    def test_simple_prefix_matching(self, tmp_path):
        """验证简单前缀匹配：redis.host → REDIS_HOST → host 字段。"""
        props_file = self._make_properties_file(tmp_path, "redis.host=10.0.0.1\nredis.port=6380\n")

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="REDIS_")
            host: str = "localhost"
            port: int = 6379

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.host == "10.0.0.1"
        assert config.port == 6380

    def test_nested_key_matching(self, tmp_path):
        """验证嵌套键匹配：model.claude.enabled → MODEL_CLAUDE_ENABLED → claude_enabled 字段。"""
        props_file = self._make_properties_file(
            tmp_path, "model.claude.enabled=true\nmodel.provider=zhipu\n"
        )

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="MODEL_")
            provider: str = "openai"
            claude_enabled: bool = False

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.provider == "zhipu"
        assert config.claude_enabled is True

    def test_logging_request_prefix(self, tmp_path):
        """验证多级前缀：logging.request.enabled → LOGGING_REQUEST_ENABLED。"""
        props_file = self._make_properties_file(
            tmp_path, "logging.request.enabled=false\nlogging.request.max_body_log_size=1024\n"
        )

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="LOGGING_REQUEST_")
            enabled: bool = True
            max_body_log_size: int = 2048

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.enabled is False
        assert config.max_body_log_size == 1024

    def test_env_overrides_properties(self, tmp_path, monkeypatch):
        """验证环境变量优先级高于 config.properties。"""
        props_file = self._make_properties_file(tmp_path, "test.src.host=from-properties\n")
        monkeypatch.setenv("TEST_SRC_HOST", "from-env")

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="TEST_SRC_")
            host: str = "default"

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    env_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.host == "from-env"

    def test_properties_overrides_default(self, tmp_path):
        """验证 config.properties 优先级高于字段默认值。"""
        props_file = self._make_properties_file(tmp_path, "test.def.port=9999\n")

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="TEST_DEF_")
            port: int = 8080

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.port == 9999

    def test_missing_properties_file_uses_defaults(self, tmp_path):
        """验证 properties 文件不存在时回退到默认值。"""
        nonexistent = tmp_path / "nonexistent.properties"

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="TEST_MISS_")
            host: str = "fallback"

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, nonexistent),
                )

        config = _TestConfig()
        assert config.host == "fallback"

    def test_type_conversion(self, tmp_path):
        """验证 properties 文件中的字符串值被正确转换为目标类型。"""
        props_file = self._make_properties_file(
            tmp_path, "test.conv.port=3306\ntest.conv.debug=true\ntest.conv.ratio=0.5\n"
        )

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="TEST_CONV_")
            port: int = 0
            debug: bool = False
            ratio: float = 1.0

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.port == 3306
        assert config.debug is True
        assert config.ratio == 0.5

    def test_unmatched_keys_ignored(self, tmp_path):
        """验证不匹配当前配置类前缀的键被忽略。"""
        props_file = self._make_properties_file(
            tmp_path, "redis.host=redis-host\ngateway.base_url=http://gw\n"
        )

        class _TestConfig(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="REDIS_")
            host: str = "default"

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    PropertiesFileSettingsSource(settings_cls, props_file),
                )

        config = _TestConfig()
        assert config.host == "redis-host"


@pytest.mark.real_config
class TestPropertiesBaseSettingsIntegration:
    """验证 PropertiesBaseSettings 基类自动集成 config.properties 源。

    本类用例意在验证「从仓库内真实 config.properties 加载」这一集成行为，
    故标注 ``@pytest.mark.real_config`` 退出全局 isolate_config_sources 隔离夹具，
    直接读取真实配置文件。
    """

    def test_loads_from_project_config_properties(self):
        """验证能从项目根目录的 config.properties 加载 redis 配置。

        此测试依赖项目中实际存在的 config.properties 文件。
        """
        from infrastructure.redis.redis_config import RedisConfig

        config = RedisConfig()
        # config.properties 中 redis.host=localhost
        assert config.host == "localhost"
        assert config.port == 6379

    def test_loads_model_config_from_properties(self):
        """验证能从 config.properties 加载模型路由配置和提供商配置。

        OpenAIProviderConfig 已重构为模板类，不再直接实例化，
        需通过 ``create_provider_config`` 工厂函数指定 env_prefix。
        """
        from infrastructure.model_access.provider_config import (
            create_provider_config,
        )
        from infrastructure.model_access.router_config import RouterConfig

        router_cfg = RouterConfig()
        # config.properties 中 MODEL_ROUTER_DEFAULT_PROVIDER=qwen
        assert router_cfg.default_provider == "qwen"

        cliproxy_cfg = create_provider_config("MODEL_CLIPROXY_")
        # config.properties 中 MODEL_CLIPROXY_PROVIDER_NAME=cliproxy
        assert cliproxy_cfg.provider_name == "cliproxy"
