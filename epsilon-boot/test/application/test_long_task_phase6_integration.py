"""阶段六 workflow Run 创建、选择与 phase 推进集成测试。"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from application.run.run_application_service import RunApplicationService
from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from domain.run.exceptions import (
    RunContinuationUnavailableError,
    RunLeaseConflictError,
    RunUnknownWorkflowError,
)
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import ApprovalResumeStoreResult, RunProgressSink, RunStorePort
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
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter
from infrastructure.run.run_worker import RunWorker
from infrastructure.run.static_workflow_registry_adapter import StaticWorkflowRegistryAdapter
from infrastructure.run.static_workflow_selector import StaticWorkflowSelector
from infrastructure.run.workflow_config import RunWorkflowConfig

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRunStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}
        self.client_index: dict[str, str] = {}
        self.next_run_number = 1
        self._lock = asyncio.Lock()

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        async with self._lock:
            if (
                request.client_request_id is not None
                and request.client_request_id in self.client_index
            ):
                return self.snapshots[self.client_index[request.client_request_id]]
            run_id = f"run-{self.next_run_number}"
            self.next_run_number += 1
            snapshot = RunSnapshot(
                run_id=run_id,
                kind=request.payload.kind,
                status=RunStatus.QUEUED,
                payload=request.payload,
                client_request_id=request.client_request_id,
                payload_hash=request.effective_payload_hash(),
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
                task_classification=request.task_classification,
                guardrail_summary=request.guardrail_summary,
                workflow_name=request.workflow_name,
                workflow_run_state=request.workflow_run_state,
                collaboration_summary=request.collaboration_summary,
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
        async with self._lock:
            for snapshot in self.snapshots.values():
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
                self.snapshots[snapshot.run_id] = updated
                return updated
        return None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        snapshot = self._require_owner(run_id, owner_id)
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
        updated = replace(
            snapshot,
            status=RunStatus.CANCEL_REQUESTED,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
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
            terminal_reason="workflow_phase_completed",
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
        self.snapshots[run_id] = replace(updated, approval_id=approval_id)
        return self.snapshots[run_id]

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

    async def enqueue_continue(self, *, run_id: str, model: str | None = None) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        payload = replace(snapshot.payload, model=model or snapshot.payload.model)
        updated = replace(
            snapshot,
            status=RunStatus.QUEUED,
            payload=payload,
            can_continue=False,
            approval_id=None,
            lease=None,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def resolve_approval_resume(
        self, *, run_id: str, owner_id: str, result: ApprovalResumeStoreResult
    ) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(run_id, "not awaiting approval")
        updated = replace(snapshot, status=RunStatus.QUEUED, approval_id=None)
        self.snapshots[run_id] = updated
        return updated

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        return []

    def update_latest_cursor(self, run_id: str, cursor: int) -> None:
        snapshot = self.snapshots.get(run_id)
        if snapshot is not None:
            self.snapshots[run_id] = replace(snapshot, latest_event_cursor=cursor)

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
            can_continue=can_continue,
            terminal_reason=terminal_reason,
            lease=None,
            workflow_run_state=workflow_run_state
            if workflow_run_state is not None
            else snapshot.workflow_run_state,
            collaboration_summary=collaboration_summary
            if collaboration_summary is not None
            else snapshot.collaboration_summary,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    def _require_owner(self, run_id: str, owner_id: str) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            raise RunLeaseConflictError(run_id, owner_id)
        return snapshot


class _MemoryEventStore:
    def __init__(self, run_store: _MemoryRunStore) -> None:
        self._run_store = run_store
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
        self._run_store.update_latest_cursor(run_id, event.cursor)
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
        return None

    async def first_cursor(self, run_id: str) -> int | None:
        events = self.events.get(run_id, [])
        return events[0].cursor if events else None

    def event_types(self, run_id: str) -> list[RunEventType]:
        return [event.event_type for event in self.events.get(run_id, [])]


class _WorkflowCoordinator:
    def __init__(
        self,
        *,
        event_store: _MemoryEventStore,
        registry: StaticWorkflowRegistryAdapter,
        outcomes: list[RunExecutionOutcome],
    ) -> None:
        self.calls: list[RunSnapshot] = []
        self._outcomes = outcomes
        self._orchestrator = WorkflowRunOrchestrator(
            event_store=event_store,
            workflow_registry=registry,
            workflow_serializer=WorkflowSerializerAdapter(),
            now=_Clock(),
        )

    async def execute(
        self,
        snapshot: RunSnapshot,
        progress: RunProgressSink,
    ) -> RunExecutionOutcome:
        self.calls.append(snapshot)

        async def execute_existing(current_snapshot: RunSnapshot) -> RunExecutionOutcome:
            assert current_snapshot.run_id == snapshot.run_id
            return self._outcomes.pop(0)

        return await self._orchestrator.execute_phase(
            snapshot=snapshot,
            execute_existing=execute_existing,
        )


class _Clock:
    def __init__(self) -> None:
        self.current = _NOW

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + timedelta(seconds=1)
        return value


class _Classifier:
    def classify_payload(self, payload: RunPayload, *, has_tools: bool):
        return "long_task"


def _fixture(outcomes: list[RunExecutionOutcome]):
    config = RunWorkflowConfig()
    registry = StaticWorkflowRegistryAdapter(config)
    selector = StaticWorkflowSelector(registry=registry, config=config)
    store = _MemoryRunStore()
    events = _MemoryEventStore(store)
    service = RunApplicationService(
        run_store=cast(RunStorePort, store),
        event_store=events,
        capacity_policy=RunCapacityPolicy(max_queued_runs=10, max_running_runs=10),
        event_retention_policy=EventRetentionPolicy(max_event_count=100, ttl_seconds=3600),
        workflow_serializer=WorkflowSerializerAdapter(),
        workflow_selector=selector,
    )
    coordinator = _WorkflowCoordinator(
        event_store=events,
        registry=registry,
        outcomes=outcomes,
    )
    worker = RunWorker(
        run_store=cast(RunStorePort, store),
        event_store=events,
        executor=coordinator,  # type: ignore[arg-type]
        lease_seconds=60,
        heartbeat_interval_seconds=60,
        owner_id="worker-1",
    )
    return service, store, events, worker, coordinator


def _service_with_classifier():
    config = RunWorkflowConfig()
    registry = StaticWorkflowRegistryAdapter(config)
    selector = StaticWorkflowSelector(registry=registry, config=config)
    store = _MemoryRunStore()
    events = _MemoryEventStore(store)
    service = RunApplicationService(
        run_store=cast(RunStorePort, store),
        event_store=events,
        capacity_policy=RunCapacityPolicy(max_queued_runs=10, max_running_runs=10),
        event_retention_policy=EventRetentionPolicy(max_event_count=100, ttl_seconds=3600),
        workflow_serializer=WorkflowSerializerAdapter(),
        workflow_selector=selector,
        guardrail_policy=_Classifier(),
    )
    return service, events


def _task_request(goal: str, *, workflow_name: str | None = None) -> RunCreateRequest:
    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="session-1",
        task={"goal": goal, "input_data": {}},
        model="model-a",
    )
    return RunCreateRequest(
        payload=payload,
        client_request_id=None,
        workflow_name=workflow_name,
    )


async def test_create_worker_continue_advances_workflow_phases() -> None:
    service, _store, events, worker, coordinator = _fixture(
        [
            RunExecutionOutcome(status=RunStatus.SUCCEEDED, result={"phase": "plan"}),
            RunExecutionOutcome(status=RunStatus.SUCCEEDED, result={"phase": "execute"}),
        ]
    )

    created = await service.create_run(_task_request("fix code path"))

    assert created.workflow_name == "code_change"
    assert created.workflow_run_state is not None
    assert created.workflow_run_state["current_phase"] == "plan"
    assert events.event_types(created.run_id)[:3] == [
        RunEventType.WORKFLOW_SELECTED,
        RunEventType.RUN_CREATED,
        RunEventType.RUN_QUEUED,
    ]

    assert await worker.run_once() is True
    paused = await service.get_run(created.run_id)
    assert paused.status is RunStatus.PAUSED
    assert paused.can_continue is True
    assert paused.terminal_reason == "workflow_phase_completed"
    assert paused.workflow_run_state is not None
    assert paused.workflow_run_state["current_phase"] == "execute"
    assert paused.workflow_run_state["phase_history"][0]["phase"] == "plan"

    queued = await service.continue_run(created.run_id, model="model-b")
    assert queued.status is RunStatus.QUEUED

    assert await worker.run_once() is True
    second_pause = await service.get_run(created.run_id)
    assert second_pause.status is RunStatus.PAUSED
    assert second_pause.workflow_run_state is not None
    assert second_pause.workflow_run_state["current_phase"] == "evaluate"
    assert [item["phase"] for item in second_pause.workflow_run_state["phase_history"]] == [
        "plan",
        "execute",
    ]
    call_phases: list[object] = []
    for snapshot in coordinator.calls:
        assert snapshot.workflow_run_state is not None
        call_phases.append(snapshot.workflow_run_state["current_phase"])
    assert call_phases == [
        "plan",
        "execute",
    ]
    assert events.event_types(created.run_id).count(RunEventType.RUN_QUEUED) == 2
    assert RunEventType.WORKFLOW_PHASE_STARTED in events.event_types(created.run_id)
    assert RunEventType.WORKFLOW_PHASE_COMPLETED in events.event_types(created.run_id)


async def test_explicit_unknown_workflow_and_auto_no_match_paths() -> None:
    service, _store, events, _worker, _coordinator = _fixture([])

    with pytest.raises(RunUnknownWorkflowError):
        await service.create_run(_task_request("ordinary", workflow_name="missing"))

    assert events.events == {}

    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": "hello"},
        model="model-a",
    )
    created = await service.create_run(RunCreateRequest(payload=payload, client_request_id=None))

    assert created.workflow_name is None
    assert created.workflow_run_state is None
    assert events.event_types(created.run_id)[0] is RunEventType.WORKFLOW_SELECTION_SKIPPED


async def test_guardrail_task_classification_participates_in_workflow_selection() -> None:
    service, events = _service_with_classifier()

    created = await service.create_run(_task_request("ordinary request"))

    assert created.task_classification == "long_task"
    assert created.workflow_name == "code_change"
    assert events.event_types(created.run_id)[:2] == [
        RunEventType.TASK_CLASSIFIED,
        RunEventType.WORKFLOW_SELECTED,
    ]
