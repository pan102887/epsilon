"""RedisSessionContextAdapter CAS 单元测试。"""

import asyncio
from typing import Any

import fakeredis.aioredis
import pytest

from domain.chat.context import ConversationContext
from domain.chat.exceptions import SessionConflictError
from infrastructure.session.redis_session_context_adapter import RedisSessionContextAdapter


@pytest.fixture
def redis_client() -> Any:
    """创建 fakeredis 异步客户端。"""
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def adapter(redis_client: Any) -> RedisSessionContextAdapter:
    """创建带 fakeredis 的适配器实例。"""
    return RedisSessionContextAdapter(
        redis_client=redis_client,
        conflict_retry_max=3,
    )


@pytest.mark.asyncio
async def test_compare_and_swap_single_writer_success(
    adapter: RedisSessionContextAdapter,
) -> None:
    """单写者 CAS 周期成功提交。"""

    async def mutator(ctx: ConversationContext) -> int:
        ctx.add_user_message("hello")
        return 42

    result = await adapter.compare_and_swap("sess-1", mutator)
    assert result == 42

    loaded = await adapter.load("sess-1")
    messages = loaded.get_messages()
    assert len(messages) == 1
    assert messages[0].content == "hello"


@pytest.mark.asyncio
async def test_compare_and_swap_two_writers_no_lost_update(redis_client: Any) -> None:
    """双写者并发 CAS 不丢更新。"""
    adapter = RedisSessionContextAdapter(
        redis_client=redis_client,
        conflict_retry_max=5,
    )

    async def writer_a(ctx: ConversationContext) -> str:
        await asyncio.sleep(0.01)
        ctx.add_user_message("from_a")
        return "a"

    async def writer_b(ctx: ConversationContext) -> str:
        await asyncio.sleep(0.01)
        ctx.add_user_message("from_b")
        return "b"

    results = await asyncio.gather(
        adapter.compare_and_swap("sess-1", writer_a),
        adapter.compare_and_swap("sess-1", writer_b),
    )
    assert set(results) == {"a", "b"}

    loaded = await adapter.load("sess-1")
    messages = loaded.get_messages()
    contents = {m.content for m in messages}
    assert "from_a" in contents
    assert "from_b" in contents


@pytest.mark.asyncio
async def test_compare_and_swap_retry_exhausted_raises_session_conflict_error(
    redis_client: Any,
) -> None:
    """持续冲突时抛出 SessionConflictError。"""
    from unittest.mock import patch

    adapter = RedisSessionContextAdapter(
        redis_client=redis_client,
        conflict_retry_max=2,
    )

    call_count = 0

    async def mutator(ctx: ConversationContext) -> None:
        nonlocal call_count
        call_count += 1
        ctx.add_user_message(f"attempt-{call_count}")
        return None

    original_pipeline = redis_client.pipeline

    class ForcedConflictPipeline:
        """模拟持续冲突的 pipeline。"""

        def __init__(self) -> None:
            self._pipe = original_pipeline(transaction=True)

        async def __aenter__(self) -> "ForcedConflictPipeline":
            self._pipe = await self._pipe.__aenter__()
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return await self._pipe.__aexit__(*args)

        async def watch(self, *args: Any) -> Any:
            return await self._pipe.watch(*args)

        async def get(self, *args: Any) -> Any:
            return await self._pipe.get(*args)

        def multi(self) -> Any:
            return self._pipe.multi()

        def set(self, *args: Any, **kwargs: Any) -> Any:
            return self._pipe.set(*args, **kwargs)

        async def execute(self) -> None:
            await self._pipe.reset()
            import redis.asyncio as aioredis

            raise aioredis.WatchError("forced conflict")

    def fake_pipeline(transaction: bool = False) -> ForcedConflictPipeline:
        return ForcedConflictPipeline()

    with (
        patch.object(redis_client, "pipeline", side_effect=fake_pipeline),
        pytest.raises(SessionConflictError) as exc_info,
    ):
        await adapter.compare_and_swap("sess-conflict", mutator)

    assert exc_info.value.session_id == "sess-conflict"
    assert exc_info.value.retry_count == 2


@pytest.mark.asyncio
async def test_compare_and_swap_preserves_ttl(redis_client: Any) -> None:
    """成功路径下 Redis key 仍带 TTL。"""
    adapter = RedisSessionContextAdapter(
        redis_client=redis_client,
        ttl_seconds=7200,
        conflict_retry_max=3,
    )

    async def mutator(ctx: ConversationContext) -> None:
        ctx.add_user_message("ttl-test")
        return None

    await adapter.compare_and_swap("sess-ttl", mutator)

    key = adapter.session_key("sess-ttl")
    ttl = await redis_client.ttl(key)
    assert ttl > 0
    assert ttl <= 7200


@pytest.mark.asyncio
async def test_save_load_delete_unchanged(
    adapter: RedisSessionContextAdapter,
) -> None:
    """既有方法签名与行为不变。"""
    ctx = ConversationContext()
    ctx.add_user_message("persist")
    await adapter.save("sess-legacy", ctx)

    loaded = await adapter.load("sess-legacy")
    assert loaded.get_messages()[0].content == "persist"

    await adapter.delete("sess-legacy")
    loaded2 = await adapter.load("sess-legacy")
    assert loaded2.get_messages() == []


@pytest.mark.asyncio
async def test_exists_tracks_redis_key_lifecycle(
    adapter: RedisSessionContextAdapter,
) -> None:
    """``exists`` 只反映 Redis 会话上下文 key 是否存在。"""
    ctx = ConversationContext()
    ctx.add_user_message("persist")

    assert await adapter.exists("sess-exists") is False

    await adapter.save("sess-exists", ctx)
    assert await adapter.exists("sess-exists") is True

    await adapter.delete("sess-exists")
    assert await adapter.exists("sess-exists") is False
