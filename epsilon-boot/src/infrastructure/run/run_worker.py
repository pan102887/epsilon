"""后台 Run worker 实现。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, cast

from domain.run.outcome import (
    RunExecutionOutcome,
    RunOutcomePersistenceDecision,
    RunStoreMutationKind,
    decide_run_outcome_persistence,
)
from domain.run.ports import RunEventStorePort, RunProgressSink, RunStorePort
from domain.run.value_objects import RunEventType, RunSnapshot, RunStatus
from infrastructure.run.worker_contracts import RunRuntimeMetricsSink, RunSegmentExecutor

logger = logging.getLogger(__name__)


class RunWorker:
    """从 RunStore 领取 queued Run 并推进一个执行段。"""

    def __init__(
        self,
        *,
        run_store: RunStorePort,
        event_store: RunEventStorePort,
        executor: RunSegmentExecutor,
        lease_seconds: int,
        heartbeat_interval_seconds: float,
        auto_continue_paused_runs: bool = False,
        auto_continue_max_segments: int = 20,
        owner_id: str | None = None,
        metrics: RunRuntimeMetricsSink | None = None,
    ) -> None:
        self._run_store = run_store
        self._event_store = event_store
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._auto_continue_paused_runs = auto_continue_paused_runs
        self._auto_continue_max_segments = auto_continue_max_segments
        self.owner_id = owner_id or f"run-worker-{uuid.uuid4().hex}"
        self._metrics = metrics

    async def run_once(self) -> bool:
        """领取并执行一个 Run；没有可领取 Run 时返回 False。"""

        snapshot = await self._run_store.claim_next(
            owner_id=self.owner_id,
            lease_seconds=self._lease_seconds,
        )
        if snapshot is None:
            return False

        if self._metrics is not None:
            self._metrics.increment_claim_success()
        logger.info(
            "Run claimed by worker",
            extra=_run_log_extra(
                snapshot,
                worker_id=self.owner_id,
                lease_until=snapshot.lease.lease_until.isoformat() if snapshot.lease else None,
                segment_count=_segment_count(snapshot.segment_metadata),
            ),
        )
        await self._append_event(
            snapshot.run_id,
            RunEventType.RUN_CLAIMED,
            {
                "owner_id": self.owner_id,
                "status": RunStatus.RUNNING,
                "lease_until": snapshot.lease.lease_until if snapshot.lease else None,
            },
        )

        if await self._cancel_requested(snapshot.run_id):
            await self._mark_cancelled(snapshot.run_id, "cancel_requested_before_segment")
            return True

        progress = _WorkerProgressSink(
            run_id=snapshot.run_id,
            event_store=self._event_store,
        )
        await progress.segment_started(
            snapshot.run_id,
            _next_segment_index(snapshot),
        )

        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self.heartbeat_loop(snapshot.run_id, self.owner_id, stop_heartbeat)
        )
        started_at = time.perf_counter()
        try:
            outcome = await self._execute(snapshot, progress)
        finally:
            stop_heartbeat.set()
            await _cancel_task_if_needed(heartbeat_task)
            if self._metrics is not None:
                self._metrics.observe_execution_duration(time.perf_counter() - started_at)

        if not progress.segment_done_written:
            await progress.segment_done(
                snapshot.run_id,
                outcome.segment_metadata or {},
            )

        if await self._cancel_requested(snapshot.run_id):
            await self._mark_cancelled(snapshot.run_id, "cancel_requested_after_segment")
            return True

        persisted = await self._persist_outcome(snapshot.run_id, outcome)
        if self._should_auto_continue(persisted, outcome):
            queued = await self._run_store.enqueue_continue(run_id=snapshot.run_id)
            await self._append_event(
                snapshot.run_id,
                RunEventType.RUN_QUEUED,
                {
                    "previous_status": persisted.status,
                    "auto_continue": True,
                    "segment_count": _segment_count(outcome.segment_metadata),
                },
            )
            logger.info(
                "Run auto continuation queued",
                extra=_run_log_extra(
                    queued,
                    worker_id=self.owner_id,
                    segment_count=_segment_count(outcome.segment_metadata),
                ),
            )
        return True

    async def heartbeat_loop(
        self,
        run_id: str,
        owner_id: str,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """周期性刷新租约，直到 stop_event 触发或租约不再可刷新。"""

        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
                break
            except TimeoutError:
                pass

            try:
                snapshot = await self._run_store.refresh_lease(
                    run_id=run_id,
                    owner_id=owner_id,
                    lease_seconds=self._lease_seconds,
                )
                if snapshot.status not in {
                    RunStatus.RUNNING,
                    RunStatus.CANCEL_REQUESTED,
                }:
                    break
                await self._append_event(
                    run_id,
                    RunEventType.RUN_HEARTBEAT,
                    {
                        "owner_id": owner_id,
                        "lease_until": snapshot.lease.lease_until if snapshot.lease else None,
                    },
                )
            except Exception:
                break

    async def _execute(
        self,
        snapshot: RunSnapshot,
        progress: RunProgressSink,
    ) -> RunExecutionOutcome:
        try:
            return await self._executor.execute(snapshot, progress)
        except Exception as exc:
            logger.warning(
                "Run execution raised and will be marked failed",
                extra=_run_log_extra(
                    snapshot,
                    worker_id=self.owner_id,
                    error_type=exc.__class__.__name__,
                ),
            )
            return RunExecutionOutcome(
                status=RunStatus.FAILED,
                error={
                    "message": str(exc) or exc.__class__.__name__,
                    "type": exc.__class__.__name__,
                },
                terminal_reason="failed",
                can_continue=False,
            )

    async def _persist_outcome(self, run_id: str, outcome: RunExecutionOutcome) -> RunSnapshot:
        decision = decide_run_outcome_persistence(outcome)
        snapshot = await self._apply_store_mutation(run_id, decision)
        await self._append_terminal_event(run_id, decision)
        self._log_terminal(snapshot, decision.terminal_outcome)
        return snapshot

    def _should_auto_continue(
        self,
        snapshot: RunSnapshot,
        outcome: RunExecutionOutcome,
    ) -> bool:
        if not self._auto_continue_paused_runs:
            return False
        if snapshot.status is not RunStatus.PAUSED or not outcome.can_continue:
            return False
        segment_count = _segment_count(outcome.segment_metadata)
        if (
            segment_count is not None
            and segment_count >= self._auto_continue_max_segments
        ):
            return False
        return _auto_continue_safe_stop_reason(outcome.segment_metadata)

    async def _apply_store_mutation(
        self,
        run_id: str,
        decision: RunOutcomePersistenceDecision,
    ) -> RunSnapshot:
        mutation = decision.mutation
        if mutation.kind is RunStoreMutationKind.MARK_SUCCEEDED:
            return await self._run_store.mark_succeeded(
                run_id=run_id,
                owner_id=self.owner_id,
                result=mutation.result or {},
                workflow_run_state=mutation.workflow_run_state,
                collaboration_summary=mutation.collaboration_summary,
            )
        if mutation.kind is RunStoreMutationKind.MARK_PAUSED:
            return await self._run_store.mark_paused(
                run_id=run_id,
                owner_id=self.owner_id,
                result=mutation.result or {},
                workflow_run_state=mutation.workflow_run_state,
                collaboration_summary=mutation.collaboration_summary,
            )
        if mutation.kind is RunStoreMutationKind.MARK_AWAITING_APPROVAL:
            if mutation.approval_id is None:
                raise ValueError("Run outcome decision is missing approval_id")
            return await self._run_store.mark_awaiting_approval(
                run_id=run_id,
                owner_id=self.owner_id,
                approval_id=mutation.approval_id,
                result=mutation.result or {},
                workflow_run_state=mutation.workflow_run_state,
                collaboration_summary=mutation.collaboration_summary,
            )
        if mutation.kind is RunStoreMutationKind.MARK_CANCELLED:
            return await self._run_store.mark_cancelled(
                run_id=run_id,
                owner_id=self.owner_id,
                reason=mutation.reason or "cancelled",
                workflow_run_state=mutation.workflow_run_state,
                collaboration_summary=mutation.collaboration_summary,
            )
        return await self._run_store.mark_failed(
            run_id=run_id,
            owner_id=self.owner_id,
            error=mutation.error or {},
            workflow_run_state=mutation.workflow_run_state,
            collaboration_summary=mutation.collaboration_summary,
        )

    async def _cancel_requested(self, run_id: str) -> bool:
        snapshot = await self._run_store.get_run(run_id)
        return snapshot is not None and snapshot.status is RunStatus.CANCEL_REQUESTED

    async def _mark_cancelled(
        self,
        run_id: str,
        reason: str,
        *,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> None:
        snapshot = await self._run_store.mark_cancelled(
            run_id=run_id,
            owner_id=self.owner_id,
            reason=reason,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )
        await self._append_event(
            run_id,
            RunEventType.RUN_CANCELLED,
            {
                "status": RunStatus.CANCELLED,
                "reason": reason,
                "workflow_run_state": workflow_run_state,
                "collaboration_summary": collaboration_summary,
            },
        )
        logger.info(
            "Run cancelled by worker",
            extra=_run_log_extra(
                snapshot,
                worker_id=self.owner_id,
                terminal_reason=reason,
                segment_count=_segment_count(snapshot.segment_metadata),
            ),
        )

    async def _append_terminal_event(
        self,
        run_id: str,
        decision: RunOutcomePersistenceDecision,
    ) -> None:
        outcome = decision.terminal_outcome
        if decision.event_type is RunEventType.RUN_CANCELLED:
            await self._append_event(
                run_id,
                RunEventType.RUN_CANCELLED,
                {
                    "status": RunStatus.CANCELLED,
                    "reason": decision.mutation.reason or outcome.terminal_reason or "cancelled",
                    "workflow_run_state": outcome.workflow_run_state,
                    "collaboration_summary": outcome.collaboration_summary,
                },
            )
            return

        await self._append_event(
            run_id,
            decision.event_type,
            {
                "status": outcome.status,
                "result": outcome.result,
                "error": outcome.error,
                "approval_id": outcome.approval_id,
                "terminal_reason": outcome.terminal_reason,
                "can_continue": outcome.can_continue,
                "segment_metadata": outcome.segment_metadata,
                "workflow_run_state": outcome.workflow_run_state,
                "collaboration_summary": outcome.collaboration_summary,
            },
        )

    async def _append_event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
    ) -> None:
        await self._event_store.append_event(run_id, event_type, _json_safe(payload))

    def _record_failed(self) -> None:
        if self._metrics is not None:
            self._metrics.increment_execution_failed()

    def _log_terminal(
        self,
        snapshot: RunSnapshot,
        outcome: RunExecutionOutcome,
    ) -> None:
        if snapshot.status is RunStatus.FAILED:
            self._record_failed()
            log = logger.warning
            message = "Run execution failed"
        else:
            log = logger.info
            message = "Run execution finished"
        log(
            message,
            extra=_run_log_extra(
                snapshot,
                worker_id=self.owner_id,
                terminal_reason=outcome.terminal_reason or snapshot.terminal_reason,
                can_continue=outcome.can_continue,
                segment_count=_segment_count(outcome.segment_metadata),
            ),
        )


class _WorkerProgressSink:
    """把协调器进度回调写入 RunEventStore，并抑制重复段事件。"""

    def __init__(self, *, run_id: str, event_store: RunEventStorePort) -> None:
        self._run_id = run_id
        self._event_store = event_store
        self._started_segments: set[int] = set()
        self._segment_done_written = False

    @property
    def segment_done_written(self) -> bool:
        return self._segment_done_written

    async def segment_started(self, run_id: str, segment_index: int) -> None:
        if run_id != self._run_id or segment_index in self._started_segments:
            return
        self._started_segments.add(segment_index)
        await self._event_store.append_event(
            run_id,
            RunEventType.SEGMENT_STARTED,
            _json_safe(
                {
                    "status": RunStatus.RUNNING,
                    "segment_index": segment_index,
                }
            ),
        )

    async def segment_done(self, run_id: str, metadata: dict[str, Any]) -> None:
        if run_id != self._run_id or self._segment_done_written:
            return
        self._segment_done_written = True
        await self._event_store.append_event(
            run_id,
            RunEventType.SEGMENT_DONE,
            _json_safe(
                {
                    "status": RunStatus.RUNNING,
                    "segment_metadata": metadata,
                }
            ),
        )


def _next_segment_index(snapshot: RunSnapshot) -> int:
    metadata = snapshot.segment_metadata or {}
    raw_count = metadata.get("segment_count", 0)
    return raw_count + 1 if isinstance(raw_count, int) and raw_count >= 0 else 1


def _segment_count(metadata: dict[str, Any] | None) -> int | None:
    if metadata is None:
        return None
    value = metadata.get("segment_count")
    return value if isinstance(value, int) else None


def _auto_continue_safe_stop_reason(metadata: dict[str, Any] | None) -> bool:
    """判断 paused Run 是否适合由 worker 自动重新入队。"""

    if metadata is None:
        return True
    reason = metadata.get("segment_stop_reason")
    return reason not in {
        "approval_required",
        "max_continuations_reached",
        "total_token_budget_reached",
        "total_duration_budget_reached",
        "consecutive_paused_limit",
        "no_progress",
        "repeated_tool_call",
        "tool_boundary_unavailable",
        "continue_precondition_failed",
        "risk_gate_required",
    }


def _run_log_extra(
    snapshot: RunSnapshot,
    *,
    worker_id: str | None,
    **extra: Any,
) -> dict[str, Any]:
    data = {
        "run_id": snapshot.run_id,
        "run_kind": snapshot.kind.value,
        "run_status": snapshot.status.value,
        "worker_id": worker_id,
        "client_request_id": snapshot.client_request_id,
    }
    data.update(extra)
    return data


def run_log_extra(
    snapshot: RunSnapshot,
    *,
    worker_id: str | None,
    **extra: Any,
) -> dict[str, Any]:
    """构造 Run worker 的结构化日志字段。"""
    return _run_log_extra(snapshot, worker_id=worker_id, **extra)


def _json_safe(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        items = cast(list[object] | tuple[object, ...], value)
        return [_json_safe(item) for item in items]
    return value


async def _cancel_task_if_needed(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
