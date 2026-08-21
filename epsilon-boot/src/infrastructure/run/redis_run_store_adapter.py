"""基于 Redis 的 Run Store 与 Event Store 适配器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable, Collection
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, TypeVar, cast

import redis.asyncio as aioredis

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

T = TypeVar("T")
_RedisValue = str | bytes


class _RunRedisCommands(Protocol):
    """Run store 直接使用的 Redis 异步命令最小协议。"""

    async def get(self, name: str | bytes) -> _RedisValue | None: ...

    def scan_iter(self, *, match: str) -> AsyncIterator[_RedisValue]: ...

    def sscan_iter(self, name: str) -> AsyncIterator[_RedisValue]: ...

    async def lrange(self, name: str, start: int, end: int) -> list[_RedisValue]: ...

    async def expire(self, name: str, seconds: int) -> bool: ...

    async def ltrim(self, name: str, start: int, end: int) -> bool: ...

    async def lindex(self, name: str, index: int) -> _RedisValue | None: ...


class _WatchedListPipeline(Protocol):
    """Redis WATCH 阶段所需的列表读取与事务排队协议。"""

    async def lindex(self, name: str, index: int) -> _RedisValue | None: ...

    async def lrange(self, name: str, start: int, end: int) -> list[_RedisValue]: ...

    def lpop(self, name: str) -> object: ...


class RedisRunStoreAdapter(RunStorePort, RunEventStorePort, RunObservationStorePort):
    """Redis 实现的 Run 快照、队列、租约和事件存储。"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        key_prefix: str = "",
        conflict_retry_max: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._commands = cast(_RunRedisCommands, redis_client)
        self._key_prefix = key_prefix.rstrip(":")
        self._conflict_retry_max = conflict_retry_max if conflict_retry_max is not None else 5
        self._state_machine = RunStateMachine()

    @property
    def redis_client(self) -> aioredis.Redis:
        """返回适配器使用的 Redis 客户端，供生命周期与迁移操作使用。"""
        return self._redis

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """创建 queued Run，或按 client_request_id 返回既有 Run。"""

        effective_request = replace(
            request,
            payload_hash=request.effective_payload_hash(),
        )
        if effective_request.client_request_id is None:
            snapshot = self._new_snapshot(effective_request)
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(self._snapshot_key(snapshot.run_id), self._encode(snapshot))
                pipe.rpush(self._queue_key(), snapshot.run_id)
                await pipe.execute()
            return snapshot

        index_key = self._client_index_key(effective_request.client_request_id)
        for _ in range(self._conflict_retry_max + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(index_key)
                    raw_index = await pipe.get(index_key)
                    if raw_index is not None:
                        index = json.loads(raw_index)
                        run_id = index.get("run_id")
                        existing = await self.get_run(run_id) if isinstance(run_id, str) else None
                        if existing is not None:
                            if existing.payload_hash != effective_request.payload_hash:
                                raise RunIdempotencyConflictError(
                                    effective_request.client_request_id
                                )
                            return existing

                    snapshot = self._new_snapshot(effective_request)
                    pipe.multi()
                    pipe.set(self._snapshot_key(snapshot.run_id), self._encode(snapshot))
                    pipe.set(
                        index_key,
                        self._encode(
                            {
                                "client_request_id_hash": self._hash_client_request_id(
                                    effective_request.client_request_id
                                ),
                                "run_id": snapshot.run_id,
                                "payload_hash": snapshot.payload_hash,
                            }
                        ),
                    )
                    pipe.rpush(self._queue_key(), snapshot.run_id)
                    await pipe.execute()
                    return snapshot
            except aioredis.WatchError:
                continue
        raise RunIdempotencyConflictError(effective_request.client_request_id)

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        raw = await self._redis.get(self._snapshot_key(run_id))
        return _snapshot_from_dict(json.loads(raw)) if raw is not None else None

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        raw = await self._redis.get(self._client_index_key(client_request_id))
        if raw is None:
            return None
        index = json.loads(raw)
        run_id = index.get("run_id")
        return await self.get_run(run_id) if isinstance(run_id, str) else None

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        count = 0
        async for key in self._commands.scan_iter(match=self._snapshot_match()):
            raw = await self._commands.get(key)
            if raw is None:
                continue
            snapshot = _snapshot_from_dict(json.loads(raw))
            if snapshot.status in statuses:
                count += 1
        return count

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        """事务化领取队首 queued Run 并写入 lease/running set。"""

        queue_key = self._queue_key()
        for _ in range(self._conflict_retry_max + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(queue_key)
                    list_pipe = cast(_WatchedListPipeline, pipe)
                    run_id = await list_pipe.lindex(queue_key, 0)
                    if run_id is None:
                        return None
                    run_id = self._decode_text(run_id)
                    snapshot_key = self._snapshot_key(run_id)
                    await pipe.watch(snapshot_key)
                    raw = await pipe.get(snapshot_key)
                    if raw is None:
                        pipe.multi()
                        list_pipe.lpop(queue_key)
                        await pipe.execute()
                        continue
                    snapshot = _snapshot_from_dict(json.loads(raw))
                    if not self._state_machine.can_claim(snapshot.status):
                        pipe.multi()
                        list_pipe.lpop(queue_key)
                        await pipe.execute()
                        continue
                    now = self._now()
                    self._state_machine.assert_transition(snapshot.status, RunStatus.RUNNING)
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
                    pipe.multi()
                    list_pipe.lpop(queue_key)
                    pipe.set(snapshot_key, self._encode(updated))
                    pipe.sadd(self._running_key(), run_id)
                    await pipe.execute()
                    return updated
            except aioredis.WatchError:
                continue
        return None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
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

        return await self._mutate_snapshot(run_id, mutate)

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

        return await self._mutate_snapshot(run_id, mutate)

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

        return await self._mutate_snapshot(run_id, mutate)

    async def request_cancel(self, run_id: str) -> RunSnapshot:
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
            return replace(
                snapshot,
                status=target,
                can_continue=False,
                terminal_reason="cancelled" if target is RunStatus.CANCELLED else None,
                lease=None if target is RunStatus.CANCELLED else snapshot.lease,
                updated_at=now,
                version=snapshot.version + 1,
            )

        return await self._mutate_snapshot(
            run_id,
            mutate,
            after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
        )

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
        """将 worker 持有的 Run 标记为成功，并可同步覆盖运行时摘要字段。"""

        return await self._worker_transition(
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
        """将 worker 持有的 Run 标记为失败，并保留或覆盖运行时摘要字段。"""

        return await self._worker_transition(
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
        """将 worker 持有的 Run 标记为暂停，并可同步最新运行时摘要。"""

        return await self._worker_transition(
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
        """将 worker 持有的 Run 转入等待审批，并持久化审批相关摘要。"""

        return await self._worker_transition(
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
        """将 worker 持有的 Run 标记为取消，并可同步终态摘要字段。"""

        return await self._worker_transition(
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
                return replace(
                    snapshot,
                    status=RunStatus.QUEUED,
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
                return replace(
                    snapshot,
                    status=RunStatus.SUCCEEDED,
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
                return replace(
                    snapshot,
                    status=RunStatus.FAILED,
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
            return replace(
                snapshot,
                status=RunStatus.CANCELLED,
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

        return await self._mutate_snapshot(
            run_id,
            mutate,
            after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
        )

    async def enqueue_continue(self, *, run_id: str, model: str | None = None) -> RunSnapshot:
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

        return await self._mutate_snapshot(
            run_id,
            mutate,
            after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
        )

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        lost: list[RunSnapshot] = []
        run_ids = [
            self._decode_text(item)
            async for item in self._commands.sscan_iter(self._running_key())
        ]
        for run_id in sorted(run_ids):

            def mutate(snapshot: RunSnapshot) -> RunSnapshot:
                if snapshot.status not in {
                    RunStatus.RUNNING,
                    RunStatus.CANCEL_REQUESTED,
                }:
                    return snapshot
                if snapshot.lease is None or snapshot.lease.lease_until >= now:
                    return snapshot
                self._state_machine.assert_transition(snapshot.status, RunStatus.LOST)
                return replace(
                    snapshot,
                    status=RunStatus.LOST,
                    lease=None,
                    can_continue=False,
                    terminal_reason="lease_expired",
                    updated_at=now,
                    version=snapshot.version + 1,
                )

            updated = await self._mutate_snapshot(
                run_id,
                mutate,
                after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
            )
            if updated.status is RunStatus.LOST and updated.terminal_reason == "lease_expired":
                lost.append(updated)
        return lost

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        expired: list[RunSnapshot] = []
        run_ids = [
            self._decode_text(item)
            async for item in self._commands.sscan_iter(self._running_key())
        ]
        for run_id in sorted(run_ids):
            snapshot = await self.get_run(run_id)
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
        """把过期租约 Run 重新入队恢复，并保守持久化恢复摘要字段。"""

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

        return await self._mutate_snapshot(
            run_id,
            mutate,
            after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
        )

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
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

        return await self._mutate_snapshot(
            run_id,
            mutate,
            after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
        )

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
        """在同一 Redis 事务内原子追加事件并更新摘要字段。"""

        snapshot_key = self._snapshot_key(run_id)
        events_key = self._events_key(run_id)
        for _ in range(self._conflict_retry_max + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(snapshot_key, events_key)
                    raw_snapshot = await pipe.get(snapshot_key)
                    if raw_snapshot is None:
                        raise RunNotFoundError(run_id)
                    snapshot = _snapshot_from_dict(json.loads(raw_snapshot))
                    now = self._now()
                    self._assert_fresh_owner_lease(snapshot, owner_id, now)
                    list_pipe = cast(_WatchedListPipeline, pipe)
                    raw_events = await list_pipe.lrange(events_key, -1, -1)
                    latest_cursor = 0
                    if raw_events:
                        latest_cursor = _event_from_dict(json.loads(raw_events[0])).cursor
                    if snapshot.latest_event_cursor is not None:
                        latest_cursor = max(latest_cursor, snapshot.latest_event_cursor)
                    event = RunEvent(
                        run_id=run_id,
                        cursor=latest_cursor + 1,
                        event_type=event_type,
                        payload=_json_safe(payload),
                        created_at=now,
                    )
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
                    pipe.multi()
                    pipe.rpush(events_key, self._encode(event))
                    pipe.set(snapshot_key, self._encode(updated_snapshot))
                    await pipe.execute()
                    return updated_snapshot, event
            except aioredis.WatchError:
                continue
        raise RunLeaseConflictError(run_id, owner_id)

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        snapshot_key = self._snapshot_key(run_id)
        events_key = self._events_key(run_id)
        for _ in range(self._conflict_retry_max + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(snapshot_key, events_key)
                    snapshot = None
                    raw_snapshot = await pipe.get(snapshot_key)
                    if raw_snapshot is not None:
                        snapshot = _snapshot_from_dict(json.loads(raw_snapshot))
                    list_pipe = cast(_WatchedListPipeline, pipe)
                    raw_events = await list_pipe.lrange(events_key, -1, -1)
                    latest_cursor = 0
                    if raw_events:
                        latest_cursor = _event_from_dict(json.loads(raw_events[0])).cursor
                    if snapshot is not None and snapshot.latest_event_cursor is not None:
                        latest_cursor = max(latest_cursor, snapshot.latest_event_cursor)
                    now = self._now()
                    event = RunEvent(
                        run_id=run_id,
                        cursor=latest_cursor + 1,
                        event_type=event_type,
                        payload=_json_safe(payload),
                        created_at=now,
                    )
                    pipe.multi()
                    pipe.rpush(events_key, self._encode(event))
                    if snapshot is not None:
                        pipe.set(
                            snapshot_key,
                            self._encode(
                                replace(
                                    snapshot,
                                    latest_event_cursor=event.cursor,
                                    updated_at=now,
                                    version=snapshot.version + 1,
                                )
                            ),
                        )
                    await pipe.execute()
                    return event
            except aioredis.WatchError:
                continue
        raise RunLeaseConflictError(run_id, "event_cursor")

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        raw_events = await self._commands.lrange(self._events_key(run_id), 0, -1)
        events = [_event_from_dict(json.loads(raw)) for raw in raw_events]
        filtered = [
            event for event in events if after_cursor is None or event.cursor > after_cursor
        ]
        return filtered[:limit]

    async def wait_events(
        self, run_id: str, after_cursor: int | None, timeout_seconds: float
    ) -> list[RunEvent]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            events = await self.list_events(run_id, after_cursor, 100)
            if events or time.monotonic() >= deadline:
                return events
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        events_key = self._events_key(run_id)
        if policy.ttl_seconds > 0:
            await self._commands.expire(events_key, policy.ttl_seconds)
        if policy.max_event_count > 0:
            await self._commands.ltrim(events_key, -policy.max_event_count, -1)

    async def first_cursor(self, run_id: str) -> int | None:
        raw = await self._commands.lindex(self._events_key(run_id), 0)
        if raw is None:
            return None
        return _event_from_dict(json.loads(raw)).cursor

    async def _worker_transition(
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

        return await self._mutate_snapshot(
            run_id,
            mutate,
            after=lambda pipe, snapshot: self._sync_status_indexes(pipe, snapshot),
        )

    async def _mutate_snapshot(
        self,
        run_id: str,
        mutator: Callable[[RunSnapshot], RunSnapshot],
        *,
        after: Callable[[Any, RunSnapshot], None] | None = None,
    ) -> RunSnapshot:
        key = self._snapshot_key(run_id)
        for _ in range(self._conflict_retry_max + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        raise RunNotFoundError(run_id)
                    snapshot = _snapshot_from_dict(json.loads(raw))
                    updated = mutator(snapshot)
                    if updated == snapshot:
                        return updated
                    pipe.multi()
                    pipe.set(key, self._encode(updated))
                    if after is not None:
                        after(pipe, updated)
                    await pipe.execute()
                    return updated
            except aioredis.WatchError:
                continue
        raise RunLeaseConflictError(run_id, "snapshot")

    def _sync_status_indexes(self, pipe: Any, snapshot: RunSnapshot) -> None:
        run_id = snapshot.run_id
        if snapshot.status is RunStatus.QUEUED:
            pipe.lrem(self._queue_key(), 0, run_id)
            pipe.rpush(self._queue_key(), run_id)
            pipe.srem(self._running_key(), run_id)
            return
        pipe.lrem(self._queue_key(), 0, run_id)
        if snapshot.status in {RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED}:
            pipe.sadd(self._running_key(), run_id)
        else:
            pipe.srem(self._running_key(), run_id)

    def _assert_owner(self, snapshot: RunSnapshot, owner_id: str) -> None:
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

    def _new_snapshot(self, request: RunCreateRequest) -> RunSnapshot:
        now = self._now()
        run_id = f"run_{uuid.uuid4().hex}"
        return RunSnapshot(
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

    def _snapshot_key(self, run_id: str) -> str:
        return self._key(f"run:{run_id}:snapshot")

    def snapshot_key(self, run_id: str) -> str:
        """返回指定 Run 的快照键。"""
        return self._snapshot_key(run_id)

    def _events_key(self, run_id: str) -> str:
        return self._key(f"run:{run_id}:events")

    def _client_index_key(self, client_request_id: str) -> str:
        return self._key(
            f"run:index:client_request:{self._hash_client_request_id(client_request_id)}"
        )

    def _queue_key(self) -> str:
        return self._key("run:queue")

    def _running_key(self) -> str:
        return self._key("run:running")

    def _snapshot_match(self) -> str:
        return self._key("run:*:snapshot")

    def _key(self, name: str) -> str:
        return f"{self._key_prefix}:{name}" if self._key_prefix else name

    @staticmethod
    def _hash_client_request_id(client_request_id: str) -> str:
        return hashlib.sha256(client_request_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


def _json_safe(value: Any) -> Any:
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
        items = cast(list[object] | tuple[object, ...], value)
        return [_json_safe(item) for item in items]
    return value


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _payload_from_dict(data: dict[str, Any]) -> RunPayload:
    return RunPayload(
        kind=RunKind(data["kind"]),
        session_id=data.get("session_id"),
        chat=data.get("chat"),
        task=data.get("task"),
        model=data.get("model"),
    )


def _lease_from_dict(data: dict[str, Any] | None) -> RunLease | None:
    if data is None:
        return None
    return RunLease(
        owner_id=data["owner_id"],
        lease_until=_parse_datetime(data["lease_until"]),
        heartbeat_at=_parse_datetime(data["heartbeat_at"]),
    )


def _snapshot_from_dict(data: dict[str, Any]) -> RunSnapshot:
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
    return RunEvent(
        run_id=data["run_id"],
        cursor=int(data["cursor"]),
        event_type=RunEventType(data["event_type"]),
        payload=data.get("payload") or {},
        created_at=_parse_datetime(data["created_at"]),
    )
