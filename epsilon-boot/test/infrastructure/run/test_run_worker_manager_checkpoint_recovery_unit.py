"""RunWorkerManager checkpoint recovery sweep tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

import infrastructure.run.run_worker_manager as run_worker_manager_module
from application.run.run_application_service import RunRuntimeMetrics
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import RunProgressSink
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from infrastructure.run.run_config import RunRuntimeConfig
from infrastructure.run.run_worker_manager import RunWorkerManager

pytestmark = pytest.mark.asyncio


class _FakeWorker:
    instances: ClassVar[list[_FakeWorker]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    async def run_once(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def _patch_run_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeWorker.instances.clear()
    monkeypatch.setattr(run_worker_manager_module, "RunWorker", _FakeWorker)


class _RunStore:
    def __init__(self, snapshot: RunSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.lost_sweep_calls = 0

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> None:
        return None

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        self.lost_sweep_calls += 1
        if self.snapshot is None:
            return []
        if self.snapshot.lease is None or self.snapshot.lease.lease_until >= now:
            return []
        lost = replace(
            self.snapshot,
            status=RunStatus.LOST,
            lease=None,
            terminal_reason="lease_expired",
            updated_at=now,
            version=self.snapshot.version + 1,
        )
        self.snapshot = lost
        return [lost]


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
    async def execute(
        self,
        snapshot: RunSnapshot,
        progress: RunProgressSink,
    ) -> RunExecutionOutcome:
        return RunExecutionOutcome(status=RunStatus.SUCCEEDED, result={})


class _RecoverySweep:
    def __init__(
        self,
        *,
        results: list[RunSnapshot] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[datetime] = []

    async def sweep_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        self.calls.append(now)
        if self.error is not None:
            raise self.error
        return self.results


async def test_checkpoint_enabled_uses_recovery_sweep_not_stage_three_lost_sweep() -> None:
    recovered = replace(_snapshot("run-1"), status=RunStatus.QUEUED, recoverable=True)
    run_store = _RunStore(_expired_running("run-1"))
    events = _EventStore()
    recovery = _RecoverySweep(results=[recovered])
    metrics = RunRuntimeMetrics()
    manager = _manager(
        run_store=run_store,
        events=events,
        recovery_sweep=recovery,
        metrics=metrics,
    )

    await manager.start()
    await asyncio.sleep(0.02)
    await manager.stop()

    assert isinstance(_FakeWorker.instances[0].kwargs["executor"], _Executor)
    assert len(recovery.calls) >= 1
    assert run_store.lost_sweep_calls == 0
    assert events.events == []
    assert metrics.snapshot().lost_count == 0


async def test_checkpoint_disabled_falls_back_to_stage_three_lost_sweep() -> None:
    run_store = _RunStore(_expired_running("run-1"))
    events = _EventStore()
    recovery = _RecoverySweep(results=[replace(_snapshot("run-1"), status=RunStatus.QUEUED)])
    manager = _manager(
        run_store=run_store,
        events=events,
        recovery_sweep=recovery,
        checkpoint_enabled=False,
    )

    await manager.start()
    await asyncio.sleep(0.02)
    await manager.stop()

    assert recovery.calls == []
    assert run_store.lost_sweep_calls >= 1
    assert run_store.snapshot is not None
    assert run_store.snapshot.status is RunStatus.LOST
    assert [event.event_type for event in events.events] == [RunEventType.RUN_LOST]


async def test_recovery_sweep_exception_does_not_kill_manager_loop() -> None:
    run_store = _RunStore(_expired_running("run-1"))
    events = _EventStore()
    recovery = _RecoverySweep(error=RuntimeError("redis down"))
    manager = _manager(
        run_store=run_store,
        events=events,
        recovery_sweep=recovery,
    )

    await manager.start()
    await asyncio.sleep(0.02)

    assert len(recovery.calls) >= 1
    assert manager.tasks
    assert all(not task.done() for task in manager.tasks)

    await manager.stop()


async def test_checkpoint_auto_recovery_disabled_falls_back_to_stage_three_lost_sweep() -> None:
    run_store = _RunStore(_expired_running("run-1"))
    events = _EventStore()
    recovery = _RecoverySweep(results=[replace(_snapshot("run-1"), status=RunStatus.QUEUED)])
    manager = _manager(
        run_store=run_store,
        events=events,
        recovery_sweep=recovery,
        checkpoint_auto_recovery_enabled=False,
    )

    await manager.start()
    await asyncio.sleep(0.02)
    await manager.stop()

    assert recovery.calls == []
    assert run_store.lost_sweep_calls >= 1
    assert [event.event_type for event in events.events] == [RunEventType.RUN_LOST]


def _manager(
    *,
    run_store: _RunStore,
    events: _EventStore,
    recovery_sweep: _RecoverySweep | None,
    metrics: RunRuntimeMetrics | None = None,
    checkpoint_enabled: bool = True,
    checkpoint_auto_recovery_enabled: bool = True,
) -> RunWorkerManager:
    return RunWorkerManager(
        run_store=run_store,  # type: ignore[arg-type]
        event_store=events,  # type: ignore[arg-type]
        executor=_Executor(),
        config=RunRuntimeConfig(
            worker_count=1,
            lease_seconds=30,
            heartbeat_interval_seconds=10,
            lost_sweep_interval_seconds=10,
            checkpoint_enabled=checkpoint_enabled,
            checkpoint_auto_recovery_enabled=checkpoint_auto_recovery_enabled,
        ),
        poll_interval_seconds=10,
        recovery_sweep=recovery_sweep,
        metrics=metrics,
    )


def _expired_running(run_id: str) -> RunSnapshot:
    return replace(
        _snapshot(run_id),
        status=RunStatus.RUNNING,
        lease=RunLease(
            owner_id="owner-a",
            lease_until=datetime.now(UTC) - timedelta(seconds=5),
            heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
        ),
    )


def _snapshot(run_id: str) -> RunSnapshot:
    now = datetime.now(UTC)
    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": "hello"},
        model="model-a",
    )
    return RunSnapshot(
        run_id=run_id,
        kind=RunKind.CHAT,
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
