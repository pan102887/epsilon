"""TUI 交互式审批面板模块。

本模块提供 Textual ``ModalScreen`` 子类 :class:`ApprovalScreen`，
用于在本地终端界面对一批待审批工具动作（``PendingActionRequest``）
逐条采集人工决策（approve / edit / reject），并对 ``edit`` 决策的
JSON 参数做原地校验。

职责边界（遵循 SRP，见 ``docs/steering/srp-principle.md``）：本面板
**只负责决策采集与 JSON 校验**，不做审批恢复编排，也不引用
``CliRuntime`` 或任何领域 Port——恢复编排由 ``CliRuntime`` /
``ChatServiceAdapter`` 承担。全部完成后 ``dismiss(list[ApprovalDecision])``；
用户取消（Esc）时 ``dismiss(None)`` 以中止本轮恢复。
"""

from __future__ import annotations

import json
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from domain.agent.value_objects import ApprovalDecision, EditedAction, PendingActionRequest


class ApprovalScreen(ModalScreen[list[ApprovalDecision] | None]):
    """交互式审批面板。

    构造入参 ``actions`` 为完整待审批动作（含 ``arguments`` 与
    ``allowed_decisions``），以及按工具名解析出的 ``risk_labels`` 映射。
    面板按 ``actions`` 索引 0..N-1 逐条推进决策（决策顺序与动作顺序
    严格一致，见正确性属性 1），全部完成后 ``dismiss(list[ApprovalDecision])``；
    用户取消时 ``dismiss(None)``。
    """

    BINDINGS: ClassVar = [
        ("a", "approve", "Approve"),
        ("e", "edit", "Edit"),
        ("r", "reject", "Reject"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        actions: tuple[PendingActionRequest, ...],
        risk_labels: dict[str, str],
    ) -> None:
        """初始化面板并置零当前决策游标。

        Args:
            actions: 完整待审批动作序列，逐条采集决策的数据源。
            risk_labels: 工具名到风险标签的映射，仅用于展示。
        """
        super().__init__()
        self._actions = actions
        self._risk_labels = risk_labels
        self._decisions: list[ApprovalDecision] = []
        self._index = 0
        self._editing = False
        self._error_text = ""

    @property
    def actions(self) -> tuple[PendingActionRequest, ...]:
        """返回本批次按展示顺序排列的待审批动作。"""
        return self._actions

    @property
    def current_index(self) -> int:
        """返回当前待决策动作索引。"""
        return self._index

    @property
    def editing(self) -> bool:
        return self._editing

    @property
    def decisions(self) -> list[ApprovalDecision]:
        return list(self._decisions)

    @property
    def error_text(self) -> str:
        return self._error_text

    def compose(self) -> ComposeResult:
        """组合当前待审批动作的展示区与（edit 子状态时的）JSON 编辑区。

        展示当前 ``actions[_index]`` 的 ``tool_name``、``risk_label``、
        ``arguments`` 与 ``allowed_decisions``；处于 edit 子状态时额外
        挂载一个预填当前 ``arguments`` 的 ``TextArea`` 供人工编辑，并在
        存在校验错误时展示错误原因。

        当游标已越过全部动作（面板正在 dismiss 收尾）时，不再渲染动作
        详情，避免越界访问。
        """
        if self._index >= len(self._actions):
            return
        action = self._actions[self._index]
        risk_label = self._risk_labels.get(action.tool_name, "")
        allowed = sorted(action.allowed_decisions)
        lines = [
            f"待审批动作 {self._index + 1}/{len(self._actions)}",
            f"tool_name: {action.tool_name}",
            f"risk_label: {risk_label}",
            f"allowed_decisions: {allowed}",
            f"arguments: {action.arguments}",
        ]
        if action.reason:
            lines.append(f"reason: {action.reason}")
        with Vertical(id="approval-dialog"):
            yield Static("\n".join(lines), id="approval-info")
            if self._editing:
                yield TextArea(action.arguments, id="approval-editor")
                if self._error_text:
                    yield Static(self._error_text, id="approval-error", classes="error")

    def action_approve(self) -> None:
        """对当前动作提交 approve 决策并推进（不含 approve 时忽略）。"""
        if not self._decision_allowed("approve"):
            return
        action = self._actions[self._index]
        self._advance_or_finish(
            ApprovalDecision(type="approve", tool_call_id=action.tool_call_id)
        )

    def action_reject(self) -> None:
        """对当前动作提交 reject 决策并推进（不含 reject 时忽略）。"""
        if not self._decision_allowed("reject"):
            return
        action = self._actions[self._index]
        self._advance_or_finish(
            ApprovalDecision(type="reject", tool_call_id=action.tool_call_id)
        )

    def action_edit(self) -> None:
        """进入 edit 子状态，展示预填当前 arguments 的可编辑区（不含 edit 时忽略）。"""
        if not self._decision_allowed("edit"):
            return
        self._editing = True
        self._error_text = ""
        self.refresh(recompose=True)

    def action_submit_edit(self) -> None:
        """校验编辑区 JSON；失败原地报错不推进，成功则构造 edit 决策并推进。

        对编辑区文本执行 ``json.loads`` 校验：解析失败时在面板内原地展示
        ``str(exc)``、保留 ``_editing=True``、不推进不 ``dismiss`` 不提交
        （见正确性属性 3）；解析成功则以校验通过文本构造 ``EditedAction``
        （``name`` 恒等于原 ``tool_name``）并推进。
        """
        if not self._editing:
            return
        editor = self.query_one("#approval-editor", TextArea)
        text = editor.text
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            self._error_text = str(exc)
            self.refresh(recompose=True)
            return
        action = self._actions[self._index]
        self._editing = False
        self._error_text = ""
        self._advance_or_finish(
            ApprovalDecision(
                type="edit",
                tool_call_id=action.tool_call_id,
                edited_action=EditedAction(name=action.tool_name, arguments=text),
            )
        )

    def action_cancel(self) -> None:
        """取消整个审批：dismiss(None)，语义为中止本轮恢复。"""
        self.dismiss(None)

    def _advance_or_finish(self, decision: ApprovalDecision) -> None:
        """记录决策，游标 +1；若已覆盖全部动作则 dismiss(self._decisions)。

        产出的 ``list[ApprovalDecision]`` 顺序即 ``actions`` 顺序
        （见正确性属性 1）。

        Args:
            decision: 当前动作采集到的决策。
        """
        self._decisions.append(decision)
        self._index += 1
        if self._index >= len(self._actions):
            self.dismiss(self._decisions)
            return
        self._editing = False
        self._error_text = ""
        self.refresh(recompose=True)

    def _decision_allowed(self, decision_type: str) -> bool:
        """判断决策类型是否在当前动作的 allowed_decisions 内。

        Args:
            decision_type: 待判定的决策类型字符串。

        Returns:
            决策类型在当前动作允许集合内时返回 True，否则 False。
        """
        return decision_type in self._actions[self._index].allowed_decisions
