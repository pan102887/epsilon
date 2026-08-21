"""Redis Run checkpoint store 适配器。"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast

import redis.asyncio as aioredis

from domain.run.exceptions import RunCheckpointSchemaError
from domain.run.ports import RunCheckpointStorePort
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)

_RedisValue = str | bytes


class _CheckpointRedisCommands(Protocol):
    """Checkpoint store 使用的 Redis 异步命令最小协议。"""

    async def incr(self, name: str) -> int: ...

    async def rpush(self, name: str, *values: str) -> int: ...

    async def lindex(self, name: str, index: int) -> _RedisValue | None: ...

    async def lrange(self, name: str, start: int, end: int) -> list[_RedisValue]: ...

    async def hsetnx(self, name: str, key: str, value: str) -> bool: ...

    async def hset(self, name: str, key: str, value: str) -> int: ...

    async def hget(self, name: str, key: str) -> _RedisValue | None: ...

    async def hvals(self, name: str) -> list[_RedisValue]: ...

    async def hdel(self, name: str, *keys: str) -> int: ...


class RedisRunCheckpointStoreAdapter(RunCheckpointStorePort):
    """Run checkpoint 和工具结果账本的 Redis 实现。"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        key_prefix: str = "",
        conflict_retry_max: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._commands = cast(_CheckpointRedisCommands, redis_client)
        self._key_prefix = key_prefix.rstrip(":")
        self._conflict_retry_max = conflict_retry_max if conflict_retry_max is not None else 5

    async def save_checkpoint(self, checkpoint: DurableCheckpoint) -> DurableCheckpoint:
        seq = await self._commands.incr(self._seq_key(checkpoint.run_id))
        saved = replace(
            checkpoint,
            sequence=int(seq),
            checkpoint_id=f"chk_{int(seq):06d}",
        )
        await self._commands.rpush(self._checkpoints_key(checkpoint.run_id), self._encode(saved))
        return saved

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        raw = await self._commands.lindex(self._checkpoints_key(run_id), -1)
        if raw is None:
            return None
        checkpoint = _checkpoint_from_dict(json.loads(self._decode(raw)))
        self._assert_schema(run_id, checkpoint)
        return checkpoint

    async def list_checkpoints(
        self, run_id: str, after_sequence: int | None, limit: int
    ) -> list[DurableCheckpoint]:
        raws = await self._commands.lrange(self._checkpoints_key(run_id), 0, -1)
        checkpoints = [_checkpoint_from_dict(json.loads(self._decode(raw))) for raw in raws]
        for checkpoint in checkpoints:
            self._assert_schema(run_id, checkpoint)
        return [
            checkpoint
            for checkpoint in checkpoints
            if after_sequence is None or checkpoint.sequence > after_sequence
        ][:limit]

    async def put_tool_pending(self, entry: ToolResultLedgerEntry) -> ToolResultLedgerEntry:
        key = self._ledger_key(entry.run_id)
        created = await self._commands.hsetnx(
            key,
            entry.tool_execution_key,
            self._encode(entry),
        )
        if created:
            return entry
        existing = await self.get_tool_result(entry.run_id, entry.tool_execution_key)
        if existing is None:
            return entry
        return existing

    async def complete_tool_result(
        self,
        *,
        run_id: str,
        tool_execution_key: str,
        result: str,
        is_error: bool,
        metadata: dict[str, Any],
    ) -> ToolResultLedgerEntry:
        existing = await self.get_tool_result(run_id, tool_execution_key)
        if existing is None:
            raise KeyError(tool_execution_key)
        completed = replace(
            existing,
            status=ToolLedgerStatus.ERROR if is_error else ToolLedgerStatus.COMPLETED,
            result=result,
            is_error=is_error,
            metadata=dict(metadata),
            updated_at=datetime.now(existing.updated_at.tzinfo),
        )
        await self._commands.hset(
            self._ledger_key(run_id),
            tool_execution_key,
            self._encode(completed),
        )
        return completed

    async def get_tool_result(
        self, run_id: str, tool_execution_key: str
    ) -> ToolResultLedgerEntry | None:
        raw = await self._commands.hget(self._ledger_key(run_id), tool_execution_key)
        return _ledger_from_dict(json.loads(self._decode(raw))) if raw is not None else None

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        values = await self._commands.hvals(self._ledger_key(run_id))
        return sorted(
            (_ledger_from_dict(json.loads(self._decode(raw))) for raw in values),
            key=lambda entry: (entry.created_at, entry.tool_execution_key),
        )

    async def trim_checkpoints(self, run_id: str, policy: CheckpointRetentionPolicy) -> None:
        checkpoints_key = self._checkpoints_key(run_id)
        raws = await self._commands.lrange(checkpoints_key, 0, -1)
        checkpoints = [_checkpoint_from_dict(json.loads(self._decode(raw))) for raw in raws]
        if checkpoints:
            newest_created_at = max(checkpoint.created_at for checkpoint in checkpoints)
            floor = newest_created_at - timedelta(seconds=policy.ttl_seconds)
            retained_checkpoints = [
                checkpoint for checkpoint in checkpoints if checkpoint.created_at >= floor
            ][-policy.max_checkpoint_count :]
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(checkpoints_key)
                if retained_checkpoints:
                    pipe.rpush(
                        checkpoints_key,
                        *[self._encode(checkpoint) for checkpoint in retained_checkpoints],
                    )
                await pipe.execute()
        ledger = await self.list_tool_ledger(run_id)
        retained = {
            entry.tool_execution_key
            for entry in sorted(
                ledger,
                key=lambda item: (item.updated_at, item.tool_execution_key),
            )[-policy.max_tool_ledger_count :]
        }
        for entry in ledger:
            if entry.tool_execution_key not in retained:
                await self._commands.hdel(self._ledger_key(run_id), entry.tool_execution_key)

    def _checkpoints_key(self, run_id: str) -> str:
        return self._key(f"run:{run_id}:checkpoints")

    def _seq_key(self, run_id: str) -> str:
        return self._key(f"run:{run_id}:checkpoint_seq")

    def _ledger_key(self, run_id: str) -> str:
        return self._key(f"run:{run_id}:tool_ledger")

    def _key(self, value: str) -> str:
        return f"{self._key_prefix}:{value}" if self._key_prefix else value

    def _encode(self, value: Any) -> str:
        return json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    @staticmethod
    def _assert_schema(run_id: str, checkpoint: DurableCheckpoint) -> None:
        if checkpoint.schema_version != 1:
            raise RunCheckpointSchemaError(
                run_id,
                checkpoint.checkpoint_id,
                checkpoint.schema_version,
                "unsupported checkpoint schema",
            )


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


