"""后台 Run 运行时配置单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from common.configuration import ConfigurationError, PropertiesFileSettingsSource
from infrastructure.run.run_config import RunRuntimeConfig


class TestRunRuntimeConfig:
    """验证 ``RunRuntimeConfig`` 的默认值、覆盖值与 fail-fast 校验。"""

    def test_default_values_are_safe_and_convert_to_domain_policies(self) -> None:
        """默认配置应提供保守容量边界并可转换为领域策略。"""
        config = RunRuntimeConfig()

        assert config.worker_enabled is True
        assert config.worker_count == 1
        assert config.lease_seconds == 60
        assert config.heartbeat_interval_seconds == 10
        assert config.max_queued_runs == 100
        assert config.max_running_runs == 2
        assert config.event_max_count == 1000
        assert config.event_ttl_seconds == 86400
        assert config.event_stream_wait_seconds == 15.0
        assert config.lost_sweep_interval_seconds == 30
        assert config.guardrail_runtime_convergence_enabled is True

        capacity_policy = config.to_capacity_policy()
        retention_policy = config.to_event_retention_policy()

        assert capacity_policy.max_queued_runs == 100
        assert capacity_policy.max_running_runs == 2
        assert retention_policy.max_event_count == 1000
        assert retention_policy.ttl_seconds == 86400

    def test_loads_values_from_config_properties(self, tmp_path: Path) -> None:
        """临时 ``config.properties`` 中的 ``RUN_*`` 键应覆盖默认值。"""
        props_file = tmp_path / "config.properties"
        props_file.write_text(
            "\n".join(
                [
                    "RUN_WORKER_ENABLED=false",
                    "RUN_WORKER_COUNT=3",
                    "RUN_LEASE_SECONDS=90",
                    "RUN_HEARTBEAT_INTERVAL_SECONDS=20",
                    "RUN_MAX_QUEUED_RUNS=250",
                    "RUN_MAX_RUNNING_RUNS=8",
                    "RUN_EVENT_MAX_COUNT=3000",
                    "RUN_EVENT_TTL_SECONDS=172800",
                    "RUN_EVENT_STREAM_WAIT_SECONDS=7.5",
                    "RUN_LOST_SWEEP_INTERVAL_SECONDS=45",
                    "RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED=false",
                ]
            ),
            encoding="utf-8",
        )

        class _ConfigFromProperties(RunRuntimeConfig):
            """仅使用临时 properties 源的 Run 配置。"""

            @classmethod
            def settings_customise_sources(
                cls: type[BaseSettings],
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                """仅从测试提供的 ``config.properties`` 加载配置。"""
                return (
                    PropertiesFileSettingsSource(
                        settings_cls,
                        properties_path=props_file,
                    ),
                )

        config = _ConfigFromProperties()

        assert config.worker_enabled is False
        assert config.worker_count == 3
        assert config.lease_seconds == 90
        assert config.heartbeat_interval_seconds == 20
        assert config.max_queued_runs == 250
        assert config.max_running_runs == 8
        assert config.event_max_count == 3000
        assert config.event_ttl_seconds == 172800
        assert config.event_stream_wait_seconds == 7.5
        assert config.lost_sweep_interval_seconds == 45
        assert config.guardrail_runtime_convergence_enabled is False

    def test_environment_variables_override_config_properties(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量应仅作为 config.properties 的覆盖来源。"""
        props_file = tmp_path / "config.properties"
        props_file.write_text(
            "RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED=false\n",
            encoding="utf-8",
        )

        class _ConfigFromProperties(RunRuntimeConfig):
            """仅使用临时 properties 与环境变量源的 Run 配置。"""

            @classmethod
            def settings_customise_sources(
                cls: type[BaseSettings],
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                """保持环境变量覆盖 properties 的项目默认优先级。"""
                return (
                    init_settings,
                    env_settings,
                    PropertiesFileSettingsSource(
                        settings_cls,
                        properties_path=props_file,
                    ),
                    dotenv_settings,
                    file_secret_settings,
                )

        monkeypatch.setenv("RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED", "true")

        config = _ConfigFromProperties()

        assert config.guardrail_runtime_convergence_enabled is True

    def test_disabling_convergence_flag_keeps_other_runtime_policies_compatible(self) -> None:
        """关闭收敛开关时只切换布尔位，不应破坏既有运行时策略转换。"""

        config = RunRuntimeConfig(guardrail_runtime_convergence_enabled=False)

        assert config.guardrail_runtime_convergence_enabled is False
        assert config.to_capacity_policy().max_queued_runs == config.max_queued_runs
        assert config.to_event_retention_policy().ttl_seconds == config.event_ttl_seconds

    @pytest.mark.parametrize(
        ("field_name", "value", "config_key"),
        [
            ("worker_count", 0, "RUN_WORKER_COUNT"),
            ("lease_seconds", 0, "RUN_LEASE_SECONDS"),
            ("heartbeat_interval_seconds", 0, "RUN_HEARTBEAT_INTERVAL_SECONDS"),
            ("max_queued_runs", 0, "RUN_MAX_QUEUED_RUNS"),
            ("max_running_runs", 0, "RUN_MAX_RUNNING_RUNS"),
            ("event_max_count", 0, "RUN_EVENT_MAX_COUNT"),
            ("event_ttl_seconds", 0, "RUN_EVENT_TTL_SECONDS"),
            ("event_stream_wait_seconds", 0, "RUN_EVENT_STREAM_WAIT_SECONDS"),
            ("lost_sweep_interval_seconds", 0, "RUN_LOST_SWEEP_INTERVAL_SECONDS"),
        ],
    )
    def test_non_positive_numeric_values_are_rejected(
        self,
        field_name: str,
        value: int | float,
        config_key: str,
    ) -> None:
        """所有数值配置必须为正数。"""
        invalid_values = cast(dict[str, Any], {field_name: value})
        with pytest.raises(ConfigurationError, match=config_key):
            RunRuntimeConfig(**invalid_values)

    @pytest.mark.parametrize(
        ("heartbeat_interval_seconds", "lease_seconds"),
        [
            (60, 60),
            (61, 60),
        ],
    )
    def test_heartbeat_interval_must_be_less_than_lease(
        self,
        heartbeat_interval_seconds: int,
        lease_seconds: int,
    ) -> None:
        """心跳间隔必须严格小于租约时长，避免租约静默过期。"""
        with pytest.raises(
            ConfigurationError,
            match="RUN_HEARTBEAT_INTERVAL_SECONDS",
        ):
            RunRuntimeConfig(
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                lease_seconds=lease_seconds,
            )
