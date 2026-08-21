"""Run store checkpoint recovery method contract tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

fakeredis = pytest.importorskip("fakeredis.aioredis")

from domain.run.exceptions import RunLeaseConflictError  # noqa: E402
from domain.run.ports import RunStorePort  # noqa: E402
from domain.run.value_objects import (  # noqa: E402
    RunCreateRequest,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter  # noqa: E402
from infrastructure.persistence.local_file.file_lock import LockFactory  # noqa: E402
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy  # noqa: E402
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter  # noqa: E402
from infrastructure.run.redis_run_store_adapter import RedisRunStoreAdapter  # noqa: E402

pytestmark = pytest.mark.asyncio


def _request() -> RunCreateRequest:
    return RunCreateRequest(
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": "hello"},
            model="model-a",
        ),
        client_request_id=None,
    )


@pytest.fixture(params=("local", "redis"))
async def store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[RunStorePort]:
    if request.param == "local":
        yield LocalFileRunStoreAdapter(
            root=tmp_path,
            lock_factory=LockFactory(acquire_timeout_ms=1000),
            path_policy=CrossPlatformPathPolicy(),
            atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
        )
        return

    redis_client = fakeredis.FakeRedis()
    yield RedisRunStoreAdapter(redis_client=redis_client, conflict_retry_max=8)
    await redis_client.aclose()


async def _running_expired(store: RunStorePort) -> RunSnapshot:
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=0)
    assert claimed is not None
    assert claimed.run_id == created.run_id
    assert claimed.lease is not None
    return claimed


async def test_list_expired_leased_runs_returns_only_expired_running_or_cancel_requested(
    store: RunStorePort,
) -> None:
    expired = await _running_expired(store)
    active_created = await store.create_run(_request())
    active = await store.claim_next(owner_id="owner-b", lease_seconds=3600)
    assert active is not None
    assert active.run_id == active_created.run_id

    cancel_created = await store.create_run(_request())
    cancel_claimed = await store.claim_next(owner_id="owner-c", lease_seconds=0)
    assert cancel_claimed is not None
    cancel_requested = await store.request_cancel(cancel_created.run_id)

    assert expired.lease is not None
    expired_runs = await store.list_expired_leased_runs(
        now=expired.lease.lease_until + timedelta(seconds=1)
    )

    assert [snapshot.run_id for snapshot in expired_runs] == [
        expired.run_id,
        cancel_requested.run_id,
    ]


async def test_enqueue_recovery_requeues_expired_run_and_records_metadata(
    store: RunStorePort,
) -> None:
    expired = await _running_expired(store)

    recovered = await store.enqueue_recovery(
        run_id=expired.run_id,
        latest_checkpoint_id="chk_000001",
        recovery_attempt_count=expired.recovery_attempt_count + 1,
    )
    loaded = await store.get_run(expired.run_id)
    claimed_again = await store.claim_next(owner_id="owner-b", lease_seconds=30)

    assert loaded == recovered
    assert recovered.status is RunStatus.QUEUED
    assert recovered.lease is None
    assert recovered.latest_checkpoint_id == "chk_000001"
    assert recovered.recoverable is True
    assert recovered.recovery_attempt_count == 1
    assert recovered.last_recovery_error is None
    assert claimed_again is not None
    assert claimed_again.run_id == expired.run_id


async def test_enqueue_recovery_rejects_non_expired_or_changed_run(
    store: RunStorePort,
) -> None:
    created = await store.create_run(_request())
    active = await store.claim_next(owner_id="owner-a", lease_seconds=3600)
    assert active is not None

    with pytest.raises(RunLeaseConflictError):
        await store.enqueue_recovery(
            run_id=created.run_id,
            latest_checkpoint_id="chk_000001",
            recovery_attempt_count=1,
        )


async def test_mark_lost_expired_run_records_recovery_error(
    store: RunStorePort,
) -> None:
    expired = await _running_expired(store)

    lost = await store.mark_lost_expired_run(
        run_id=expired.run_id,
        reason="checkpoint_schema_mismatch",
        recovery_error={"message": "schema mismatch", "checkpoint_id": "chk_000001"},
    )
    loaded = await store.get_run(expired.run_id)

    assert loaded == lost
    assert lost.status is RunStatus.LOST
    assert lost.lease is None
    assert lost.terminal_reason == "checkpoint_schema_mismatch"
    assert lost.recoverable is False
    assert lost.last_recovery_error == {
        "message": "schema mismatch",
        "checkpoint_id": "chk_000001",
    }


async def test_stage_three_mark_lost_expired_leases_remains_compatible(
    store: RunStorePort,
) -> None:
    expired = await _running_expired(store)

    assert expired.lease is not None
    lost = await store.mark_lost_expired_leases(
        now=expired.lease.lease_until + timedelta(seconds=1)
    )

    assert [snapshot.run_id for snapshot in lost] == [expired.run_id]
    assert lost[0].status is RunStatus.LOST
    assert lost[0].terminal_reason == "lease_expired"
