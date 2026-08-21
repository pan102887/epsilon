"""Workflow checkpoint 保存与恢复兼容测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_checkpoint_recovery_service import RunRecoveryService
from application.run.run_checkpoint_sink import RunCheckpointSink
from domain.chat.context import ConversationContext
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
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
from domain.run.workflow import CollaborationLimit, WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SNAPSHOT_WORKFLOW = {"workflow_name": "code_change", "current_phase": "execute"}
_CHECKPOINT_WORKFLOW = {"workflow_name": "code_change", "current_phase": "plan"}
_CHECKPOINT_SUMMARY = {"delegation_count": 3, "handoff_count": 1}


class _CheckpointStore:
    def __init__(
        self,
        checkpoint: DurableCheckpoint | None = None,
        ledger: list[ToolResultLedgerEntry] | None = None,
    ) -> None:
        self.checkpoint = checkpoint
        self.ledger = ledger or []
        self.saved: list[DurableCheckpoint] = []

    async def save_checkpoint(self, checkpoint: DurableCheckpoint) -> DurableCheckpoint:
        saved = replace(
            checkpoint,
            checkpoint_id=f"chk-{len(self.saved) + 1}",
            sequence=len(self.saved) + 1,
        )
        self.saved.append(saved)
        self.checkpoint = saved
        return saved

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        return self.checkpoint

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        return list(self.ledger)

    async def trim_checkpoints(self, run_id: str, policy: CheckpointRetentionPolicy) -> None:
        return None


class _EventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


class _RunStore:
    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot
        self.enqueued: list[dict[str, Any]] = []
        self.lost: list[dict[str, Any]] = []

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        return [self.snapshot]

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
        self.snapshot = replace(
            self.snapshot,
            status=RunStatus.QUEUED,
            latest_checkpoint_id=latest_checkpoint_id,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )
        return self.snapshot

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        self.lost.append({"run_id": run_id, "reason": reason, "recovery_error": recovery_error})
        self.snapshot = replace(
            self.snapshot,
            status=RunStatus.LOST,
            last_recovery_error=recovery_error,
        )
        return self.snapshot


def _policy() -> CheckpointRetentionPolicy:
    return CheckpointRetentionPolicy(10, 3600, 4096, 100)


def _context() -> ConversationContext:
    context = ConversationContext()
    context.add_user_message("hello")
    return context


def _snapshot(
    *,
    workflow_run_state: dict[str, Any] | None = None,
    collaboration_summary: dict[str, Any] | None = None,
) -> RunSnapshot:
    payload = RunPayload(RunKind.CHAT, "session-1", chat={"message": "hi"})
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.RUNNING,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={},
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_run_state=workflow_run_state,
        collaboration_summary=collaboration_summary,
    )


def _checkpoint(
    *,
    workflow_run_state: dict[str, Any] | None = _CHECKPOINT_WORKFLOW,
    collaboration_summary: dict[str, Any] | None = _CHECKPOINT_SUMMARY,
) -> DurableCheckpoint:
    metadata: dict[str, Any] = {}
    if workflow_run_state is not None:
        metadata["workflow_run_state"] = workflow_run_state
    if collaboration_summary is not None:
        metadata["collaboration_summary"] = collaboration_summary
    return DurableCheckpoint(
        run_id="run-1",
        checkpoint_id="chk-1",
        sequence=1,
        phase=CheckpointPhase.MODEL_COMPLETED,
        context_snapshot=_context().to_dict(),
        round_num=1,
        usage={},
        trace_summary={},
        segment_metadata=metadata,
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=_NOW,
    )


def _service(
    run_store: _RunStore,
    checkpoint_store: _CheckpointStore,
    event_store: _EventStore,
) -> RunRecoveryService:
    return RunRecoveryService(
        run_store=cast(RunStorePort, run_store),
        checkpoint_store=cast(RunCheckpointStorePort, checkpoint_store),
        event_store=cast(RunEventStorePort, event_store),
        retention_policy=_policy(),
        max_recovery_attempts=3,
        auto_recovery_enabled=True,
        guardrail_serializer=GuardrailSerializerAdapter(),
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


async def test_checkpoint_sink_merges_workflow_context_into_segment_metadata() -> None:
    store = _CheckpointStore()
    events = _EventStore()
    sink = RunCheckpointSink(
        checkpoint_store=cast(RunCheckpointStorePort, store),
        event_store=cast(RunEventStorePort, events),
        retention_policy=_policy(),
        now=lambda: _NOW,
    )
    run_token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "owner-1", 1, False, sink)
    )
    workflow_token = set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="run-1",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(),
            depth=2,
            handoff_count=1,
            delegation_count=4,
        )
    )
    try:
        checkpoint = await sink.segment_done(
            context=_context(),
            segment_metadata={"segment_count": 1},
            usage={},
        )
    finally:
        reset_workflow_collaboration_context(workflow_token)
        reset_run_checkpoint_context(run_token)

    assert checkpoint.segment_metadata["workflow_run_state"] == {
        "workflow_name": "code_change",
        "current_phase": "execute",
    }
    assert checkpoint.segment_metadata["collaboration_summary"] == {
        "delegation_count": 4,
        "handoff_count": 1,
        "max_depth_seen": 2,
    }
    assert events.events[-1].event_type is RunEventType.CHECKPOINT_SAVED


async def test_recovery_prefers_snapshot_workflow_state_over_checkpoint_metadata() -> None:
    run_store = _RunStore(
        _snapshot(
            workflow_run_state=_SNAPSHOT_WORKFLOW,
            collaboration_summary={"delegation_count": 9},
        )
    )
    service = _service(run_store, _CheckpointStore(_checkpoint()), _EventStore())

    await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued[0]["workflow_run_state"] == _SNAPSHOT_WORKFLOW
    assert run_store.enqueued[0]["collaboration_summary"] == {"delegation_count": 9}


async def test_recovery_uses_checkpoint_workflow_metadata_when_snapshot_missing() -> None:
    run_store = _RunStore(_snapshot())
    service = _service(run_store, _CheckpointStore(_checkpoint()), _EventStore())

    recovered = await service.sweep_expired_leases(now=_NOW)

    assert recovered[0].workflow_run_state == _CHECKPOINT_WORKFLOW
    assert recovered[0].collaboration_summary == _CHECKPOINT_SUMMARY
    assert run_store.enqueued[0]["workflow_run_state"] == _CHECKPOINT_WORKFLOW


async def test_invalid_checkpoint_workflow_phase_blocks_recovery() -> None:
    run_store = _RunStore(_snapshot())
    service = _service(
        run_store,
        _CheckpointStore(
            _checkpoint(
                workflow_run_state={
                    "workflow_name": "code_change",
                    "current_phase": "bad_phase",
                }
            )
        ),
        _EventStore(),
    )

    decision = await service.evaluate_recovery(_snapshot())
    await service.sweep_expired_leases(now=_NOW)

    assert decision.recoverable is False
    assert decision.reason == "workflow_state_invalid"
    assert run_store.enqueued == []
    assert run_store.lost[0]["reason"] == "workflow_state_invalid"


async def test_pending_tool_replay_policy_still_blocks_recovery() -> None:
    run_store = _RunStore(_snapshot())
    service = _service(
        run_store,
        _CheckpointStore(
            _checkpoint(),
            [_pending_ledger(ToolReplayPolicy.NEVER_REPLAY)],
        ),
        _EventStore(),
    )

    decision = await service.evaluate_recovery(_snapshot())

    assert decision.recoverable is False
    assert decision.reason == "pending_tool_replay_blocked"


# Public test builders reused by higher-level integration coverage.
checkpoint = _checkpoint
CheckpointStore = _CheckpointStore
pending_ledger = _pending_ledger
RunStore = _RunStore
build_service = _service
snapshot = _snapshot
EventStore = _EventStore
