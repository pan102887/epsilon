"""Redis Run Store 适配器契约测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import pytest
import redis.asyncio as aioredis

fakeredis = pytest.importorskip("fakeredis.aioredis")

from domain.run.exceptions import (  # noqa: E402
    RunContinuationUnavailableError,
    RunIdempotencyConflictError,
    RunLeaseConflictError,
)
from domain.run.ports import ApprovalResumeStoreResult  # noqa: E402
from domain.run.value_objects import (  # noqa: E402
    EventRetentionPolicy,
    RunCreateRequest,
    RunEventType,
    RunKind,
    RunPayload,
    RunStatus,
)
from infrastructure.run.redis_run_store_adapter import RedisRunStoreAdapter  # noqa: E402

pytestmark = pytest.mark.asyncio


class _TestRedisClient(Protocol):
    """测试断言直接使用的最小 Redis 命令集合。"""

    async def exists(self, name: str) -> int: ...

    async def llen(self, name: str) -> int: ...

    async def ttl(self, name: str) -> int: ...

    def scan_iter(self, match: str) -> AsyncIterator[bytes]: ...


@pytest.fixture
def redis_client() -> _TestRedisClient:
    return cast(_TestRedisClient, fakeredis.FakeRedis())


@pytest.fixture
def store(redis_client: _TestRedisClient) -> RedisRunStoreAdapter:
    return RedisRunStoreAdapter(
        redis_client=cast(aioredis.Redis, redis_client), conflict_retry_max=8
    )


def _payload(message: str = "hello") -> RunPayload:
    return RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": message, "metadata": {"b": 2, "a": 1}},
        model="model-a",
    )


def _request(
    message: str = "hello", client_request_id: str | None = "client-1"
) -> RunCreateRequest:
    return RunCreateRequest(
        payload=_payload(message),
        client_request_id=client_request_id,
    )


async def test_create_run_persists_snapshot_index_and_default_keys(
    store: RedisRunStoreAdapter,
    redis_client: _TestRedisClient,
) -> None:
    """创建 Run 应写入 spec 要求的快照、幂等索引和队列 key。"""

    snapshot = await store.create_run(_request())
    loaded = await store.get_run(snapshot.run_id)
    indexed = await store.get_by_client_request_id("client-1")

    assert loaded == snapshot
    assert indexed == snapshot
    assert snapshot.status is RunStatus.QUEUED
    assert snapshot.payload.kind is RunKind.CHAT
    assert snapshot.created_at.tzinfo is not None
    assert await redis_client.exists(f"run:{snapshot.run_id}:snapshot") == 1
    assert await redis_client.llen("run:queue") == 1

    index_keys = [
        key.decode("utf-8") async for key in redis_client.scan_iter("run:index:client_request:*")
    ]
    assert len(index_keys) == 1


async def test_create_run_returns_existing_snapshot_for_same_idempotent_payload(
    store: RedisRunStoreAdapter,
) -> None:
    """相同 client_request_id 与相同 payload_hash 应返回既有 Run。"""

    first = await store.create_run(_request())
    second = await store.create_run(_request())

    assert second == first
    assert await store.count_by_status({RunStatus.QUEUED}) == 1


async def test_create_run_rejects_same_idempotency_key_with_different_payload(
    store: RedisRunStoreAdapter,
) -> None:
    """相同 client_request_id 但 payload_hash 不同必须抛幂等冲突。"""

    await store.create_run(_request("first"))

    with pytest.raises(RunIdempotencyConflictError) as exc_info:
        await store.create_run(_request("second"))

    assert "second" not in exc_info.value.message


async def test_concurrent_create_run_with_same_idempotency_key_creates_one_snapshot(
    store: RedisRunStoreAdapter,
    redis_client: _TestRedisClient,
) -> None:
    """并发提交相同幂等键和 payload 时只创建一个 queued Run。"""

    results = await asyncio.gather(*(store.create_run(_request()) for _ in range(8)))

    assert len({snapshot.run_id for snapshot in results}) == 1
    assert await store.count_by_status({RunStatus.QUEUED}) == 1
    snapshot_keys = [key async for key in redis_client.scan_iter("run:*:snapshot")]
    assert len(snapshot_keys) == 1
    assert await redis_client.llen("run:queue") == 1


async def test_concurrent_claim_next_allows_only_one_owner(
    store: RedisRunStoreAdapter,
) -> None:
    """并发 claim_next 同一 queued Run 时只允许一个 worker 成功领取。"""

    created = await store.create_run(_request(client_request_id=None))

    results = await asyncio.gather(
        store.claim_next(owner_id="owner-a", lease_seconds=30),
        store.claim_next(owner_id="owner-b", lease_seconds=30),
        store.claim_next(owner_id="owner-c", lease_seconds=30),
    )

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].run_id == created.run_id
    assert claimed[0].status is RunStatus.RUNNING
    assert claimed[0].lease is not None
    assert claimed[0].lease.owner_id in {"owner-a", "owner-b", "owner-c"}
    assert await store.count_by_status({RunStatus.RUNNING}) == 1


async def test_owner_mismatch_refresh_and_mark_raise_lease_conflict(
    store: RedisRunStoreAdapter,
) -> None:
    """心跳和 worker 终态写入必须校验当前 lease owner。"""

    created = await store.create_run(_request(client_request_id=None))
    await store.claim_next(owner_id="owner-a", lease_seconds=30)

    with pytest.raises(RunLeaseConflictError):
        await store.refresh_lease(
            run_id=created.run_id,
            owner_id="owner-b",
            lease_seconds=30,
        )
    with pytest.raises(RunLeaseConflictError):
        await store.mark_failed(
            run_id=created.run_id,
            owner_id="owner-b",
            error={"message": "failed"},
        )


async def test_request_cancel_and_worker_mark_cancelled_preserve_local_semantics(
    store: RedisRunStoreAdapter,
) -> None:
    """queued 取消直接终态，running 取消先进入 cancel_requested 再由 owner 收敛。"""

    queued = await store.create_run(_request(client_request_id=None))
    cancelled = await store.request_cancel(queued.run_id)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.terminal_reason == "cancelled"
    assert await store.claim_next(owner_id="owner-a", lease_seconds=30) is None

    running = await store.create_run(_request(client_request_id=None))
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=30)
    assert claimed is not None
    requested = await store.request_cancel(running.run_id)
    assert requested.status is RunStatus.CANCEL_REQUESTED
    assert requested.lease is not None
    finished = await store.mark_cancelled(
        run_id=running.run_id,
        owner_id="owner-a",
        reason="user_cancelled",
    )
    assert finished.status is RunStatus.CANCELLED
    assert finished.lease is None


async def test_mark_lost_expired_leases_marks_running_as_lost(
    store: RedisRunStoreAdapter,
) -> None:
    """过期 running lease 必须被 sweep 标记为 lost 终态。"""

    created = await store.create_run(_request(client_request_id=None))
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=1)
    assert claimed is not None
    assert claimed.lease is not None

    lost = await store.mark_lost_expired_leases(
        now=claimed.lease.lease_until + timedelta(seconds=1)
    )
    loaded = await store.get_run(created.run_id)

    assert [snapshot.run_id for snapshot in lost] == [created.run_id]
    assert loaded is not None
    assert loaded.status is RunStatus.LOST
    assert loaded.lease is None
    assert loaded.terminal_reason == "lease_expired"


async def test_event_cursor_is_monotonic_and_updates_snapshot(
    store: RedisRunStoreAdapter,
) -> None:
    """追加事件必须分配同一 Run 内单调 cursor 并更新快照 latest_event_cursor。"""

    created = await store.create_run(_request(client_request_id=None))

    first = await store.append_event(
        created.run_id,
        RunEventType.RUN_CREATED,
        {"at": datetime(2026, 1, 1, tzinfo=UTC), "status": RunStatus.QUEUED},
    )
    second = await store.append_event(
        created.run_id,
        RunEventType.RUN_QUEUED,
        {"nested": {"kind": RunKind.CHAT}},
    )
    loaded = await store.get_run(created.run_id)

    assert (first.cursor, second.cursor) == (1, 2)
    assert first.payload["at"] == "2026-01-01T00:00:00+00:00"
    assert second.payload["nested"]["kind"] == "chat"
    assert loaded is not None
    assert loaded.latest_event_cursor == 2
    assert await store.list_events(created.run_id, after_cursor=None, limit=10) == [
        first,
        second,
    ]


async def test_trim_events_exposes_retention_floor_and_sets_ttl(
    store: RedisRunStoreAdapter,
    redis_client: _TestRedisClient,
) -> None:
    """LTRIM 裁剪后 first_cursor 暴露 replay 窗口，TTL 应写入事件 list。"""

    created = await store.create_run(_request(client_request_id=None))
    for index in range(5):
        await store.append_event(
            created.run_id,
            RunEventType.SEGMENT_DONE,
            {"index": index},
        )

    await store.trim_events(
        created.run_id,
        EventRetentionPolicy(max_event_count=2, ttl_seconds=3600),
    )

    retained = await store.list_events(created.run_id, after_cursor=None, limit=10)
    assert await store.first_cursor(created.run_id) == 4
    assert [event.cursor for event in retained] == [4, 5]
    assert await store.list_events(created.run_id, after_cursor=3, limit=10) == retained
    assert await store.list_events(created.run_id, after_cursor=0, limit=10) == retained
    assert await redis_client.ttl(f"run:{created.run_id}:events") > 0


async def test_enqueue_continue_and_resolve_approval_resume_match_local_semantics(
    store: RedisRunStoreAdapter,
) -> None:
    """paused continue 和 awaiting_approval resume 应重新入队同一 Run。"""

    created = await store.create_run(_request(client_request_id=None))

    with pytest.raises(RunContinuationUnavailableError):
        await store.enqueue_continue(run_id=created.run_id)

    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=30)
    assert claimed is not None
    paused = await store.mark_paused(
        run_id=created.run_id,
        owner_id="owner-a",
        result={"partial": True},
    )
    assert paused.status is RunStatus.PAUSED
    continued = await store.enqueue_continue(run_id=created.run_id, model="model-b")
    assert continued.status is RunStatus.QUEUED
    assert continued.payload.model == "model-b"
    reclamed = await store.claim_next(owner_id="owner-b", lease_seconds=30)
    assert reclamed is not None
    awaiting = await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="owner-b",
        approval_id="approval-1",
        result={"tool": "dangerous"},
    )
    assert awaiting.status is RunStatus.AWAITING_APPROVAL

    await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        lease_seconds=60,
    )
    resumed = await store.resolve_approval_resume(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        result=ApprovalResumeStoreResult(status="queued", result={"accepted": True}),
    )

    assert resumed.status is RunStatus.QUEUED
    assert resumed.approval_id is None
    assert resumed.lease is None
    assert resumed.result == {"accepted": True}
