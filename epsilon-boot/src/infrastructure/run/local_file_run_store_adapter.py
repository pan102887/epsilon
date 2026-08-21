"""本地文件 Run Store 与 Event Store 适配器。

本模块使用项目既有本地持久化 helper 实现 `RunStorePort` 与
`RunEventStorePort`。同一 Run 的快照状态、租约和事件 cursor 修改统一
持有快照文件对应的 EXCLUSIVE 文件锁，避免后台 worker 并发 claim 或事件
追加时产生重复执行权和非单调 cursor。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Collection
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from domain.run.exceptions import (
    RunCancelUnavailableError,
    RunContinuationUnavailableError,
    RunIdempotencyConflictError,
    RunLeaseConflictError,
    RunNotFoundError,
)
from domain.run.ports import (
    ApprovalResumeStoreResult,
    RunEventStorePort,
    RunObservationStorePort,
    RunStorePort,
)
from domain.run.state_machine import RunStateMachine
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from domain.run.workflow import canonicalize_collaboration_summary
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import FileLock, LockMode
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy


class LocalFileRunStoreAdapter(RunStorePort, RunEventStorePort, RunObservationStorePort):
    """Run 存储和事件存储的本地文件实现。

    文件布局：

    - `runs/snapshots/<bucket>/<run_id>.json`：Run 最新快照；
    - `runs/events/<bucket>/<run_id>.jsonl`：Run 事件历史；
    - `runs/indexes/client_request/<bucket>/<hash>.json`：幂等键索引。

    `<bucket>` 和索引文件名通过 `CrossPlatformPathPolicy.hash_session_id`
    生成；`run_id` 由适配器生成十六进制 UUID，天然满足跨平台文件名约束。
    """

    def __init__(
        self,
        root: Path,
        lock_factory: Callable[[Path], FileLock],
        path_policy: CrossPlatformPathPolicy,
        atomic_writer: TempFileAtomicWriter,
    ) -> None:
        """初始化本地文件 Run 存储适配器。

        Args:
            root: 本地持久化根目录。
            lock_factory: 跨平台文件锁工厂。
            path_policy: 跨平台路径策略。
            atomic_writer: 临时文件原子替换写入器。
        """

        self._root = root.resolve()
        self._runs_root = self._root / "runs"
        self._snapshots_root = self._runs_root / "snapshots"
        self._events_root = self._runs_root / "events"
        self._client_index_root = self._runs_root / "indexes" / "client_request"
        self._lock_factory = lock_factory
        self._policy = path_policy
        self._writer = atomic_writer
        self._state_machine = RunStateMachine()

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """创建 queued Run，或在幂等命中时返回既有快照。"""

        effective_request = replace(
            request,
            payload_hash=request.effective_payload_hash(),
        )
        if effective_request.client_request_id is None:
            return self._create_new_snapshot(effective_request)

        index_path = self._client_index_path(effective_request.client_request_id)
        index_lock = self._lock_factory(index_path.with_suffix(".json.lock"))
        with index_lock.acquire(LockMode.EXCLUSIVE):
            existing_run_id = self._read_client_index(index_path)
            if existing_run_id is not None:
                existing = self._read_snapshot_by_id(existing_run_id)
                if existing is not None:
                    if existing.payload_hash != effective_request.payload_hash:
                        raise RunIdempotencyConflictError(effective_request.client_request_id)
                    return existing

            snapshot = self._create_new_snapshot(effective_request)
            self._write_json_atomic(
                index_path,
                {
                    "client_request_id_hash": self._index_hash(effective_request.client_request_id),
                    "run_id": snapshot.run_id,
                    "payload_hash": snapshot.payload_hash,
                },
            )
            return snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        """按 run_id 查询最新快照。"""

        path = self._snapshot_path(run_id)
        if not path.exists():
            return None
        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.SHARED):
            return self._read_snapshot(path)

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        """按客户端幂等键查询既有 Run 快照。"""

        index_path = self._client_index_path(client_request_id)
        if not index_path.exists():
            return None
        index_lock = self._lock_factory(index_path.with_suffix(".json.lock"))
        with index_lock.acquire(LockMode.SHARED):
            run_id = self._read_client_index(index_path)
        if run_id is None:
            return None
        return await self.get_run(run_id)

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        """统计指定状态集合内的 Run 数量。"""

        return sum(
            1
            for snapshot in self._iter_snapshots()
            if snapshot is not None and snapshot.status in statuses
        )

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        """原子领取下一个 queued Run，并在同一锁内写入 running 与 lease。"""

        now = self._now()
        lease = RunLease(
            owner_id=owner_id,
            lease_until=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
        )
        for path in sorted(self._snapshots_root.glob("*/*.json")):
            run_id = path.stem
            lock = self._lock_factory(self._run_lock_path(run_id))
            with lock.acquire(LockMode.EXCLUSIVE):
                snapshot = self._read_snapshot(path)
                if snapshot is None or not self._state_machine.can_claim(snapshot.status):
                    continue
                self._state_machine.assert_transition(
                    snapshot.status,
                    RunStatus.RUNNING,
                )
                updated = replace(
                    snapshot,
                    status=RunStatus.RUNNING,
                    lease=lease,
                    can_continue=False,
                    updated_at=now,
                    version=snapshot.version + 1,
                )
                self._write_snapshot(updated)
                return updated
        return None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        """刷新当前 owner 持有的 Run 租约。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            self._assert_owner(snapshot, owner_id)
            return replace(
                snapshot,
                lease=RunLease(
                    owner_id=owner_id,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def acquire_approval_resume_lease(
        self, *, run_id: str, owner_id: str, lease_seconds: int
    ) -> RunSnapshot:
        """为 awaiting_approval Run 建立审批恢复期间的短租约。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status is not RunStatus.AWAITING_APPROVAL:
                raise RunContinuationUnavailableError(
                    run_id,
                    f"当前状态为 {snapshot.status.value}，不是 awaiting_approval",
                )
            if (
                snapshot.lease is not None
                and snapshot.lease.lease_until > now
                and snapshot.lease.owner_id.startswith("approval-resume-")
                and snapshot.lease.owner_id != owner_id
            ):
                raise RunLeaseConflictError(run_id, owner_id)
            return replace(
                snapshot,
                lease=RunLease(
                    owner_id=owner_id,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def release_approval_resume_lease(self, *, run_id: str, owner_id: str) -> RunSnapshot:
        """释放仍由当前审批恢复 owner 持有的短租约。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status is not RunStatus.AWAITING_APPROVAL:
                return snapshot
            if (
                snapshot.lease is None
                or snapshot.lease.owner_id != owner_id
                or not owner_id.startswith("approval-resume-")
            ):
                return snapshot
            return replace(
                snapshot,
                lease=None,
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        """请求取消 Run，并按状态机写入取消目标状态。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status is RunStatus.CANCEL_REQUESTED:
                return snapshot
            if not self._state_machine.can_cancel(snapshot.status):
                raise RunCancelUnavailableError(
                    run_id,
                    f"当前状态为 {snapshot.status.value}",
                )
            target = self._state_machine.cancellation_target(snapshot.status)
            self._state_machine.assert_transition(snapshot.status, target)
            terminal_reason = "cancelled" if target is RunStatus.CANCELLED else None
            return replace(
                snapshot,
                status=target,
                can_continue=False,
                terminal_reason=terminal_reason,
                lease=None if target is RunStatus.CANCELLED else snapshot.lease,
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def mark_succeeded(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验 owner 后将 Run 标记为 succeeded。"""

        return self._worker_transition(
            run_id=run_id,
            owner_id=owner_id,
            target=RunStatus.SUCCEEDED,
            result=result,
            error=None,
            approval_id=None,
            can_continue=False,
            terminal_reason="completed",
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def mark_failed(
        self,
        *,
        run_id: str,
        owner_id: str,
        error: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验 owner 后将 Run 标记为 failed。"""

        return self._worker_transition(
            run_id=run_id,
            owner_id=owner_id,
            target=RunStatus.FAILED,
            result=None,
            error=error,
            approval_id=None,
            can_continue=False,
            terminal_reason="failed",
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def mark_paused(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验 owner 后将 Run 标记为 paused。"""

        return self._worker_transition(
            run_id=run_id,
            owner_id=owner_id,
            target=RunStatus.PAUSED,
            result=result,
            error=None,
            approval_id=None,
            can_continue=True,
            terminal_reason=None,
            guardrail_summary=guardrail_summary,
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
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验 owner 后将 Run 标记为 awaiting_approval。"""

        return self._worker_transition(
            run_id=run_id,
            owner_id=owner_id,
            target=RunStatus.AWAITING_APPROVAL,
            result=result,
            error=None,
            approval_id=approval_id,
            can_continue=True,
            terminal_reason=None,
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def mark_cancelled(
        self,
        *,
        run_id: str,
        owner_id: str,
        reason: str,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验 owner 后将 cancel_requested Run 标记为 cancelled。"""

        return self._worker_transition(
            run_id=run_id,
            owner_id=owner_id,
            target=RunStatus.CANCELLED,
            result={"reason": reason},
            error=None,
            approval_id=None,
            can_continue=False,
            terminal_reason=reason,
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )

    async def resolve_approval_resume(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: ApprovalResumeStoreResult,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验审批恢复 owner 后原子完成入队或终态迁移。"""

        now = self._now()
        next_guardrail_summary = _next_optional(
            result.guardrail_summary,
            guardrail_summary,
        )
        next_workflow_run_state = _next_optional(
            result.workflow_run_state,
            workflow_run_state,
        )
        next_collaboration_summary = _next_optional(
            result.collaboration_summary,
            collaboration_summary,
        )

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status is not RunStatus.AWAITING_APPROVAL:
                raise RunContinuationUnavailableError(
                    run_id,
                    f"当前状态为 {snapshot.status.value}，不是 awaiting_approval",
                )
            if (
                snapshot.lease is None
                or snapshot.lease.owner_id != owner_id
                or not owner_id.startswith("approval-resume-")
            ):
                raise RunLeaseConflictError(run_id, owner_id)
            if result.status == "queued":
                status = RunStatus.QUEUED
                return replace(
                    snapshot,
                    status=status,
                    result=result.result,
                    error=None,
                    approval_id=None,
                    can_continue=False,
                    terminal_reason=None,
                    lease=None,
                    guardrail_summary=_next_optional(
                        snapshot.guardrail_summary,
                        next_guardrail_summary,
                    ),
                    workflow_run_state=_next_optional(
                        snapshot.workflow_run_state,
                        next_workflow_run_state,
                    ),
                    collaboration_summary=_next_collaboration_summary(
                        snapshot.collaboration_summary,
                        next_collaboration_summary,
                    ),
                    updated_at=now,
                    version=snapshot.version + 1,
                )
            if result.status == "awaiting_approval":
                return replace(
                    snapshot,
                    status=RunStatus.AWAITING_APPROVAL,
                    result=result.result,
                    error=None,
                    approval_id=result.approval_id,
                    can_continue=True,
                    terminal_reason=None,
                    lease=None,
                    guardrail_summary=_next_optional(
                        snapshot.guardrail_summary,
                        next_guardrail_summary,
                    ),
                    workflow_run_state=_next_optional(
                        snapshot.workflow_run_state,
                        next_workflow_run_state,
                    ),
                    collaboration_summary=_next_collaboration_summary(
                        snapshot.collaboration_summary,
                        next_collaboration_summary,
                    ),
                    updated_at=now,
                    version=snapshot.version + 1,
                )
            if result.status == "succeeded":
                status = RunStatus.SUCCEEDED
                return replace(
                    snapshot,
                    status=status,
                    result=result.result
                    or {"terminal_reason": result.terminal_reason or "completed"},
                    error=None,
                    approval_id=None,
                    can_continue=False,
                    terminal_reason=result.terminal_reason or "completed",
                    lease=None,
                    guardrail_summary=_next_optional(
                        snapshot.guardrail_summary,
                        next_guardrail_summary,
                    ),
                    workflow_run_state=_next_optional(
                        snapshot.workflow_run_state,
                        next_workflow_run_state,
                    ),
                    collaboration_summary=_next_collaboration_summary(
                        snapshot.collaboration_summary,
                        next_collaboration_summary,
                    ),
                    updated_at=now,
                    version=snapshot.version + 1,
                )
            if result.status == "failed":
                status = RunStatus.FAILED
                return replace(
                    snapshot,
                    status=status,
                    result=None,
                    error=result.error or {"message": "审批恢复失败"},
                    approval_id=None,
                    can_continue=False,
                    terminal_reason=result.terminal_reason or "failed",
                    lease=None,
                    guardrail_summary=_next_optional(
                        snapshot.guardrail_summary,
                        next_guardrail_summary,
                    ),
                    workflow_run_state=_next_optional(
                        snapshot.workflow_run_state,
                        next_workflow_run_state,
                    ),
                    collaboration_summary=_next_collaboration_summary(
                        snapshot.collaboration_summary,
                        next_collaboration_summary,
                    ),
                    updated_at=now,
                    version=snapshot.version + 1,
                )
            status = RunStatus.CANCELLED
            return replace(
                snapshot,
                status=status,
                result=result.result or {"reason": result.terminal_reason or "cancelled"},
                error=None,
                approval_id=None,
                can_continue=False,
                terminal_reason=result.terminal_reason or "cancelled",
                lease=None,
                guardrail_summary=_next_optional(
                    snapshot.guardrail_summary,
                    next_guardrail_summary,
                ),
                workflow_run_state=_next_optional(
                    snapshot.workflow_run_state,
                    next_workflow_run_state,
                ),
                collaboration_summary=_next_collaboration_summary(
                    snapshot.collaboration_summary,
                    next_collaboration_summary,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def enqueue_continue(self, *, run_id: str, model: str | None = None) -> RunSnapshot:
        """将 paused Run 重新入队，保留原 payload 并可覆盖 model。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status is not RunStatus.PAUSED:
                raise RunContinuationUnavailableError(
                    run_id,
                    f"当前状态为 {snapshot.status.value}，不是 paused",
                )
            if not snapshot.can_continue:
                raise RunContinuationUnavailableError(run_id, "can_continue=false")
            self._state_machine.assert_transition(snapshot.status, RunStatus.QUEUED)
            return replace(
                snapshot,
                status=RunStatus.QUEUED,
                payload=replace(snapshot.payload, model=model or snapshot.payload.model),
                approval_id=None,
                can_continue=False,
                lease=None,
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        """扫描过期 running/cancel_requested lease 并标记为 lost。"""

        lost: list[RunSnapshot] = []
        for path in sorted(self._snapshots_root.glob("*/*.json")):
            run_id = path.stem
            lock = self._lock_factory(self._run_lock_path(run_id))
            with lock.acquire(LockMode.EXCLUSIVE):
                snapshot = self._read_snapshot(path)
                if snapshot is None:
                    continue
                if snapshot.status not in {
                    RunStatus.RUNNING,
                    RunStatus.CANCEL_REQUESTED,
                }:
                    continue
                if snapshot.lease is None or snapshot.lease.lease_until >= now:
                    continue
                self._state_machine.assert_transition(snapshot.status, RunStatus.LOST)
                updated = replace(
                    snapshot,
                    status=RunStatus.LOST,
                    lease=None,
                    can_continue=False,
                    terminal_reason="lease_expired",
                    updated_at=now,
                    version=snapshot.version + 1,
                )
                self._write_snapshot(updated)
                lost.append(updated)
        return lost

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        """列出过期 running/cancel_requested lease，供恢复服务逐个判定。"""

        expired: list[RunSnapshot] = []
        for path in sorted(self._snapshots_root.glob("*/*.json")):
            run_id = path.stem
            lock = self._lock_factory(self._run_lock_path(run_id))
            with lock.acquire(LockMode.SHARED):
                snapshot = self._read_snapshot(path)
            if snapshot is None:
                continue
            if snapshot.status not in {
                RunStatus.RUNNING,
                RunStatus.CANCEL_REQUESTED,
            }:
                continue
            if snapshot.lease is None or snapshot.lease.lease_until >= now:
                continue
            expired.append(snapshot)
        return sorted(expired, key=lambda snapshot: (snapshot.created_at, snapshot.run_id))

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
        """将仍持有过期 lease 的 Run 重新入队并记录恢复元数据。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status not in {
                RunStatus.RUNNING,
                RunStatus.CANCEL_REQUESTED,
            }:
                raise RunLeaseConflictError(run_id, "recovery")
            if snapshot.lease is None or snapshot.lease.lease_until >= now:
                raise RunLeaseConflictError(run_id, "recovery")
            return replace(
                snapshot,
                status=RunStatus.QUEUED,
                lease=None,
                can_continue=False,
                terminal_reason=None,
                latest_checkpoint_id=latest_checkpoint_id,
                recoverable=True,
                recovery_attempt_count=recovery_attempt_count,
                last_recovery_error=None,
                guardrail_summary=_next_optional(
                    snapshot.guardrail_summary,
                    guardrail_summary,
                ),
                workflow_run_state=_next_optional(
                    snapshot.workflow_run_state,
                    workflow_run_state,
                ),
                collaboration_summary=_next_collaboration_summary(
                    snapshot.collaboration_summary,
                    collaboration_summary,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将单个过期 leased Run 标记为 lost 并保存恢复失败摘要。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            if snapshot.status not in {
                RunStatus.RUNNING,
                RunStatus.CANCEL_REQUESTED,
            }:
                raise RunLeaseConflictError(run_id, "lost")
            if snapshot.lease is None or snapshot.lease.lease_until >= now:
                raise RunLeaseConflictError(run_id, "lost")
            self._state_machine.assert_transition(snapshot.status, RunStatus.LOST)
            return replace(
                snapshot,
                status=RunStatus.LOST,
                lease=None,
                can_continue=False,
                terminal_reason=reason,
                recoverable=False,
                last_recovery_error=recovery_error,
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    async def record_runtime_observation(
        self,
        *,
        run_id: str,
        owner_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> tuple[RunSnapshot, RunEvent]:
        """在同一 Run 锁区内原子追加事件并更新摘要字段。"""

        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            now = self._now()
            snapshot = self._read_snapshot_by_id(run_id)
            if snapshot is None:
                raise RunNotFoundError(run_id)
            self._assert_fresh_owner_lease(snapshot, owner_id, now)
            events = self._read_events_unlocked(run_id)
            latest_cursor = events[-1].cursor if events else 0
            if snapshot.latest_event_cursor is not None:
                latest_cursor = max(latest_cursor, snapshot.latest_event_cursor)
            event = RunEvent(
                run_id=run_id,
                cursor=latest_cursor + 1,
                event_type=event_type,
                payload=_json_safe(payload),
                created_at=now,
            )
            events.append(event)
            updated_snapshot = replace(
                snapshot,
                latest_event_cursor=event.cursor,
                guardrail_summary=_next_observation_guardrail_summary(
                    snapshot.guardrail_summary,
                    guardrail_summary,
                    event_cursor=event.cursor,
                ),
                workflow_run_state=_next_optional(
                    snapshot.workflow_run_state,
                    workflow_run_state,
                ),
                collaboration_summary=_next_collaboration_summary(
                    snapshot.collaboration_summary,
                    collaboration_summary,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )
            self._write_events_unlocked(run_id, events)
            self._write_snapshot(updated_snapshot)
            return updated_snapshot, event

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        """追加事件并分配同一 Run 内单调递增 cursor。"""

        now = self._now()
        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            events = self._read_events_unlocked(run_id)
            snapshot = self._read_snapshot_by_id(run_id)
            latest_cursor = events[-1].cursor if events else 0
            if snapshot is not None and snapshot.latest_event_cursor is not None:
                latest_cursor = max(latest_cursor, snapshot.latest_event_cursor)
            event = RunEvent(
                run_id=run_id,
                cursor=latest_cursor + 1,
                event_type=event_type,
                payload=_json_safe(payload),
                created_at=now,
            )
            events.append(event)
            self._write_events_unlocked(run_id, events)
            if snapshot is not None:
                self._write_snapshot(
                    replace(
                        snapshot,
                        latest_event_cursor=event.cursor,
                        updated_at=now,
                        version=snapshot.version + 1,
                    )
                )
            return event

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        """列出 after_cursor 之后的当前保留事件。"""

        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.SHARED):
            events = self._read_events_unlocked(run_id)
        filtered = [
            event for event in events if after_cursor is None or event.cursor > after_cursor
        ]
        return filtered[:limit]

    async def wait_events(
        self, run_id: str, after_cursor: int | None, timeout_seconds: float
    ) -> list[RunEvent]:
        """通过短轮询等待 after_cursor 之后的新事件。"""

        deadline = time.monotonic() + timeout_seconds
        while True:
            events = await self.list_events(run_id, after_cursor, 100)
            if events or time.monotonic() >= deadline:
                return events
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        """按 max_event_count 和 ttl_seconds 裁剪事件历史。"""

        now = self._now()
        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            events = self._read_events_unlocked(run_id)
            if policy.ttl_seconds > 0:
                floor = now - timedelta(seconds=policy.ttl_seconds)
                events = [event for event in events if event.created_at >= floor]
            if policy.max_event_count > 0 and len(events) > policy.max_event_count:
                events = events[-policy.max_event_count :]
            self._write_events_unlocked(run_id, events)

    async def first_cursor(self, run_id: str) -> int | None:
        """返回当前保留窗口内最早事件 cursor。"""

        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.SHARED):
            events = self._read_events_unlocked(run_id)
        if not events:
            return None
        return events[0].cursor

    def _create_new_snapshot(self, request: RunCreateRequest) -> RunSnapshot:
        """创建新 Run 快照并写入本地文件。"""

        now = self._now()
        run_id = f"run_{uuid.uuid4().hex}"
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
            created_at=now,
            updated_at=now,
            version=1,
            task_classification=request.task_classification,
            guardrail_summary=request.guardrail_summary,
            workflow_name=request.workflow_name,
            workflow_run_state=request.workflow_run_state,
            collaboration_summary=canonicalize_collaboration_summary(request.collaboration_summary),
        )
        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            self._write_snapshot(snapshot)
        return snapshot

    def _worker_transition(
        self,
        *,
        run_id: str,
        owner_id: str,
        target: RunStatus,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        approval_id: str | None,
        can_continue: bool,
        terminal_reason: str | None,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """执行需要 worker lease owner 校验的状态迁移。"""

        now = self._now()

        def mutate(snapshot: RunSnapshot) -> RunSnapshot:
            self._assert_owner(snapshot, owner_id)
            self._state_machine.assert_transition(snapshot.status, target)
            return replace(
                snapshot,
                status=target,
                result=result,
                error=error,
                approval_id=approval_id,
                can_continue=can_continue,
                terminal_reason=terminal_reason,
                lease=None if self._state_machine.is_terminal(target) else snapshot.lease,
                guardrail_summary=_next_optional(
                    snapshot.guardrail_summary,
                    guardrail_summary,
                ),
                workflow_run_state=_next_optional(
                    snapshot.workflow_run_state,
                    workflow_run_state,
                ),
                collaboration_summary=_next_collaboration_summary(
                    snapshot.collaboration_summary,
                    collaboration_summary,
                ),
                updated_at=now,
                version=snapshot.version + 1,
            )

        return self._mutate_snapshot(run_id, mutate)

    def _mutate_snapshot(
        self,
        run_id: str,
        mutator: Callable[[RunSnapshot], RunSnapshot],
    ) -> RunSnapshot:
        """在 run EXCLUSIVE 锁内读取、修改并原子写回快照。"""

        path = self._snapshot_path(run_id)
        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            snapshot = self._read_snapshot(path)
            if snapshot is None:
                raise RunNotFoundError(run_id)
            updated = mutator(snapshot)
            if updated != snapshot:
                self._write_snapshot(updated)
            return updated

    def _assert_owner(self, snapshot: RunSnapshot, owner_id: str) -> None:
        """校验快照当前 lease owner 与调用方一致。"""

        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            raise RunLeaseConflictError(snapshot.run_id, owner_id)

    def _assert_fresh_owner_lease(
        self,
        snapshot: RunSnapshot,
        owner_id: str,
        now: datetime,
    ) -> None:
        """校验调用方持有当前且未过期的租约。"""

        self._assert_owner(snapshot, owner_id)
        assert snapshot.lease is not None
        if snapshot.lease.lease_until <= now:
            raise RunLeaseConflictError(snapshot.run_id, owner_id)

    def _iter_snapshots(self) -> list[RunSnapshot | None]:
        """读取所有快照文件，供统计和扫描使用。"""

        if not self._snapshots_root.exists():
            return []
        snapshots: list[RunSnapshot | None] = []
        for path in sorted(self._snapshots_root.glob("*/*.json")):
            run_id = path.stem
            lock = self._lock_factory(self._run_lock_path(run_id))
            with lock.acquire(LockMode.SHARED):
                snapshots.append(self._read_snapshot(path))
        return snapshots

    def _snapshot_path(self, run_id: str) -> Path:
        """根据 run_id 解析快照文件路径。"""

        self._policy.check_dirname(f"{run_id}.json")
        bucket, _ = self._policy.hash_session_id(run_id)
        path = self._snapshots_root / bucket / f"{run_id}.json"
        self._policy.check_absolute_path_length(path)
        return self._policy.ensure_within_root(self._root, path)

    def snapshot_path(self, run_id: str) -> Path:
        """返回 Run 快照路径，供诊断与迁移工具使用。"""
        return self._snapshot_path(run_id)

    def _events_path(self, run_id: str) -> Path:
        """根据 run_id 解析事件 JSONL 文件路径。"""

        self._policy.check_dirname(f"{run_id}.jsonl")
        bucket, _ = self._policy.hash_session_id(run_id)
        path = self._events_root / bucket / f"{run_id}.jsonl"
        self._policy.check_absolute_path_length(path)
        return self._policy.ensure_within_root(self._root, path)

    def _run_lock_path(self, run_id: str) -> Path:
        """返回同一 Run 状态、lease 和事件 cursor 共用的锁文件路径。"""

        return self._snapshot_path(run_id).with_suffix(".json.lock")

    def run_lock_path(self, run_id: str) -> Path:
        """返回 Run 状态与事件写入共用的锁文件路径。"""
        return self._run_lock_path(run_id)

    def _client_index_path(self, client_request_id: str) -> Path:
        """根据 client_request_id 解析幂等索引文件路径。"""

        bucket, stem = self._policy.hash_session_id(client_request_id)
        path = self._client_index_root / bucket / f"{stem}.json"
        self._policy.check_absolute_path_length(path)
        return self._policy.ensure_within_root(self._root, path)

    def _index_hash(self, client_request_id: str) -> str:
        """返回 client_request_id 的完整哈希字符串。"""

        bucket, stem = self._policy.hash_session_id(client_request_id)
        return f"{bucket}{stem}"

    def _read_client_index(self, path: Path) -> str | None:
        """读取幂等索引中的 run_id。"""

        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        run_id = data.get("run_id")
        return run_id if isinstance(run_id, str) else None

    def _read_snapshot_by_id(self, run_id: str) -> RunSnapshot | None:
        """不加锁读取指定 run_id 快照，调用方负责锁边界。"""

        return self._read_snapshot(self._snapshot_path(run_id))

    def _read_snapshot(self, path: Path) -> RunSnapshot | None:
        """从 JSON 文件反序列化 RunSnapshot。"""

        if not path.exists():
            return None
        return _snapshot_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _write_snapshot(self, snapshot: RunSnapshot) -> None:
        """把 RunSnapshot 序列化为 JSON 并原子写入。"""

        self._write_json_atomic(self._snapshot_path(snapshot.run_id), snapshot)

    def _read_events_unlocked(self, run_id: str) -> list[RunEvent]:
        """不加锁读取事件文件，调用方负责持有 run 锁。"""

        path = self._events_path(run_id)
        if not path.exists():
            return []
        events: list[RunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(_event_from_dict(json.loads(line)))
        return events

    def _write_events_unlocked(self, run_id: str, events: list[RunEvent]) -> None:
        """不加锁写入事件 JSONL，调用方负责持有 run 锁。"""

        payload = "".join(
            json.dumps(_json_safe(event), ensure_ascii=False, sort_keys=True) + "\n"
            for event in events
        ).encode("utf-8")
        self._writer.write_bytes_atomic(self._events_path(run_id), payload)

    def _write_json_atomic(self, path: Path, value: Any) -> None:
        """把任意 JSON-safe 值编码后原子写入指定路径。"""

        payload = json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._writer.write_bytes_atomic(path, payload)

    @staticmethod
    def _now() -> datetime:
        """返回带 UTC 时区的当前时间。"""

        return datetime.now(UTC)


def _json_safe(value: Any) -> Any:
    """把 dataclass、枚举、datetime 递归转换为 JSON-safe 值。"""

    if is_dataclass(value):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [_json_safe(item) for item in sequence]
    return value


def _parse_datetime(value: str) -> datetime:
    """从 ISO-8601 字符串恢复 datetime。"""

    return datetime.fromisoformat(value)


def _payload_from_dict(data: dict[str, Any]) -> RunPayload:
    """从 JSON dict 恢复 RunPayload。"""

    return RunPayload(
        kind=RunKind(data["kind"]),
        session_id=data.get("session_id"),
        chat=data.get("chat"),
        task=data.get("task"),
        model=data.get("model"),
    )


def _lease_from_dict(data: dict[str, Any] | None) -> RunLease | None:
    """从 JSON dict 恢复 RunLease。"""

    if data is None:
        return None
    return RunLease(
        owner_id=data["owner_id"],
        lease_until=_parse_datetime(data["lease_until"]),
        heartbeat_at=_parse_datetime(data["heartbeat_at"]),
    )


def _snapshot_from_dict(data: dict[str, Any]) -> RunSnapshot:
    """从 JSON dict 恢复 RunSnapshot。"""

    return RunSnapshot(
        run_id=data["run_id"],
        kind=RunKind(data["kind"]),
        status=RunStatus(data["status"]),
        payload=_payload_from_dict(data["payload"]),
        client_request_id=data.get("client_request_id"),
        payload_hash=data.get("payload_hash"),
        result=data.get("result"),
        error=data.get("error"),
        approval_id=data.get("approval_id"),
        segment_metadata=data.get("segment_metadata"),
        latest_event_cursor=data.get("latest_event_cursor"),
        can_continue=bool(data["can_continue"]),
        terminal_reason=data.get("terminal_reason"),
        lease=_lease_from_dict(data.get("lease")),
        created_at=_parse_datetime(data["created_at"]),
        updated_at=_parse_datetime(data["updated_at"]),
        version=int(data["version"]),
        latest_checkpoint_id=data.get("latest_checkpoint_id"),
        recoverable=bool(data.get("recoverable", False)),
        recovery_attempt_count=int(data.get("recovery_attempt_count", 0)),
        last_recovery_error=data.get("last_recovery_error"),
        task_classification=data.get("task_classification"),
        guardrail_summary=data.get("guardrail_summary"),
        workflow_name=data.get("workflow_name"),
        workflow_run_state=data.get("workflow_run_state"),
        collaboration_summary=canonicalize_collaboration_summary(data.get("collaboration_summary")),
    )


def _next_optional(current: Any, incoming: Any) -> Any:
    """返回可选覆盖值；None 表示保留当前值。"""

    return current if incoming is None else incoming


def _next_observation_guardrail_summary(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
    *,
    event_cursor: int,
) -> dict[str, Any] | None:
    """返回与本次观察事件游标同步的 guardrail 摘要副本。"""

    if incoming is None:
        return current
    next_summary = _json_safe(incoming)
    if not isinstance(next_summary, dict):
        return current
    next_summary["last_event_cursor"] = event_cursor
    return cast(dict[str, Any], next_summary)


def _next_collaboration_summary(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """返回规范化后的协作摘要；None 表示保留当前值。"""

    if incoming is None:
        return current
    return canonicalize_collaboration_summary(incoming)


def _event_from_dict(data: dict[str, Any]) -> RunEvent:
    """从 JSON dict 恢复 RunEvent。"""

    return RunEvent(
        run_id=data["run_id"],
        cursor=int(data["cursor"]),
        event_type=RunEventType(data["event_type"]),
        payload=data.get("payload") or {},
        created_at=_parse_datetime(data["created_at"]),
    )
