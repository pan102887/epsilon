"""Agent guardrail 配置单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from common.configuration import ConfigurationError, PropertiesFileSettingsSource
from domain.agent.guardrails import (
    GuardrailMode,
    GuardrailModelPricing,
    GuardrailRuntimeStats,
)
from infrastructure.agent.guardrail_config import AgentGuardrailConfig


def test_guardrail_config_defaults_to_observe_policy() -> None:
    config = AgentGuardrailConfig()

    assert config.enabled is True
    assert config.mode == "observe"
    assert config.enforce_critical_tools is True
    assert config.enforce_high_risk_tools is False

    policy = config.to_policy()

    assert policy.mode is GuardrailMode.OBSERVE
    assert policy.max_total_tokens is None


def test_guardrail_config_loads_from_config_properties(tmp_path: Path) -> None:
    props_file = tmp_path / "config.properties"
    props_file.write_text(
        "\n".join(
            [
                "AGENT_GUARDRAILS_MODE=enforce",
                "AGENT_GUARDRAILS_MAX_TOTAL_TOKENS=100",
                "AGENT_GUARDRAILS_ENFORCE_HIGH_RISK_TOOLS=true",
            ]
        ),
        encoding="utf-8",
    )

    class _ConfigFromProperties(AgentGuardrailConfig):
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

    policy = _ConfigFromProperties().to_policy()

    assert policy.mode is GuardrailMode.ENFORCE
    assert policy.max_total_tokens == 100
    assert policy.enforce_high_risk_tools is True


def test_guardrail_config_parses_legacy_scalar_model_pricing_json_object() -> None:
    config = AgentGuardrailConfig(model_pricing='{"qwen3": 0.12, "glm-4.7": "0.34"}')

    policy = config.to_policy()

    assert policy.model_pricing == {
        "qwen3": GuardrailModelPricing(total_per_1m=0.12),
        "glm-4.7": GuardrailModelPricing(total_per_1m=0.34),
    }


def test_guardrail_config_parses_object_model_pricing_with_total_per_1m() -> None:
    config = AgentGuardrailConfig(model_pricing='{"qwen3": {"total_per_1m": 1.2}}')

    policy = config.to_policy()

    assert policy.model_pricing == {"qwen3": GuardrailModelPricing(total_per_1m=1.2)}


def test_guardrail_config_parses_object_model_pricing_with_prompt_and_completion_prices() -> None:
    config = AgentGuardrailConfig(
        model_pricing=('{"qwen3": {"prompt_per_1m": 0.8, "completion_per_1m": 2.0}}')
    )

    policy = config.to_policy()

    assert policy.model_pricing == {
        "qwen3": GuardrailModelPricing(prompt_per_1m=0.8, completion_per_1m=2.0)
    }


def test_guardrail_config_split_pricing_estimates_prompt_and_completion_cost() -> None:
    config = AgentGuardrailConfig(
        model_pricing=('{"qwen3": {"prompt_per_1m": 1.0, "completion_per_1m": 3.0}}')
    )

    stats = GuardrailRuntimeStats.from_model_usage(
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        model="qwen3",
        model_pricing=config.to_policy().model_pricing,
    )

    assert stats.estimated_cost == 0.0025
    assert stats.cost_available is True


def test_guardrail_config_missing_pricing_marks_cost_unavailable_without_policy_change() -> None:
    config = AgentGuardrailConfig(mode="enforce", model_pricing="{}")

    policy = config.to_policy()
    stats = GuardrailRuntimeStats.from_model_usage(
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
        model="unpriced-model",
        model_pricing=policy.model_pricing,
    )

    assert policy.mode is GuardrailMode.ENFORCE
    assert stats.estimated_cost is None
    assert stats.cost_available is False


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("mode", "invalid", "AGENT_GUARDRAILS_MODE"),
        ("max_total_tokens", -1, "AGENT_GUARDRAILS_MAX_TOTAL_TOKENS"),
        ("max_repeated_tool_calls", 0, "AGENT_GUARDRAILS_MAX_REPEATED_TOOL_CALLS"),
        ("max_consecutive_failures", 0, "AGENT_GUARDRAILS_MAX_CONSECUTIVE_FAILURES"),
        ("model_pricing", "{bad", "AGENT_GUARDRAILS_MODEL_PRICING"),
        ("model_pricing", "[1, 2]", "AGENT_GUARDRAILS_MODEL_PRICING"),
        ("model_pricing", '{"": 0.1}', "AGENT_GUARDRAILS_MODEL_PRICING"),
        ("model_pricing", '{"qwen3": "bad"}', "AGENT_GUARDRAILS_MODEL_PRICING"),
        ("model_pricing", '{"qwen3": -0.1}', "AGENT_GUARDRAILS_MODEL_PRICING"),
        (
            "model_pricing",
            '{"qwen3": {"prompt_per_1m": 0.8}}',
            "AGENT_GUARDRAILS_MODEL_PRICING",
        ),
        (
            "model_pricing",
            '{"qwen3": {"total_per_1m": 1.2, "prompt_per_1m": 0.8}}',
            "AGENT_GUARDRAILS_MODEL_PRICING",
        ),
        (
            "model_pricing",
            '{"qwen3": {"unexpected": 1.2}}',
            "AGENT_GUARDRAILS_MODEL_PRICING",
        ),
        (
            "model_pricing",
            '{"qwen3": {"completion_per_1m": -1}}',
            "AGENT_GUARDRAILS_MODEL_PRICING",
        ),
    ],
)
def test_guardrail_config_rejects_invalid_values(
    field_name: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        AgentGuardrailConfig.model_validate({field_name: value})
