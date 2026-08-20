"""模型 Provider 健康策略测试。"""

from unittest.mock import Mock

import pytest

from domain.model_access.exceptions import ModelAccessError
from infrastructure.model_access.provider_health_policy import ProviderHealthPolicy
from infrastructure.model_access.provider_registry import ProviderRegistry


class _FakeClock:
    """用于确定性推进单调时间的测试时钟。"""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """推进测试时钟。

        Args:
            seconds: 向前推进的秒数。

        Returns:
            无返回值。
        """
        self._now += seconds


def test_provider_health_policy_opens_circuit_after_consecutive_failures() -> None:
    """连续失败达到阈值后 Provider 应进入短期熔断状态。"""

    clock = _FakeClock()
    policy = ProviderHealthPolicy(
        max_consecutive_failures=3,
        cooldown_seconds=60,
        time_fn=clock,
    )

    policy.record_failure("qwen")
    policy.record_failure("qwen")
    assert policy.is_available("qwen") is True

    policy.record_failure("qwen")

    assert policy.is_available("qwen") is False


def test_provider_health_policy_recovers_after_cooldown_ttl() -> None:
    """冷却 TTL 结束后 Provider 应重新允许被选择。"""

    clock = _FakeClock()
    policy = ProviderHealthPolicy(
        max_consecutive_failures=1,
        cooldown_seconds=60,
        time_fn=clock,
    )

    policy.record_failure("deepseek")
    assert policy.is_available("deepseek") is False

    clock.advance(59.9)
    assert policy.is_available("deepseek") is False

    clock.advance(0.1)
    assert policy.is_available("deepseek") is True


def test_provider_health_policy_record_success_clears_cooldown() -> None:
    """成功记录应清零失败计数并结束冷却。"""

    clock = _FakeClock()
    policy = ProviderHealthPolicy(
        max_consecutive_failures=1,
        cooldown_seconds=60,
        time_fn=clock,
    )

    policy.record_failure("qwen")
    assert policy.is_available("qwen") is False

    policy.record_success("qwen")

    assert policy.is_available("qwen") is True


def test_provider_registry_skips_unavailable_provider_and_preserves_round_robin() -> None:
    """注册中心应跳过冷却 Provider，并在健康 Provider 间保持轮询。"""

    policy = ProviderHealthPolicy(max_consecutive_failures=1, cooldown_seconds=60)
    qwen_adapter = Mock(name="qwen_adapter")
    deepseek_adapter = Mock(name="deepseek_adapter")
    registry = ProviderRegistry(default_model="", health_policy=policy)
    registry.register_provider("qwen", qwen_adapter, ["shared-model"])
    registry.register_provider("deepseek", deepseek_adapter, ["shared-model"])

    policy.record_failure("qwen")

    assert registry.get_adapter_for_model("shared-model") is deepseek_adapter
    assert registry.get_adapter_for_model("shared-model") is deepseek_adapter

    policy.record_success("qwen")

    assert registry.get_adapter_for_model("shared-model") is qwen_adapter
    assert registry.get_adapter_for_model("shared-model") is deepseek_adapter


def test_provider_registry_raises_when_all_providers_are_in_cooldown() -> None:
    """所有 Provider 都在冷却中时不应静默回退到不健康 Provider。"""

    policy = ProviderHealthPolicy(max_consecutive_failures=1, cooldown_seconds=60)
    registry = ProviderRegistry(default_model="", health_policy=policy)
    registry.register_provider("qwen", Mock(), ["shared-model"])
    registry.register_provider("deepseek", Mock(), ["shared-model"])
    policy.record_failure("qwen")
    policy.record_failure("deepseek")

    with pytest.raises(ModelAccessError) as exc_info:
        registry.get_adapter_for_model("shared-model")

    assert exc_info.value.details == {
        "model": "shared-model",
        "provider_count": 2,
        "unavailable_providers": ["deepseek", "qwen"],
        "reason": "all_providers_in_cooldown",
    }
