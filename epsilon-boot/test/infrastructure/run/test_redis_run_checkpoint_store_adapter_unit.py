"""Redis Run checkpoint store 适配器测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

fakeredis = pytest.importorskip("fakeredis.aioredis")

from domain.run.exceptions import RunCheckpointSchemaError  # noqa: E402
from domain.run.value_objects import (  # noqa: E402
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from infrastructure.run.redis_run_checkpoint_store_adapter import (  # noqa: E402
    RedisRunCheckpointStoreAdapter,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


@pytest.fixture
def store(redis_client) -> RedisRunCheckpointStoreAdapter:
    return RedisRunCheckpointStoreAdapter(redis_client=redis_client, conflict_retry_max=8)


def _checkpoint() -> DurableCheckpoint:
    return DurableCheckpoint(
        run_id="run-1",
        checkpoint_id="pending",
        sequence=0,
        phase=CheckpointPhase.MODEL_COMPLETED,
        context_snapshot={"messages": []},
        round_num=1,
        usage={"total_tokens": 1},
        trace_summary={},
        segment_metadata={},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=_NOW,
    )


def _checkpoint_at(created_at: datetime) -> DurableCheckpoint:
    return DurableCheckpoint(
        **{
            **_checkpoint().__dict__,
            "created_at": created_at,
        }
    )


def _ledger(key: str = "key-1") -> ToolResultLedgerEntry:
    return ToolResultLedgerEntry(
        run_id="run-1",
        tool_execution_key=key,
        status=ToolLedgerStatus.PENDING,
        tool_name="write_file",
        tool_call_id="call-1",
        arguments_digest="digest",
        replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
        side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
        idempotency_key=None,
        result=None,
        is_error=False,
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )


async def test_save_latest_and_list_checkpoints_use_redis_keys(
    store: RedisRunCheckpointStoreAdapter,
    redis_client,
) -> None:
    first = await store.save_checkpoint(_checkpoint())
    second = await store.save_checkpoint(_checkpoint())

    assert (first.sequence, second.sequence) == (1, 2)
    assert await store.latest_checkpoint("run-1") == second
    assert await store.list_checkpoints("run-1", after_sequence=1, limit=10) == [second]
    assert await redis_client.llen("run:run-1:checkpoints") == 2
    assert int(await redis_client.get("run:run-1:checkpoint_seq")) == 2


async def test_tool_ledger_pending_and_completed_roundtrip(
    store: RedisRunCheckpointStoreAdapter,
    redis_client,
) -> None:
    pending = await store.put_tool_pending(_ledger())
    completed = await store.complete_tool_result(
        run_id="run-1",
        tool_execution_key=pending.tool_execution_key,
        result="ok",
        is_error=False,
        metadata={"duration_ms": 1},
    )

    assert completed.status is ToolLedgerStatus.COMPLETED
    assert await store.get_tool_result("run-1", "key-1") == completed
    assert await redis_client.hlen("run:run-1:tool_ledger") == 1


async def test_put_tool_pending_is_idempotent_for_same_key(
    store: RedisRunCheckpointStoreAdapter,
) -> None:
    first = await store.put_tool_pending(_ledger())
    second = await store.put_tool_pending(_ledger())

    assert second == first
    assert len(await store.list_tool_ledger("run-1")) == 1


async def test_latest_checkpoint_rejects_incompatible_schema(
    store: RedisRunCheckpointStoreAdapter,
    redis_client,
) -> None:
    await store.save_checkpoint(_checkpoint())
    raw = (await redis_client.lindex("run:run-1:checkpoints", -1)).decode("utf-8")
    await redis_client.lset(
        "run:run-1:checkpoints",
        0,
        raw.replace('"schema_version":1', '"schema_version":2'),
    )

    with pytest.raises(RunCheckpointSchemaError):
        await store.latest_checkpoint("run-1")


async def test_trim_checkpoints_and_ledger_limits(
    store: RedisRunCheckpointStoreAdapter,
) -> None:
    await store.save_checkpoint(_checkpoint())
    await store.save_checkpoint(_checkpoint())
    await store.put_tool_pending(_ledger("key-1"))
    await store.put_tool_pending(_ledger("key-2"))

    await store.trim_checkpoints(
        "run-1",
        CheckpointRetentionPolicy(1, 3600, 4096, 1),
    )

    checkpoints = await store.list_checkpoints("run-1", after_sequence=None, limit=10)
    ledger = await store.list_tool_ledger("run-1")

    assert [checkpoint.sequence for checkpoint in checkpoints] == [2]
    assert [entry.tool_execution_key for entry in ledger] == ["key-2"]


async def test_trim_checkpoints_applies_ttl(
    store: RedisRunCheckpointStoreAdapter,
) -> None:
    await store.save_checkpoint(_checkpoint_at(_NOW - timedelta(seconds=7200)))
    await store.save_checkpoint(_checkpoint_at(_NOW))

    await store.trim_checkpoints(
        "run-1",
        CheckpointRetentionPolicy(10, 3600, 4096, 10),
    )

    checkpoints = await store.list_checkpoints("run-1", after_sequence=None, limit=10)

    assert [checkpoint.created_at for checkpoint in checkpoints] == [_NOW]
