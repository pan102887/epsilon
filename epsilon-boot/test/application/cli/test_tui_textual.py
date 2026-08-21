"""Textual TUI tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from application.cli.runtime import CliRuntime, ResumeSessionResult
from application.cli.session import TuiSessionState
from application.cli.tui import EpsilonTextualApp
from domain.agent.value_objects import AgentStreamEvent
from domain.chat.value_objects import SessionMetadata


def test_textual_tui_loads_external_css_file() -> None:
    assert EpsilonTextualApp.CSS_PATH == "tui.css"
    assert "CSS" not in EpsilonTextualApp.__dict__


class FakeRuntime:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.cleared: list[str] = []
        self.deleted: list[str] = []
        self.resumed: list[str] = []

    def default_model(self) -> str:
        return "test-model"

    async def clear_session(self, session_id: str) -> None:
        self.cleared.append(session_id)

    async def list_sessions(self, limit: int = 20) -> list[SessionMetadata]:
        return [
            SessionMetadata(
                session_id="tui-existing",
                updated_at_epoch_ms=1000,
                message_count=2,
                preview="existing",
            )
        ][:limit]

    async def resume_session(self, session_id: str) -> ResumeSessionResult:
        self.resumed.append(session_id)
        return ResumeSessionResult(
            found=True,
            metadata=SessionMetadata(
                session_id=session_id,
                updated_at_epoch_ms=1000,
                message_count=2,
                preview="existing",
            ),
            approval_summaries=[],
        )

    async def delete_session(self, session_id: str) -> bool:
        self.deleted.append(session_id)
        return True

    async def stream_main_agent_events(
        self,
        message: str,
        state: TuiSessionState,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.messages.append(message)
        yield AgentStreamEvent(
            kind="tool_start",
            tool_name="lookup",
            tool_call_id="call-1",
            arguments='{"query": "hello"}',
        )
        yield AgentStreamEvent(
            kind="tool_result",
            tool_name="lookup",
            tool_call_id="call-1",
            content="tool ok",
        )
        yield AgentStreamEvent(kind="assistant_delta", content="hello")
        yield AgentStreamEvent(kind="assistant_delta", content=" world")
        yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 3})


async def test_textual_tui_submits_message_and_renders_events() -> None:
    runtime = FakeRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.set_composer_text("hello")
        await app.action_submit()

        for _ in range(20):
            await pilot.pause(0.01)
            if app.current_task is None:
                break

        assert runtime.messages == ["hello"]
        assert app.current_task is None
        assert len(app.query(".message")) >= 4


async def test_textual_tui_routes_slash_commands() -> None:
    runtime = FakeRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.set_composer_text("/model qwen3")
        await app.action_submit()
        await pilot.pause(0.01)

        assert app.session_state.model == "qwen3"
        assert runtime.messages == []


async def test_textual_tui_new_keeps_old_session_resumable() -> None:
    runtime = FakeRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        old_session_id = app.session_state.session_id

        app.set_composer_text("/new")
        await app.action_submit()
        await pilot.pause(0.01)

        assert app.session_state.session_id != old_session_id
        assert runtime.cleared == []
        assert runtime.deleted == []
        assert runtime.messages == []

        app.set_composer_text(f"/resume {old_session_id}")
        await app.action_submit()
        await pilot.pause(0.01)

        assert app.session_state.session_id == old_session_id
        assert runtime.resumed == [old_session_id]
        assert runtime.messages == []
