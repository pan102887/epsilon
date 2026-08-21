"""TUI Run view tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from typing import cast

import pytest
from rich.console import Console
from rich.text import Text

from application.cli.runtime import CliRuntime
from application.cli.tui import (
    EpsilonTextualApp,
    TuiApp,
    render_run_event,
    render_run_event_log,
    render_run_snapshot,
)
from domain.run.exceptions import RunEventReplayExpiredError
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)


class FakeRunRuntime:
    def __init__(self, *, replay_expired: bool = False) -> None:
        self.replay_expired = replay_expired
        self.get_calls: list[str] = []
        self.watch_calls: list[tuple[str, int | None]] = []
        self.cancel_calls: list[str] = []

    def default_model(self) -> str:
        return "test-model"

    async def clear_session(self, session_id: str) -> None:
        return None

    async def get_run(self, run_id: str) -> RunSnapshot:
        self.get_calls.append(run_id)
        return _snapshot(run_id, RunStatus.RUNNING, latest_event_cursor=3)

    def watch_run_events(self, run_id: str, after_cursor: int | None) -> AsyncIterator[RunEvent]:
        self.watch_calls.append((run_id, after_cursor))
        return self._watch(run_id)

    async def _watch(self, run_id: str) -> AsyncIterator[RunEvent]:
        if self.replay_expired:
            raise RunEventReplayExpiredError(run_id, 0)
        yield _event(run_id, 4, RunEventType.SEGMENT_DONE)
        yield _event(run_id, 5, RunEventType.RUN_SUCCEEDED)

    async def cancel_run(self, run_id: str) -> RunSnapshot:
        self.cancel_calls.append(run_id)
        return _snapshot(run_id, RunStatus.CANCEL_REQUESTED)


def _snapshot(
    run_id: str,
    status: RunStatus,
    *,
    latest_event_cursor: int | None = 1,
) -> RunSnapshot:
    now = datetime.now(UTC)
    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="tui-test",
        chat={"message": "hello"},
        model="test-model",
    )
    return RunSnapshot(
        run_id=run_id,
        kind=RunKind.CHAT,
        status=status,
        payload=payload,
        client_request_id="client-1",
        payload_hash=payload.stable_hash(),
        result={"content": "done"} if status is RunStatus.SUCCEEDED else None,
        error={"message": "failed"} if status is RunStatus.FAILED else None,
        approval_id="approval-1" if status is RunStatus.AWAITING_APPROVAL else None,
        segment_metadata={"segment_count": 2, "segment_stop_reason": "max_rounds"},
        latest_event_cursor=latest_event_cursor,
        can_continue=status in {RunStatus.PAUSED, RunStatus.AWAITING_APPROVAL},
        terminal_reason="done" if status is RunStatus.SUCCEEDED else None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _event(run_id: str, cursor: int, event_type: RunEventType) -> RunEvent:
    return RunEvent(
        run_id=run_id,
        cursor=cursor,
        event_type=event_type,
        payload={"summary": event_type.value},
        created_at=datetime.now(UTC),
    )


def test_render_run_snapshot_covers_all_statuses() -> None:
    rendered = "\n".join(
        render_run_snapshot(_snapshot(f"run-{status.value}", status)) for status in RunStatus
    )

    for status in RunStatus:
        assert f"status: {status.value}" in rendered
    assert "segment_count" in rendered
    assert "approval_id: approval-1" in rendered
    assert "error: failed" in rendered
    assert "result: done" in rendered


def test_render_run_snapshot_includes_recovery_fields() -> None:
    snapshot = replace(
        _snapshot("run-1", RunStatus.RUNNING),
        latest_checkpoint_id="chk_000001",
        recoverable=True,
        recovery_attempt_count=2,
        last_recovery_error={"reason": "pending_tool_replay_blocked"},
    )

    rendered = render_run_snapshot(snapshot)

    assert "latest_checkpoint_id: chk_000001" in rendered
    assert "recoverable: true" in rendered
    assert "recovery_attempt_count: 2" in rendered
    assert "last_recovery_error: pending_tool_replay_blocked" in rendered


def test_render_run_snapshot_includes_guardrail_fields() -> None:
    snapshot = replace(
        _snapshot("run-1", RunStatus.RUNNING),
        task_classification="tool_task",
        guardrail_summary={"action": "observe", "reason": "none"},
    )

    rendered = render_run_snapshot(snapshot)

    assert "task_classification: tool_task" in rendered
    assert "guardrail_summary: {'action': 'observe', 'reason': 'none'}" in rendered


def test_render_run_snapshot_reads_latest_steps_with_recent_steps_fallback() -> None:
    latest_snapshot = replace(
        _snapshot("run-1", RunStatus.RUNNING),
        collaboration_summary={
            "latest_steps": [
                {
                    "action": "handoff",
                    "target_agent": "reviewer",
                    "result_summary": "latest wins",
                }
            ],
            "recent_steps": [
                {
                    "action": "delegation",
                    "target_agent": "legacy",
                    "result_summary": "legacy ignored",
                }
            ],
        },
    )
    legacy_snapshot = replace(
        _snapshot("run-2", RunStatus.RUNNING),
        collaboration_summary={
            "recent_steps": [
                {
                    "action": "delegation",
                    "target_agent": "legacy",
                    "result_summary": "fallback works",
                }
            ],
        },
    )

    latest_rendered = render_run_snapshot(latest_snapshot)
    legacy_rendered = render_run_snapshot(legacy_snapshot)

    assert "latest_collaboration_summary:" in latest_rendered
    assert "handoff / reviewer / latest wins" in latest_rendered
    assert "legacy ignored" not in latest_rendered
    assert "latest_collaboration_summary:" in legacy_rendered
    assert "delegation / legacy / fallback works" in legacy_rendered
    assert "recent_collaboration_summary" not in latest_rendered
    assert "recent_collaboration_summary" not in legacy_rendered


def test_render_run_event_log_includes_cursor_and_type() -> None:
    events = [
        _event("run-1", 1, RunEventType.RUN_CREATED),
        _event("run-1", 2, RunEventType.REPLAY_EXPIRED),
    ]

    assert "cursor: 1" in render_run_event(events[0])
    log = render_run_event_log(events)
    assert "type: run_created" in log
    assert "type: replay_expired" in log


def test_message_renderable_omits_box_drawing_separators() -> None:
    buffer = StringIO()
    console = Console(file=buffer, width=120)

    console.print(EpsilonTextualApp.message_renderable("Tool", Text("read_file"), "yellow"))
    rendered = buffer.getvalue()

    assert "Tool" in rendered
    assert "read_file" in rendered
    assert not set("╭╮╰╯│─").intersection(rendered)


async def test_watch_replay_expired_falls_back_to_snapshot() -> None:
    runtime = FakeRunRuntime(replay_expired=True)
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.start_run_watch("run-1")
        for _ in range(20):
            await pilot.pause(0.01)
            if app.current_task is None:
                break

        assert runtime.watch_calls == [("run-1", 3)]
        assert runtime.get_calls == ["run-1", "run-1"]
        assert app.active_run_id is None


async def test_ctrl_c_active_run_requests_cancel_without_cancelling_watch_task() -> None:
    runtime = FakeRunRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    async with app.run_test(size=(100, 30)) as pilot:
        task = asyncio.create_task(wait_forever())
        app.attach_active_run_task("run-1", task)

        app.action_cancel()
        for _ in range(20):
            await pilot.pause(0.01)
            if runtime.cancel_calls:
                break

        assert runtime.cancel_calls == ["run-1"]
        assert not task.cancelled()
        task.cancel()


async def test_tui_wrapper_keeps_textual_mouse_capture_for_scrolling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_async(self: EpsilonTextualApp, **kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(EpsilonTextualApp, "run_async", fake_run_async)

    result = await TuiApp(cast(CliRuntime, FakeRunRuntime())).run()

    assert result == 0
    assert captured["mouse"] is True


async def test_copy_last_assistant_uses_textual_clipboard() -> None:
    app = EpsilonTextualApp(cast(CliRuntime, FakeRunRuntime()))
    app.set_last_assistant_text("final answer")

    await app.action_copy_last_assistant()

    assert app.clipboard_text == "final answer"


async def test_copy_transcript_includes_active_assistant_text() -> None:
    app = EpsilonTextualApp(cast(CliRuntime, FakeRunRuntime()))
    app.record_transcript("You", "hello")
    app.set_active_assistant_text("streaming answer")

    await app.action_copy_transcript()

    assert app.clipboard_text == "You:\nhello\n\nAssistant:\nstreaming answer"
