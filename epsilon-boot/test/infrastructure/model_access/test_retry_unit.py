"""build_retry 装饰器单元测试。

覆盖：
- passthrough（attempts<=1）
- 重试到第 N 次成功
- 不可重试异常立即抛
- 达上限抛原始异常类型
"""

import pytest

from domain.model_access.exceptions import (
    ModelAccessError,
    ModelConnectionError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from infrastructure.model_access._retry import build_retry


class TestBuildRetryPassthrough:
    """attempts<=1 时返回恒等装饰器。"""

    @pytest.mark.asyncio
    async def test_attempts_zero_passthrough(self):
        retry = build_retry(attempts=0)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert await fn() == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_attempts_one_passthrough(self):
        retry = build_retry(attempts=1)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ModelTimeoutError(timeout_seconds=10.0, request_info={})

        with pytest.raises(ModelTimeoutError):
            await fn()
        assert call_count == 1  # 不重试


class TestBuildRetrySuccess:
    """重试后成功。"""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        retry = build_retry(attempts=3)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ModelConnectionError(reason="refused")
            return "recovered"

        result = await fn()
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_last_attempt(self):
        retry = build_retry(attempts=3)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ModelRateLimitError(retry_after_seconds=1.0)
            return "done"

        result = await fn()
        assert result == "done"
        assert call_count == 3


class TestBuildRetryNonRetryable:
    """不可重试异常立即抛出。"""

    @pytest.mark.asyncio
    async def test_model_access_error_not_retried(self):
        retry = build_retry(attempts=3)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ModelAccessError(message="auth failed", details={})

        with pytest.raises(ModelAccessError, match="auth failed"):
            await fn()
        assert call_count == 1  # 一次就抛

    @pytest.mark.asyncio
    async def test_generic_exception_not_retried(self):
        retry = build_retry(attempts=3)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            await fn()
        assert call_count == 1


class TestBuildRetryExhausted:
    """达到上限后抛出原始异常类型。"""

    @pytest.mark.asyncio
    async def test_exhausted_raises_original_type(self):
        retry = build_retry(attempts=2)
        call_count = 0

        @retry
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ModelTimeoutError(timeout_seconds=5.0, request_info={})

        with pytest.raises(ModelTimeoutError):
            await fn()
        assert call_count == 2
