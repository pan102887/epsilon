"""阶段三后台 Run runtime 后端集成测试。

这些测试组合 RunApplicationService、内存 Run/Event store、RunWorker 与 fake
coordinator，覆盖阶段三端到端生命周期，同时用轻量静态断言保护阶段二同步
HTTP 入口兼容性。测试不依赖真实模型、网络或启动 FastAPI 服务。
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from application.run.run_application_service import (
    ApprovalResumeResult,
    RunApplicationService,
)
from domain.agent.value_objects import ApprovalDecision
from domain.run.exceptions import RunContinuationUnavailableError, RunLeaseConflictError
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

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRunStore:
    """集成测试用内存 RunStore，保留核心状态机与 owner 语义。"""

    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}
        self.client_index: dict[str, str] = {}
        self.next_run_number = 1
        self._lock = asyncio.Lock()

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        async with self._lock:
            if request.client_request_id in self.client_index:
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
        self.snapshots[run_id] = updated
        return updated

    async def acquire_approval_resume_lease(
        self, *, run_id: str, owner_id: str, lease_seconds: int
    ) -> RunSnapshot:
        """为审批恢复测试路径写入短生命周期租约。"""

        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(
                run_id,
                f"当前状态为 {snapshot.status.value}，不是 awaiting_approval",
            )
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

    async def release_approval_resume_lease(self, *, run_id: str, owner_id: str) -> RunSnapshot:
        """释放测试 fake 中的审批恢复短租约。"""

        snapshot = self.snapshots[run_id]
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            return snapshot
        updated = replace(snapshot, lease=None, updated_at=datetime.now(UTC))
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
            can_continue=False,
            terminal_reason="cancelled" if target is RunStatus.CANCELLED else None,
            lease=None if target is RunStatus.CANCELLED else snapshot.lease,
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
        )
        updated = replace(updated, approval_id=approval_id)
        self.snapshots[run_id] = updated
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
        )

    async def resolve_approval_resume(
        self, *, run_id: str, owner_id: str, result: ApprovalResumeStoreResult
    ) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(
                run_id,
                f"当前状态为 {snapshot.status.value}，不是 awaiting_approval",
            )
        if result.status == "queued":
            updated = replace(
                snapshot,
                status=RunStatus.QUEUED,
                result=result.result or snapshot.result,
                error=None,
                approval_id=None,
                can_continue=False,
                terminal_reason=None,
                lease=None,
                updated_at=datetime.now(UTC),
                version=snapshot.version + 1,
            )
        elif result.status == "succeeded":
            updated = replace(
                snapshot,
                status=RunStatus.SUCCEEDED,
                result=result.result or {"ok": True},
                error=None,
                approval_id=None,
                can_continue=False,
                terminal_reason=result.terminal_reason or "completed",
                lease=None,
                updated_at=datetime.now(UTC),
                version=snapshot.version + 1,
            )
        elif result.status == "failed":
            updated = replace(
                snapshot,
                status=RunStatus.FAILED,
                result=None,
                error=result.error or {"message": "approval failed"},
                approval_id=None,
                can_continue=False,
                terminal_reason=result.terminal_reason or "failed",
                lease=None,
                updated_at=datetime.now(UTC),
                version=snapshot.version + 1,
            )
        else:
            updated = replace(
                snapshot,
                status=RunStatus.CANCELLED,
                result=result.result or {"reason": "cancelled"},
                error=None,
                approval_id=None,
                can_continue=False,
                terminal_reason=result.terminal_reason or "cancelled",
                lease=None,
                updated_at=datetime.now(UTC),
                version=snapshot.version + 1,
            )
        self.snapshots[run_id] = updated
        return updated

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
            segment_metadata=(result or {}).get("segment_metadata", snapshot.segment_metadata),
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
    """内存事件 store，并把 latest cursor 同步回快照 fake。"""

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
        events = self.events.get(run_id, [])
        if len(events) > policy.max_event_count:
            self.events[run_id] = events[-policy.max_event_count :]

    async def first_cursor(self, run_id: str) -> int | None:
        events = self.events.get(run_id, [])
        return events[0].cursor if events else None

    def event_types(self, run_id: str) -> list[RunEventType]:
        return [event.event_type for event in self.events.get(run_id, [])]


class _SequenceCoordinator:
    """按顺序返回 outcome 的 fake coordinator，可阻塞以测试 running cancel。"""

    def __init__(
        self,
        outcomes: list[RunExecutionOutcome],
        *,
        block: bool = False,
    ) -> None:
        self._outcomes = outcomes
        self._block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[RunSnapshot] = []

    async def execute(
        self,
        snapshot: RunSnapshot,
        progress: RunProgressSink,
    ) -> RunExecutionOutcome:
        self.calls.append(snapshot)
        self.started.set()
        if self._block:
            await self.release.wait()
        await progress.segment_done(
            snapshot.run_id,
            self._outcomes[0].segment_metadata or {"segment_count": len(self.calls)},
        )
        return self._outcomes.pop(0)


@pytest.mark.asyncio
async def test_create_worker_query_events_terminal_success_path() -> None:
    service, store, events, worker = _fixture(
        [RunExecutionOutcome(status=RunStatus.SUCCEEDED, result={"answer": "done"})]
    )

    created = await service.create_run(_request("hello"))
    assert created.status is RunStatus.QUEUED

    assert await worker.run_once() is True

    snapshot = await service.get_run(created.run_id)
    assert snapshot.status is RunStatus.SUCCEEDED
    assert snapshot.result == {"answer": "done"}
    assert snapshot.latest_event_cursor is not None

    replayed = await service.list_events(created.run_id, after_cursor=None, limit=20)
    assert [event.event_type for event in replayed] == events.event_types(created.run_id)
    assert events.event_types(created.run_id) == [
        RunEventType.RUN_CREATED,
        RunEventType.RUN_QUEUED,
        RunEventType.RUN_CLAIMED,
        RunEventType.SEGMENT_STARTED,
        RunEventType.SEGMENT_DONE,
        RunEventType.RUN_SUCCEEDED,
    ]
    assert replayed[-1].event_type is RunEventType.RUN_SUCCEEDED
    assert await store.count_by_status({RunStatus.RUNNING}) == 0


@pytest.mark.asyncio
async def test_paused_continue_then_succeeded_same_run() -> None:
    service, _, events, worker = _fixture(
        [
            RunExecutionOutcome(
                status=RunStatus.PAUSED,
                result={"partial": True, "segment_metadata": {"segment_count": 1}},
                can_continue=True,
                segment_metadata={"segment_count": 1},
            ),
            RunExecutionOutcome(
                status=RunStatus.SUCCEEDED,
                result={"answer": "continued"},
                segment_metadata={"segment_count": 2},
            ),
        ]
    )
    created = await service.create_run(_request("pause"))

    assert await worker.run_once() is True
    paused = await service.get_run(created.run_id)
    assert paused.status is RunStatus.PAUSED
    assert paused.can_continue is True

    queued = await service.continue_run(created.run_id, model="model-b")
    assert queued.run_id == created.run_id
    assert queued.status is RunStatus.QUEUED

    assert await worker.run_once() is True
    completed = await service.get_run(created.run_id)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.result == {"answer": "continued"}
    assert events.event_types(created.run_id).count(RunEventType.RUN_QUEUED) == 2
    assert events.event_types(created.run_id)[-1] is RunEventType.RUN_SUCCEEDED


@pytest.mark.asyncio
async def test_running_cancel_converges_to_cancelled_at_segment_boundary() -> None:
    coordinator = _SequenceCoordinator(
        [RunExecutionOutcome(status=RunStatus.SUCCEEDED, result={"late": True})],
        block=True,
    )
    service, _, events, worker = _fixture_with_coordinator(coordinator)
    created = await service.create_run(_request("cancel"))

    worker_task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(coordinator.started.wait(), timeout=1)

    cancelling = await service.request_cancel(created.run_id)
    assert cancelling.status is RunStatus.CANCEL_REQUESTED

    coordinator.release.set()
    assert await asyncio.wait_for(worker_task, timeout=1) is True

    snapshot = await service.get_run(created.run_id)
    assert snapshot.status is RunStatus.CANCELLED
    assert snapshot.terminal_reason == "cancel_requested_after_segment"
    assert events.event_types(created.run_id)[-1] is RunEventType.RUN_CANCELLED


@pytest.mark.asyncio
async def test_approval_wait_and_resume_requeues_then_terminal_same_run() -> None:
    service, _, events, worker = _fixture(
        [
            RunExecutionOutcome(
                status=RunStatus.AWAITING_APPROVAL,
                result={"tool": "write"},
                approval_id="approval-1",
                can_continue=True,
            ),
            RunExecutionOutcome(status=RunStatus.SUCCEEDED, result={"approved": True}),
        ],
        approval_resume_status="queued",
    )
    created = await service.create_run(_request("needs approval"))

    assert await worker.run_once() is True
    waiting = await service.get_run(created.run_id)
    assert waiting.status is RunStatus.AWAITING_APPROVAL
    assert waiting.approval_id == "approval-1"

    resumed = await service.resume_approval_run(
        created.run_id,
        [ApprovalDecision(type="approve", tool_call_id="call-1")],
    )
    assert resumed.run_id == created.run_id
    assert resumed.status is RunStatus.QUEUED

    assert await worker.run_once() is True
    completed = await service.get_run(created.run_id)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.result == {"approved": True}
    assert RunEventType.APPROVAL_REQUIRED in events.event_types(created.run_id)
    assert events.event_types(created.run_id)[-1] is RunEventType.RUN_SUCCEEDED


def test_phase_two_synchronous_chat_and_task_routes_remain_available() -> None:
    chat_router_module = importlib.import_module("application.api.routers.chat")
    task_router_module = importlib.import_module("application.api.routers.task")
    chat_compat = importlib.import_module("application.routers.chat")
    task_compat = importlib.import_module("application.routers.task")

    chat_paths = {route.path for route in chat_router_module.router.routes}
    task_paths = {route.path for route in task_router_module.router.routes}

    assert "/api/chat" in chat_paths
    assert "/api/chat/sessions/{session_id}/continue" in chat_paths
    assert "/api/chat/sessions/{session_id}/approvals/{approval_id}/resume" in chat_paths
    assert "/api/task/execute" in task_paths
    assert "/api/task/sessions/{session_id}/continue" in task_paths
    assert chat_compat.chat is chat_router_module.chat
    assert chat_compat.continue_chat is chat_router_module.continue_chat
    assert task_compat.execute_task is task_router_module.execute_task
    assert task_compat.continue_task is task_router_module.continue_task


def _fixture(
    outcomes: list[RunExecutionOutcome],
    *,
    approval_resume_status: str = "queued",
) -> tuple[
    RunApplicationService,
    _MemoryRunStore,
    _MemoryEventStore,
    RunWorker,
]:
    return _fixture_with_coordinator(
        _SequenceCoordinator(outcomes),
        approval_resume_status=approval_resume_status,
    )


def _fixture_with_coordinator(
    coordinator: _SequenceCoordinator,
    *,
    approval_resume_status: str = "queued",
) -> tuple[
    RunApplicationService,
    _MemoryRunStore,
    _MemoryEventStore,
    RunWorker,
]:
    store = _MemoryRunStore()
    events = _MemoryEventStore(store)

    async def approval_resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        assert snapshot.status is RunStatus.AWAITING_APPROVAL
        assert decisions
        return ApprovalResumeResult(
            status=approval_resume_status,  # type: ignore[arg-type]
            result={"approval": "resolved", "model": model},
            terminal_reason="approval_resumed",
        )

    service = RunApplicationService(
        run_store=cast(RunStorePort, store),
        event_store=events,
        capacity_policy=RunCapacityPolicy(max_queued_runs=20, max_running_runs=20),
        event_retention_policy=EventRetentionPolicy(
            max_event_count=100,
            ttl_seconds=3600,
        ),
        workflow_serializer=WorkflowSerializerAdapter(),
        approval_resumer=approval_resumer,
        event_stream_wait_seconds=0.01,
    )
    worker = RunWorker(
        run_store=cast(RunStorePort, store),
        event_store=events,
        executor=coordinator,  # type: ignore[arg-type]
        lease_seconds=30,
        heartbeat_interval_seconds=10,
        owner_id="test-worker",
    )
    return service, store, events, worker


def _request(message: str) -> RunCreateRequest:
    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"session_id": "session-1", "message": message},
        model="model-a",
    )
    return RunCreateRequest(
        payload=payload,
        client_request_id=f"client-{message}",
    )
