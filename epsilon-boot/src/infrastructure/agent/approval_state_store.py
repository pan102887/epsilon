"""审批中断状态存储适配器模块。

提供 ApprovalInterrupt 的 JSON 序列化 helper，以及本地文件和 Redis 两种
审批状态存储实现。存储具备 TTL 与消费语义，用于 HITL 恢复执行。
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol, cast

import redis.asyncio as aioredis

from domain.agent.ports import ApprovalStateStorePort
from domain.agent.value_objects import (
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    PendingActionRequest,
)
from infrastructure.agent.approval_serialization import approval_actions_to_dicts
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import CrossPlatformFileLock, LockMode
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy


class _RedisScanner(Protocol):
    def scan_iter(self, *, match: str) -> AsyncIterator[bytes | str]: ...


def approval_interrupt_to_dict(interrupt: ApprovalInterrupt) -> dict[str, Any]:
    """将 ApprovalInterrupt 序列化为 JSON 友好的 dict。"""
    return {
        "session_id": interrupt.session_id,
        "approval_id": interrupt.approval_id,
        "actions": approval_actions_to_dicts(interrupt.actions),
        "context_snapshot": interrupt.context_snapshot,
        "round_num": interrupt.round_num,
        "model": interrupt.model,
        "usage_so_far": interrupt.usage_so_far,
        "created_at_epoch": interrupt.created_at_epoch,
        "expires_at_epoch": interrupt.expires_at_epoch,
        "metadata": interrupt.metadata,
    }


def approval_interrupt_from_dict(data: dict[str, Any]) -> ApprovalInterrupt:
    """从 dict 反序列化 ApprovalInterrupt。"""
    return ApprovalInterrupt(
        session_id=str(data["session_id"]),
        approval_id=str(data["approval_id"]),
        actions=tuple(
            PendingActionRequest(
                tool_call_id=str(action["tool_call_id"]),
                tool_name=str(action["tool_name"]),
                arguments=str(action["arguments"]),
                allowed_decisions=frozenset(action["allowed_decisions"]),
                reason=str(action.get("reason", "")),
            )
            for action in data["actions"]
        ),
        context_snapshot=dict(data["context_snapshot"]),
        round_num=int(data["round_num"]),
        model=str(data["model"]),
        usage_so_far=dict(data.get("usage_so_far", {})),
        created_at_epoch=float(data.get("created_at_epoch", 0.0)),
        expires_at_epoch=float(data.get("expires_at_epoch", 0.0)),
        metadata=dict(data.get("metadata", {})),
    )


def approval_interrupt_to_summary(
    interrupt: ApprovalInterrupt,
    *,
    now_epoch: float,
) -> ApprovalInterruptSummary:
    """将完整审批中断状态转换为用于展示的轻量摘要。"""
    return ApprovalInterruptSummary(
        session_id=interrupt.session_id,
        approval_id=interrupt.approval_id,
        action_count=len(interrupt.actions),
        created_at_epoch=interrupt.created_at_epoch,
        expires_at_epoch=interrupt.expires_at_epoch,
        expired=interrupt.is_expired(now_epoch),
        tool_names=tuple(action.tool_name for action in interrupt.actions),
    )


class LocalFileApprovalStateStore(ApprovalStateStorePort):
    """审批状态存储的本地文件实现。"""

    def __init__(
        self,
        root: Path,
        lock_factory: Callable[[Path], CrossPlatformFileLock],
        path_policy: CrossPlatformPathPolicy,
        atomic_writer: TempFileAtomicWriter,
        ttl_seconds: int,
    ) -> None:
        """初始化本地文件审批状态存储。"""
        self._approvals_root = root / "approvals"
        self._lock_factory = lock_factory
        self._policy = path_policy
        self._writer = atomic_writer
        self._ttl_seconds = ttl_seconds

    def _resolve_path(self, session_id: str, approval_id: str) -> Path:
        """解析审批状态文件路径。"""
        bucket, stem = self._policy.hash_session_id(session_id)
        self._policy.check_dirname(approval_id)
        path = self._approvals_root / bucket / stem / f"{approval_id}.json"
        self._policy.check_absolute_path_length(path)
        return path

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """保存审批中断状态。"""
        now = time.time()
        expires_at = interrupt.expires_at_epoch
        if expires_at <= 0 and self._ttl_seconds > 0:
            expires_at = now + self._ttl_seconds
            interrupt = ApprovalInterrupt(
                session_id=interrupt.session_id,
                approval_id=interrupt.approval_id,
                actions=interrupt.actions,
                context_snapshot=interrupt.context_snapshot,
                round_num=interrupt.round_num,
                model=interrupt.model,
                usage_so_far=interrupt.usage_so_far,
                created_at_epoch=interrupt.created_at_epoch or now,
                expires_at_epoch=expires_at,
                metadata=interrupt.metadata,
            )
        path = self._resolve_path(interrupt.session_id, interrupt.approval_id)
        payload = json.dumps(
            approval_interrupt_to_dict(interrupt),
            ensure_ascii=False,
        ).encode("utf-8")
        lock = self._lock_factory(path.with_suffix(".json.lock"))
        with lock.acquire(LockMode.EXCLUSIVE):
            self._writer.write_bytes_atomic(path, payload)

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """加载审批中断状态，不存在或过期时返回 None。"""
        path = self._resolve_path(session_id, approval_id)
        if not path.exists():
            return None
        lock = self._lock_factory(path.with_suffix(".json.lock"))
        with lock.acquire(LockMode.SHARED):
            if not path.exists():
                return None
            interrupt = approval_interrupt_from_dict(json.loads(path.read_bytes()))
        if interrupt.is_expired(time.time()):
            return None
        return interrupt

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """原子消费审批中断状态。"""
        path = self._resolve_path(session_id, approval_id)
        lock = self._lock_factory(path.with_suffix(".json.lock"))
        with lock.acquire(LockMode.EXCLUSIVE):
            if not path.exists():
                return None
            interrupt = approval_interrupt_from_dict(json.loads(path.read_bytes()))
            if interrupt.is_expired(time.time()):
                path.unlink(missing_ok=True)
                return None
            path.unlink(missing_ok=True)
            return interrupt

    async def delete(self, session_id: str, approval_id: str) -> None:
        """幂等删除指定审批状态。"""
        self._resolve_path(session_id, approval_id).unlink(missing_ok=True)

    async def delete_session(self, session_id: str) -> None:
        """幂等删除指定会话下的全部审批状态。"""
        bucket, stem = self._policy.hash_session_id(session_id)
        session_dir = self._approvals_root / bucket / stem
        if not session_dir.exists():
            return
        for path in session_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        for path in session_dir.glob("*.json.lock"):
            path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            session_dir.rmdir()

    async def list_pending_by_session(
        self,
        session_id: str,
    ) -> list[ApprovalInterruptSummary]:
        """列出指定会话未过期的审批中断摘要。"""
        bucket, stem = self._policy.hash_session_id(session_id)
        session_dir = self._approvals_root / bucket / stem
        if not session_dir.exists():
            return []

        now = time.time()
        summaries: list[ApprovalInterruptSummary] = []
        for path in session_dir.glob("*.json"):
            lock = self._lock_factory(path.with_suffix(".json.lock"))
            with lock.acquire(LockMode.SHARED):
                if not path.exists():
                    continue
                interrupt = approval_interrupt_from_dict(json.loads(path.read_bytes()))
            if interrupt.is_expired(now):
                path.unlink(missing_ok=True)
                continue
            summaries.append(approval_interrupt_to_summary(interrupt, now_epoch=now))
        return summaries


class RedisApprovalStateStore(ApprovalStateStorePort):
    """审批状态存储的 Redis 实现。"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        key_prefix: str = "agent:approval:",
        ttl_seconds: int = 3600,
    ) -> None:
        """初始化 Redis 审批状态存储。"""
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def _make_key(self, session_id: str, approval_id: str) -> str:
        """构造 Redis key。"""
        return f"{self._key_prefix}{session_id}:{approval_id}"

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """保存审批状态到 Redis。"""
        payload = json.dumps(approval_interrupt_to_dict(interrupt), ensure_ascii=False)
        await self._redis.set(
            self._make_key(interrupt.session_id, interrupt.approval_id),
            payload,
            ex=self._ttl_seconds,
        )

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """从 Redis 加载审批状态。"""
        raw = await self._redis.get(self._make_key(session_id, approval_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        interrupt = approval_interrupt_from_dict(json.loads(raw))
        if interrupt.is_expired(time.time()):
            return None
        return interrupt

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """从 Redis 原子消费审批状态。"""
        key = self._make_key(session_id, approval_id)
        try:
            raw = await self._redis.getdel(key)
        except AttributeError:
            raw = await self._consume_with_watch(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        interrupt = approval_interrupt_from_dict(json.loads(raw))
        if interrupt.is_expired(time.time()):
            return None
        return interrupt

    async def _consume_with_watch(self, key: str) -> str | bytes | None:
        """使用 WATCH/MULTI/EXEC 兼容不支持 GETDEL 的 Redis。"""
        async with self._redis.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        await pipe.unwatch()
                        return None
                    pipe.multi()
                    pipe.delete(key)
                    await pipe.execute()
                    return raw
                except aioredis.WatchError:
                    continue

    async def delete(self, session_id: str, approval_id: str) -> None:
        """幂等删除 Redis 审批状态。"""
        await self._redis.delete(self._make_key(session_id, approval_id))

    async def delete_session(self, session_id: str) -> None:
        """按 session 前缀删除 Redis 审批状态。"""
        pattern = f"{self._key_prefix}{session_id}:*"
        scan = cast(_RedisScanner, self._redis).scan_iter(match=pattern)
        keys: list[bytes | str] = [key async for key in scan]
        if keys:
            await self._redis.delete(*keys)

    async def list_pending_by_session(
        self,
        session_id: str,
    ) -> list[ApprovalInterruptSummary]:
        """列出指定会话未过期的 Redis 审批中断摘要。"""
        pattern = f"{self._key_prefix}{session_id}:*"
        now = time.time()
        summaries: list[ApprovalInterruptSummary] = []
        scan = cast(_RedisScanner, self._redis).scan_iter(match=pattern)
        async for key in scan:
            raw = await self._redis.get(key)
            if raw is None:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            interrupt = approval_interrupt_from_dict(json.loads(raw))
            if interrupt.is_expired(now):
                await self._redis.delete(key)
                continue
            summaries.append(approval_interrupt_to_summary(interrupt, now_epoch=now))
        return summaries
