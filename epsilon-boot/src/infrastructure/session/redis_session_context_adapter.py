"""基于 Redis 的会话上下文存储适配器。

实现 SessionContextStorePort，将 ConversationContext 以 JSON 格式
存储到 Redis，支持 TTL 自动过期。新增 ``compare_and_swap`` 方法提供
基于 WATCH/MULTI/EXEC 的乐观锁 CAS 周期。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

import redis.asyncio as aioredis

from domain.chat.exceptions import SessionConflictError
from domain.chat.ports import SessionContextStorePort

if TYPE_CHECKING:
    from domain.chat.context import ConversationContext

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RedisSessionContextAdapter(SessionContextStorePort):
    """基于 Redis 的会话上下文存储适配器。

    实现 SessionContextStorePort，将 ConversationContext 以 JSON 格式
    存储到 Redis，支持 TTL 自动过期。``compare_and_swap`` 提供 CAS 周期。
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        key_prefix: str = "session:context:",
        ttl_seconds: int = 3600,
        conflict_retry_max: int | None = None,
    ) -> None:
        """初始化 Redis 会话上下文存储适配器。

        Args:
            redis_client: aioredis 异步客户端实例。
            key_prefix: Redis key 前缀，默认 ``session:context:``。
            ttl_seconds: 会话 key TTL（秒），默认 ``3600``。
            conflict_retry_max: CAS 重试上限；``None`` 时使用默认值 ``3``。
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._conflict_retry_max = conflict_retry_max if conflict_retry_max is not None else 3

    def _make_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    async def save(self, session_id: str, context: "ConversationContext") -> None:
        key = self._make_key(session_id)
        data = json.dumps(context.to_dict(), ensure_ascii=False)
        try:
            await self._redis.set(key, data, ex=self._ttl_seconds)
        except aioredis.RedisError as e:
            logger.error("Failed to save session context [%s]: %s", session_id, e)
            raise

    async def load(self, session_id: str) -> "ConversationContext":
        from domain.chat.context import ConversationContext

        key = self._make_key(session_id)
        try:
            raw = await self._redis.get(key)
        except aioredis.RedisError as e:
            logger.error("Failed to load session context [%s]: %s", session_id, e)
            raise

        if raw is None:
            return ConversationContext()

        try:
            data = json.loads(raw)
            return ConversationContext.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Failed to deserialize session context [%s]: %s", session_id, e)
            return ConversationContext()

    async def delete(self, session_id: str) -> None:
        key = self._make_key(session_id)
        try:
            await self._redis.delete(key)
        except aioredis.RedisError as e:
            logger.error("Failed to delete session context [%s]: %s", session_id, e)
            raise

    async def exists(self, session_id: str) -> bool:
        """判断指定会话上下文 key 是否真实存在。

        本方法只执行 Redis ``EXISTS``，不读取完整上下文，也不刷新 key TTL。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            Redis 中存在对应会话上下文 key 时返回 ``True``。
        """
        key = self._make_key(session_id)
        try:
            return bool(await self._redis.exists(key))
        except aioredis.RedisError as e:
            logger.error("Failed to check session context exists [%s]: %s", session_id, e)
            raise

    async def compare_and_swap(
        self,
        session_id: str,
        mutator: Callable[["ConversationContext"], Awaitable[T]],
    ) -> T:
        """基于 WATCH/MULTI/EXEC 的乐观锁 CAS 实现。

        读取当前会话上下文，调用 mutator 修改后原子提交；若提交时
        检测到写入冲突则自动重试至 ``conflict_retry_max`` 上限。

        Args:
            session_id: 会话唯一标识符。
            mutator: 异步修改回调；可能因冲突被多次调用，必须幂等。

        Returns:
            mutator 的返回值。

        Raises:
            SessionConflictError: 重试上限耗尽。
            aioredis.RedisError: Redis 客户端层异常透传。
        """
        from domain.chat.context import ConversationContext

        key = self._make_key(session_id)

        for attempt in range(self._conflict_retry_max + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await pipe.get(key)

                    if raw is None:
                        ctx = ConversationContext()
                    else:
                        try:
                            ctx = ConversationContext.from_dict(json.loads(raw))
                        except (json.JSONDecodeError, KeyError, TypeError):
                            ctx = ConversationContext()

                    result = await mutator(ctx)
                    data = json.dumps(ctx.to_dict(), ensure_ascii=False)

                    pipe.multi()
                    pipe.set(key, data, ex=self._ttl_seconds)
                    exec_result = await pipe.execute()

                    if exec_result is None:
                        raise aioredis.WatchError("WATCH conflict")

                    logger.info(
                        "compare_and_swap session_id=%s retry_count=%d outcome=success",
                        session_id,
                        attempt,
                    )
                    return result

            except aioredis.WatchError as exc:
                if attempt < self._conflict_retry_max:
                    logger.info(
                        "compare_and_swap session_id=%s retry_count=%d outcome=retry",
                        session_id,
                        attempt,
                    )
                    continue
                logger.info(
                    "compare_and_swap session_id=%s retry_count=%d outcome=give_up",
                    session_id,
                    attempt,
                )
                logger.error(
                    "SessionConflictError session_id=%s "
                    "error_class=SessionConflictError retry_count=%d",
                    session_id,
                    attempt,
                )
                raise SessionConflictError(
                    session_id=session_id,
                    retry_count=attempt,
                ) from exc
            except aioredis.RedisError as e:
                logger.error("compare_and_swap failed session_id=%s: %s", session_id, e)
                raise

        # 不可达：循环内必定 return 或 raise
        raise SessionConflictError(session_id=session_id, retry_count=self._conflict_retry_max)
