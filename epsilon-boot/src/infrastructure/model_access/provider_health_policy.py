"""模型 Provider 健康策略模块。

本模块提供基础设施层使用的轻量级 Provider 熔断状态管理。它只记录
Provider 名称对应的连续失败次数和短期冷却截止时间，不直接调用外部
模型服务，也不改变领域层 Port 定义。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _ProviderHealthState:
    """单个 Provider 的健康状态。"""

    consecutive_failures: int = 0
    cooldown_until: float = 0.0


class ProviderHealthPolicy:
    """Provider 健康策略。

    该策略用于在模型路由前判断 Provider 是否处于短期冷却窗口内。连续
    失败达到阈值后，Provider 会在 ``cooldown_seconds`` 秒内被视为不可用；
    成功调用会清零连续失败次数并结束冷却。``time_fn`` 支持测试注入，使
    TTL 行为可以用确定性时间推进验证。
    """

    def __init__(
        self,
        *,
        max_consecutive_failures: int = 3,
        cooldown_seconds: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化 Provider 健康策略。

        Args:
            max_consecutive_failures: 进入冷却状态所需的连续失败次数，必须大于 0。
            cooldown_seconds: 达到阈值后的冷却秒数，必须大于等于 0。
            time_fn: 单调时间函数，默认使用 ``time.monotonic``，测试可注入假时钟。
        """
        if max_consecutive_failures <= 0:
            raise ValueError("max_consecutive_failures 必须大于 0")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds 必须大于等于 0")

        self._max_consecutive_failures = max_consecutive_failures
        self._cooldown_seconds = cooldown_seconds
        self._time_fn = time_fn
        self._states: dict[str, _ProviderHealthState] = {}

    def record_success(self, provider_name: str) -> None:
        """记录 Provider 成功调用并恢复健康状态。

        Args:
            provider_name: Provider 唯一名称。

        Returns:
            无返回值。
        """
        self._states[provider_name] = _ProviderHealthState()

    def record_failure(self, provider_name: str) -> None:
        """记录 Provider 失败调用并在达到阈值时进入冷却。

        Args:
            provider_name: Provider 唯一名称。

        Returns:
            无返回值。
        """
        state = self._states.setdefault(provider_name, _ProviderHealthState())
        state.consecutive_failures += 1

        if state.consecutive_failures >= self._max_consecutive_failures:
            state.cooldown_until = self._time_fn() + self._cooldown_seconds

    def is_available(self, provider_name: str) -> bool:
        """判断 Provider 当前是否可被路由选择。

        Args:
            provider_name: Provider 唯一名称。

        Returns:
            Provider 不在冷却窗口内返回 ``True``，否则返回 ``False``。
        """
        state = self._states.get(provider_name)
        if state is None:
            return True
        return self._time_fn() >= state.cooldown_until
