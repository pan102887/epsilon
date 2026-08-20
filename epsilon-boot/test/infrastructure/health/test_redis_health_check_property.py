"""Redis 健康检查适配器属性测试。

使用 Hypothesis 对 RedisHealthCheckAdapter 的异常处理行为进行属性测试，
验证任意 Redis 异常均产生 DOWN 状态并携带失败原因。
"""

from unittest.mock import AsyncMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.health.value_objects import HealthCheckResult, HealthStatus
from infrastructure.health.redis_health_check_adapter import RedisHealthCheckAdapter

aioredis = pytest.importorskip("redis.asyncio")

# ── Hypothesis 生成策略 ──

exception_message_st = st.text(min_size=1, max_size=200).filter(lambda s: s.strip() != "")

redis_error_st = exception_message_st.map(lambda msg: aioredis.RedisError(msg))

timeout_error_st = exception_message_st.map(lambda msg: TimeoutError(msg))

generic_error_st = exception_message_st.map(lambda msg: RuntimeError(msg))

any_exception_st = st.one_of(redis_error_st, timeout_error_st, generic_error_st)


# ── Property 5: Redis 异常产生 DOWN 结果并携带原因 ──
# Feature: readiness-probe, Property 5: Redis 异常产生 DOWN 结果并携带原因


@settings(max_examples=100)
@given(exc=redis_error_st)
@pytest.mark.asyncio
async def test_redis_error_produces_down_with_reason(
    exc: aioredis.RedisError,
) -> None:
    """验证任意 RedisError 异常均产生 DOWN 状态并携带失败原因。

    使用 Hypothesis 生成随机异常消息构造 RedisError，mock Redis 客户端
    的 ping 方法抛出该异常，断言适配器返回 status=DOWN 且 reason 不为 None。
    """
    mock_redis = AsyncMock(spec=aioredis.Redis)
    mock_redis.ping.side_effect = exc
    adapter = RedisHealthCheckAdapter(redis_client=mock_redis)

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None


@settings(max_examples=100)
@given(exc=timeout_error_st)
@pytest.mark.asyncio
async def test_timeout_error_produces_down_with_reason(
    exc: TimeoutError,
) -> None:
    """验证任意 TimeoutError 异常均产生 DOWN 状态并携带超时原因。

    使用 Hypothesis 生成随机异常消息构造 TimeoutError，通过 mock
    asyncio.wait_for 抛出 asyncio.TimeoutError，断言适配器返回
    status=DOWN 且 reason 包含超时描述。
    """
    mock_redis = AsyncMock(spec=aioredis.Redis)
    mock_redis.ping.side_effect = TimeoutError()
    adapter = RedisHealthCheckAdapter(redis_client=mock_redis)

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "超时" in result.reason


@settings(max_examples=100)
@given(exc=generic_error_st)
@pytest.mark.asyncio
async def test_generic_exception_produces_down_with_reason(
    exc: RuntimeError,
) -> None:
    """验证任意通用异常均产生 DOWN 状态并携带异常信息。

    使用 Hypothesis 生成随机异常消息构造 RuntimeError，mock Redis 客户端
    的 ping 方法抛出该异常，断言适配器返回 status=DOWN 且 reason 包含
    原始异常消息。
    """
    mock_redis = AsyncMock(spec=aioredis.Redis)
    mock_redis.ping.side_effect = exc
    adapter = RedisHealthCheckAdapter(redis_client=mock_redis)

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert str(exc) in result.reason


@settings(max_examples=100)
@given(exc=any_exception_st)
@pytest.mark.asyncio
async def test_any_exception_never_propagates(
    exc: Exception,
) -> None:
    """验证适配器捕获所有异常，健康检查本身不会抛出异常。

    对于任意类型的异常（RedisError、TimeoutError、RuntimeError），
    适配器应始终返回 HealthCheckResult 而非向上传播异常。
    """
    mock_redis = AsyncMock(spec=aioredis.Redis)
    mock_redis.ping.side_effect = exc
    adapter = RedisHealthCheckAdapter(redis_client=mock_redis)

    result: HealthCheckResult = await adapter.check()

    assert isinstance(result, HealthCheckResult)
    assert result.status == HealthStatus.DOWN
