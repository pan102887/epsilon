"""LLM 模型接入重试退避装饰器工厂。

基于 :mod:`tenacity` 提供"指数退避 + 随机 jitter"的统一重试策略，
覆盖 :class:`OpenAICompatibleAdapter` 的 ``chat`` 与 ``stream`` 首次握手。
设计目标见 ``docs/spec/llm-and-tool-resilience/design.md`` C1 / R1。

仅对**真正瞬时**的网络/服务侧异常重试；语义错误（如鉴权、参数错）一次抛出。

使用示例::

    from infrastructure.model_access._retry import build_retry

    retry = build_retry(attempts=3)

    @retry
    async def _chat_once(self, params):
        return await self._client.chat.completions.create(**params)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, Protocol, TypeVar

from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from domain.model_access.exceptions import (
    ModelConnectionError,
    ModelRateLimitError,
    ModelTimeoutError,
)

logger = logging.getLogger(__name__)

# 仅这三类视为"可重试的瞬时错误"。
# - ModelTimeoutError 来自 APITimeoutError（网络抖动 / 服务侧延时）；
# - ModelRateLimitError 来自 RateLimitError（HTTP 429）；
# - ModelConnectionError 来自 APIConnectionError（DNS / refused / unreachable）。
# `ModelAccessError`、`ModelTokenLimitExceeded` 等业务错误故意不在白名单中。
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ModelTimeoutError,
    ModelRateLimitError,
    ModelConnectionError,
)

P = ParamSpec("P")
T = TypeVar("T")


class _AsyncRetryDecorator(Protocol):
    """保留异步函数参数与返回类型的重试装饰器协议。"""

    def __call__(self, fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        """装饰异步函数且保持原始可调用签名。"""
        ...


def build_retry(attempts: int) -> _AsyncRetryDecorator:
    """构造重试装饰器。

    Args:
        attempts: 最大尝试次数（含首次）。``<= 1`` 时返回恒等装饰器，
            完全无 tenacity 开销，保持向下兼容。

    Returns:
        装饰器，可作用于 ``async def`` 函数。受 ``_RETRYABLE_EXCEPTIONS``
        触发的异常进入指数退避；其他异常立即抛出。
    """
    if attempts <= 1:
        # passthrough：无延迟、无包装，向下兼容。
        return lambda fn: fn

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            retry_state = AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_random_exponential(min=1, max=30),
                retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
                before_sleep=before_sleep_log(logger, logging.INFO),
                reraise=True,
            )
            async for attempt in retry_state:
                with attempt:
                    return await fn(*args, **kwargs)
            # 实际不可达：reraise=True 时上面会抛出最后一次异常
            raise RuntimeError("AsyncRetrying exited without value or raise")

        return wrapped

    return decorator


__all__ = ["build_retry"]
