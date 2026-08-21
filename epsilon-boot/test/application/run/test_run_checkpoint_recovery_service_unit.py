"""Run checkpoint recovery service 单元测试模块。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_checkpoint_recovery_service import RunRecoveryService
from domain.agent.guardrails import GuardrailAction, GuardrailMode
from domain.chat.context import ConversationContext
from domain.run.ports import RunCheckpointStorePort, RunEventStorePort, RunStorePort
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRunStore:
    def __init__(self, snapshots: list[RunSnapshot]) -> None:
        self.snapshots = snapshots
        self.enqueued: list[dict[str, Any]] = []
        self.lost: list[tuple[str, str, dict[str, Any] | None]] = []

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        return list(self.snapshots)

    async def enqueue_recovery(
        self,
        *,
        run_id: str,
        latest_checkpoint_id: str,
        recovery_attempt_count: int,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        self.enqueued.append(
            {
                "run_id": run_id,
                "latest_checkpoint_id": latest_checkpoint_id,
                "recovery_attempt_count": recovery_attempt_count,
                "guardrail_summary": guardrail_summary,
                "workflow_run_state": workflow_run_state,
                "collaboration_summary": collaboration_summary,
            }
        )
        snapshot = self.snapshots[0]
        return replace(
            snapshot,
            status=RunStatus.QUEUED,
            latest_checkpoint_id=latest_checkpoint_id,
            recoverable=True,
            recovery_attempt_count=recovery_attempt_count,
            guardrail_summary=guardrail_summary
            if guardrail_summary is not None
            else snapshot.guardrail_summary,
            workflow_run_state=workflow_run_state
            if workflow_run_state is not None
            else snapshot.workflow_run_state,
            collaboration_summary=collaboration_summary
            if collaboration_summary is not None
            else snapshot.collaboration_summary,
            updated_at=_NOW,
        )

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        self.lost.append((run_id, reason, recovery_error))
        snapshot = self.snapshots[0]
        return replace(
            snapshot,
            status=RunStatus.LOST,
            recoverable=False,
            last_recovery_error=recovery_error or {"reason": reason},
            updated_at=_NOW,
        )


class _MemoryCheckpointStore:
    def __init__(
        self,
        checkpoint: DurableCheckpoint | None,
        ledger: list[ToolResultLedgerEntry] | None = None,
    ) -> None:
        self.checkpoint = checkpoint
        self.ledger = ledger or []

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        return self.checkpoint

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        return list(self.ledger)


class _MemoryEventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(run_id, len(self.events) + 1, event_type, payload, _NOW)
        self.events.append(event)
        return event


def _policy() -> CheckpointRetentionPolicy:
    return CheckpointRetentionPolicy(10, 3600, 4096, 100)


def _snapshot(
    *,
    status: RunStatus = RunStatus.RUNNING,
    recovery_attempt_count: int = 0,
    latest_checkpoint_id: str | None = None,
    guardrail_summary: dict[str, Any] | None = None,
    latest_event_cursor: int | None = None,
) -> RunSnapshot:
    payload = RunPayload(RunKind.CHAT, "s1", chat={"message": "hi"})
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=status,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={},
        latest_event_cursor=latest_event_cursor,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        recovery_attempt_count=recovery_attempt_count,
        latest_checkpoint_id=latest_checkpoint_id,
        guardrail_summary=guardrail_summary,
    )


def _checkpoint(
    *,
    schema_version: int = 1,
    context_snapshot: dict[str, Any] | None = None,
    usage: dict[str, int] | None = None,
    segment_metadata: dict[str, Any] | None = None,
) -> DurableCheckpoint:
    ctx = ConversationContext()
    ctx.add_user_message("hello")
    return DurableCheckpoint(
        run_id="run-1",
        checkpoint_id="chk-1",
        sequence=1,
        phase=CheckpointPhase.MODEL_COMPLETED,
        context_snapshot=context_snapshot if context_snapshot is not None else ctx.to_dict(),
        round_num=1,
        usage=usage or {},
        trace_summary={},
        segment_metadata=segment_metadata or {},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=schema_version,
        sanitized=False,
        truncated_fields=(),
        created_at=_NOW,
    )


def _pending_ledger(policy: ToolReplayPolicy) -> ToolResultLedgerEntry:
    return ToolResultLedgerEntry(
        run_id="run-1",
        tool_execution_key="key-1",
        status=ToolLedgerStatus.PENDING,
        tool_name="write_file",
        tool_call_id="call-1",
        arguments_digest="digest",
        replay_policy=policy,
        side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
        idempotency_key=None,
        result=None,
        is_error=False,
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )


def _service(
    run_store: _MemoryRunStore,
    checkpoint_store: _MemoryCheckpointStore,
    event_store: _MemoryEventStore,
    *,
    max_recovery_attempts: int = 3,
    auto_recovery_enabled: bool = True,
) -> RunRecoveryService:
    return RunRecoveryService(
        run_store=cast(RunStorePort, run_store),
        checkpoint_store=cast(RunCheckpointStorePort, checkpoint_store),
        event_store=cast(RunEventStorePort, event_store),
        retention_policy=_policy(),
        max_recovery_attempts=max_recovery_attempts,
        auto_recovery_enabled=auto_recovery_enabled,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )


@pytest.mark.asyncio
async def test_recoverable_run_is_requeued_and_evented() -> None:
    run_store = _MemoryRunStore([_snapshot()])
    events = _MemoryEventStore()
    service = _service(run_store, _MemoryCheckpointStore(_checkpoint()), events)

    snapshots = await service.sweep_expired_leases(now=_NOW)

    assert snapshots[0].status is RunStatus.QUEUED
    assert run_store.enqueued == [
        {
            "run_id": "run-1",
            "latest_checkpoint_id": "chk-1",
            "recovery_attempt_count": 1,
            "guardrail_summary": None,
            "workflow_run_state": None,
            "collaboration_summary": None,
        }
    ]
    assert events.events[-1].event_type is RunEventType.RUN_RECOVERY_QUEUED


@pytest.mark.asyncio
async def test_run_without_checkpoint_is_marked_lost() -> None:
    run_store = _MemoryRunStore([_snapshot()])
    events = _MemoryEventStore()
    service = _service(run_store, _MemoryCheckpointStore(None), events)

    await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued == []
    assert run_store.lost[0][1] == "checkpoint_missing"
    assert events.events[-1].event_type is RunEventType.RUN_RECOVERY_FAILED


@pytest.mark.asyncio
async def test_schema_incompatible_checkpoint_is_not_recovered() -> None:
    run_store = _MemoryRunStore([_snapshot()])
    service = _service(
        run_store,
        _MemoryCheckpointStore(_checkpoint(schema_version=2)),
        _MemoryEventStore(),
    )

    decision = await service.evaluate_recovery(_snapshot())

    assert decision.recoverable is False
    assert decision.reason == "checkpoint_schema_incompatible"


@pytest.mark.asyncio
async def test_invalid_context_snapshot_is_not_recovered() -> None:
    run_store = _MemoryRunStore([_snapshot()])
    service = _service(
        run_store,
        _MemoryCheckpointStore(_checkpoint(context_snapshot={"messages": [{"role": "bad"}]})),
        _MemoryEventStore(),
    )

    decision = await service.evaluate_recovery(_snapshot())

    assert decision.recoverable is False
    assert decision.reason == "context_deserialize_failed"


@pytest.mark.asyncio
async def test_unsafe_pending_ledger_blocks_recovery() -> None:
    run_store = _MemoryRunStore([_snapshot()])
    service = _service(
        run_store,
        _MemoryCheckpointStore(_checkpoint(), [_pending_ledger(ToolReplayPolicy.MANUAL_REVIEW)]),
        _MemoryEventStore(),
    )

    decision = await service.evaluate_recovery(_snapshot())

    assert decision.recoverable is False
    assert decision.reason == "pending_tool_replay_blocked"


@pytest.mark.asyncio
async def test_cancel_requested_is_not_business_recovered() -> None:
    run_store = _MemoryRunStore([_snapshot(status=RunStatus.CANCEL_REQUESTED)])
    events = _MemoryEventStore()
    service = _service(run_store, _MemoryCheckpointStore(_checkpoint()), events)

    await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued == []
    assert run_store.lost[0][1] == "cancel_requested"


@pytest.mark.asyncio
async def test_recovery_attempt_limit_blocks_recovery() -> None:
    run_store = _MemoryRunStore([_snapshot(recovery_attempt_count=3)])
    service = _service(
        run_store,
        _MemoryCheckpointStore(_checkpoint()),
        _MemoryEventStore(),
        max_recovery_attempts=3,
    )

    decision = await service.evaluate_recovery(_snapshot(recovery_attempt_count=3))

    assert decision.recoverable is False
    assert decision.reason == "recovery_attempts_exhausted"


@pytest.mark.asyncio
async def test_recovery_preserves_existing_guardrail_summary() -> None:
    summary = {
        "mode": GuardrailMode.OBSERVE.value,
        "action": GuardrailAction.OBSERVE.value,
        "message": "kept",
        "metadata": {"source": "run_runtime"},
        "evaluation_count": 2,
        "blocked_count": 0,
        "approval_request_count": 0,
        "last_event_cursor": 8,
        "updated_at": _NOW.isoformat(),
        "runtime_stats": {},
        "stale": False,
        "stale_reason": None,
    }
    run_store = _MemoryRunStore(
        [_snapshot(latest_checkpoint_id="chk-previous", guardrail_summary=summary)]
    )
    events = _MemoryEventStore()
    service = _service(run_store, _MemoryCheckpointStore(_checkpoint()), events)

    snapshots = await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued[0]["guardrail_summary"] == summary
    assert snapshots[0].guardrail_summary == summary


@pytest.mark.asyncio
async def test_recovery_reuses_checkpoint_guardrail_summary_without_recounting_usage() -> None:
    checkpoint_summary = {
        "mode": GuardrailMode.OBSERVE.value,
        "action": GuardrailAction.OBSERVE.value,
        "message": "from checkpoint",
        "metadata": {"source": "checkpoint"},
        "evaluation_count": 4,
        "blocked_count": 1,
        "approval_request_count": 1,
        "last_event_cursor": 9,
        "updated_at": _NOW.isoformat(),
        "runtime_stats": {
            "total_tokens": 30,
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tool_calls": 3,
            "consecutive_failure_count": 1,
        },
        "stale": False,
        "stale_reason": None,
    }
    run_store = _MemoryRunStore(
        [_snapshot(latest_checkpoint_id="chk-previous", latest_event_cursor=12)]
    )
    service = _service(
        run_store,
        _MemoryCheckpointStore(
            _checkpoint(
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                segment_metadata={"guardrail_summary": checkpoint_summary},
            )
        ),
        _MemoryEventStore(),
    )

    snapshots = await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued[0]["guardrail_summary"] == checkpoint_summary
    assert snapshots[0].guardrail_summary == checkpoint_summary
    assert snapshots[0].guardrail_summary is not None
    assert snapshots[0].guardrail_summary["runtime_stats"] == {
        "total_tokens": 30,
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tool_calls": 3,
        "consecutive_failure_count": 1,
    }


@pytest.mark.asyncio
async def test_recovery_marks_guardrail_summary_stale_when_missing_on_checkpointed_run() -> None:
    run_store = _MemoryRunStore(
        [_snapshot(latest_checkpoint_id="chk-previous", latest_event_cursor=12)]
    )
    service = _service(
        run_store,
        _MemoryCheckpointStore(
            _checkpoint(usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        ),
        _MemoryEventStore(),
    )

    snapshots = await service.sweep_expired_leases(now=_NOW)

    summary = run_store.enqueued[0]["guardrail_summary"]
    assert summary is not None
    assert summary["mode"] == GuardrailMode.OBSERVE.value
    assert summary["action"] == GuardrailAction.OBSERVE.value
    assert summary["message"] == "guardrail summary recovered conservatively"
    assert summary["metadata"] == {"source": "checkpoint_recovery"}
    assert summary["evaluation_count"] == 0
    assert summary["blocked_count"] == 0
    assert summary["approval_request_count"] == 0
    assert summary["last_event_cursor"] == 12
    assert summary["runtime_stats"] == {}
    assert summary["stale"] is True
    assert summary["stale_reason"] == "recovered_without_persisted_guardrail_summary"
    assert isinstance(summary["updated_at"], str)
    assert snapshots[0].guardrail_summary == summary
