"""Redis 会话索引适配器。

本模块实现 ``SessionIndexPort``，使用 Redis String 保存会话元数据 JSON，
并使用 ZSET 按更新时间维护最近会话列表。索引与 Redis 会话上下文共用 TTL，
用于 TUI `/sessions` 和 `/resume` 的轻量发现路径。
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis

from domain.chat.ports import SessionIndexPort
from domain.chat.value_objects import SessionMetadata

logger = logging.getLogger(__name__)


class RedisSessionIndexAdapter(SessionIndexPort):
    """会话索引的 Redis 实现。"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        key_prefix: str = "session:index:",
        recent_zset_key: str = "session:index:recent",
        ttl_seconds: int = 3600,
    ) -> None:
        """初始化 Redis 会话索引适配器。"""
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._recent_zset_key = recent_zset_key
        self._ttl_seconds = ttl_seconds

    def _make_key(self, session_id: str) -> str:
        """生成会话元数据 Redis key。"""
        return f"{self._key_prefix}{session_id}"

    async def upsert(self, metadata: SessionMetadata) -> None:
        """新增或更新会话索引。"""
        key = self._make_key(metadata.session_id)
        payload = json.dumps(_metadata_to_dict(metadata), ensure_ascii=False)
        try:
            pipe = self._redis.pipeline()
            pipe.set(key, payload, ex=self._ttl_seconds)
            pipe.zadd(self._recent_zset_key, {metadata.session_id: metadata.updated_at_epoch_ms})
            await pipe.execute()
        except aioredis.RedisError as exc:
            logger.error(
                "Failed to upsert session index [%s]: %s",
                metadata.session_id,
                exc,
            )
            raise

    async def get(self, session_id: str) -> SessionMetadata | None:
        """按会话 ID 读取索引元数据。"""
        key = self._make_key(session_id)
        try:
            raw = await self._redis.get(key)
        except aioredis.RedisError as exc:
            logger.error("Failed to get session index [%s]: %s", session_id, exc)
            raise

        if raw is None:
            return None

        try:
            return _metadata_from_dict(json.loads(_decode(raw)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(
                "Failed to deserialize session index [%s]: %s",
                session_id,
                exc,
            )
            return None

    async def list_recent(self, limit: int = 20) -> list[SessionMetadata]:
        """按更新时间倒序列出最近会话索引。"""
        if limit <= 0:
            return []

        try:
            raw_ids = await self._redis.zrevrange(self._recent_zset_key, 0, limit - 1)
        except aioredis.RedisError as exc:
            logger.error("Failed to list recent session index ids: %s", exc)
            raise

        results: list[SessionMetadata] = []
        for raw_id in raw_ids:
            session_id = _decode(raw_id)
            metadata = await self.get(session_id)
            if metadata is None:
                try:
                    await self._redis.zrem(self._recent_zset_key, session_id)
                except aioredis.RedisError as exc:
                    logger.error(
                        "Failed to remove stale session index member [%s]: %s",
                        session_id,
                        exc,
                    )
                    raise
                continue
            results.append(metadata)
        return results

    async def delete(self, session_id: str) -> None:
        """幂等删除指定会话索引。"""
        key = self._make_key(session_id)
        try:
            pipe = self._redis.pipeline()
            pipe.delete(key)
            pipe.zrem(self._recent_zset_key, session_id)
            await pipe.execute()
        except aioredis.RedisError as exc:
            logger.error("Failed to delete session index [%s]: %s", session_id, exc)
            raise


def _decode(value: bytes | str) -> str:
    """把 Redis 返回值解码为字符串。"""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _metadata_to_dict(metadata: SessionMetadata) -> dict[str, object]:
    """把会话元数据转为 JSON 友好的字典。"""
    data: dict[str, object] = {
        "session_id": metadata.session_id,
        "updated_at_epoch_ms": metadata.updated_at_epoch_ms,
        "message_count": metadata.message_count,
        "preview": metadata.preview,
    }
    if metadata.created_at_epoch_ms is not None:
        data["created_at_epoch_ms"] = metadata.created_at_epoch_ms
    if metadata.model is not None:
        data["model"] = metadata.model
    return data


def _metadata_from_dict(data: dict[str, object]) -> SessionMetadata:
    """从字典恢复会话元数据值对象。"""
    return SessionMetadata(
        session_id=str(data["session_id"]),
        updated_at_epoch_ms=int(data["updated_at_epoch_ms"]),
        message_count=int(data["message_count"]),
        preview=str(data["preview"]),
        created_at_epoch_ms=(
            int(data["created_at_epoch_ms"])
            if data.get("created_at_epoch_ms") is not None
            else None
        ),
        model=str(data["model"]) if data.get("model") is not None else None,
    )
