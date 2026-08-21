"""ReActAgentAdapter HITL checkpoint replay tests."""

from __future__ import annotations

from contextvars import Token
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalInterrupt,
    EditedAction,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.ports import RunCheckpointSinkPort
from domain.run.value_objects import (
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

pytestmark = pytest.mark.asyncio


class _Sink:
    def __init__(self, before_result: ToolResultLedgerEntry | None = None) -> None:
        self.before_result = before_result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def before_tool_call(self, **kwargs: Any) -> ToolResultLedgerEntry | None:
        self.calls.append(("before_tool_call", kwargs))
        return self.before_result

    async def after_tool_call(self, **kwargs: Any) -> None:
        self.calls.append(("after_tool_call", kwargs))
        return None


async def test_approve_reuses_completed_ledger_without_executing_tool() -> None:
    sink = _Sink(before_result=_ledger("cached approve"))
    registry = _registry()
    adapter = _adapter(registry)
    ctx = _context_with_tool_call()
    token = _set_checkpoint(sink)

    try:
        await adapter.apply_approval_decisions(
            ctx,
            _config(),
            _interrupt(ctx),
            (ApprovalDecision(type="approve", tool_call_id="call-1"),),
        )
    finally:
        reset_run_checkpoint_context(token)

    registry.execute.assert_not_awaited()
    assert [name for name, _ in sink.calls] == ["before_tool_call"]
    assert isinstance(ctx.get_messages()[-1], ToolMessage)
    assert ctx.get_messages()[-1].content == "cached approve"


async def test_edit_reuses_completed_ledger_without_executing_tool() -> None:
    sink = _Sink(before_result=_ledger("cached edit"))
    registry = _registry()
    adapter = _adapter(registry)
    ctx = _context_with_tool_call()
    token = _set_checkpoint(sink)

    try:
        await adapter.apply_approval_decisions(
            ctx,
            _config(),
            _interrupt(ctx),
            (
                ApprovalDecision(
                    type="edit",
                    tool_call_id="call-1",
                    edited_action=EditedAction(name="echo", arguments='{"x":2}'),
                ),
            ),
        )
    finally:
        reset_run_checkpoint_context(token)

    registry.execute.assert_not_awaited()
    assert [name for name, _ in sink.calls] == ["before_tool_call"]
    assert ctx.get_messages()[-1].content == "cached edit"


async def test_reject_decision_writes_checkpoint_ledger() -> None:
    sink = _Sink()
    registry = _registry()
    adapter = _adapter(registry)
    ctx = _context_with_tool_call()
    token = _set_checkpoint(sink)

    try:
        await adapter.apply_approval_decisions(
            ctx,
            _config(),
            _interrupt(ctx),
            (
                ApprovalDecision(
                    type="reject",
                    tool_call_id="call-1",
                    message="not allowed",
                ),
            ),
        )
    finally:
        reset_run_checkpoint_context(token)

    registry.execute.assert_not_awaited()
    assert [name for name, _ in sink.calls] == ["before_tool_call", "after_tool_call"]
    assert sink.calls[0][1]["round_num"] == 7
    assert sink.calls[1][1]["round_num"] == 7
    assert sink.calls[1][1]["result"] == "not allowed"
    assert sink.calls[1][1]["is_error"] is True
    assert ctx.get_messages()[-1].content == "not allowed"


def _adapter(registry: MagicMock) -> ReActAgentAdapter:
    return ReActAgentAdapter(tool_registry=registry, context_builder=MagicMock())


def _registry() -> MagicMock:
    registry = MagicMock()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="executed"))
    tool = MagicMock()
    tool.replay_policy = ToolReplayPolicy.REPLAY_RESULT
    tool.side_effect_level = ToolSideEffectLevel.NONE
    tool.idempotency_key = MagicMock(return_value="idem")
    def identity(value: Any) -> Any:
        return value

    tool.cast_params = MagicMock(side_effect=identity)
    tool.validate_params = MagicMock(return_value=[])
    registry.get = MagicMock(return_value=tool)
    return registry


def _config() -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[{"type": "function", "function": {"name": "echo"}}],
        model="m",
        max_rounds=2,
        prompt_id="chat-default@v1",
    )


def _context_with_tool_call() -> ConversationContext:
    ctx = ConversationContext()
    ctx.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="echo", arguments='{"x":1}')],
    )
    return ctx


def _interrupt(ctx: ConversationContext) -> ApprovalInterrupt:
    return ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="echo",
                arguments='{"x":1}',
                allowed_decisions=frozenset({"approve", "edit", "reject"}),
            ),
        ),
        context_snapshot=ctx.to_dict(),
        round_num=7,
        model="m",
        usage_so_far={"total_tokens": 11},
    )


def _set_checkpoint(
    sink: _Sink,
) -> Token[RunCheckpointExecutionContext | None]:
    return set_run_checkpoint_context(
        RunCheckpointExecutionContext(
            run_id="run-1",
            owner_id="owner-a",
            segment_index=2,
            recovery_mode=True,
            sink=cast(RunCheckpointSinkPort, sink),
        )
    )


def _ledger(result: str) -> ToolResultLedgerEntry:
    now = datetime.now(UTC)
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
        created_at=now,
        updated_at=now,
    )
