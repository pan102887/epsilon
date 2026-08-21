"""ReActAgentAdapter checkpoint hook tests."""

from __future__ import annotations

from contextvars import Token
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.run.run_checkpoint_sink import RunCheckpointSink
from domain.agent.ports import ApprovalPolicyPort
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig, ApprovalPolicy
from domain.chat.context import BaseMessage, ConversationContext, ToolMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.ports import RunCheckpointSinkPort, RunEventStorePort
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
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

pytestmark = pytest.mark.asyncio


class _Sink:
    def __init__(
        self,
        *,
        before_result: ToolResultLedgerEntry | None = None,
        before_error: Exception | None = None,
    ) -> None:
        self.before_result = before_result
        self.before_error = before_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def model_completed(self, **kwargs: Any) -> DurableCheckpoint:
        self.calls.append(("model_completed", kwargs))
        return _checkpoint(CheckpointPhase.MODEL_COMPLETED)

    async def before_tool_call(self, **kwargs: Any) -> ToolResultLedgerEntry | None:
        self.calls.append(("before_tool_call", kwargs))
        if self.before_error is not None:
            raise self.before_error
        return self.before_result

    async def after_tool_call(self, **kwargs: Any) -> DurableCheckpoint:
        self.calls.append(("after_tool_call", kwargs))
        return _checkpoint(CheckpointPhase.TOOL_COMPLETED)

    async def approval_interrupt(self, **kwargs: Any) -> DurableCheckpoint:
        self.calls.append(("approval_interrupt", kwargs))
        return _checkpoint(CheckpointPhase.APPROVAL_INTERRUPT)

    async def segment_done(self, **kwargs: Any) -> DurableCheckpoint:
        self.calls.append(("segment_done", kwargs))
        return _checkpoint(CheckpointPhase.SEGMENT_DONE)


class _MemoryCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints: list[DurableCheckpoint] = []
        self.ledger: dict[str, ToolResultLedgerEntry] = {}

    async def save_checkpoint(self, checkpoint: DurableCheckpoint) -> DurableCheckpoint:
        saved = replace(
            checkpoint,
            sequence=len(self.checkpoints) + 1,
            checkpoint_id=f"chk_{len(self.checkpoints) + 1:06d}",
        )
        self.checkpoints.append(saved)
        return saved

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None

    async def list_checkpoints(
        self, run_id: str, after_sequence: int | None, limit: int
    ) -> list[DurableCheckpoint]:
        return self.checkpoints[:limit]

    async def put_tool_pending(self, entry: ToolResultLedgerEntry) -> ToolResultLedgerEntry:
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
            updated_at=datetime.now(UTC),
        )
        self.ledger[tool_execution_key] = completed
        return completed

    async def get_tool_result(
        self, run_id: str, tool_execution_key: str
    ) -> ToolResultLedgerEntry | None:
        return self.ledger.get(tool_execution_key)

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        return list(self.ledger.values())

    async def trim_checkpoints(
        self, run_id: str, policy: CheckpointRetentionPolicy
    ) -> None:
        return None


class _MemoryEventStore:
    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        return cast(RunEvent, MagicMock())


class _Tool:
    def side_effect_level(self):
        return ToolSideEffectLevel.NONE

    def replay_policy(self):
        return ToolReplayPolicy.REPLAY_RESULT

    def idempotency_key(self, request: ToolCallRequest, execution_key: str):
        return f"idem:{request.id}:{execution_key[:8]}"


class _ApprovalPolicy:
    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=True,
            allowed_decisions=frozenset({"approve", "reject"}),
            risk_label="write",
        )


async def test_tool_pending_is_written_before_execute_and_completed_after() -> None:
    sink = _Sink()
    registry = _registry(result="tool ok")
    adapter = _adapter(registry)
    ctx = ConversationContext()
    token = _set_checkpoint(sink)

    try:
        result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), _config())
    finally:
        reset_run_checkpoint_context(token)

    assert (result.content, is_error) == ("tool ok", False)
    assert registry.execute.await_count == 1
    assert [name for name, _ in sink.calls] == ["before_tool_call", "after_tool_call"]
    assert sink.calls[0][1]["round_num"] == 0
    assert sink.calls[0][1]["segment_index"] == 3
    assert sink.calls[0][1]["replay_policy"] is ToolReplayPolicy.REPLAY_RESULT
    assert sink.calls[0][1]["side_effect_level"] is ToolSideEffectLevel.NONE
    assert sink.calls[0][1]["idempotency_key"].startswith("idem:call-1:")
    assert sink.calls[1][1]["result"] == "tool ok"
    assert sink.calls[1][1]["is_error"] is False


async def test_pending_write_failure_prevents_tool_execution() -> None:
    sink = _Sink(before_error=RuntimeError("checkpoint unavailable"))
    registry = _registry(result="tool ok")
    adapter = _adapter(registry)
    ctx = ConversationContext()
    token = _set_checkpoint(sink)

    try:
        with pytest.raises(RuntimeError, match="checkpoint unavailable"):
            await adapter.execute_tool_call_result(ctx, _tool_call(), _config())
    finally:
        reset_run_checkpoint_context(token)

    registry.execute.assert_not_awaited()
    assert [name for name, _ in sink.calls] == ["before_tool_call"]


