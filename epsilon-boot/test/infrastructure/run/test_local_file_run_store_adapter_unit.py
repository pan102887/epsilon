"""本地文件 Run Store 适配器契约测试。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest

from domain.run.exceptions import (
    RunContinuationUnavailableError,
    RunIdempotencyConflictError,
    RunLeaseConflictError,
)
from domain.run.ports import ApprovalResumeStoreResult
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCreateRequest,
    RunEventType,
    RunKind,
    RunPayload,
    RunStatus,
)
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter

pytestmark = pytest.mark.asyncio

T = TypeVar("T")


async def _run_in_threads(  # noqa: UP047 - Python 3.11 compatibility
    operations: list[Callable[[], Coroutine[Any, Any, T]]],
) -> list[T]:
    """在线程内用独立 event loop 同时执行 async adapter 操作。"""

    barrier = threading.Barrier(len(operations))
    results: list[T | None] = [None] * len(operations)
    errors: list[BaseException | None] = [None] * len(operations)

    def run(index: int, operation: Callable[[], Coroutine[Any, Any, T]]) -> None:
        try:
            barrier.wait(timeout=5)
            results[index] = asyncio.run(operation())
        except BaseException as exc:
            errors[index] = exc

    threads = [
        threading.Thread(target=run, args=(index, operation))
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    alive_threads = [thread for thread in threads if thread.is_alive()]
    if alive_threads:
        raise TimeoutError("threaded adapter operations did not finish")
    for error in errors:
        if error is not None:
            raise error
    return [result for result in results if result is not None]


def _adapter(tmp_path: Path) -> LocalFileRunStoreAdapter:
    """使用真实本地文件 helper 构造测试适配器。"""

    return LocalFileRunStoreAdapter(
        root=tmp_path,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _payload(message: str = "hello") -> RunPayload:
    """构造可稳定哈希的聊天 Run payload。"""

    return RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": message, "metadata": {"b": 2, "a": 1}},
        model="model-a",
    )


def _request(
    message: str = "hello", client_request_id: str | None = "client-1"
) -> RunCreateRequest:
    """构造创建请求。"""

    return RunCreateRequest(
        payload=_payload(message),
        client_request_id=client_request_id,
    )


async def test_create_run_persists_snapshot_and_client_index_atomically(tmp_path: Path) -> None:
    """创建 Run 应写入快照和幂等索引，并能完整反序列化。"""

    store = _adapter(tmp_path)

    snapshot = await store.create_run(_request())
    loaded = await store.get_run(snapshot.run_id)
    indexed = await store.get_by_client_request_id("client-1")

    assert loaded == snapshot
    assert indexed == snapshot
    assert snapshot.status is RunStatus.QUEUED
    assert snapshot.payload.kind is RunKind.CHAT
    assert snapshot.created_at.tzinfo is not None

    policy = CrossPlatformPathPolicy()
    snapshot_bucket, _ = policy.hash_session_id(snapshot.run_id)
    index_bucket, index_stem = policy.hash_session_id("client-1")
    assert (tmp_path / "runs" / "snapshots" / snapshot_bucket / f"{snapshot.run_id}.json").exists()
    assert (
        tmp_path / "runs" / "indexes" / "client_request" / index_bucket / f"{index_stem}.json"
    ).exists()


async def test_create_run_returns_existing_snapshot_for_same_idempotent_payload(
    tmp_path: Path,
) -> None:
    """相同 client_request_id 与相同 payload_hash 应返回既有 Run。"""

    store = _adapter(tmp_path)

    first = await store.create_run(_request())
    second = await store.create_run(_request())

    assert second == first
    assert await store.count_by_status({RunStatus.QUEUED}) == 1


async def test_concurrent_create_run_with_same_idempotency_key_creates_one_snapshot(
    tmp_path: Path,
) -> None:
    """并发提交相同幂等键和 payload 时只创建一个 queued Run。"""

    results = await _run_in_threads(
        [
            lambda: _adapter(tmp_path).create_run(_request()),
            lambda: _adapter(tmp_path).create_run(_request()),
        ]
    )

    run_ids = {snapshot.run_id for snapshot in results}
    assert len(run_ids) == 1

    store = _adapter(tmp_path)
    assert await store.count_by_status({RunStatus.QUEUED}) == 1
    snapshot_paths = list((tmp_path / "runs" / "snapshots").glob("*/*.json"))
    index_paths = list((tmp_path / "runs" / "indexes" / "client_request").glob("*/*.json"))
    assert len(snapshot_paths) == 1
    assert len(index_paths) == 1


async def test_create_run_rejects_same_idempotency_key_with_different_payload(
    tmp_path: Path,
) -> None:
    """相同 client_request_id 但 payload_hash 不同必须抛幂等冲突。"""

    store = _adapter(tmp_path)
    await store.create_run(_request("first"))

    with pytest.raises(RunIdempotencyConflictError) as exc_info:
        await store.create_run(_request("second"))

    assert "second" not in exc_info.value.message


async def test_concurrent_claim_next_allows_only_one_owner(tmp_path: Path) -> None:
    """并发 claim_next 同一 queued Run 时只允许一个 worker 成功领取。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request(client_request_id=None))

    results = await _run_in_threads(
        [
            lambda: _adapter(tmp_path).claim_next(owner_id="owner-a", lease_seconds=30),
            lambda: _adapter(tmp_path).claim_next(owner_id="owner-b", lease_seconds=30),
        ]
    )

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].run_id == created.run_id
    assert claimed[0].status is RunStatus.RUNNING
    assert claimed[0].lease is not None
    assert claimed[0].lease.owner_id in {"owner-a", "owner-b"}
    assert await store.count_by_status({RunStatus.RUNNING}) == 1


async def test_owner_mismatch_mark_failed_raises_lease_conflict(tmp_path: Path) -> None:
    """worker 终态写入必须校验当前 lease owner。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request(client_request_id=None))
    await store.claim_next(owner_id="owner-a", lease_seconds=30)

    with pytest.raises(RunLeaseConflictError):
        await store.mark_failed(
            run_id=created.run_id,
            owner_id="owner-b",
            error={"message": "failed"},
        )


async def test_mark_lost_expired_leases_marks_running_as_lost(tmp_path: Path) -> None:
    """过期 running lease 必须被 sweep 标记为 lost 终态。"""

    store = _adapter(tmp_path)
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


async def test_event_cursor_is_monotonic_and_updates_snapshot(tmp_path: Path) -> None:
    """追加事件必须分配同一 Run 内单调 cursor 并更新快照 latest_event_cursor。"""

    store = _adapter(tmp_path)
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
    assert loaded is not None
    assert loaded.latest_event_cursor == 2
    assert (await store.list_events(created.run_id, after_cursor=None, limit=10)) == [
        first,
        second,
    ]


async def test_trim_events_exposes_retention_floor_for_replay_expiry(tmp_path: Path) -> None:
    """max_event_count 裁剪后 first_cursor 暴露 replay 可用窗口起点。"""

    store = _adapter(tmp_path)
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


async def test_resolve_approval_resume_only_allows_awaiting_approval(tmp_path: Path) -> None:
    """审批恢复入口只能从 awaiting_approval 迁移到四类设计结果。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request(client_request_id=None))

    with pytest.raises(RunContinuationUnavailableError):
        await store.resolve_approval_resume(
            run_id=created.run_id,
            owner_id="approval-resume-a",
            result=ApprovalResumeStoreResult(status="queued", result={"ok": True}),
        )

    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=30)
    assert claimed is not None
    awaiting = await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="owner-a",
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
