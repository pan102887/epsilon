"""Workflow 角色能力属性测试。

本模块验证 P2 最小权限治理的纯领域不变量：未声明的工具、委派、
handoff 与 child run 默认拒绝；活动角色切换后必须重新读取对应角色能力，
不得沿用上一角色的判定结果。
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.run.workflow import (
    AgentRoleCapability,
    WorkflowCapabilityAction,
    WorkflowCapabilityCheck,
    evaluate_role_capability,
)

_ACTIONS = st.sampled_from(tuple(WorkflowCapabilityAction))
_TARGETS = st.from_regex(r"[a-z][a-z0-9_]{0,19}", fullmatch=True)


@settings(max_examples=80, deadline=5000)
@given(action=_ACTIONS, target=_TARGETS)
def test_empty_capability_set_denies_every_undeclared_action(
    action: WorkflowCapabilityAction,
    target: str,
) -> None:
    """空 capability 集合必须默认拒绝所有未声明动作。"""

    decision = evaluate_role_capability(
        roles=(),
        check=WorkflowCapabilityCheck(action=action, role="executor", target=target),
    )

    assert decision.allowed is False
    assert decision.reason == "role_capability_missing"
    assert decision.action is action
    assert decision.target == target


@settings(max_examples=80, deadline=5000)
@given(
    tool_name=_TARGETS,
    delegate_agent=_TARGETS,
    handoff_agent=_TARGETS,
)
def test_role_switch_reloads_capability_without_cache_leak(
    tool_name: str,
    delegate_agent: str,
    handoff_agent: str,
) -> None:
    """切换 active_role 后应按新角色能力判定，旧角色拒绝结果不得泄漏。"""

    roles = (
        AgentRoleCapability("planner"),
        AgentRoleCapability(
            "executor",
            allowed_tool_names=frozenset({tool_name}),
            can_delegate=True,
            allowed_delegate_agents=frozenset({delegate_agent}),
            can_handoff=True,
            allowed_handoff_agents=frozenset({handoff_agent}),
            can_create_child_run=True,
        ),
    )

    planner_tool = evaluate_role_capability(
        roles=roles,
        check=WorkflowCapabilityCheck(
            action=WorkflowCapabilityAction.TOOL,
            role="planner",
            target=tool_name,
        ),
    )
    executor_tool = evaluate_role_capability(
        roles=roles,
        check=WorkflowCapabilityCheck(
            action=WorkflowCapabilityAction.TOOL,
            role="executor",
            target=tool_name,
        ),
    )
    executor_delegate = evaluate_role_capability(
        roles=roles,
        check=WorkflowCapabilityCheck(
            action=WorkflowCapabilityAction.DELEGATION,
            role="executor",
            target=delegate_agent,
        ),
    )
    executor_handoff = evaluate_role_capability(
        roles=roles,
        check=WorkflowCapabilityCheck(
            action=WorkflowCapabilityAction.HANDOFF,
            role="executor",
            target=handoff_agent,
        ),
    )
    executor_child_run = evaluate_role_capability(
        roles=roles,
        check=WorkflowCapabilityCheck(
            action=WorkflowCapabilityAction.CHILD_RUN,
            role="executor",
            target="child",
        ),
    )

    assert planner_tool.allowed is False
    assert executor_tool.allowed is True
    assert executor_delegate.allowed is True
    assert executor_handoff.allowed is True
    assert executor_child_run.allowed is True
