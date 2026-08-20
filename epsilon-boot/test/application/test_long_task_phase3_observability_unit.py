"""Long Task Phase 3 可观测性单元测试。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from application.run.run_application_service import (
    RunApplicationService,
    RunRuntimeMetrics,
)
from domain.run.exceptions import RunEventReplayExpiredError, RunQueueFullError
from domain.run.outcome import RunExecutionOutcome
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
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
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter
from infrastructure.run.run_worker import RunWorker
from infrastructure.run.run_worker_manager import RunWorkerManager

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SECRET = "SECRET_USER_MESSAGE_SHOULD_NOT_BE_LOGGED"


class _MemoryRunStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}
        self.client_index: dict[str, str] = {}

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        run_id = f"run-{len(self.snapshots) + 1}"
        snapshot = _snapshot(
            run_id,
            payload=request.payload,
            client_request_id=request.client_request_id,
            status=RunStatus.QUEUED,
        )
        self.snapshots[run_id] = snapshot
        if request.client_request_id is not None:
            self.client_index[request.client_request_id] = run_id
        return snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        return self.snapshots.get(run_id)

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        run_id = self.client_index.get(client_request_id)
        return self.snapshots.get(run_id) if run_id is not None else None

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        return sum(1 for snapshot in self.snapshots.values() if snapshot.status in statuses)

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        for run_id, snapshot in list(self.snapshots.items()):
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
                updated_at=now,
                version=snapshot.version + 1,
            )
            self.snapshots[run_id] = updated
            return updated
        return None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            raise RuntimeError("lease conflict")
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
        self.snapshots[run_id] = updated
        return updated

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        target = (
            RunStatus.CANCELLED
            if snapshot.status is RunStatus.QUEUED
            else RunStatus.CANCEL_REQUESTED
        )
        updated = replace(
            snapshot,
            status=target,
            terminal_reason="cancelled" if target is RunStatus.CANCELLED else None,
            can_continue=False,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def mark_succeeded(
        self, *, run_id: str, owner_id: str, result: dict[str, Any], **_: Any
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            RunStatus.SUCCEEDED,
            result=result,
            error=None,
            terminal_reason="completed",
        )

    async def mark_failed(
        self, *, run_id: str, owner_id: str, error: dict[str, Any], **_: Any
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            RunStatus.FAILED,
            result=None,
            error=error,
            terminal_reason="failed",
        )

    async def mark_paused(
        self, *, run_id: str, owner_id: str, result: dict[str, Any], **_: Any
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            RunStatus.PAUSED,
            result=result,
            error=None,
            terminal_reason=None,
        )

    async def mark_awaiting_approval(
        self, *, run_id: str, owner_id: str, approval_id: str, result: dict[str, Any], **_: Any
    ) -> RunSnapshot:
        updated = self._worker_transition(
            run_id,
            RunStatus.AWAITING_APPROVAL,
            result=result,
            error=None,
            terminal_reason=None,
        )
        updated = replace(updated, approval_id=approval_id)
        self.snapshots[run_id] = updated
        return updated

    async def mark_cancelled(self, *, run_id: str, owner_id: str, reason: str) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            RunStatus.CANCELLED,
            result={"reason": reason},
            error=None,
            terminal_reason=reason,
        )

    async def resolve_approval_resume(self, *, run_id: str, owner_id: str, result):
        raise NotImplementedError

    async def enqueue_continue(self, *, run_id: str, model: str | None = None):
        raise NotImplementedError

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        lost: list[RunSnapshot] = []
        for run_id, snapshot in list(self.snapshots.items()):
            if snapshot.status not in {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}:
                continue
            if snapshot.lease is None or snapshot.lease.lease_until >= now:
                continue
            updated = replace(
                snapshot,
                status=RunStatus.LOST,
                lease=None,
                terminal_reason="lease_expired",
                can_continue=False,
                updated_at=now,
                version=snapshot.version + 1,
            )
            self.snapshots[run_id] = updated
            lost.append(updated)
        return lost

    def _worker_transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        terminal_reason: str | None,
    ) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        updated = replace(
            snapshot,
            status=status,
            result=result,
            error=error,
            lease=None,
            terminal_reason=terminal_reason,
            can_continue=status in {RunStatus.PAUSED, RunStatus.AWAITING_APPROVAL},
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated


class _MemoryEventStore:
    def __init__(self) -> None:
        self.events: dict[str, list[RunEvent]] = {}

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        run_events = self.events.setdefault(run_id, [])
        event = RunEvent(
            run_id=run_id,
            cursor=run_events[-1].cursor + 1 if run_events else 1,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        run_events.append(event)
        return event

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        return [
            event
            for event in self.events.get(run_id, [])
            if after_cursor is None or event.cursor > after_cursor
        ][:limit]

    async def wait_events(
        self, run_id: str, after_cursor: int | None, timeout_seconds: float
    ) -> list[RunEvent]:
        return await self.list_events(run_id, after_cursor, 100)

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        events = self.events.get(run_id, [])
        if len(events) > policy.max_event_count:
            self.events[run_id] = events[-policy.max_event_count :]

    async def first_cursor(self, run_id: str) -> int | None:
        events = self.events.get(run_id, [])
        return events[0].cursor if events else None


class _FailingCoordinator:
    async def execute(self, snapshot: RunSnapshot, progress) -> RunExecutionOutcome:
        await progress.segment_started(snapshot.run_id, 1)
        raise RuntimeError("coordinator exploded")


async def test_run_service_logs_structured_fields_and_redacts_payload(caplog) -> None:
    """应用服务日志包含 run 字段，且不泄露用户消息。"""

    metrics = RunRuntimeMetrics()
    store = _MemoryRunStore()
    service = _service(store=store, metrics=metrics)

    with caplog.at_level(logging.INFO, logger="application.run.run_application_service"):
        snapshot = await service.create_run(_request())
        await service.request_cancel(snapshot.run_id)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert _SECRET not in messages
    created = _record(caplog.records, "Run created and queued")
    assert created.run_id == snapshot.run_id
    assert created.run_kind == RunKind.CHAT.value
    assert created.run_status == RunStatus.QUEUED.value
    assert created.worker_id is None
    assert created.client_request_id == "client-1"
    cancelled = _record(caplog.records, "Run cancel requested")
    assert cancelled.event_type == RunEventType.RUN_CANCELLED.value
    assert metrics.snapshot().cancel_request_count == 1


async def test_queue_saturation_has_distinct_log_and_metric(caplog) -> None:
    """队列饱和与执行失败使用不同日志与计数。"""

    metrics = RunRuntimeMetrics()
    service = _service(metrics=metrics, max_queued_runs=0)

    with (
        caplog.at_level(logging.WARNING, logger="application.run.run_application_service"),
        pytest.raises(RunQueueFullError),
    ):
        await service.create_run(_request())

    record = _record(caplog.records, "Run queue capacity full")
    assert record.limit_name == "max_queued_runs"
    assert record.run_status == RunStatus.QUEUED.value
    assert metrics.snapshot().queue_full_count == 1
    assert metrics.snapshot().execution_failed_count == 0
    assert _SECRET not in "\n".join(item.getMessage() for item in caplog.records)


async def test_worker_failure_logs_structured_fields_without_payload(caplog) -> None:
    """worker 执行失败日志可与容量拒绝区分，且不记录 payload。"""

    metrics = RunRuntimeMetrics()
    store = _MemoryRunStore()
    events = _MemoryEventStore()
    snapshot = _snapshot("run-1", client_request_id="client-1")
    store.snapshots[snapshot.run_id] = snapshot
    worker = RunWorker(
        run_store=store,
        event_store=events,
        executor=_FailingCoordinator(),  # type: ignore[arg-type]
        lease_seconds=30,
        heartbeat_interval_seconds=10,
        owner_id="worker-a",
        metrics=metrics,
    )

    with caplog.at_level(logging.INFO, logger="infrastructure.run.run_worker"):
        assert await worker.run_once() is True

    failed = _record(caplog.records, "Run execution failed")
    assert failed.run_id == "run-1"
    assert failed.run_kind == RunKind.CHAT.value
    assert failed.run_status == RunStatus.FAILED.value
    assert failed.worker_id == "worker-a"
    assert failed.client_request_id == "client-1"
    assert metrics.snapshot().claim_success_count == 1
    assert metrics.snapshot().execution_failed_count == 1
    assert metrics.snapshot().execution_duration_count == 1
    assert metrics.snapshot().queue_full_count == 0
    assert _SECRET not in "\n".join(record.getMessage() for record in caplog.records)


async def test_replay_expired_increments_metric_and_logs_signal(caplog) -> None:
    """事件 replay 过期有独立日志和计数。"""

    metrics = RunRuntimeMetrics()
    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store=store, event_store=events, metrics=metrics, max_event_count=2)
    snapshot = await service.create_run(_request())
    await events.append_event(snapshot.run_id, RunEventType.SEGMENT_STARTED, {})
    await events.trim_events(
        snapshot.run_id,
        EventRetentionPolicy(max_event_count=2, ttl_seconds=3600),
    )

    with (
        caplog.at_level(logging.WARNING, logger="application.run.run_application_service"),
        pytest.raises(RunEventReplayExpiredError),
    ):
        await service.list_events(snapshot.run_id, after_cursor=0, limit=10)

    record = _record(caplog.records, "Run event replay expired")
    assert record.run_id == snapshot.run_id
    assert record.after_cursor == 0
    assert metrics.snapshot().replay_expired_count == 1


async def test_lost_sweep_exposes_event_log_and_metrics(caplog) -> None:
    """lost sweep 通过事件、日志和 metrics 暴露 lease 过期信号。"""

    metrics = RunRuntimeMetrics()
    store = _MemoryRunStore()
    events = _MemoryEventStore()
    expired = replace(
        _snapshot("run-1", client_request_id="client-1", status=RunStatus.RUNNING),
        lease=RunLease(
            owner_id="worker-a",
            lease_until=datetime.now(UTC) - timedelta(seconds=5),
            heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
        ),
    )
    store.snapshots[expired.run_id] = expired
    manager = RunWorkerManager(
        run_store=store,
        event_store=events,
        executor=_FailingCoordinator(),  # type: ignore[arg-type]
        config=RunRuntimeConfig(
            worker_count=1,
            lease_seconds=30,
            heartbeat_interval_seconds=10,
            lost_sweep_interval_seconds=10,
        ),
        poll_interval_seconds=10,
        owner_prefix="manager-a",
        metrics=metrics,
    )

    with caplog.at_level(logging.WARNING, logger="infrastructure.run.run_worker_manager"):
        await manager.start()
        await asyncio.sleep(0.02)
        await manager.stop()

    loaded = await store.get_run("run-1")
    assert loaded is not None
    assert loaded.status is RunStatus.LOST
    assert events.events["run-1"][-1].event_type is RunEventType.RUN_LOST
    record = _record(caplog.records, "Run marked lost by lease sweep")
    assert record.run_id == "run-1"
    assert record.run_status == RunStatus.LOST.value
    assert record.worker_id == "manager-a"
    assert metrics.snapshot().lease_expired_count == 1
    assert metrics.snapshot().lost_count == 1


def _service(
    *,
    store: _MemoryRunStore | None = None,
    event_store: _MemoryEventStore | None = None,
    metrics: RunRuntimeMetrics | None = None,
    max_queued_runs: int = 10,
    max_event_count: int = 100,
) -> RunApplicationService:
    return RunApplicationService(
        run_store=store or _MemoryRunStore(),
        event_store=event_store or _MemoryEventStore(),
        capacity_policy=RunCapacityPolicy(
            max_queued_runs=max_queued_runs,
            max_running_runs=10,
        ),
        event_retention_policy=EventRetentionPolicy(
            max_event_count=max_event_count,
            ttl_seconds=3600,
        ),
        workflow_serializer=WorkflowSerializerAdapter(),
        metrics=metrics,
        event_stream_wait_seconds=0,
    )


def _request() -> RunCreateRequest:
    return RunCreateRequest(
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": _SECRET, "tool_args": {"token": "secret-token"}},
            model="model-a",
        ),
        client_request_id="client-1",
    )


def _snapshot(
    run_id: str,
    *,
    payload: RunPayload | None = None,
    client_request_id: str | None = None,
    status: RunStatus = RunStatus.QUEUED,
) -> RunSnapshot:
    payload = payload or _request().payload
    return RunSnapshot(
        run_id=run_id,
        kind=payload.kind,
        status=status,
        payload=payload,
        client_request_id=client_request_id,
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={"segment_count": 0},
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
    )


def _record(records, message: str):
    for record in records:
        if record.getMessage() == message:
            return record
    raise AssertionError(f"missing log record: {message}")
