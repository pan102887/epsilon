"""``evaluate_approval_mode`` 纯函数单元测试。

覆盖需求 6.1/6.4/6.5/6.6：manual 恒需人工审批；auto 仅在整批低风险时
自动放行且顺序与 actions 一致；auto 含任一高风险时强制打开面板；ask 与
非法值均需人工审批；判定只依赖注入的 ``policy_for``。
"""

from __future__ import annotations

from collections.abc import Callable

from application.cli.approval_mode import APPROVAL_MODES, evaluate_approval_mode
from domain.agent.value_objects import (
    ApprovalDecisionType,
    ApprovalPolicy,
    PendingActionRequest,
)

_APPROVE_REJECT: frozenset[ApprovalDecisionType] = frozenset({"approve", "reject"})


def _make_action(
    tool_call_id: str,
    tool_name: str,
    *,
    allowed: frozenset[ApprovalDecisionType] = _APPROVE_REJECT,
) -> PendingActionRequest:
    """构造一个待审批动作值对象，供测试驱动。"""
    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments="{}",
        allowed_decisions=allowed,
    )


def _policy_for_map(
    policies: dict[str, ApprovalPolicy],
) -> tuple[Callable[[str], ApprovalPolicy], list[str]]:
    """基于给定映射构造记录调用的假 ``policy_for``。

    返回 ``(callable, calls)``：``callable`` 依据工具名查表返回策略并把
    查询到的工具名记入 ``calls``，用于断言判定只依赖注入的 ``policy_for``。
    """
    calls: list[str] = []

    def _policy_for(tool_name: str) -> ApprovalPolicy:
        calls.append(tool_name)
        return policies[tool_name]

    return _policy_for, calls


def _low_risk(tool_name: str) -> ApprovalPolicy:
    """构造低风险（不中断）策略。"""
    allowed: frozenset[ApprovalDecisionType] = frozenset({"approve", "reject"})
    return ApprovalPolicy(
        tool_name=tool_name,
        interrupt=False,
        allowed_decisions=allowed,
        risk_label="low",
    )


def _high_risk(tool_name: str) -> ApprovalPolicy:
    """构造高风险（中断）策略。"""
    allowed: frozenset[ApprovalDecisionType] = frozenset({"approve", "edit", "reject"})
    return ApprovalPolicy(
        tool_name=tool_name,
        interrupt=True,
        allowed_decisions=allowed,
        risk_label="high",
    )


def test_manual_mode_always_returns_none() -> None:
    """manual 模式对整批低风险动作也恒返回 None（要求人工审批）。"""
    actions = (
        _make_action("call_1", "read_file"),
        _make_action("call_2", "list_dir"),
    )
    policy_for, calls = _policy_for_map(
        {"read_file": _low_risk("read_file"), "list_dir": _low_risk("list_dir")}
    )

    result = evaluate_approval_mode("manual", actions, policy_for)

    assert result is None
    # manual 恒需人工审批，函数不应据风险做区分，无需查询策略
    assert calls == []


def test_auto_mode_all_low_risk_returns_ordered_approvals() -> None:
    """auto 模式整批低风险时返回与 actions 顺序一致的全 approve 序列。"""
    actions = (
        _make_action("call_a", "read_file"),
        _make_action("call_b", "list_dir"),
        _make_action("call_c", "search"),
    )
    policy_for, _ = _policy_for_map(
        {
            "read_file": _low_risk("read_file"),
            "list_dir": _low_risk("list_dir"),
            "search": _low_risk("search"),
        }
    )

    result = evaluate_approval_mode("auto", actions, policy_for)

    assert result is not None
    assert [d.type for d in result] == ["approve", "approve", "approve"]
    # 顺序与 tool_call_id 必须与 actions 严格一致
    assert [d.tool_call_id for d in result] == ["call_a", "call_b", "call_c"]


def test_auto_mode_any_high_risk_returns_none() -> None:
    """auto 模式含任一高风险动作即返回 None，强制打开面板。"""
    actions = (
        _make_action("call_1", "read_file"),
        _make_action("call_2", "shell_exec"),
    )
    policy_for, _ = _policy_for_map(
        {"read_file": _low_risk("read_file"), "shell_exec": _high_risk("shell_exec")}
    )

    result = evaluate_approval_mode("auto", actions, policy_for)

    assert result is None


def test_auto_mode_missing_approve_returns_none() -> None:
    """auto 模式下低风险但不允许 approve 的动作也返回 None。"""
    reject_only: frozenset[ApprovalDecisionType] = frozenset({"reject"})
    actions = (_make_action("call_1", "read_file", allowed=reject_only),)
    policy_for, _ = _policy_for_map({"read_file": _low_risk("read_file")})

    result = evaluate_approval_mode("auto", actions, policy_for)

    assert result is None


def test_ask_and_invalid_modes_return_none() -> None:
    """ask 与任意非法模式值均返回 None（按后端策略走人工审批）。"""
    actions = (_make_action("call_1", "read_file"),)
    policy_for, calls = _policy_for_map({"read_file": _low_risk("read_file")})

    assert evaluate_approval_mode("ask", actions, policy_for) is None
    assert evaluate_approval_mode("unknown", actions, policy_for) is None
    assert evaluate_approval_mode("", actions, policy_for) is None
    # 非 auto 分支不应查询策略
    assert calls == []


def test_decision_relies_only_on_injected_policy_for() -> None:
    """同一工具名下，判定结果完全由注入的 policy_for 返回的策略决定。"""
    actions = (_make_action("call_1", "shell_exec"),)

    # 注入低风险策略：应自动放行
    low_policy_for, low_calls = _policy_for_map({"shell_exec": _low_risk("shell_exec")})
    low_result = evaluate_approval_mode("auto", actions, low_policy_for)
    assert low_result is not None
    assert low_calls == ["shell_exec"]

    # 同一工具名注入高风险策略：应打开面板
    high_policy_for, high_calls = _policy_for_map({"shell_exec": _high_risk("shell_exec")})
    high_result = evaluate_approval_mode("auto", actions, high_policy_for)
    assert high_result is None
    assert high_calls == ["shell_exec"]


def test_approval_modes_constant_shape() -> None:
    """取值域常量与需求 6.1 定义的三档一致，供 commands.py 复用。"""
    assert frozenset({"ask", "auto", "manual"}) == APPROVAL_MODES
