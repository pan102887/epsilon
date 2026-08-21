"""Redis 健康检查适配器。

实现 HealthCheckPort，通过 Redis PING 命令检测连通性。
超时时长从 RedisConfig.health_check_timeout 配置项动态读取，支持配置热刷新。
所有异常均被捕获并转化为 DOWN 状态，健康检查本身不会抛出异常。
"""

import asyncio
import logging
from typing import Protocol, cast

import redis.asyncio as aioredis

from domain.health.ports import HealthCheckPort
from domain.health.value_objects import HealthCheckResult, HealthStatus
from infrastructure.redis.redis_config import redis_config

logger = logging.getLogger(__name__)


class _RedisPingClient(Protocol):
    async def ping(self) -> bool: ...


class RedisHealthCheckAdapter(HealthCheckPort):
    """Redis 健康检查适配器。

    通过执行 Redis PING 命令检测 Redis 服务的连通性。
    使用 asyncio.wait_for 包裹 PING 调用，超时时长从 redis_config.health_check_timeout 动态读取。
    捕获 TimeoutError、RedisError 和通用 Exception，均返回 DOWN 状态并携带 reason。
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        """初始化适配器。

        Args:
            redis_client: 已初始化的 Redis 异步客户端
        """
        self._redis = cast(_RedisPingClient, redis_client)

    async def check(self) -> HealthCheckResult:
        """执行 Redis PING 检查。

        通过 asyncio.wait_for 包裹 Redis PING 命令，超时时长从
        redis_config.health_check_timeout 动态读取，支持配置热刷新。
        捕获所有可能的异常并转化为 DOWN 状态，确保健康检查不会抛出异常。

        Returns:
            Redis 连通性检查结果，name 固定为 "redis"
        """
        timeout = redis_config.health_check_timeout
        try:
            await asyncio.wait_for(
                self._redis.ping(),
                timeout=timeout,
            )
            return HealthCheckResult(name="redis", status=HealthStatus.UP)
        except TimeoutError:
            reason = f"Redis PING 超时（>{timeout}s）"
            logger.warning(reason)
            return HealthCheckResult(name="redis", status=HealthStatus.DOWN, reason=reason)
        except aioredis.RedisError as e:
            reason = f"Redis 连接异常: {e}"
            logger.warning(reason)
            return HealthCheckResult(name="redis", status=HealthStatus.DOWN, reason=str(e))
        except Exception as e:
            reason = f"Redis 健康检查未知异常: {e}"
            logger.error(reason)
            return HealthCheckResult(name="redis", status=HealthStatus.DOWN, reason=str(e))
