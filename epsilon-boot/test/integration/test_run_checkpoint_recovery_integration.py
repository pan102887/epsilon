"""Phase four Run checkpoint recovery integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from application.run.run_checkpoint_recovery_service import RunRecoveryService
from domain.chat.context import ConversationContext
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RunCreateRequest,
    RunEventType,
    RunKind,
    RunPayload,
    RunStatus,
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
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter
from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter

pytestmark = pytest.mark.asyncio


async def test_file_backend_recovers_expired_run_from_compatible_checkpoint(tmp_path) -> None:
    run_store, checkpoint_store = _stores(tmp_path)
    service = _service(run_store, checkpoint_store)
    claimed = await _expired_run(run_store)
    checkpoint = await checkpoint_store.save_checkpoint(_checkpoint(claimed.run_id))

    recovered = await service.sweep_expired_leases(
        now=claimed.lease.lease_until + timedelta(seconds=1)
    )
    loaded = await run_store.get_run(claimed.run_id)
    events = await run_store.list_events(claimed.run_id, after_cursor=None, limit=10)

    assert [snapshot.status for snapshot in recovered] == [RunStatus.QUEUED]
    assert loaded is not None
    assert loaded.latest_checkpoint_id == checkpoint.checkpoint_id
    assert loaded.recovery_attempt_count == 1
    assert [event.event_type for event in events] == [RunEventType.RUN_RECOVERY_QUEUED]


async def test_completed_tool_ledger_does_not_block_recovery(tmp_path) -> None:
    run_store, checkpoint_store = _stores(tmp_path)
    service = _service(run_store, checkpoint_store)
    claimed = await _expired_run(run_store)
    await checkpoint_store.save_checkpoint(_checkpoint(claimed.run_id))
    await checkpoint_store.put_tool_pending(
        _ledger(claimed.run_id, status=ToolLedgerStatus.COMPLETED, result="cached")
    )

    recovered = await service.sweep_expired_leases(
        now=claimed.lease.lease_until + timedelta(seconds=1)
    )

    assert [snapshot.status for snapshot in recovered] == [RunStatus.QUEUED]


async def test_pending_manual_review_tool_marks_run_lost(tmp_path) -> None:
    run_store, checkpoint_store = _stores(tmp_path)
    service = _service(run_store, checkpoint_store)
    claimed = await _expired_run(run_store)
    await checkpoint_store.save_checkpoint(_checkpoint(claimed.run_id))
    await checkpoint_store.put_tool_pending(
        _ledger(claimed.run_id, status=ToolLedgerStatus.PENDING, result=None)
    )

    recovered = await service.sweep_expired_leases(
        now=claimed.lease.lease_until + timedelta(seconds=1)
    )
    loaded = await run_store.get_run(claimed.run_id)
    events = await run_store.list_events(claimed.run_id, after_cursor=None, limit=10)

    assert [snapshot.status for snapshot in recovered] == [RunStatus.LOST]
    assert loaded is not None
    assert loaded.last_recovery_error is not None
    assert loaded.last_recovery_error["reason"] == "pending_tool_replay_blocked"
    assert [event.event_type for event in events] == [RunEventType.RUN_RECOVERY_FAILED]


def _stores(tmp_path):
    lock_factory = LockFactory(acquire_timeout_ms=1000)
    path_policy = CrossPlatformPathPolicy()
    atomic_writer = TempFileAtomicWriter(fsync_on_write=False)
    return (
        LocalFileRunStoreAdapter(
            root=tmp_path,
            lock_factory=lock_factory,
            path_policy=path_policy,
            atomic_writer=atomic_writer,
        ),
        LocalFileRunCheckpointStoreAdapter(
            root=tmp_path,
            lock_factory=lock_factory,
            path_policy=path_policy,
            atomic_writer=atomic_writer,
        ),
    )


def _service(run_store, checkpoint_store) -> RunRecoveryService:
    return RunRecoveryService(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        event_store=run_store,
        retention_policy=CheckpointRetentionPolicy(10, 3600, 4096, 10),
        max_recovery_attempts=3,
        auto_recovery_enabled=True,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )


async def _expired_run(run_store):
    created = await run_store.create_run(
        RunCreateRequest(
            payload=RunPayload(
                kind=RunKind.CHAT,
                session_id="session-1",
                chat={"message": "hello"},
                model="m",
            ),
            client_request_id=None,
        )
    )
    claimed = await run_store.claim_next(owner_id="owner-a", lease_seconds=0)
    assert claimed is not None
    assert claimed.run_id == created.run_id
    assert claimed.lease is not None
    return claimed


def _checkpoint(run_id: str) -> DurableCheckpoint:
    context = ConversationContext()
    context.add_user_message("hello")
    return DurableCheckpoint(
        run_id=run_id,
        checkpoint_id="pending",
        sequence=0,
        phase=CheckpointPhase.MODEL_COMPLETED,
        context_snapshot=context.to_dict(),
        round_num=1,
        usage={"total_tokens": 1},
        trace_summary={},
        segment_metadata={},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=datetime.now(UTC),
    )


def _ledger(
    run_id: str,
    *,
    status: ToolLedgerStatus,
    result: str | None,
) -> ToolResultLedgerEntry:
    now = datetime.now(UTC)
    return ToolResultLedgerEntry(
        run_id=run_id,
        tool_execution_key="tool-key",
        status=status,
        tool_name="write_file",
        tool_call_id="call-1",
        arguments_digest="digest",
        replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
        side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
        idempotency_key=None,
        result=result,
        is_error=False,
        metadata={},
        created_at=now,
        updated_at=now,
    )
