"""Agent guardrail 配置模块。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config
from domain.agent.guardrails import GuardrailMode, GuardrailModelPricing, GuardrailPolicy


class AgentGuardrailConfig(PropertiesBaseSettings):
    """Agent 智能调度与护栏配置。"""

    model_config = SettingsConfigDict(env_prefix="AGENT_GUARDRAILS_")

    enabled: bool = True
    mode: str = "observe"
    enforce_critical_tools: bool = True
    enforce_high_risk_tools: bool = False
    max_total_tokens: int = 0
    max_duration_seconds: float = 0.0
    max_context_growth_messages: int = 0
    max_repeated_tool_calls: int = 2
    max_consecutive_failures: int = 3
    model_pricing: str = ""

    @model_validator(mode="after")
    def _validate_guardrail_config(self) -> AgentGuardrailConfig:
        """校验配置，非法时 fail-fast。"""

        if self.mode not in {GuardrailMode.OBSERVE.value, GuardrailMode.ENFORCE.value}:
            raise ConfigurationError("AGENT_GUARDRAILS_MODE 必须为 observe 或 enforce")
        if self.max_total_tokens < 0:
            raise ConfigurationError("AGENT_GUARDRAILS_MAX_TOTAL_TOKENS 必须大于等于 0")
        if self.max_duration_seconds < 0:
            raise ConfigurationError("AGENT_GUARDRAILS_MAX_DURATION_SECONDS 必须大于等于 0")
        if self.max_context_growth_messages < 0:
            raise ConfigurationError("AGENT_GUARDRAILS_MAX_CONTEXT_GROWTH_MESSAGES 必须大于等于 0")
        if self.max_repeated_tool_calls <= 0:
            raise ConfigurationError("AGENT_GUARDRAILS_MAX_REPEATED_TOOL_CALLS 必须为正整数")
        if self.max_consecutive_failures <= 0:
            raise ConfigurationError("AGENT_GUARDRAILS_MAX_CONSECUTIVE_FAILURES 必须为正整数")
        self._parse_model_pricing()
        return self

    def _parse_model_pricing(self) -> dict[str, GuardrailModelPricing]:
        """解析模型单价表并保留可解释的 prompt/completion/total 单价。"""

        return self._parse_model_pricing_payload()

    def _parse_model_pricing_payload(self) -> dict[str, GuardrailModelPricing]:
        """解析模型单价表，兼容旧标量与新对象格式。"""

        if not self.model_pricing.strip():
            return {}
        try:
            parsed: Any = json.loads(self.model_pricing)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("AGENT_GUARDRAILS_MODEL_PRICING 不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise ConfigurationError("AGENT_GUARDRAILS_MODEL_PRICING 必须为 JSON object")
        result: dict[str, GuardrailModelPricing] = {}
        for key, value in parsed.items():
            if not isinstance(key, str) or not key:
                raise ConfigurationError("AGENT_GUARDRAILS_MODEL_PRICING 模型名非法")
            result[key] = self._parse_model_pricing_entry(key, value)
        return result

    def _parse_model_pricing_entry(self, model_name: str, value: Any) -> GuardrailModelPricing:
        """解析单个模型价格项。"""

        if isinstance(value, dict):
            return self._parse_model_pricing_object(model_name, value)
        return GuardrailModelPricing(
            total_per_1m=self._coerce_non_negative_price(model_name, value),
        )

    def _parse_model_pricing_object(
        self,
        model_name: str,
        value: dict[str, Any],
    ) -> GuardrailModelPricing:
        """解析新对象格式的模型价格项。"""

        allowed_keys = {"total_per_1m", "prompt_per_1m", "completion_per_1m"}
        unknown_keys = sorted(set(value) - allowed_keys)
        if unknown_keys:
            raise ConfigurationError(
                "AGENT_GUARDRAILS_MODEL_PRICING 中 "
                f"{model_name} 包含未知字段: {', '.join(unknown_keys)}"
            )
        if "total_per_1m" in value:
            if len(value) != 1:
                raise ConfigurationError(
                    "AGENT_GUARDRAILS_MODEL_PRICING 中 "
                    f"{model_name} 使用 total_per_1m 时不得同时声明其他价格字段"
                )
            return GuardrailModelPricing(
                total_per_1m=self._coerce_non_negative_price(
                    model_name,
                    value["total_per_1m"],
                    field_name="total_per_1m",
                ),
            )
        if {"prompt_per_1m", "completion_per_1m"}.issubset(value):
            return GuardrailModelPricing(
                prompt_per_1m=self._coerce_non_negative_price(
                    model_name,
                    value["prompt_per_1m"],
                    field_name="prompt_per_1m",
                ),
                completion_per_1m=self._coerce_non_negative_price(
                    model_name,
                    value["completion_per_1m"],
                    field_name="completion_per_1m",
                ),
            )
        raise ConfigurationError(
            "AGENT_GUARDRAILS_MODEL_PRICING 中 "
            f"{model_name} 的价格对象必须包含 total_per_1m，或同时包含 "
            "prompt_per_1m 与 completion_per_1m"
        )

    def _coerce_non_negative_price(
        self,
        model_name: str,
        value: Any,
        *,
        field_name: str = "price",
    ) -> float:
        """把价格值转换为非负浮点数。"""

        try:
            price = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"AGENT_GUARDRAILS_MODEL_PRICING 中 {model_name} 的 {field_name} 非法"
            ) from exc
        if price < 0:
            raise ConfigurationError(
                f"AGENT_GUARDRAILS_MODEL_PRICING 中 {model_name} 的 {field_name} 不得小于 0"
            )
        return price

    def to_policy(self) -> GuardrailPolicy:
        """转换为领域层护栏策略。"""

        return GuardrailPolicy(
            enabled=self.enabled,
            mode=GuardrailMode(self.mode),
            enforce_critical_tools=self.enforce_critical_tools,
            enforce_high_risk_tools=self.enforce_high_risk_tools,
            max_total_tokens=(self.max_total_tokens if self.max_total_tokens > 0 else None),
            max_duration_seconds=(
                self.max_duration_seconds if self.max_duration_seconds > 0 else None
            ),
            max_context_growth_messages=(
                self.max_context_growth_messages if self.max_context_growth_messages > 0 else None
            ),
            max_repeated_tool_calls=self.max_repeated_tool_calls,
            max_consecutive_failures=self.max_consecutive_failures,
            model_pricing=self._parse_model_pricing(),
        )


agent_guardrail_config = create_config(AgentGuardrailConfig)
"""全局 Agent guardrail 配置实例。"""
