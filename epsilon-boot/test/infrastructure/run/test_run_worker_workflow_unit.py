"""RunWorker workflow outcome 持久化单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from domain.run.exceptions import RunLeaseConflictError
from domain.run.outcome import RunExecutionOutcome
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from infrastructure.run.run_worker import RunWorker

pytestmark = pytest.mark.asyncio

_WORKFLOW_STATE = {"workflow_name": "code_change", "current_phase": "execute"}
_COLLAB_SUMMARY = {"delegation_count": 2, "handoff_count": 1}


class _RunStore:
    """RunWorker workflow 测试 fake store。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        if self.snapshot.status is not RunStatus.QUEUED:
            return None
        now = datetime.now(UTC)
        self.snapshot = replace(
            self.snapshot,
            status=RunStatus.RUNNING,
            lease=RunLease(
                owner_id=owner_id,
                lease_until=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
            ),
        )
        return self.snapshot

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        return self._require_owner(owner_id)

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        return self.snapshot if self.snapshot.run_id == run_id else None

    async def mark_succeeded(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._transition(
            owner_id,
            RunStatus.SUCCEEDED,
            result=result,
            error=None,
            terminal_reason="completed",
            can_continue=False,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def mark_failed(
        self,
        *,
        run_id: str,
        owner_id: str,
        error: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._transition(
            owner_id,
            RunStatus.FAILED,
            result=None,
            error=error,
            terminal_reason="failed",
            can_continue=False,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def mark_paused(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._transition(
            owner_id,
            RunStatus.PAUSED,
            result=result,
            error=None,
            terminal_reason=None,
            can_continue=True,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def mark_awaiting_approval(
        self,
        *,
        run_id: str,
        owner_id: str,
        approval_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        updated = self._transition(
            owner_id,
            RunStatus.AWAITING_APPROVAL,
            result=result,
            error=None,
            terminal_reason=None,
            can_continue=True,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )
        self.snapshot = replace(updated, approval_id=approval_id)
        return self.snapshot

    async def mark_cancelled(
        self,
        *,
        run_id: str,
        owner_id: str,
        reason: str,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._transition(
            owner_id,
            RunStatus.CANCELLED,
            result={"reason": reason},
            error=None,
            terminal_reason=reason,
            can_continue=False,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    def force_cancel_requested(self) -> None:
        self.snapshot = replace(self.snapshot, status=RunStatus.CANCEL_REQUESTED)

    def _transition(
        self,
        owner_id: str,
        status: RunStatus,
        *,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        terminal_reason: str | None,
        can_continue: bool,
        workflow_run_state: dict[str, Any] | None,
        collaboration_summary: dict[str, Any] | None,
    ) -> RunSnapshot:
        current = self._require_owner(owner_id)
        self.snapshot = replace(
            current,
            status=status,
            result=result,
            error=error,
            terminal_reason=terminal_reason,
            can_continue=can_continue,
            lease=None,
            workflow_run_state=workflow_run_state
            if workflow_run_state is not None
            else current.workflow_run_state,
            collaboration_summary=collaboration_summary
            if collaboration_summary is not None
            else current.collaboration_summary,
        )
        return self.snapshot

    def _require_owner(self, owner_id: str) -> RunSnapshot:
        if self.snapshot.lease is None or self.snapshot.lease.owner_id != owner_id:
            raise RunLeaseConflictError(self.snapshot.run_id, owner_id)
        return self.snapshot


class _EventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            cursor=len([item for item in self.events if item.run_id == run_id]) + 1,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        self.events.append(event)
        return event


class _Executor:
    def __init__(
        self, outcome: RunExecutionOutcome, *, cancel_after: _RunStore | None = None
    ) -> None:
        self.outcome = outcome
        self.cancel_after = cancel_after

    async def execute(self, snapshot: RunSnapshot, progress) -> RunExecutionOutcome:
        await progress.segment_started(snapshot.run_id, 1)
        await progress.segment_done(snapshot.run_id, self.outcome.segment_metadata or {})
        if self.cancel_after is not None:
            self.cancel_after.force_cancel_requested()
        return self.outcome


def _worker(store: _RunStore, events: _EventStore, outcome: RunExecutionOutcome) -> RunWorker:
    return RunWorker(
        run_store=store,
        event_store=events,
        executor=_Executor(outcome),
        owner_id="owner-a",
        lease_seconds=30,
        heartbeat_interval_seconds=10,
    )


def _snapshot() -> RunSnapshot:
    now = datetime.now(UTC)
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": "hello"},
            model="model-a",
        ),
        client_request_id=None,
        payload_hash=None,
        result=None,
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
        workflow_run_state={"workflow_name": "code_change", "current_phase": "plan"},
        collaboration_summary={"delegation_count": 0},
    )


def _outcome(
    status: RunStatus,
    *,
    approval_id: str | None = None,
) -> RunExecutionOutcome:
    return RunExecutionOutcome(
        status=status,
        result={"ok": True},
        error={"message": "failed"} if status is RunStatus.FAILED else None,
        terminal_reason="cancelled" if status is RunStatus.CANCELLED else None,
        can_continue=status in {RunStatus.PAUSED, RunStatus.AWAITING_APPROVAL},
        approval_id=approval_id,
        segment_metadata={"segment_count": 1},
        workflow_run_state=_WORKFLOW_STATE,
        collaboration_summary=_COLLAB_SUMMARY,
    )


@pytest.mark.parametrize(
    ("status", "approval_id", "terminal_event"),
    [
        (RunStatus.SUCCEEDED, None, RunEventType.RUN_SUCCEEDED),
        (RunStatus.PAUSED, None, RunEventType.RUN_PAUSED),
        (RunStatus.AWAITING_APPROVAL, "approval-1", RunEventType.APPROVAL_REQUIRED),
        (RunStatus.FAILED, None, RunEventType.RUN_FAILED),
        (RunStatus.CANCELLED, None, RunEventType.RUN_CANCELLED),
    ],
)
async def test_worker_persists_workflow_fields_for_outcome_statuses(
    status: RunStatus,
    approval_id: str | None,
    terminal_event: RunEventType,
) -> None:
    store = _RunStore(_snapshot())
    events = _EventStore()

    assert await _worker(store, events, _outcome(status, approval_id=approval_id)).run_once()

    assert store.snapshot.status is status
    assert store.snapshot.workflow_run_state == _WORKFLOW_STATE
    assert store.snapshot.collaboration_summary == _COLLAB_SUMMARY
    assert events.events[-1].event_type is terminal_event
    assert events.events[-1].payload["workflow_run_state"] == _WORKFLOW_STATE
    if status is RunStatus.CANCELLED:
        assert events.events[-1].payload["reason"] == "cancelled"
    assert events.events[-1].payload["collaboration_summary"] == _COLLAB_SUMMARY


async def test_missing_approval_id_failure_preserves_workflow_fields() -> None:
    store = _RunStore(_snapshot())
    events = _EventStore()
    outcome = _outcome(RunStatus.AWAITING_APPROVAL, approval_id=None)

    assert await _worker(store, events, outcome).run_once()

    assert store.snapshot.status is RunStatus.FAILED
    assert store.snapshot.workflow_run_state == _WORKFLOW_STATE
    assert store.snapshot.collaboration_summary == _COLLAB_SUMMARY
    assert events.events[-1].event_type is RunEventType.RUN_FAILED
    assert events.events[-1].payload["status"] == RunStatus.FAILED.value
    assert events.events[-1].payload["workflow_run_state"] == _WORKFLOW_STATE


async def test_cancel_requested_after_segment_keeps_cancel_priority() -> None:
    store = _RunStore(_snapshot())
    events = _EventStore()
    outcome = _outcome(RunStatus.SUCCEEDED)
    worker = RunWorker(
        run_store=store,
        event_store=events,
        executor=_Executor(outcome, cancel_after=store),
        owner_id="owner-a",
        lease_seconds=30,
        heartbeat_interval_seconds=10,
    )

    assert await worker.run_once()

    assert store.snapshot.status is RunStatus.CANCELLED
    assert store.snapshot.workflow_run_state == {
        "workflow_name": "code_change",
        "current_phase": "plan",
    }
    assert store.snapshot.workflow_run_state != _WORKFLOW_STATE
    assert events.events[-1].event_type is RunEventType.RUN_CANCELLED
