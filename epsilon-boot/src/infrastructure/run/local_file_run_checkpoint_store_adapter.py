"""本地文件 Run checkpoint store 适配器。"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

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
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockMode
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy


class LocalFileRunCheckpointStoreAdapter(RunCheckpointStorePort):
    """Run checkpoint 和工具结果账本的本地文件实现。"""

    def __init__(
        self,
        root: Path,
        lock_factory: Any,
        path_policy: CrossPlatformPathPolicy,
        atomic_writer: TempFileAtomicWriter,
    ) -> None:
        self._root = root.resolve()
        self._runs_root = self._root / "runs"
        self._checkpoints_root = self._runs_root / "checkpoints"
        self._tool_ledgers_root = self._runs_root / "tool_ledgers"
        self._lock_factory = lock_factory
        self._policy = path_policy
        self._writer = atomic_writer

    async def save_checkpoint(self, checkpoint: DurableCheckpoint) -> DurableCheckpoint:
        """追加 checkpoint，并在同一 run_id 内分配单调 sequence。"""

        run_id = checkpoint.run_id
        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            checkpoints = self._read_checkpoints(run_id)
            sequence = (checkpoints[-1].sequence + 1) if checkpoints else 1
            saved = replace(
                checkpoint,
                sequence=sequence,
                checkpoint_id=f"chk_{sequence:06d}",
            )
            path = self._checkpoint_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(
                    json.dumps(
                        _json_safe(saved),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            return saved

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        """读取指定 Run 的最新 checkpoint。"""

        checkpoints = self._read_checkpoints(run_id)
        return checkpoints[-1] if checkpoints else None

    async def list_checkpoints(
        self, run_id: str, after_sequence: int | None, limit: int
    ) -> list[DurableCheckpoint]:
        """按 sequence 列出 checkpoint。"""

        return [
            checkpoint
            for checkpoint in self._read_checkpoints(run_id)
            if after_sequence is None or checkpoint.sequence > after_sequence
        ][:limit]

    async def put_tool_pending(self, entry: ToolResultLedgerEntry) -> ToolResultLedgerEntry:
        """写入 pending 工具账本；同 key 已存在时返回既有记录。"""

        lock = self._lock_factory(self._run_lock_path(entry.run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            ledger = self._read_ledger(entry.run_id)
            existing = ledger.get(entry.tool_execution_key)
            if existing is not None:
                return existing
            ledger[entry.tool_execution_key] = entry
            self._write_ledger(entry.run_id, ledger)
            return entry

    async def complete_tool_result(
        self,
        *,
        run_id: str,
        tool_execution_key: str,
        result: str,
        is_error: bool,
        metadata: dict[str, Any],
    ) -> ToolResultLedgerEntry:
        """将 pending 工具账本更新为 completed/error。"""

        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            ledger = self._read_ledger(run_id)
            existing = ledger[tool_execution_key]
            completed = replace(
                existing,
                status=ToolLedgerStatus.ERROR if is_error else ToolLedgerStatus.COMPLETED,
                result=result,
                is_error=is_error,
                metadata=dict(metadata),
                updated_at=datetime.now(existing.updated_at.tzinfo),
            )
            ledger[tool_execution_key] = completed
            self._write_ledger(run_id, ledger)
            return completed

    async def get_tool_result(
        self, run_id: str, tool_execution_key: str
    ) -> ToolResultLedgerEntry | None:
        """读取工具账本记录。"""

        return self._read_ledger(run_id).get(tool_execution_key)

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        """列出工具账本记录，按创建时间和 key 稳定排序。"""

        return sorted(
            self._read_ledger(run_id).values(),
            key=lambda entry: (entry.created_at, entry.tool_execution_key),
        )

    async def trim_checkpoints(self, run_id: str, policy: CheckpointRetentionPolicy) -> None:
        """按数量、TTL 与 ledger 上限裁剪 checkpoint/ledger。"""

        lock = self._lock_factory(self._run_lock_path(run_id))
        with lock.acquire(LockMode.EXCLUSIVE):
            checkpoints = self._read_checkpoints(run_id)
            if checkpoints:
                newest_created_at = max(checkpoint.created_at for checkpoint in checkpoints)
                floor = newest_created_at - timedelta(seconds=policy.ttl_seconds)
                retained = [
                    checkpoint for checkpoint in checkpoints if checkpoint.created_at >= floor
                ][-policy.max_checkpoint_count :]
                self._write_checkpoints(run_id, retained)

            ledger = self._read_ledger(run_id)
            retained_entries = sorted(
                ledger.values(),
                key=lambda entry: (entry.updated_at, entry.tool_execution_key),
            )[-policy.max_tool_ledger_count :]
            self._write_ledger(
                run_id,
                {entry.tool_execution_key: entry for entry in retained_entries},
            )

    def _checkpoint_path(self, run_id: str) -> Path:
        bucket, _ = self._policy.hash_session_id(run_id)
        return self._checkpoints_root / bucket / f"{run_id}.jsonl"

    def _ledger_path(self, run_id: str) -> Path:
        bucket, _ = self._policy.hash_session_id(run_id)
        return self._tool_ledgers_root / bucket / f"{run_id}.json"

    def _run_lock_path(self, run_id: str) -> Path:
        bucket, _ = self._policy.hash_session_id(run_id)
        return self._runs_root / "locks" / bucket / f"{run_id}.lock"

    def _read_checkpoints(self, run_id: str) -> list[DurableCheckpoint]:
        path = self._checkpoint_path(run_id)
        if not path.exists():
            return []
        checkpoints = [
            _checkpoint_from_dict(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for checkpoint in checkpoints:
            if checkpoint.schema_version != 1:
                raise RunCheckpointSchemaError(
                    run_id,
                    checkpoint.checkpoint_id,
                    checkpoint.schema_version,
                    "unsupported checkpoint schema",
                )
        return checkpoints

    def _write_checkpoints(self, run_id: str, checkpoints: list[DurableCheckpoint]) -> None:
        payload = "".join(
            json.dumps(
                _json_safe(checkpoint),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for checkpoint in checkpoints
        ).encode("utf-8")
        self._writer.write_bytes_atomic(self._checkpoint_path(run_id), payload)

    def _read_ledger(self, run_id: str) -> dict[str, ToolResultLedgerEntry]:
        path = self._ledger_path(run_id)
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        return {key: _ledger_from_dict(value) for key, value in raw.items()}

    def _write_ledger(self, run_id: str, ledger: dict[str, ToolResultLedgerEntry]) -> None:
        payload = json.dumps(
            {key: _json_safe(value) for key, value in ledger.items()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._writer.write_bytes_atomic(self._ledger_path(run_id), payload)


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
