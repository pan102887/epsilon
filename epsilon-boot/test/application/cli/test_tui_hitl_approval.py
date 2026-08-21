"""TUI HITL approval_required 交互测试。

需求 2.1 替换语义：收到 ``approval_required`` 事件时打开交互式
``ApprovalScreen``（而非渲染纯文本审批提示），并保持整体流程不崩溃。
"""

from collections.abc import AsyncIterator
from typing import cast

from application.cli.approval_screen import ApprovalScreen
from application.cli.session import TuiSessionState
from application.cli.runtime import CliRuntime
from application.cli.tui import EpsilonTextualApp
from domain.agent.value_objects import (
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalPolicy,
    PendingActionRequest,
)


class FakeApprovalRuntime:
    """产出 approval_required 事件并支持续播的 fake runtime。"""

    def __init__(self) -> None:
        self.resumed: list[tuple[str, str, list[ApprovalDecision]]] = []

    def default_model(self) -> str:
        return "test-model"

    async def clear_session(self, session_id: str) -> None:
        return None

    async def load_pending_actions(
        self, session_id: str, approval_id: str
    ) -> tuple[PendingActionRequest, ...]:
        return (
            PendingActionRequest(
                "call-1",
                "write_file",
                '{"path": "a.txt", "token": "secret"}',
                frozenset({"approve", "reject"}),
            ),
        )

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=True,
            allowed_decisions=frozenset({"approve", "reject"}),
            risk_label="高风险文件写入",
        )

    async def stream_main_agent_events(
        self,
        message: str,
        state: TuiSessionState,
    ) -> AsyncIterator[AgentStreamEvent]:
        yield AgentStreamEvent(
            kind="approval_required",
            metadata={
                "session_id": "s1",
                "approval_id": "a1",
                "action_summaries": [
                    {
                        "tool_name": "write_file",
                        "allowed_decisions": ["approve", "reject"],
                    }
                ],
            },
        )

    async def resume_main_agent_events(
        self,
        session_id: str,
        approval_id: str,
        decisions: list[ApprovalDecision],
        *,
        model: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.resumed.append((session_id, approval_id, decisions))
        yield AgentStreamEvent(kind="assistant_delta", content="resumed")
        yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 1})


async def test_tui_opens_approval_screen_on_approval_required() -> None:
    """验证收到 approval_required 时打开 ApprovalScreen 并可提交决策续播。"""
    runtime = FakeApprovalRuntime()
    app = EpsilonTextualApp(cast(CliRuntime, runtime))

    async with app.run_test(size=(100, 30)) as pilot:
        app.set_composer_text("write")
        await app.action_submit()

        screen: ApprovalScreen | None = None
        for _ in range(50):
            await pilot.pause(0.01)
            top = app.screen_stack[-1]
            if isinstance(top, ApprovalScreen):
                screen = top
                break
        assert screen is not None

        screen.action_approve()

        for _ in range(50):
            await pilot.pause(0.01)
            if app.current_task is None:
                break

    assert app.current_task is None
    assert runtime.resumed == [("s1", "a1", [ApprovalDecision("approve", "call-1")])]
