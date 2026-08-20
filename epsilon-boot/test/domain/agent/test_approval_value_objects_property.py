"""Agent 审批值对象属性测试模块。

使用 Hypothesis 验证待审批动作顺序和不可变容器语义。
"""

from dataclasses import FrozenInstanceError

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.value_objects import ApprovalInterrupt, PendingActionRequest

decision_st = st.sampled_from(["approve", "edit", "reject"])


@settings(max_examples=100, deadline=5000)
@given(tool_call_ids=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=10))
def test_pending_action_order_is_preserved(tool_call_ids: list[str]) -> None:
    """验证 PendingActionRequest 在 tuple 中的顺序稳定保留。"""
    actions = tuple(
        PendingActionRequest(
            tool_call_id=tool_call_id,
            tool_name="write_file",
            arguments="{}",
            allowed_decisions=frozenset({"approve", "reject"}),
        )
        for tool_call_id in tool_call_ids
    )

    interrupt = ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=actions,
        context_snapshot={},
        round_num=1,
        model="gpt-test",
    )

    assert tuple(action.tool_call_id for action in interrupt.actions) == tuple(tool_call_ids)


@settings(max_examples=100, deadline=5000)
@given(
    actions=st.lists(
        st.builds(
            PendingActionRequest,
            tool_call_id=st.text(min_size=1, max_size=20),
            tool_name=st.text(min_size=1, max_size=20),
            arguments=st.text(max_size=100),
            allowed_decisions=st.sets(decision_st, min_size=1).map(frozenset),
            reason=st.text(max_size=50),
        ),
        min_size=1,
        max_size=8,
    )
)
def test_approval_interrupt_actions_tuple_is_not_reassignable(
    actions: list[PendingActionRequest],
) -> None:
    """验证 ApprovalInterrupt.actions 以 tuple 保存且 frozen 字段不可重绑定。"""
    interrupt = ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=tuple(actions),
        context_snapshot={},
        round_num=1,
        model="gpt-test",
    )

    assert isinstance(interrupt.actions, tuple)
    assert interrupt.actions == tuple(actions)
    with pytest.raises(FrozenInstanceError):
        interrupt.actions = ()  # type: ignore[misc]
