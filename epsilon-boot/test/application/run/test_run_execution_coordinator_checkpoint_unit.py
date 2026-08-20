"""RunExecutionCoordinator checkpoint 编排单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from application.run.run_execution_coordinator import RunExecutionCoordinator
from domain.chat.context import ConversationContext
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO, ChatResponseVO
from domain.run.checkpoint_context import get_run_checkpoint_context
from domain.run.runtime_context import get_run_execution_context
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from infrastructure.run.run_serialization_adapters import SegmentSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _ObservingChatService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.context_run_ids: list[str | None] = []
        self.runtime_run_ids: list[str | None] = []
        self.runtime_recovery_modes: list[bool | None] = []
        self.runtime_segment_indexes: list[int | None] = []
        self.chat_calls = 0
        self.continue_calls = 0
        self.recovered_message_counts: list[int | None] = []
        self.restored_message_counts: list[int] = []

    async def restore_checkpoint_context(
        self,
        session_id: str,
        context_snapshot: dict[str, Any],
    ) -> None:
        self.restored_message_counts.append(len(context_snapshot.get("messages", [])))

    async def chat(self, request: ChatRequestVO) -> ChatResponseVO:
        self.chat_calls += 1
        current = get_run_checkpoint_context()
        runtime = get_run_execution_context()
        self.context_run_ids.append(current.run_id if current is not None else None)
        self.runtime_run_ids.append(runtime.run_id if runtime is not None else None)
        self.runtime_recovery_modes.append(runtime.recovery_mode if runtime is not None else None)
        self.runtime_segment_indexes.append(runtime.segment_index if runtime is not None else None)
        self.recovered_message_counts.append(_context_message_count(current))
        if self.fail:
            raise RuntimeError("boom")
        return ChatResponseVO(
            session_id=request.session_id,
            reply="ok",
            model=request.model,
            usage={},
            prompt_id="chat-default@v1",
        )

    async def continue_chat(self, request: ChatContinueRequestVO) -> ChatResponseVO:
        self.continue_calls += 1
        current = get_run_checkpoint_context()
        runtime = get_run_execution_context()
        self.context_run_ids.append(current.run_id if current is not None else None)
        self.runtime_run_ids.append(runtime.run_id if runtime is not None else None)
        self.runtime_recovery_modes.append(runtime.recovery_mode if runtime is not None else None)
        self.runtime_segment_indexes.append(runtime.segment_index if runtime is not None else None)
        self.recovered_message_counts.append(_context_message_count(current))
        return ChatResponseVO(
            session_id=request.session_id,
            reply="ok",
            model=request.model,
            usage={},
            prompt_id="chat-default@v1",
        )


class _UnusedTaskAgent:
    pass


class _FakeProgress:
    async def segment_started(self, run_id: str, segment_index: int) -> None:
        return None

    async def segment_done(self, run_id: str, metadata: dict[str, Any]) -> None:
        return None


class _FakeCheckpointStore:
    def __init__(self, checkpoint: DurableCheckpoint | None = None) -> None:
        self.checkpoint = checkpoint
        self.latest_calls: list[str] = []

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        self.latest_calls.append(run_id)
        return self.checkpoint


class _FakeEventStore:
    pass


def _snapshot() -> RunSnapshot:
    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="s-chat",
        chat={"session_id": "s-chat", "message": "hello", "model": "m1"},
        model="m1",
    )
    return RunSnapshot(
        run_id="run-chat",
        kind=RunKind.CHAT,
        status=RunStatus.RUNNING,
        payload=payload,
        client_request_id=None,
        payload_hash=None,
        result=None,
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
    )


def _policy() -> CheckpointRetentionPolicy:
    return CheckpointRetentionPolicy(10, 3600, 4096, 100)


def _checkpoint() -> DurableCheckpoint:
    context = ConversationContext()
    context.add_user_message("hello")
    context.add_assistant_message("need tool")
    return DurableCheckpoint(
        run_id="run-chat",
        checkpoint_id="chk_000007",
        sequence=7,
        phase=CheckpointPhase.MODEL_COMPLETED,
        context_snapshot=context.to_dict(),
        round_num=3,
        usage={"total_tokens": 42},
        trace_summary={},
        segment_metadata={"segment_count": 1},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=_NOW,
    )


def _context_message_count(current: Any) -> int | None:
    if current is None or current.context_snapshot is None:
        return None
    return len(current.context_snapshot.get("messages", []))


async def test_checkpoint_enabled_sets_context_during_execution_and_resets_after() -> None:
    chat = _ObservingChatService()
    coordinator = RunExecutionCoordinator(
        chat_service=chat,
        task_agent=_UnusedTaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=_FakeCheckpointStore(),
        event_store=_FakeEventStore(),
        retention_policy=_policy(),
        checkpoint_enabled=True,
    )

    await coordinator.execute(_snapshot(), _FakeProgress())

    assert chat.context_run_ids == ["run-chat"]
    assert chat.runtime_run_ids == ["run-chat"]
    assert chat.runtime_recovery_modes == [False]
    assert chat.runtime_segment_indexes == [1]
    assert get_run_execution_context() is None
    assert get_run_checkpoint_context() is None


async def test_checkpoint_disabled_does_not_set_context() -> None:
    chat = _ObservingChatService()
    coordinator = RunExecutionCoordinator(
        chat_service=chat,
        task_agent=_UnusedTaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=_FakeCheckpointStore(),
        event_store=_FakeEventStore(),
        retention_policy=_policy(),
        checkpoint_enabled=False,
    )

    await coordinator.execute(_snapshot(), _FakeProgress())

    assert chat.context_run_ids == [None]
    assert get_run_checkpoint_context() is None


async def test_checkpoint_disabled_still_sets_run_execution_context() -> None:
    chat = _ObservingChatService()
    coordinator = RunExecutionCoordinator(
        chat_service=chat,
        task_agent=_UnusedTaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=_FakeCheckpointStore(),
        event_store=_FakeEventStore(),
        retention_policy=_policy(),
        checkpoint_enabled=False,
    )

    await coordinator.execute(_snapshot(), _FakeProgress())

    assert chat.context_run_ids == [None]
    assert chat.runtime_run_ids == ["run-chat"]
    assert chat.runtime_recovery_modes == [False]
    assert chat.runtime_segment_indexes == [1]
    assert get_run_execution_context() is None
    assert get_run_checkpoint_context() is None


async def test_checkpoint_context_resets_when_execution_fails() -> None:
    chat = _ObservingChatService(fail=True)
    coordinator = RunExecutionCoordinator(
        chat_service=chat,
        task_agent=_UnusedTaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=_FakeCheckpointStore(),
        event_store=_FakeEventStore(),
        retention_policy=_policy(),
        checkpoint_enabled=True,
    )

    outcome = await coordinator.execute(_snapshot(), _FakeProgress())

    assert outcome.status is RunStatus.FAILED
    assert chat.context_run_ids == ["run-chat"]
    assert chat.runtime_run_ids == ["run-chat"]
    assert chat.runtime_recovery_modes == [False]
    assert get_run_execution_context() is None
    assert get_run_checkpoint_context() is None


async def test_recovered_chat_run_sets_run_execution_context_recovery_mode() -> None:
    checkpoint = _checkpoint()
    checkpoint_store = _FakeCheckpointStore(checkpoint)
    chat = _ObservingChatService()
    coordinator = RunExecutionCoordinator(
        chat_service=chat,
        task_agent=_UnusedTaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=checkpoint_store,
        event_store=_FakeEventStore(),
        retention_policy=_policy(),
        checkpoint_enabled=True,
    )
    snapshot = RunSnapshot(
        **{
            **_snapshot().__dict__,
            "latest_checkpoint_id": checkpoint.checkpoint_id,
        }
    )

    await coordinator.execute(snapshot, _FakeProgress())

    assert chat.runtime_run_ids == ["run-chat"]
    assert chat.runtime_recovery_modes == [True]
    assert chat.runtime_segment_indexes == [2]
    assert get_run_execution_context() is None


async def test_recovered_chat_run_loads_latest_checkpoint_and_uses_continue_path() -> None:
    checkpoint = _checkpoint()
    checkpoint_store = _FakeCheckpointStore(checkpoint)
    chat = _ObservingChatService()
    coordinator = RunExecutionCoordinator(
        chat_service=chat,
        task_agent=_UnusedTaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=checkpoint_store,
        event_store=_FakeEventStore(),
        retention_policy=_policy(),
        checkpoint_enabled=True,
    )
    snapshot = RunSnapshot(
        **{
            **_snapshot().__dict__,
            "latest_checkpoint_id": checkpoint.checkpoint_id,
        }
    )

    await coordinator.execute(snapshot, _FakeProgress())

    assert checkpoint_store.latest_calls == ["run-chat"]
    assert chat.chat_calls == 0
    assert chat.continue_calls == 1
    assert chat.restored_message_counts == [2]
    assert chat.context_run_ids == ["run-chat"]
    assert chat.recovered_message_counts == [2]
    assert get_run_execution_context() is None
    assert get_run_checkpoint_context() is None
