"""``RedisSessionIndexAdapter`` 单元测试。"""

from typing import cast

import pytest
import redis.asyncio as aioredis

fakeredis = pytest.importorskip("fakeredis.aioredis")

from domain.chat.value_objects import SessionMetadata  # noqa: E402
from infrastructure.session.redis_session_index_adapter import (  # noqa: E402
    RedisSessionIndexAdapter,
)


@pytest.fixture
def redis_client() -> aioredis.Redis:
    """创建 fakeredis 异步客户端。"""
    return cast(aioredis.Redis, fakeredis.FakeRedis())


def _metadata(
    session_id: str,
    *,
    updated_at: int,
    preview: str = "hello",
) -> SessionMetadata:
    """构造测试用会话元数据。"""
    return SessionMetadata(
        session_id=session_id,
        created_at_epoch_ms=updated_at - 10,
        updated_at_epoch_ms=updated_at,
        message_count=1,
        preview=preview,
        model="qwen3",
    )


async def test_upsert_then_get_roundtrip(redis_client: aioredis.Redis) -> None:
    """验证 Redis upsert 后可读取同等元数据。"""
    adapter = RedisSessionIndexAdapter(redis_client, ttl_seconds=42)
    metadata = _metadata("s1", updated_at=1000)

    await adapter.upsert(metadata)

    assert await adapter.get("s1") == metadata
    assert await redis_client.ttl("session:index:s1") <= 42
    assert await redis_client.ttl("session:index:s1") > 0


async def test_get_missing_returns_none(redis_client: aioredis.Redis) -> None:
    """验证缺失 metadata key 返回 None。"""
    adapter = RedisSessionIndexAdapter(redis_client)

    assert await adapter.get("missing") is None


async def test_list_recent_uses_zset_descending_order(redis_client: aioredis.Redis) -> None:
    """验证 list_recent 按 ZSET score 倒序返回。"""
    adapter = RedisSessionIndexAdapter(redis_client)
    await adapter.upsert(_metadata("old", updated_at=1000))
    await adapter.upsert(_metadata("new", updated_at=3000))
    await adapter.upsert(_metadata("mid", updated_at=2000))

    sessions = await adapter.list_recent(limit=2)

    assert [item.session_id for item in sessions] == ["new", "mid"]


async def test_list_recent_removes_stale_zset_member(redis_client: aioredis.Redis) -> None:
    """验证 ZSET 中存在但 metadata 缺失的成员会被清理。"""
    adapter = RedisSessionIndexAdapter(redis_client)
    await redis_client.zadd("session:index:recent", {"stale": 1000})

    assert await adapter.list_recent() == []
    assert await redis_client.zscore("session:index:recent", "stale") is None


async def test_delete_removes_metadata_and_zset_member(redis_client: aioredis.Redis) -> None:
    """验证 delete 同时删除 metadata key 与 recent zset member。"""
    adapter = RedisSessionIndexAdapter(redis_client)
    await adapter.upsert(_metadata("s1", updated_at=1000))

    await adapter.delete("s1")
    await adapter.delete("s1")

    assert await adapter.get("s1") is None
    assert await redis_client.zscore("session:index:recent", "s1") is None


async def test_list_recent_non_positive_limit_returns_empty(
    redis_client: aioredis.Redis,
) -> None:
    """验证非正 limit 不访问 Redis 列表。"""
    adapter = RedisSessionIndexAdapter(redis_client)
    await adapter.upsert(_metadata("s1", updated_at=1000))

    assert await adapter.list_recent(limit=0) == []


async def test_corrupted_metadata_returns_none(redis_client: aioredis.Redis) -> None:
    """验证损坏 metadata JSON 读取为 None。"""
    adapter = RedisSessionIndexAdapter(redis_client)
    await redis_client.set("session:index:bad", "{not-json")

    assert await adapter.get("bad") is None
