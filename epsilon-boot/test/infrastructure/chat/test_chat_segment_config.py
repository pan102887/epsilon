"""Chat 分段执行配置测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from common.configuration import ConfigurationError, PropertiesFileSettingsSource
from infrastructure.chat.chat_config import ChatConfig


class TestChatSegmentConfig:
    """验证 ChatConfig 的分段执行配置。"""

    def test_default_segment_policy_is_disabled_with_safe_limits(self) -> None:
        """默认策略关闭自动续跑并使用保守阈值。"""
        policy = ChatConfig().to_segment_policy()

        assert policy.auto_continue_enabled is False
        assert policy.max_continuations == 3
        assert policy.max_total_tokens is None
        assert policy.max_duration_seconds is None
        assert policy.max_consecutive_paused == 2
        assert policy.max_no_progress_segments == 2
        assert policy.max_repeated_tool_calls == 2

    def test_zero_token_and_duration_map_to_unlimited_policy(self) -> None:
        """外部配置的 0 预算映射为领域层无限制。"""
        policy = ChatConfig(
            segment_max_total_tokens=0,
            segment_max_duration_seconds=0,
        ).to_segment_policy()

        assert policy.max_total_tokens is None
        assert policy.max_duration_seconds is None

    def test_loads_segment_policy_from_config_properties(self, tmp_path: Path) -> None:
        """config.properties 中的 CHAT_SEGMENT_* 可被读取并映射为策略。"""
        props_file = tmp_path / "config.properties"
        props_file.write_text(
            "\n".join(
                [
                    "CHAT_SEGMENT_AUTO_CONTINUE_ENABLED=true",
                    "CHAT_SEGMENT_MAX_CONTINUATIONS=5",
                    "CHAT_SEGMENT_MAX_TOTAL_TOKENS=1200",
                    "CHAT_SEGMENT_MAX_DURATION_SECONDS=15.5",
                    "CHAT_SEGMENT_MAX_CONSECUTIVE_PAUSED=4",
                    "CHAT_SEGMENT_MAX_NO_PROGRESS_SEGMENTS=3",
                    "CHAT_SEGMENT_MAX_REPEATED_TOOL_CALLS=6",
                ]
            ),
            encoding="utf-8",
        )

        class _ConfigFromProperties(ChatConfig):
            """仅使用临时 properties 源的 ChatConfig。"""

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                del cls, init_settings, env_settings, dotenv_settings, file_secret_settings
                return (
                    PropertiesFileSettingsSource(
                        settings_cls,
                        properties_path=props_file,
                    ),
                )

        policy = _ConfigFromProperties().to_segment_policy()

        assert policy.auto_continue_enabled is True
        assert policy.max_continuations == 5
        assert policy.max_total_tokens == 1200
        assert policy.max_duration_seconds == 15.5
        assert policy.max_consecutive_paused == 4
        assert policy.max_no_progress_segments == 3
        assert policy.max_repeated_tool_calls == 6

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("segment_max_continuations", -1),
            ("segment_max_total_tokens", -1),
            ("segment_max_duration_seconds", -0.1),
            ("segment_max_consecutive_paused", 0),
            ("segment_max_no_progress_segments", 0),
            ("segment_max_repeated_tool_calls", 0),
        ],
    )
    def test_invalid_segment_config_rejected(
        self,
        field_name: str,
        value: int | float,
    ) -> None:
        """非法分段阈值在配置层 fail-fast。"""
        with pytest.raises(ConfigurationError, match="CHAT_SEGMENT"):
            ChatConfig.model_validate({field_name: value})
