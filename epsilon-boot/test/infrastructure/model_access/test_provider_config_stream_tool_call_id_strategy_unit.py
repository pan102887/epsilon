"""ProviderConfig 流式工具调用 id 策略配置单元测试。

覆盖 ``stream_tool_call_id_strategy`` 的默认值、config.properties 读取、
环境变量覆盖，以及非法值暂由配置层透传、后续交给模型适配器 fail-fast 的
边界。该配置属于基础设施层 Provider 适配策略，不进入 domain。
"""

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from common.configuration import PropertiesFileSettingsSource
from infrastructure.model_access.provider_config import ProviderConfig


def test_default_strategy_is_recover() -> None:
    """未显式配置时默认采用 recover，优先修复兼容 Provider 的运行故障。"""
    config = ProviderConfig()

    assert config.stream_tool_call_id_strategy == "recover"


def test_config_reads_strategy_from_properties_file(tmp_path: Path) -> None:
    """验证 ``MODEL_QWEN_STREAM_TOOL_CALL_ID_STRATEGY`` 可从 properties 读取。"""
    props_file = tmp_path / "config.properties"
    props_file.write_text(
        "MODEL_QWEN_PROVIDER_NAME=qwen\nMODEL_QWEN_STREAM_TOOL_CALL_ID_STRATEGY=raise\n",
        encoding="utf-8",
    )

    class _QwenConfig(ProviderConfig):
        """仅使用临时 properties 文件源的 Qwen Provider 配置。"""

        model_config = SettingsConfigDict(
            env_prefix="MODEL_QWEN_",
            extra="ignore",
            frozen=True,
        )

        @classmethod
        def settings_customise_sources(
            cls: type[BaseSettings],
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

    config = _QwenConfig()

    assert config.provider_name == "qwen"
    assert config.stream_tool_call_id_strategy == "raise"


def test_env_overrides_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证环境变量仍可覆盖默认策略。"""
    monkeypatch.setenv("MODEL_OPENAI_STREAM_TOOL_CALL_ID_STRATEGY", "raise")

    class _OpenAIConfig(ProviderConfig):
        """使用 OpenAI Provider 前缀读取环境变量的测试配置。"""

        model_config = SettingsConfigDict(
            env_prefix="MODEL_OPENAI_",
            extra="ignore",
            frozen=True,
        )

    config = _OpenAIConfig()

    assert config.stream_tool_call_id_strategy == "raise"


def test_invalid_strategy_is_carried_for_adapter_fail_fast() -> None:
    """配置层只透传策略字符串；非法值由 adapter 使用点统一 fail-fast。"""
    config = ProviderConfig(stream_tool_call_id_strategy="invalid")

    assert config.stream_tool_call_id_strategy == "invalid"
