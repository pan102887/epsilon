"""Redis 健康检查适配器单元测试。

针对 RedisHealthCheckAdapter 的具体场景编写单元测试，覆盖
Redis PING 成功、超时、连接异常等边界情况。
"""

from unittest.mock import AsyncMock, patch

import pytest

from domain.health.value_objects import HealthCheckResult, HealthStatus
from infrastructure.health.redis_health_check_adapter import RedisHealthCheckAdapter

aioredis = pytest.importorskip("redis.asyncio")


@pytest.fixture
def mock_redis() -> AsyncMock:
    """创建 mock Redis 异步客户端，ping 方法为 AsyncMock。"""
    client = AsyncMock(spec=aioredis.Redis)
    client.ping = AsyncMock(return_value=True)
    return client


@pytest.fixture
def adapter(mock_redis: AsyncMock) -> RedisHealthCheckAdapter:
    """创建使用 mock Redis 客户端的适配器实例。"""
    return RedisHealthCheckAdapter(redis_client=mock_redis)


# ── Redis PING 成功返回 UP ──


@pytest.mark.asyncio
async def test_ping_success_returns_up(
    adapter: RedisHealthCheckAdapter, mock_redis: AsyncMock
) -> None:
    """验证 Redis PING 成功时返回 status=UP，name 为 'redis'，无 reason。"""
    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.UP
    assert result.reason is None
    mock_redis.ping.assert_awaited_once()


# ── Redis PING 超时返回 DOWN（3 秒内） ──


@pytest.mark.asyncio
async def test_ping_timeout_returns_down(
    adapter: RedisHealthCheckAdapter, mock_redis: AsyncMock
) -> None:
    """验证 Redis PING 超时时返回 status=DOWN，reason 包含超时描述。"""
    mock_redis.ping.side_effect = TimeoutError()

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "超时" in result.reason


@pytest.mark.asyncio
async def test_ping_timeout_uses_configured_timeout(
    mock_redis: AsyncMock,
) -> None:
    """验证适配器使用 redis_config.health_check_timeout 作为超时时长。"""
    adapter = RedisHealthCheckAdapter(redis_client=mock_redis)

    with patch("infrastructure.health.redis_health_check_adapter.redis_config") as mock_config:
        mock_config.health_check_timeout = 5
        result = await adapter.check()

    assert result.status == HealthStatus.UP
    mock_redis.ping.assert_awaited_once()


# ── Redis 连接异常返回 DOWN 并携带 reason ──


@pytest.mark.asyncio
async def test_redis_error_returns_down_with_reason(
    adapter: RedisHealthCheckAdapter, mock_redis: AsyncMock
) -> None:
    """验证 RedisError 异常时返回 status=DOWN，reason 包含异常信息。"""
    mock_redis.ping.side_effect = aioredis.RedisError("Connection refused")

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "Connection refused" in result.reason


@pytest.mark.asyncio
async def test_connection_error_returns_down(
    adapter: RedisHealthCheckAdapter, mock_redis: AsyncMock
) -> None:
    """验证 ConnectionError（RedisError 子类）时返回 status=DOWN。"""
    mock_redis.ping.side_effect = aioredis.ConnectionError("Cannot connect to Redis")

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "Cannot connect to Redis" in result.reason


@pytest.mark.asyncio
async def test_unexpected_exception_returns_down(
    adapter: RedisHealthCheckAdapter, mock_redis: AsyncMock
) -> None:
    """验证未预期的通用异常时返回 status=DOWN，reason 包含异常信息。"""
    mock_redis.ping.side_effect = RuntimeError("unexpected failure")

    result: HealthCheckResult = await adapter.check()

    assert result.name == "redis"
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "unexpected failure" in result.reason
