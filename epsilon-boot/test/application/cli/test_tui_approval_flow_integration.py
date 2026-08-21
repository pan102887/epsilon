"""TUI inline 审批闭环集成测试。

用假 runtime 驱动 :class:`_EpsilonTextualApp` 的 ``run_test()``，覆盖设计
§inline 审批闭环时序与需求 2.1–2.3 / 4.1–4.4：

- 首次 ``approval_required`` → 打开 ``ApprovalScreen`` → 提交 approve →
  ``resume_main_agent_events`` 续播 ``assistant_delta`` / ``assistant_done``
  → 再次 ``approval_required`` → 再次打开面板（闭环，4.1/4.2）；
- 续播流 ``kind="error"`` 结束本轮续播（4.4）；
- 进行中取消不使会话进入不可恢复状态（4.3）；
- 面板逐条决策按 actions 顺序展示与产出（2.1–2.3）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from io import StringIO
from typing import cast

from rich.console import Console
from textual.pilot import Pilot
from textual.widget import Widget

from application.cli.approval_screen import ApprovalScreen
from application.cli.runtime import CliRuntime
from application.cli.session import TuiSessionState
from application.cli.tui import EpsilonTextualApp
from domain.agent.value_objects import (
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalPolicy,
    PendingActionRequest,
)


def _approval_event(session_id: str, approval_id: str) -> AgentStreamEvent:
    """构造一个 approval_required 事件。"""
    return AgentStreamEvent(
        kind="approval_required",
        metadata={
            "session_id": session_id,
            "approval_id": approval_id,
            "action_summaries": [],
        },
    )


def _action(tool_call_id: str, tool_name: str) -> PendingActionRequest:
    """构造一个待审批动作。"""
    return PendingActionRequest(
        tool_call_id,
        tool_name,
        '{"path": "a.txt"}',
        frozenset({"approve", "reject"}),
    )


class _BaseRuntime:
    """集成测试用假 runtime 基类，提供高风险策略与批次读取。"""

    def __init__(self, actions_by_id: dict[str, tuple[PendingActionRequest, ...]]) -> None:
        self._actions_by_id = actions_by_id
        self.resume_calls: list[tuple[str, str, list[ApprovalDecision]]] = []

    def default_model(self) -> str:
        return "test-model"

    async def clear_session(self, session_id: str) -> None:
        return None

    async def load_pending_actions(
        self, session_id: str, approval_id: str
    ) -> tuple[PendingActionRequest, ...]:
        return self._actions_by_id.get(approval_id, ())

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=True,
            allowed_decisions=frozenset({"approve", "reject"}),
            risk_label="高风险",
        )


def _widget_text(widget: Widget) -> str:
    """把消息组件承载的 rich renderable 渲染为纯文本，供断言检索内容。"""
    rendered = widget.render()
    renderable = getattr(rendered, "_renderable", rendered)
    buffer = StringIO()
    console = Console(file=buffer, width=200)
    console.print(renderable)
    return buffer.getvalue()


async def _wait_for_screen(
    app: EpsilonTextualApp, pilot: Pilot[int]
) -> ApprovalScreen | None:
    """轮询等待 ApprovalScreen 出现在屏幕栈顶。"""
    for _ in range(60):
        await pilot.pause(0.01)
        top = app.screen_stack[-1]
        if isinstance(top, ApprovalScreen):
            return top
    return None


async def _wait_idle(app: EpsilonTextualApp, pilot: Pilot[int]) -> None:
    """轮询等待当前交互任务结束。"""
    for _ in range(80):
        await pilot.pause(0.01)
        if app.current_task is None:
            return


class _ClosedLoopRuntime(_BaseRuntime):
    """首次中断 → approve 续播 → 再次中断 → 再次打开面板的闭环 runtime。"""

    def __init__(self) -> None:
        super().__init__(
            {
                "a1": (_action("call-1", "write_file"),),
                "a2": (_action("call-2", "shell_exec"),),
            }
        )

    async def stream_main_agent_events(
        self, message: str, state: TuiSessionState
    ) -> AsyncIterator[AgentStreamEvent]:
        yield _approval_event("s1", "a1")

    async def resume_main_agent_events(
        self,
        session_id: str,
        approval_id: str,
        decisions: list[ApprovalDecision],
        *,
        model: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.resume_calls.append((session_id, approval_id, decisions))
        if approval_id == "a1":
            yield AgentStreamEvent(kind="assistant_delta", content="continuing")
            yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 2})
            yield _approval_event("s1", "a2")
        else:
            yield AgentStreamEvent(kind="assistant_delta", content="finished")
            yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 3})


async def test_approval_flow_closed_loop_reopens_panel() -> None:
    """验证首次审批→approve 续播→再次审批→再次打开面板的闭环（4.1/4.2）。"""
    runtime = _ClosedLoopRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.set_composer_text("write")
        await app.action_submit()

        first = await _wait_for_screen(app, pilot)
        assert first is not None
        assert first.actions[0].tool_call_id == "call-1"
        first.action_approve()

        second = await _wait_for_screen(app, pilot)
        assert second is not None
        assert second.actions[0].tool_call_id == "call-2"
        second.action_approve()

        await _wait_idle(app, pilot)

    assert app.current_task is None
    assert [call[1] for call in runtime.resume_calls] == ["a1", "a2"]
    assert runtime.resume_calls[0][2] == [ApprovalDecision("approve", "call-1")]
    assert runtime.resume_calls[1][2] == [ApprovalDecision("approve", "call-2")]


class _ErrorResumeRuntime(_BaseRuntime):
    """续播流产出 kind="error" 的 runtime。"""

    def __init__(self) -> None:
        super().__init__({"a1": (_action("call-1", "write_file"),)})

    async def stream_main_agent_events(
        self, message: str, state: TuiSessionState
    ) -> AsyncIterator[AgentStreamEvent]:
        yield _approval_event("s1", "a1")

    async def resume_main_agent_events(
        self,
        session_id: str,
        approval_id: str,
        decisions: list[ApprovalDecision],
        *,
        model: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.resume_calls.append((session_id, approval_id, decisions))
        yield AgentStreamEvent(kind="error", content="resume failed")


async def test_approval_flow_resume_error_ends_stream() -> None:
    """验证续播流 kind="error" 由既有 error 分支渲染并结束续播（4.4）。"""
    runtime = _ErrorResumeRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.set_composer_text("write")
        await app.action_submit()

        screen = await _wait_for_screen(app, pilot)
        assert screen is not None
        screen.action_approve()

        await _wait_idle(app, pilot)

        assert app.current_task is None
        assert len(runtime.resume_calls) == 1
        errors = [_widget_text(w) for w in app.query(".error")]
        assert any("resume failed" in text for text in errors)


class _CancelRuntime(_BaseRuntime):
    """在续播过程中长时间挂起，用于验证进行中取消的 runtime。"""

    def __init__(self) -> None:
        super().__init__({"a1": (_action("call-1", "write_file"),)})

    async def stream_main_agent_events(
        self, message: str, state: TuiSessionState
    ) -> AsyncIterator[AgentStreamEvent]:
        yield _approval_event("s1", "a1")

    async def resume_main_agent_events(
        self,
        session_id: str,
        approval_id: str,
        decisions: list[ApprovalDecision],
        *,
        model: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.resume_calls.append((session_id, approval_id, decisions))
        yield AgentStreamEvent(kind="assistant_delta", content="working")
        await asyncio.sleep(10)
        yield AgentStreamEvent(kind="assistant_done")


async def test_approval_flow_cancel_during_resume_keeps_recoverable() -> None:
    """验证进行中取消复用既有取消路径、会话不进入不可恢复状态（4.3）。"""
    runtime = _CancelRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        session_id = app.session_state.session_id
        app.set_composer_text("write")
        await app.action_submit()

        screen = await _wait_for_screen(app, pilot)
        assert screen is not None
        screen.action_approve()

        # 等待续播进入挂起状态后触发取消。
        for _ in range(30):
            await pilot.pause(0.01)
            if runtime.resume_calls:
                break
        await pilot.pause(0.02)
        app.action_cancel()

        await _wait_idle(app, pilot)

        assert app.current_task is None
        # 会话 id 未被清理或替换，仍可再次恢复。
        assert app.session_state.session_id == session_id
        cancelled = [_widget_text(w) for w in app.query(".message")]
        assert any("已中止" in text for text in cancelled)


class _MultiActionRuntime(_BaseRuntime):
    """单批次多待审批动作的 runtime，用于逐条决策展示验证。"""

    def __init__(self) -> None:
        super().__init__(
            {"a1": (_action("call-1", "write_file"), _action("call-2", "shell_exec"))}
        )

    async def stream_main_agent_events(
        self, message: str, state: TuiSessionState
    ) -> AsyncIterator[AgentStreamEvent]:
        yield _approval_event("s1", "a1")

    async def resume_main_agent_events(
        self,
        session_id: str,
        approval_id: str,
        decisions: list[ApprovalDecision],
        *,
        model: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.resume_calls.append((session_id, approval_id, decisions))
        yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 1})


async def test_approval_flow_multi_action_per_decision() -> None:
    """验证单批次多动作逐条推进并产出与顺序一致的决策序列（2.1–2.3）。"""
    runtime = _MultiActionRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.set_composer_text("write")
        await app.action_submit()

        screen = await _wait_for_screen(app, pilot)
        assert screen is not None
        assert len(screen.actions) == 2
        # 第一条 approve，面板逐条推进到第二条。
        screen.action_approve()
        assert screen.current_index == 1
        # 第二条 reject，全部完成后关闭面板并续播。
        screen.action_reject()

        await _wait_idle(app, pilot)

    assert app.current_task is None
    assert len(runtime.resume_calls) == 1
    decisions = runtime.resume_calls[0][2]
    assert [d.type for d in decisions] == ["approve", "reject"]
    assert [d.tool_call_id for d in decisions] == ["call-1", "call-2"]
