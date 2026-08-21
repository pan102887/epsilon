"""工具级 circuit breaker 实现。

基于简单的三态状态机（CLOSED → OPEN → HALF_OPEN → CLOSED）保护下游工具，
当某工具连续失败达到阈值后暂时拒绝调用，经恢复超时后放行少量探测请求。

设计目标见 ``docs/spec/llm-and-tool-resilience/design.md`` C3 / R3。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from domain.agent.exceptions import (
    ToolCircuitOpenError,
    ToolNotFoundError,
    ToolParameterValidationError,
    ToolPermissionDeniedError,
)

logger = logging.getLogger(__name__)

# 这些异常类型不计入熔断失败统计（语义错误，非工具本身故障）
_NON_FAILURE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ToolNotFoundError,
    ToolParameterValidationError,
    ToolPermissionDeniedError,
)


@dataclass
class _BreakerState:
    """单工具的熔断器状态。"""

    state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
    failure_count: int = 0
    opened_at: float = 0.0
    half_open_in_flight: int = 0


class ToolCircuitBreaker:
    """工具级熔断器。

    Args:
        failure_threshold: 连续失败次数阈值。
        recovery_timeout: OPEN → HALF_OPEN 的等待秒数。
        half_open_max_calls: HALF_OPEN 允许同时探测的最大调用数。
        time_fn: 时间函数，默认 ``time.monotonic``，便于测试注入。
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._time_fn = time_fn
        self._states: dict[str, _BreakerState] = {}
        self._lock = asyncio.Lock()

    def _get_state(self, tool_name: str) -> _BreakerState:
        if tool_name not in self._states:
            self._states[tool_name] = _BreakerState()
        return self._states[tool_name]

    def state_for(self, tool_name: str) -> _BreakerState:
        """Return the current breaker state for diagnostics and tests."""
        return self._get_state(tool_name)

    @asynccontextmanager
    async def guard(self, tool_name: str) -> AsyncGenerator[None, None]:
        """熔断保护上下文管理器。

        进入时检查状态：
        - CLOSED → 放行
        - OPEN → 检查是否可转 HALF_OPEN，否则抛 ToolCircuitOpenError
        - HALF_OPEN → 放行有限探测，超限则拒绝

        退出时：
        - 成功 → HALF_OPEN 转 CLOSED（重置计数）
        - 失败（计入类型） → 累加失败计数，必要时切 OPEN
        """
        async with self._lock:
            bs = self._get_state(tool_name)
            now = self._time_fn()

            if bs.state == "OPEN":
                if now - bs.opened_at >= self._recovery_timeout:
                    bs.state = "HALF_OPEN"
                    bs.half_open_in_flight = 0
                    logger.info("熔断器 %s: OPEN → HALF_OPEN", tool_name)
                else:
                    raise ToolCircuitOpenError(tool_name)

            if bs.state == "HALF_OPEN":
                if bs.half_open_in_flight >= self._half_open_max_calls:
                    raise ToolCircuitOpenError(tool_name)
                bs.half_open_in_flight += 1

        # 执行实际调用（不持锁）
        try:
            yield
        except Exception as exc:
            if isinstance(exc, _NON_FAILURE_EXCEPTIONS):
                # 语义错误不计入失败
                async with self._lock:
                    bs = self._get_state(tool_name)
                    if bs.state == "HALF_OPEN":
                        bs.half_open_in_flight -= 1
                raise
            # 计入失败
            async with self._lock:
                bs = self._get_state(tool_name)
                if bs.state == "HALF_OPEN":
                    bs.half_open_in_flight -= 1
                    bs.state = "OPEN"
                    bs.opened_at = self._time_fn()
                    logger.warning("熔断器 %s: HALF_OPEN 探测失败 → OPEN", tool_name)
                elif bs.state == "CLOSED":
                    bs.failure_count += 1
                    if bs.failure_count >= self._failure_threshold:
                        bs.state = "OPEN"
                        bs.opened_at = self._time_fn()
                        logger.warning(
                            "熔断器 %s: 连续失败 %d 次 → OPEN",
                            tool_name,
                            bs.failure_count,
                        )
            raise
        else:
            # 成功
            async with self._lock:
                bs = self._get_state(tool_name)
                if bs.state == "HALF_OPEN":
                    bs.half_open_in_flight -= 1
                    bs.state = "CLOSED"
                    bs.failure_count = 0
                    logger.info("熔断器 %s: HALF_OPEN 探测成功 → CLOSED", tool_name)
                elif bs.state == "CLOSED":
                    bs.failure_count = 0
