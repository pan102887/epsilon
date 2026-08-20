"""本地文件 Run checkpoint store 适配器测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.run.exceptions import RunCheckpointSchemaError
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.run.local_file_run_checkpoint_store_adapter import (
    LocalFileRunCheckpointStoreAdapter,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _adapter(tmp_path) -> LocalFileRunCheckpointStoreAdapter:
    return LocalFileRunCheckpointStoreAdapter(
        root=tmp_path,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _checkpoint(run_id: str = "run-1", *, created_at: datetime = _NOW) -> DurableCheckpoint:
    return DurableCheckpoint(
        run_id=run_id,
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
        created_at=created_at,
    )


def _ledger(run_id: str = "run-1", key: str = "key-1") -> ToolResultLedgerEntry:
    return ToolResultLedgerEntry(
        run_id=run_id,
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


async def test_save_latest_and_list_checkpoints_are_monotonic(tmp_path) -> None:
    store = _adapter(tmp_path)

    first = await store.save_checkpoint(_checkpoint())
    second = await store.save_checkpoint(_checkpoint())

    assert (first.sequence, second.sequence) == (1, 2)
    assert first.checkpoint_id == "chk_000001"
    assert await store.latest_checkpoint("run-1") == second
    assert await store.list_checkpoints("run-1", after_sequence=None, limit=10) == [
        first,
        second,
    ]
    assert await store.list_checkpoints("run-1", after_sequence=1, limit=10) == [second]


async def test_tool_ledger_pending_and_completed_roundtrip(tmp_path) -> None:
    store = _adapter(tmp_path)

    pending = await store.put_tool_pending(_ledger())
    completed = await store.complete_tool_result(
        run_id="run-1",
        tool_execution_key=pending.tool_execution_key,
        result="ok",
        is_error=False,
        metadata={"duration_ms": 1},
    )

    assert pending.status is ToolLedgerStatus.PENDING
    assert completed.status is ToolLedgerStatus.COMPLETED
    assert completed.result == "ok"
    assert await store.get_tool_result("run-1", pending.tool_execution_key) == completed
    assert await store.list_tool_ledger("run-1") == [completed]


async def test_put_tool_pending_is_idempotent_for_same_key(tmp_path) -> None:
    store = _adapter(tmp_path)

    first = await store.put_tool_pending(_ledger())
    second = await store.put_tool_pending(_ledger())

    assert second == first
    assert len(await store.list_tool_ledger("run-1")) == 1


async def test_latest_checkpoint_rejects_incompatible_schema(tmp_path) -> None:
    store = _adapter(tmp_path)
    await store.save_checkpoint(_checkpoint())
    path = next((tmp_path / "runs" / "checkpoints").glob("*/*.jsonl"))
    line = path.read_text(encoding="utf-8").strip()
    path.write_text(
        line.replace('"schema_version":1', '"schema_version":2') + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RunCheckpointSchemaError):
        await store.latest_checkpoint("run-1")


async def test_trim_checkpoints_applies_count_ttl_and_ledger_limits(tmp_path) -> None:
    store = _adapter(tmp_path)
    old = _checkpoint(created_at=_NOW - timedelta(seconds=7200))
    await store.save_checkpoint(old)
    second = await store.save_checkpoint(_checkpoint())
    third = await store.save_checkpoint(_checkpoint())
    await store.put_tool_pending(_ledger(key="key-1"))
    await store.put_tool_pending(_ledger(key="key-2"))

    await store.trim_checkpoints(
        "run-1",
        CheckpointRetentionPolicy(
            max_checkpoint_count=1,
            ttl_seconds=3600,
            max_payload_bytes=4096,
            max_tool_ledger_count=1,
        ),
    )

    assert await store.list_checkpoints("run-1", after_sequence=None, limit=10) == [third]
    ledger = await store.list_tool_ledger("run-1")
    assert len(ledger) == 1
    assert ledger[0].tool_execution_key == "key-2"
    assert second.sequence == 2
