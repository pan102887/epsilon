"""Run checkpoint sink 单元测试模块。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from application.run.run_checkpoint_sink import RunCheckpointSink
from domain.chat.context import ConversationContext
from domain.model_access.value_objects import ToolCallRequest
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.exceptions import RunCheckpointWriteError
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RunEvent,
    RunEventType,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints: list[DurableCheckpoint] = []
        self.ledger: dict[str, ToolResultLedgerEntry] = {}
        self.fail_pending = False

    async def save_checkpoint(self, checkpoint: DurableCheckpoint) -> DurableCheckpoint:
        saved = replace(
            checkpoint,
            sequence=len(self.checkpoints) + 1,
            checkpoint_id=f"chk_{len(self.checkpoints) + 1:06d}",
        )
        self.checkpoints.append(saved)
        return saved

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        for checkpoint in reversed(self.checkpoints):
            if checkpoint.run_id == run_id:
                return checkpoint
        return None

    async def list_checkpoints(
        self, run_id: str, after_sequence: int | None, limit: int
    ) -> list[DurableCheckpoint]:
        return [
            checkpoint
            for checkpoint in self.checkpoints
            if checkpoint.run_id == run_id
            and (after_sequence is None or checkpoint.sequence > after_sequence)
        ][:limit]

    async def put_tool_pending(self, entry: ToolResultLedgerEntry) -> ToolResultLedgerEntry:
        if self.fail_pending:
            raise RuntimeError("disk down")
        self.ledger[entry.tool_execution_key] = entry
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
        existing = self.ledger[tool_execution_key]
        completed = replace(
            existing,
            status=ToolLedgerStatus.ERROR if is_error else ToolLedgerStatus.COMPLETED,
            result=result,
            is_error=is_error,
            metadata=metadata,
            updated_at=_NOW,
        )
        self.ledger[tool_execution_key] = completed
        return completed

    async def get_tool_result(
        self, run_id: str, tool_execution_key: str
    ) -> ToolResultLedgerEntry | None:
        return self.ledger.get(tool_execution_key)

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        return list(self.ledger.values())

    async def trim_checkpoints(self, run_id: str, policy: CheckpointRetentionPolicy) -> None:
        return None


class _MemoryEventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


def _policy(max_payload_bytes: int = 4096) -> CheckpointRetentionPolicy:
    return CheckpointRetentionPolicy(
        max_checkpoint_count=10,
        ttl_seconds=3600,
        max_payload_bytes=max_payload_bytes,
        max_tool_ledger_count=100,
    )


def _context() -> ConversationContext:
    ctx = ConversationContext()
    ctx.add_user_message("hello")
    return ctx


def _sink(
    store: _MemoryCheckpointStore,
    events: _MemoryEventStore,
    *,
    max_payload_bytes: int = 4096,
) -> RunCheckpointSink:
    return RunCheckpointSink(
        checkpoint_store=store,
        event_store=events,
        retention_policy=_policy(max_payload_bytes=max_payload_bytes),
        now=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_model_completed_saves_checkpoint_and_event() -> None:
    store = _MemoryCheckpointStore()
    events = _MemoryEventStore()
    sink = _sink(store, events)
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=0,
            recovery_mode=False,
            sink=sink,
        )
    )
    try:
        checkpoint = await sink.model_completed(
            context=_context(),
            round_num=2,
            usage={"total_tokens": 10},
            trace_summary={"model": "gpt-x"},
            segment_metadata={"segment_index": 0},
        )
    finally:
        reset_run_checkpoint_context(token)

    assert checkpoint.phase is CheckpointPhase.MODEL_COMPLETED
    assert checkpoint.run_id == "run-1"
    assert checkpoint.context_snapshot["messages"][0]["role"] == "user"
    assert events.events[-1].event_type is RunEventType.CHECKPOINT_SAVED


@pytest.mark.asyncio
async def test_before_tool_call_writes_pending_before_execution() -> None:
    store = _MemoryCheckpointStore()
    events = _MemoryEventStore()
    sink = _sink(store, events)
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "worker-1", 0, False, sink)
    )
    try:
        replay = await sink.before_tool_call(
            tool_call=ToolCallRequest(id="call-1", name="write_file", arguments='{"b":2,"a":1}'),
            round_num=1,
            segment_index=0,
            replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
            side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
            idempotency_key=None,
        )
    finally:
        reset_run_checkpoint_context(token)

    assert replay is None
    assert len(store.ledger) == 1
    entry = next(iter(store.ledger.values()))
    assert entry.status is ToolLedgerStatus.PENDING
    assert entry.tool_name == "write_file"


@pytest.mark.asyncio
async def test_before_tool_call_replays_completed_without_new_pending() -> None:
    store = _MemoryCheckpointStore()
    events = _MemoryEventStore()
    sink = _sink(store, events)
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "worker-1", 0, False, sink)
    )
    tool_call = ToolCallRequest(id="call-1", name="read_file", arguments='{"path":"a"}')
    try:
        await sink.before_tool_call(
            tool_call=tool_call,
            round_num=1,
            segment_index=0,
            replay_policy=ToolReplayPolicy.REPLAY_RESULT,
            side_effect_level=ToolSideEffectLevel.NONE,
            idempotency_key=None,
        )
        key = next(iter(store.ledger))
        await store.complete_tool_result(
            run_id="run-1",
            tool_execution_key=key,
            result="cached",
            is_error=False,
            metadata={},
        )

        replay = await sink.before_tool_call(
            tool_call=tool_call,
            round_num=1,
            segment_index=0,
            replay_policy=ToolReplayPolicy.REPLAY_RESULT,
            side_effect_level=ToolSideEffectLevel.NONE,
            idempotency_key=None,
        )
    finally:
        reset_run_checkpoint_context(token)

    assert replay is not None
    assert replay.result == "cached"
    assert len(store.ledger) == 1
    assert events.events[-1].event_type is RunEventType.TOOL_RESULT_REPLAYED


@pytest.mark.asyncio
async def test_pending_write_failure_raises_checkpoint_error() -> None:
    store = _MemoryCheckpointStore()
    store.fail_pending = True
    events = _MemoryEventStore()
    sink = _sink(store, events)
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "worker-1", 0, False, sink)
    )
    try:
        with pytest.raises(RunCheckpointWriteError):
            await sink.before_tool_call(
                tool_call=ToolCallRequest(id="call-1", name="write_file", arguments='{"path":"a"}'),
                round_num=1,
                segment_index=0,
                replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
                side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
                idempotency_key=None,
            )
    finally:
        reset_run_checkpoint_context(token)

    assert store.ledger == {}


@pytest.mark.asyncio
async def test_after_tool_call_completes_ledger_and_saves_checkpoint() -> None:
    store = _MemoryCheckpointStore()
    events = _MemoryEventStore()
    sink = _sink(store, events)
    ctx = _context()
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "worker-1", 0, False, sink)
    )
    try:
        await sink.before_tool_call(
            tool_call=ToolCallRequest(id="call-1", name="write_file", arguments='{"path":"a"}'),
            round_num=1,
            segment_index=0,
            replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
            side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
            idempotency_key=None,
        )
        key = next(iter(store.ledger))
        ctx.add_tool_result("write_file", "ok", "call-1")
        checkpoint = await sink.after_tool_call(
            context=ctx,
            tool_execution_key=key,
            result="ok",
            is_error=False,
            metadata={"duration_ms": 5},
            round_num=1,
            usage={"total_tokens": 11},
        )
    finally:
        reset_run_checkpoint_context(token)

    assert store.ledger[key].status is ToolLedgerStatus.COMPLETED
    assert checkpoint.phase is CheckpointPhase.TOOL_COMPLETED
    assert checkpoint.tool_execution_key == key


@pytest.mark.asyncio
async def test_after_tool_call_records_truncated_tool_result() -> None:
    store = _MemoryCheckpointStore()
    events = _MemoryEventStore()
    sink = _sink(store, events, max_payload_bytes=120)
    ctx = _context()
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "worker-1", 0, False, sink)
    )
    try:
        await sink.before_tool_call(
            tool_call=ToolCallRequest(id="call-1", name="write_file", arguments='{"path":"a"}'),
            round_num=1,
            segment_index=0,
            replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
            side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
            idempotency_key=None,
        )
        key = next(iter(store.ledger))
        checkpoint = await sink.after_tool_call(
            context=ctx,
            tool_execution_key=key,
            result="x" * 1000,
            is_error=False,
            metadata={},
            round_num=1,
            usage={"total_tokens": 11},
        )
    finally:
        reset_run_checkpoint_context(token)

    assert "tool_result" in checkpoint.truncated_fields
    stored_result = store.ledger[key].result
    assert stored_result is not None
    assert len(stored_result) < 1000


@pytest.mark.asyncio
async def test_checkpoint_payload_is_truncated_when_too_large() -> None:
    store = _MemoryCheckpointStore()
    events = _MemoryEventStore()
    sink = _sink(store, events, max_payload_bytes=120)
    token = set_run_checkpoint_context(
        RunCheckpointExecutionContext("run-1", "worker-1", 0, False, sink)
    )
    try:
        checkpoint = await sink.model_completed(
            context=_context(),
            round_num=1,
            usage={"total_tokens": 1},
            trace_summary={"large": "x" * 1000},
            segment_metadata={},
        )
    finally:
        reset_run_checkpoint_context(token)

    assert checkpoint.sanitized is True
    assert "trace_summary.large" in checkpoint.truncated_fields
