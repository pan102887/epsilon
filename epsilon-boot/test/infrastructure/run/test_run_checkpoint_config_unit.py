"""Run checkpoint 配置单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.configuration import ConfigurationError, PropertiesFileSettingsSource
from infrastructure.run.run_config import RunRuntimeConfig


def test_checkpoint_config_defaults_convert_to_retention_policy() -> None:
    config = RunRuntimeConfig()

    assert config.checkpoint_enabled is True
    assert config.checkpoint_auto_recovery_enabled is True
    assert config.checkpoint_max_recovery_attempts == 3
    assert config.checkpoint_max_count == 200
    assert config.checkpoint_ttl_seconds == 604800
    assert config.checkpoint_max_payload_bytes == 262144
    assert config.checkpoint_tool_ledger_max_count == 1000

    policy = config.to_checkpoint_retention_policy()

    assert policy.max_checkpoint_count == 200
    assert policy.ttl_seconds == 604800
    assert policy.max_payload_bytes == 262144
    assert policy.max_tool_ledger_count == 1000


def test_checkpoint_config_loads_from_config_properties(tmp_path: Path) -> None:
    props_file = tmp_path / "config.properties"
    props_file.write_text(
        "\n".join(
            [
                "RUN_CHECKPOINT_ENABLED=false",
                "RUN_CHECKPOINT_AUTO_RECOVERY_ENABLED=false",
                "RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS=5",
                "RUN_CHECKPOINT_MAX_COUNT=300",
                "RUN_CHECKPOINT_TTL_SECONDS=120",
                "RUN_CHECKPOINT_MAX_PAYLOAD_BYTES=4096",
                "RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT=50",
            ]
        ),
        encoding="utf-8",
    )

    class _ConfigFromProperties(RunRuntimeConfig):
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

    assert config.checkpoint_enabled is False
    assert config.checkpoint_auto_recovery_enabled is False
    assert config.checkpoint_max_recovery_attempts == 5
    assert config.checkpoint_max_count == 300
    assert config.checkpoint_ttl_seconds == 120
    assert config.checkpoint_max_payload_bytes == 4096
    assert config.checkpoint_tool_ledger_max_count == 50


@pytest.mark.parametrize(
    ("field_name", "value", "config_key"),
    [
        ("checkpoint_max_recovery_attempts", -1, "RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS"),
        ("checkpoint_max_count", 0, "RUN_CHECKPOINT_MAX_COUNT"),
        ("checkpoint_ttl_seconds", 0, "RUN_CHECKPOINT_TTL_SECONDS"),
        ("checkpoint_max_payload_bytes", 0, "RUN_CHECKPOINT_MAX_PAYLOAD_BYTES"),
        ("checkpoint_tool_ledger_max_count", 0, "RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT"),
    ],
)
def test_checkpoint_config_rejects_invalid_values(
    field_name: str,
    value: int,
    config_key: str,
) -> None:
    with pytest.raises(ConfigurationError, match=config_key):
        RunRuntimeConfig(**{field_name: value})


def test_config_properties_declares_checkpoint_keys() -> None:
    content = Path("config.properties").read_text(encoding="utf-8")

    for key in [
        "RUN_CHECKPOINT_ENABLED",
        "RUN_CHECKPOINT_AUTO_RECOVERY_ENABLED",
        "RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS",
        "RUN_CHECKPOINT_MAX_COUNT",
        "RUN_CHECKPOINT_TTL_SECONDS",
        "RUN_CHECKPOINT_MAX_PAYLOAD_BYTES",
        "RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT",
    ]:
        assert key in content