async def test_real_checkpoint_sink_uses_same_tool_execution_key_as_agent_for_json_arguments() -> (
    None
):
    store = _MemoryCheckpointStore()
    sink = RunCheckpointSink(
        checkpoint_store=store,
        event_store=cast(RunEventStorePort, _MemoryEventStore()),
        retention_policy=CheckpointRetentionPolicy(10, 3600, 4096, 10),
        now=lambda: datetime.now(UTC),
    )
    registry = _registry(result="tool ok")
    adapter = _adapter(registry)
    ctx = ConversationContext()
    tool_call = ToolCallRequest(
        id="call-1",
        name="echo",
        arguments='{\n  "b": 2,\n  "a": 1\n}',
    )
    token = _set_checkpoint(sink)

    try:
        result, is_error = await adapter.execute_tool_call_result(
            ctx,
            tool_call,
            _config(),
            round_num=1,
        )
    finally:
        reset_run_checkpoint_context(token)

    assert (result.content, is_error) == ("tool ok", False)
    assert len(store.ledger) == 1
    entry = next(iter(store.ledger.values()))
    assert entry.status is ToolLedgerStatus.COMPLETED
    assert entry.result == "tool ok"


async def test_completed_ledger_replay_skips_tool_execution() -> None:
    sink = _Sink(before_result=_ledger(result="cached result"))
    registry = _registry(result="tool ok")
    adapter = _adapter(registry)
    ctx = ConversationContext()
    token = _set_checkpoint(sink)

    try:
        result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), _config())
    finally:
        reset_run_checkpoint_context(token)

    registry.execute.assert_not_awaited()
    assert (result.content, is_error) == ("cached result", False)
    assert [name for name, _ in sink.calls] == ["before_tool_call"]
    last = ctx.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.content == "cached result"


async def test_iter_rounds_saves_model_completed_checkpoint() -> None:
    sink = _Sink()
    model_access = MagicMock()
    install_stream_mock(
        model_access,
        [LLMResponse(content="done", model="m", usage={"total_tokens": 1}, tool_calls=[])],
    )
    adapter = _adapter(_registry())
    ctx = ConversationContext()
    token = _set_checkpoint(sink)

    try:
        outcome = await anext(adapter.iter_rounds(ctx, _config(), model_access))
    finally:
        reset_run_checkpoint_context(token)

    assert outcome.kind == "text"
    assert [name for name, _ in sink.calls] == ["model_completed"]
    assert sink.calls[0][1]["round_num"] == 1
    assert sink.calls[0][1]["usage"] == {"total_tokens": 1}


async def test_approval_interrupt_saves_checkpoint_before_returning() -> None:
    sink = _Sink()
    model_access = MagicMock()
    install_stream_mock(
        model_access,
        [
            LLMResponse(
                content="",
                model="m",
                usage={},
                tool_calls=[_tool_call()],
            )
        ],
    )
    adapter = _adapter(_registry(), approval_policy=_ApprovalPolicy())
    ctx = ConversationContext()
    ctx.session_id = "session-1"
    token = _set_checkpoint(sink)

    try:
        outcome = await anext(adapter.iter_rounds(ctx, _config(), model_access))
    finally:
        reset_run_checkpoint_context(token)

    assert outcome.kind == "approval"
    assert [name for name, _ in sink.calls] == [
        "model_completed",
        "approval_interrupt",
    ]
    assert sink.calls[1][1]["round_num"] == 1
    assert outcome.approval is not None
    assert sink.calls[1][1]["approval_id"] == outcome.approval.approval_id


def _adapter(
    registry: MagicMock,
    approval_policy: ApprovalPolicyPort | None = None,
) -> ReActAgentAdapter:
    builder = MagicMock()

    async def build_context(
        msgs: list[BaseMessage], **kwargs: Any
    ) -> ContextBuilderResult:
        return ContextBuilderResult(messages=msgs, usage={})

    builder.build = AsyncMock(side_effect=build_context)
    return ReActAgentAdapter(
        tool_registry=registry,
        context_builder=builder,
        approval_policy=approval_policy,
    )

def _registry(result: str = "ok") -> MagicMock:
    registry = MagicMock()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content=result))
    registry.get = MagicMock(return_value=_Tool())
    return registry


def _config() -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[{"type": "function", "function": {"name": "echo"}}],
        model="m",
        max_rounds=2,
        prompt_id="chat-default@v1",
    )


def _tool_call() -> ToolCallRequest:
    return ToolCallRequest(id="call-1", name="echo", arguments='{"x":1}')


def _set_checkpoint(
    sink: _Sink | RunCheckpointSink,
) -> Token[RunCheckpointExecutionContext | None]:
    return set_run_checkpoint_context(
        RunCheckpointExecutionContext(
            run_id="run-1",
            owner_id="owner-a",
            segment_index=3,
            recovery_mode=False,
            sink=cast(RunCheckpointSinkPort, sink),
        )
    )


def _checkpoint(phase: CheckpointPhase) -> DurableCheckpoint:
    return DurableCheckpoint(
        run_id="run-1",
        checkpoint_id="chk_000001",
        sequence=1,
        phase=phase,
        context_snapshot={},
        round_num=1,
        usage={},
        trace_summary={},
        segment_metadata={},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=datetime.now(UTC),
    )


def _ledger(result: str) -> ToolResultLedgerEntry:
    return ToolResultLedgerEntry(
        run_id="run-1",
        tool_execution_key="key-1",
        status=ToolLedgerStatus.COMPLETED,
        tool_name="echo",
        tool_call_id="call-1",
        arguments_digest="digest",
        replay_policy=ToolReplayPolicy.REPLAY_RESULT,
        side_effect_level=ToolSideEffectLevel.NONE,
        idempotency_key="idem",
        result=result,
        is_error=False,
        metadata={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
