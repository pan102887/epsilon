"""ApprovalScreen 交互式审批面板单元测试。

沿用本仓库 Textual 测试模式（``app.run_test()`` / ``pilot.pause()``），
通过将 :class:`ApprovalScreen` 以 ``push_screen`` 挂载到一个最小宿主 App，
并直接调用其 ``action_*`` 方法驱动决策状态机，验证：

- 逐条推进顺序与 ``actions`` 一致、产出顺序即 ``actions`` 顺序（Property 1）；
- ``allowed_decisions`` 不含某类型时该决策被忽略（需求 2.4）；
- edit 子状态预填原 ``arguments``（需求 3.1）；
- edit 非法 JSON 原地报错、不推进不关面板不提交（需求 3.2/3.3）；
- edit 合法 JSON 构造 ``EditedAction(name == 原 tool_name)``（需求 3.4）；
- Esc 取消 ``dismiss(None)``（需求 4.3 语义）。
"""

from __future__ import annotations

from textual.app import App

from application.cli.approval_screen import ApprovalScreen
from domain.agent.value_objects import (
    ApprovalDecision,
    ApprovalDecisionType,
    PendingActionRequest,
)


class _HostApp(App[int]):
    """用于承载 ApprovalScreen 的最小宿主 App。"""


def _make_action(
    tool_call_id: str,
    tool_name: str,
    arguments: str,
    allowed: tuple[ApprovalDecisionType, ...],
) -> PendingActionRequest:
    """构造测试用 PendingActionRequest。"""
    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
        allowed_decisions=frozenset(allowed),
    )


async def test_sequential_decisions_preserve_action_order() -> None:
    """验证逐条推进顺序与 actions 一致、产出顺序即 actions 顺序（Property 1）。"""
    actions = (
        _make_action("call-1", "write_file", '{"path": "a.txt"}', ("approve", "reject")),
        _make_action("call-2", "shell_exec", '{"cmd": "ls"}', ("approve", "reject")),
    )
    result: list[list[ApprovalDecision] | None] = []
    app = _HostApp()

    async with app.run_test(size=(100, 30)):
        screen = ApprovalScreen(actions, {})
        await app.push_screen(screen, result.append)
        screen.action_approve()
        screen.action_reject()

    assert len(result) == 1
    decisions = result[0]
    assert decisions is not None
    assert [d.type for d in decisions] == ["approve", "reject"]
    assert [d.tool_call_id for d in decisions] == ["call-1", "call-2"]


async def test_disallowed_decision_is_ignored() -> None:
    """验证 allowed_decisions 不含某类型时该决策被忽略（需求 2.4）。"""
    actions = (_make_action("call-1", "write_file", "{}", ("reject",)),)
    result: list[list[ApprovalDecision] | None] = []
    app = _HostApp()

    async with app.run_test(size=(100, 30)):
        screen = ApprovalScreen(actions, {})
        await app.push_screen(screen, result.append)
        # approve 不在 allowed_decisions 内，应被忽略：不推进、不 dismiss。
        screen.action_approve()
        assert result == []
        assert screen.current_index == 0
        # 允许的 reject 决策正常推进并完成。
        screen.action_reject()

    assert len(result) == 1
    decisions = result[0]
    assert decisions is not None
    assert [d.type for d in decisions] == ["reject"]


async def test_edit_prefills_original_arguments() -> None:
    """验证 edit 子状态预填原 arguments（需求 3.1）。"""
    actions = (_make_action("call-1", "write_file", '{"path": "a.txt"}', ("edit",)),)
    app = _HostApp()

    async with app.run_test(size=(100, 30)):
        screen = ApprovalScreen(actions, {})
        await app.push_screen(screen)
        screen.action_edit()
        await screen.recompose()
        from textual.widgets import TextArea

        editor = screen.query_one("#approval-editor", TextArea)
        assert editor.text == '{"path": "a.txt"}'
        assert screen.editing is True


async def test_edit_invalid_json_stays_open_and_does_not_advance() -> None:
    """验证 edit 非法 JSON 原地报错、不推进不关面板不提交（需求 3.2/3.3）。"""
    actions = (_make_action("call-1", "write_file", "{}", ("edit",)),)
    result: list[list[ApprovalDecision] | None] = []
    app = _HostApp()

    async with app.run_test(size=(100, 30)):
        screen = ApprovalScreen(actions, {})
        await app.push_screen(screen, result.append)
        screen.action_edit()
        await screen.recompose()
        from textual.widgets import TextArea

        editor = screen.query_one("#approval-editor", TextArea)
        editor.text = "{not-json"
        screen.action_submit_edit()
        await screen.recompose()

        # 不推进、不 dismiss、不提交，保留 editing 子状态并展示错误。
        assert result == []
        assert screen.current_index == 0
        assert screen.editing is True
        assert screen.decisions == []
        assert screen.error_text != ""


async def test_edit_valid_json_builds_edited_action() -> None:
    """验证 edit 合法 JSON 构造 EditedAction(name == 原 tool_name)（需求 3.4）。"""
    actions = (_make_action("call-1", "write_file", '{"path": "a.txt"}', ("edit",)),)
    result: list[list[ApprovalDecision] | None] = []
    app = _HostApp()

    async with app.run_test(size=(100, 30)):
        screen = ApprovalScreen(actions, {})
        await app.push_screen(screen, result.append)
        screen.action_edit()
        await screen.recompose()
        from textual.widgets import TextArea

        editor = screen.query_one("#approval-editor", TextArea)
        editor.text = '{"path": "b.txt"}'
        screen.action_submit_edit()

    assert len(result) == 1
    decisions = result[0]
    assert decisions is not None
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.type == "edit"
    assert decision.tool_call_id == "call-1"
    assert decision.edited_action is not None
    assert decision.edited_action.name == "write_file"
    assert decision.edited_action.arguments == '{"path": "b.txt"}'


async def test_cancel_dismisses_none() -> None:
    """验证 Esc 取消 dismiss(None)（需求 4.3 语义）。"""
    actions = (_make_action("call-1", "write_file", "{}", ("approve", "reject")),)
    result: list[list[ApprovalDecision] | None] = []
    app = _HostApp()

    async with app.run_test(size=(100, 30)):
        screen = ApprovalScreen(actions, {})
        await app.push_screen(screen, result.append)
        screen.action_cancel()

    assert result == [None]
