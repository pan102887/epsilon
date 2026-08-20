"""Run worker 与 worker manager 单元测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from domain.run.exceptions import RunLeaseConflictError
from domain.run.outcome import RunExecutionOutcome
from domain.run.value_objects import (
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from infrastructure.run.run_config import RunRuntimeConfig
from infrastructure.run.run_worker import RunWorker
from infrastructure.run.run_worker_manager import RunWorkerManager

pytestmark = pytest.mark.asyncio


class _MemoryRunStore:
    """测试用 RunStore fake，保留 claim/owner/lease 的核心语义。"""

    def __init__(self) -> None:
        self._runs: dict[str, RunSnapshot] = {}
        self._lock = asyncio.Lock()
        self.refresh_count = 0
        self.awaiting_approval_calls: list[dict[str, Any]] = []
        self.enqueue_continue_calls: list[dict[str, Any]] = []
        self.after_claim_next: Callable[[RunSnapshot], None] | None = None

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        snapshot = _snapshot(f"run-{len(self._runs) + 1}", request.payload)
        self._runs[snapshot.run_id] = snapshot
        return snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        return self._runs.get(run_id)

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        for snapshot in self._runs.values():
            if snapshot.client_request_id == client_request_id:
                return snapshot
        return None

    async def count_by_status(self, statuses) -> int:
        return sum(1 for snapshot in self._runs.values() if snapshot.status in statuses)

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        async with self._lock:
            for snapshot in self._runs.values():
                if snapshot.status is not RunStatus.QUEUED:
                    continue
                now = datetime.now(UTC)
                updated = replace(
                    snapshot,
                    status=RunStatus.RUNNING,
                    lease=RunLease(
                        owner_id=owner_id,
                        lease_until=now + timedelta(seconds=lease_seconds),
                        heartbeat_at=now,
                    ),
                    can_continue=False,
                    updated_at=now,
                    version=snapshot.version + 1,
                )
                self._runs[snapshot.run_id] = updated
                if self.after_claim_next is not None:
                    self.after_claim_next(updated)
                return updated
        return None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        async with self._lock:
            snapshot = self._require_owner(run_id, owner_id)
            if snapshot.status not in {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}:
                raise RunLeaseConflictError(run_id, owner_id)
            now = datetime.now(UTC)
            updated = replace(
                snapshot,
                lease=RunLease(
                    owner_id=owner_id,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )
            self._runs[run_id] = updated
            self.refresh_count += 1
            return updated

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        snapshot = self._runs[run_id]
        target = (
            RunStatus.CANCELLED
            if snapshot.status is RunStatus.QUEUED
            else RunStatus.CANCEL_REQUESTED
        )
        updated = replace(
            snapshot,
            status=target,
            lease=None if target is RunStatus.CANCELLED else snapshot.lease,
            can_continue=False,
            terminal_reason="cancelled" if target is RunStatus.CANCELLED else None,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self._runs[run_id] = updated
        return updated

    async def mark_succeeded(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.SUCCEEDED,
            result=result,
            error=None,
            can_continue=False,
            terminal_reason="completed",
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
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.FAILED,
            result=None,
            error=error,
            can_continue=False,
            terminal_reason="failed",
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
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.PAUSED,
            result=result,
            error=None,
            can_continue=True,
            terminal_reason=None,
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
        self.awaiting_approval_calls.append(
            {
                "run_id": run_id,
                "owner_id": owner_id,
                "approval_id": approval_id,
                "result": result,
                "workflow_run_state": workflow_run_state,
                "collaboration_summary": collaboration_summary,
            }
        )
        updated = self._worker_transition(
            run_id,
            owner_id,
            RunStatus.AWAITING_APPROVAL,
            result=result,
            error=None,
            can_continue=True,
            terminal_reason=None,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )
        updated = replace(updated, approval_id=approval_id)
        self._runs[run_id] = updated
        return updated

    async def mark_cancelled(
        self,
        *,
        run_id: str,
        owner_id: str,
        reason: str,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.CANCELLED,
            result={"reason": reason},
            error=None,
            can_continue=False,
            terminal_reason=reason,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def resolve_approval_resume(self, *, run_id: str, owner_id: str, result):
        raise NotImplementedError

    async def enqueue_continue(self, *, run_id: str, model: str | None = None):
        snapshot = self._runs[run_id]
        if snapshot.status is not RunStatus.PAUSED or not snapshot.can_continue:
            raise AssertionError("enqueue_continue called for non-continuable run")
        updated = replace(
            snapshot,
            status=RunStatus.QUEUED,
            payload=replace(snapshot.payload, model=model or snapshot.payload.model),
            can_continue=False,
            lease=None,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.enqueue_continue_calls.append({"run_id": run_id, "model": model})
        self._runs[run_id] = updated
        return updated

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        lost: list[RunSnapshot] = []
        async with self._lock:
            for run_id, snapshot in list(self._runs.items()):
                if snapshot.status not in {
                    RunStatus.RUNNING,
                    RunStatus.CANCEL_REQUESTED,
                }:
                    continue
                if snapshot.lease is None or snapshot.lease.lease_until >= now:
                    continue
                updated = replace(
                    snapshot,
                    status=RunStatus.LOST,
                    lease=None,
                    can_continue=False,
                    terminal_reason="lease_expired",
                    updated_at=now,
                    version=snapshot.version + 1,
                )
                self._runs[run_id] = updated
                lost.append(updated)
        return lost

    def put(self, snapshot: RunSnapshot) -> None:
        self._runs[snapshot.run_id] = snapshot

    def force_status(self, run_id: str, status: RunStatus) -> None:
        snapshot = self._runs[run_id]
        self._runs[run_id] = replace(
            snapshot,
            status=status,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )

    def _worker_transition(
        self,
        run_id: str,
        owner_id: str,
        status: RunStatus,
        *,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        can_continue: bool,
        terminal_reason: str | None,
        workflow_run_state: dict[str, Any] | None,
        collaboration_summary: dict[str, Any] | None,
    ) -> RunSnapshot:
        snapshot = self._require_owner(run_id, owner_id)
        updated = replace(
            snapshot,
            status=status,
            result=result,
            error=error,
            lease=None,
            can_continue=can_continue,
            terminal_reason=terminal_reason,
            workflow_run_state=workflow_run_state
            if workflow_run_state is not None
            else snapshot.workflow_run_state,
            collaboration_summary=collaboration_summary
            if collaboration_summary is not None
            else snapshot.collaboration_summary,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self._runs[run_id] = updated
        return updated

    def _require_owner(self, run_id: str, owner_id: str) -> RunSnapshot:
        snapshot = self._runs[run_id]
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            raise RunLeaseConflictError(run_id, owner_id)
        return snapshot


class _MemoryEventStore:
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

    async def list_events(self, run_id: str, after_cursor: int | None, limit: int):
        return [
            event
            for event in self.events
            if event.run_id == run_id and (after_cursor is None or event.cursor > after_cursor)
        ][:limit]

    async def wait_events(self, run_id: str, after_cursor: int | None, timeout_seconds: float):
        return await self.list_events(run_id, after_cursor, limit=100)

    async def trim_events(self, run_id: str, policy) -> None:
        return None

    async def first_cursor(self, run_id: str) -> int | None:
        events = [event for event in self.events if event.run_id == run_id]
        return events[0].cursor if events else None

    def event_types(self, run_id: str) -> list[RunEventType]:
        return [event.event_type for event in self.events if event.run_id == run_id]


class _FakeExecutor:
    def __init__(
        self,
        outcome: RunExecutionOutcome | None = None,
        *,
        delay_seconds: float = 0,
        raise_error: Exception | None = None,
        after_execute=None,
    ) -> None:
        self.outcome = outcome or RunExecutionOutcome(
            status=RunStatus.SUCCEEDED,
            result={"ok": True},
            segment_metadata={"segment_count": 1},
        )
        self.delay_seconds = delay_seconds
        self.raise_error = raise_error
        self.after_execute = after_execute
        self.calls: list[str] = []

    async def execute(self, snapshot: RunSnapshot, progress) -> RunExecutionOutcome:
        self.calls.append(snapshot.run_id)
        await progress.segment_started(snapshot.run_id, 1)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.after_execute is not None:
            self.after_execute(snapshot.run_id)
        if self.raise_error is not None:
            raise self.raise_error
        await progress.segment_done(
            snapshot.run_id,
            self.outcome.segment_metadata or {},
        )
        return self.outcome


async def test_run_once_claims_queued_run_starts_segment_and_succeeds() -> None:
    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.SUCCEEDED,
            result={"answer": "done"},
            segment_metadata={"segment_count": 1},
        )
    )
    run = store.put(_snapshot("run-1"))
    worker = _worker(store, events, coordinator)

    assert await worker.run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.SUCCEEDED
    assert loaded.result == {"answer": "done"}
    assert coordinator.calls == ["run-1"]
    assert events.event_types("run-1") == [
        RunEventType.RUN_CLAIMED,
        RunEventType.SEGMENT_STARTED,
        RunEventType.SEGMENT_DONE,
        RunEventType.RUN_SUCCEEDED,
    ]
    assert run is None


async def test_run_once_returns_false_when_no_queued_run() -> None:
    store, events, coordinator = _fixture()
    worker = _worker(store, events, coordinator)

    assert await worker.run_once() is False
    assert coordinator.calls == []
    assert events.events == []


async def test_heartbeat_refreshes_lease_while_segment_is_running() -> None:
    store, events, coordinator = _fixture(delay_seconds=0.05)
    store.put(_snapshot("run-1"))
    worker = _worker(
        store,
        events,
        coordinator,
        lease_seconds=1,
        heartbeat_interval_seconds=0.01,
    )

    assert await worker.run_once() is True

    assert store.refresh_count >= 1
    assert RunEventType.RUN_HEARTBEAT in events.event_types("run-1")


async def test_paused_outcome_marks_run_paused_and_continue_capable() -> None:
    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.PAUSED,
            result={"partial": True},
            can_continue=True,
            segment_metadata={"segment_count": 1},
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.PAUSED
    assert loaded.can_continue is True
    assert events.event_types("run-1")[-1] is RunEventType.RUN_PAUSED


async def test_paused_outcome_auto_continues_when_enabled() -> None:
    """可继续 paused Run 在开启自动续跑时会重新入队。"""

    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.PAUSED,
            result={"partial": True},
            can_continue=True,
            segment_metadata={"segment_count": 1, "segment_stop_reason": "auto_disabled"},
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(
        store,
        events,
        coordinator,
        auto_continue_paused_runs=True,
    ).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.QUEUED
    assert loaded.can_continue is False
    assert store.enqueue_continue_calls == [{"run_id": "run-1", "model": None}]
    assert events.event_types("run-1")[-2:] == [
        RunEventType.RUN_PAUSED,
        RunEventType.RUN_QUEUED,
    ]


@pytest.mark.parametrize(
    "stop_reason",
    ["risk_gate_required", "no_progress", "max_continuations_reached"],
)
async def test_paused_outcome_auto_continue_respects_stop_reasons(stop_reason: str) -> None:
    """风险、无进展和预算类停止原因不会被 Run 自动续跑绕过。"""

    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.PAUSED,
            result={"partial": True},
            can_continue=True,
            segment_metadata={"segment_count": 1, "segment_stop_reason": stop_reason},
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(
        store,
        events,
        coordinator,
        auto_continue_paused_runs=True,
    ).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.PAUSED
    assert store.enqueue_continue_calls == []
    assert events.event_types("run-1")[-1] is RunEventType.RUN_PAUSED


async def test_paused_outcome_auto_continue_respects_segment_limit() -> None:
    """达到 Run 自动续跑段数上限后保留 paused，等待人工处理。"""

    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.PAUSED,
            result={"partial": True},
            can_continue=True,
            segment_metadata={"segment_count": 3, "segment_stop_reason": "auto_disabled"},
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(
        store,
        events,
        coordinator,
        auto_continue_paused_runs=True,
        auto_continue_max_segments=3,
    ).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.PAUSED
    assert store.enqueue_continue_calls == []


async def test_awaiting_approval_outcome_marks_run_awaiting_approval() -> None:
    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.AWAITING_APPROVAL,
            result={"tool": "write"},
            approval_id="approval-1",
            can_continue=True,
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.AWAITING_APPROVAL
    assert loaded.approval_id == "approval-1"
    assert events.event_types("run-1")[-1] is RunEventType.APPROVAL_REQUIRED


async def test_awaiting_approval_outcome_without_approval_id_marks_run_failed() -> None:
    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.AWAITING_APPROVAL,
            result={"tool": "write"},
            approval_id=None,
            can_continue=True,
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.FAILED
    assert loaded.error is not None
    assert "approval_id" in loaded.error["message"]
    assert store.awaiting_approval_calls == []
    assert RunEventType.APPROVAL_REQUIRED not in events.event_types("run-1")
    assert events.event_types("run-1")[-1] is RunEventType.RUN_FAILED
    assert events.events[-1].payload["status"] == RunStatus.FAILED.value


async def test_unsupported_outcome_status_marks_run_failed() -> None:
    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.QUEUED,
            result={"unexpected": True},
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.FAILED
    assert loaded.error is not None
    assert loaded.error["status"] == RunStatus.QUEUED.value
    assert events.event_types("run-1")[-1] is RunEventType.RUN_FAILED


async def test_failed_outcome_and_execution_exception_mark_run_failed() -> None:
    store, events, coordinator = _fixture(
        RunExecutionOutcome(
            status=RunStatus.FAILED,
            error={"message": "business failed"},
        )
    )
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True
    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.FAILED
    assert loaded.error == {"message": "business failed"}
    assert events.event_types("run-1")[-1] is RunEventType.RUN_FAILED

    store2, events2, coordinator2 = _fixture(raise_error=RuntimeError("boom"))
    store2.put(_snapshot("run-2"))

    assert await _worker(store2, events2, coordinator2).run_once() is True
    loaded2 = await store2.get_run("run-2")
    assert loaded2 is not None
    assert loaded2.status is RunStatus.FAILED
    assert loaded2.error is not None
    assert loaded2.error["message"] == "boom"


async def test_cancel_requested_after_segment_marks_cancelled_without_interrupting_execute() -> (
    None
):
    store = _MemoryRunStore()
    events = _MemoryEventStore()

    def cancel_after_execute(run_id: str) -> None:
        store.force_status(run_id, RunStatus.CANCEL_REQUESTED)

    coordinator = _FakeExecutor(after_execute=cancel_after_execute)
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.CANCELLED
    assert coordinator.calls == ["run-1"]
    assert events.event_types("run-1")[-1] is RunEventType.RUN_CANCELLED


async def test_cancel_requested_before_segment_marks_cancelled_without_executing() -> None:
    store, events, coordinator = _fixture()

    def cancel_after_claim(snapshot: RunSnapshot) -> None:
        store.force_status(snapshot.run_id, RunStatus.CANCEL_REQUESTED)

    store.after_claim_next = cancel_after_claim
    store.put(_snapshot("run-1"))

    assert await _worker(store, events, coordinator).run_once() is True

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.CANCELLED
    assert coordinator.calls == []
    assert events.event_types("run-1") == [
        RunEventType.RUN_CLAIMED,
        RunEventType.RUN_CANCELLED,
    ]


async def test_heartbeat_loop_exits_on_owner_mismatch() -> None:
    store, events, coordinator = _fixture()
    store.put(
        replace(
            _snapshot("run-1"),
            status=RunStatus.RUNNING,
            lease=RunLease(
                owner_id="owner-b",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                heartbeat_at=datetime.now(UTC),
            ),
        )
    )
    worker = _worker(store, events, coordinator, heartbeat_interval_seconds=0.01)

    await asyncio.wait_for(
        worker.heartbeat_loop("run-1", "owner-a"),
        timeout=0.2,
    )

    assert store.refresh_count == 0
    assert events.event_types("run-1") == []


async def test_heartbeat_loop_exits_on_terminal_status() -> None:
    store, events, coordinator = _fixture()
    store.put(
        replace(
            _snapshot("run-1"),
            status=RunStatus.SUCCEEDED,
            lease=RunLease(
                owner_id="owner-a",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                heartbeat_at=datetime.now(UTC),
            ),
        )
    )
    worker = _worker(store, events, coordinator, heartbeat_interval_seconds=0.01)

    await asyncio.wait_for(
        worker.heartbeat_loop("run-1", "owner-a"),
        timeout=0.2,
    )

    assert store.refresh_count == 0
    assert events.event_types("run-1") == []


async def test_heartbeat_loop_exits_when_stop_event_is_set() -> None:
    store, events, coordinator = _fixture()
    store.put(
        replace(
            _snapshot("run-1"),
            status=RunStatus.RUNNING,
            lease=RunLease(
                owner_id="owner-a",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                heartbeat_at=datetime.now(UTC),
            ),
        )
    )
    worker = _worker(store, events, coordinator, heartbeat_interval_seconds=0.01)
    stop_event = asyncio.Event()
    stop_event.set()

    await asyncio.wait_for(
        worker.heartbeat_loop("run-1", "owner-a", stop_event),
        timeout=0.2,
    )

    assert store.refresh_count == 0
    assert events.event_types("run-1") == []


async def test_lost_sweep_marks_expired_lease_and_writes_event() -> None:
    store = _MemoryRunStore()
    events = _MemoryEventStore()
    expired = replace(
        _snapshot("run-1"),
        status=RunStatus.RUNNING,
        lease=RunLease(
            owner_id="owner-a",
            lease_until=datetime.now(UTC) - timedelta(seconds=5),
            heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
        ),
    )
    store.put(expired)
    manager = RunWorkerManager(
        run_store=store,
        event_store=events,
        executor=_FakeExecutor(),
        config=_config(worker_count=1, lost_sweep_interval_seconds=10),
        poll_interval_seconds=10,
    )

    await manager.start()
    await asyncio.sleep(0.02)
    await manager.stop()

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.LOST
    assert events.event_types("run-1") == [RunEventType.RUN_LOST]


async def test_concurrent_workers_execute_single_claim_only_once() -> None:
    store, events, coordinator = _fixture(delay_seconds=0.02)
    store.put(_snapshot("run-1"))
    workers = [_worker(store, events, coordinator, owner_id=f"owner-{index}") for index in range(3)]

    results = await asyncio.gather(*(worker.run_once() for worker in workers))

    assert results.count(True) == 1
    assert results.count(False) == 2
    assert coordinator.calls == ["run-1"]
    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.SUCCEEDED


async def test_worker_manager_start_stop_wake_up_does_not_leave_running_tasks() -> None:
    store, events, coordinator = _fixture()
    manager = RunWorkerManager(
        run_store=store,
        event_store=events,
        executor=coordinator,
        config=_config(
            worker_count=2,
            heartbeat_interval_seconds=1,
            lease_seconds=2,
            lost_sweep_interval_seconds=1,
        ),
        poll_interval_seconds=0.01,
    )

    await manager.start()
    tasks = manager.tasks
    assert len(tasks) == 3
    assert all(not task.done() for task in tasks)

    manager.wake_up()
    await asyncio.sleep(0.01)
    await manager.stop()

    assert manager.tasks == ()
    assert all(task.done() for task in tasks)


def _fixture(
    outcome: RunExecutionOutcome | None = None,
    *,
    delay_seconds: float = 0,
    raise_error: Exception | None = None,
) -> tuple[_MemoryRunStore, _MemoryEventStore, _FakeExecutor]:
    return (
        _MemoryRunStore(),
        _MemoryEventStore(),
        _FakeExecutor(
            outcome,
            delay_seconds=delay_seconds,
            raise_error=raise_error,
        ),
    )


def _worker(
    store: _MemoryRunStore,
    events: _MemoryEventStore,
    executor: _FakeExecutor,
    *,
    owner_id: str = "owner-a",
    lease_seconds: int = 30,
    heartbeat_interval_seconds: float = 10,
    auto_continue_paused_runs: bool = False,
    auto_continue_max_segments: int = 20,
) -> RunWorker:
    return RunWorker(
        run_store=store,
        event_store=events,
        executor=executor,
        owner_id=owner_id,
        lease_seconds=lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        auto_continue_paused_runs=auto_continue_paused_runs,
        auto_continue_max_segments=auto_continue_max_segments,
    )


def _config(
    *,
    worker_count: int = 1,
    lease_seconds: int = 30,
    heartbeat_interval_seconds: int = 10,
    lost_sweep_interval_seconds: int = 30,
) -> RunRuntimeConfig:
    return RunRuntimeConfig(
        worker_count=worker_count,
        lease_seconds=lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        lost_sweep_interval_seconds=lost_sweep_interval_seconds,
    )


def _snapshot(
    run_id: str,
    payload: RunPayload | None = None,
) -> RunSnapshot:
    now = datetime.now(UTC)
    payload = payload or RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": "hello"},
        model="model-a",
    )
    return RunSnapshot(
        run_id=run_id,
        kind=payload.kind,
        status=RunStatus.QUEUED,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
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
    )
