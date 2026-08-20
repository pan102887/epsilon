"""Task Agent 配置单元测试。

验证 ``TaskAgentConfig`` 通过项目统一配置基类表达 ``TASK_AGENT_*`` 配置项。
"""

from common.configuration.configuration_utils import PropertiesFileSettingsSource
from infrastructure.task.task_config import (
    UNLIMITED_MAX_ROUNDS_SENTINEL,
    TaskAgentConfig,
)


class TestTaskAgentConfig:
    """Task Agent 配置默认值与覆盖值测试。"""

    def test_max_rounds_field_default_is_10(self) -> None:
        """验证 ``max_rounds`` 字段默认值为 10（正数直接生效，不触发归一化）。"""
        config = TaskAgentConfig(max_rounds=10)
        assert config.max_rounds == 10

    def test_max_rounds_zero_normalizes_to_unlimited(self) -> None:
        """验证 ``TASK_AGENT_MAX_ROUNDS=0``（不限制）归一化为哨兵值。"""
        config = TaskAgentConfig(max_rounds=0)
        assert config.max_rounds == UNLIMITED_MAX_ROUNDS_SENTINEL

    def test_max_rounds_negative_normalizes_to_unlimited(self) -> None:
        """验证 ``max_rounds`` 为负数（不限制）归一化为哨兵值。"""
        config = TaskAgentConfig(max_rounds=-1)
        assert config.max_rounds == UNLIMITED_MAX_ROUNDS_SENTINEL

    def test_max_rounds_accepts_configured_value(self) -> None:
        """验证配置系统能解析 ``TASK_AGENT_MAX_ROUNDS`` 对应的整数值。"""
        config = TaskAgentConfig(max_rounds="7")
        assert config.max_rounds == 7

    def test_max_rounds_accepts_environment_override(self, monkeypatch) -> None:
        """验证环境变量覆盖仍由 TaskAgentConfig 统一处理。"""
        monkeypatch.setenv("TASK_AGENT_MAX_ROUNDS", "12")

        config = TaskAgentConfig()

        assert config.max_rounds == 12

    def test_max_rounds_loads_from_config_properties(self, tmp_path) -> None:
        """验证 TaskAgentConfig 能通过 config.properties source 读取配置。"""
        props_file = tmp_path / "config.properties"
        props_file.write_text("TASK_AGENT_MAX_ROUNDS=6\n", encoding="utf-8")

        class _ConfigFromProperties(TaskAgentConfig):
            """仅使用临时 config.properties 源的测试配置类。"""

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
                    PropertiesFileSettingsSource(
                        settings_cls,
                        properties_path=props_file,
                    ),
                )

        config = _ConfigFromProperties()

        assert config.max_rounds == 6