def _checkpoint_from_dict(data: dict[str, Any]) -> DurableCheckpoint:
    return DurableCheckpoint(
        run_id=data["run_id"],
        checkpoint_id=data["checkpoint_id"],
        sequence=int(data["sequence"]),
        phase=CheckpointPhase(data["phase"]),
        context_snapshot=data.get("context_snapshot") or {},
        round_num=data.get("round_num"),
        usage=data.get("usage") or {},
        trace_summary=data.get("trace_summary") or {},
        segment_metadata=data.get("segment_metadata") or {},
        tool_execution_key=data.get("tool_execution_key"),
        tool_result_ref=data.get("tool_result_ref"),
        schema_version=int(data.get("schema_version", 1)),
        sanitized=bool(data.get("sanitized", False)),
        truncated_fields=tuple(data.get("truncated_fields") or ()),
        created_at=datetime.fromisoformat(data["created_at"]),
    )


def _ledger_from_dict(data: dict[str, Any]) -> ToolResultLedgerEntry:
    return ToolResultLedgerEntry(
        run_id=data["run_id"],
        tool_execution_key=data["tool_execution_key"],
        status=ToolLedgerStatus(data["status"]),
        tool_name=data["tool_name"],
        tool_call_id=data["tool_call_id"],
        arguments_digest=data["arguments_digest"],
        replay_policy=ToolReplayPolicy(data["replay_policy"]),
        side_effect_level=ToolSideEffectLevel(data["side_effect_level"]),
        idempotency_key=data.get("idempotency_key"),
        result=data.get("result"),
        is_error=bool(data.get("is_error", False)),
        metadata=data.get("metadata") or {},
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )
