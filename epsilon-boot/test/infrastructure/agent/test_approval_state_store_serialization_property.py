"""审批状态序列化属性测试模块。"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.agent.value_objects import ApprovalInterrupt, PendingActionRequest
from infrastructure.agent.approval_state_store import (
    approval_interrupt_from_dict,
    approval_interrupt_to_dict,
)


@settings(max_examples=100, deadline=5000)
@given(tool_call_ids=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=8))
def test_approval_interrupt_serialization_preserves_order_and_snapshot(
    tool_call_ids: list[str],
) -> None:
    """验证 ApprovalInterrupt 序列化往返保持 action 顺序和 context_snapshot。"""
    context_snapshot = {"messages": [{"role": "assistant", "content": "hi"}]}
    interrupt = ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=tuple(
            PendingActionRequest(
                tool_call_id=tool_call_id,
                tool_name="write_file",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            )
            for tool_call_id in tool_call_ids
        ),
        context_snapshot=context_snapshot,
        round_num=1,
        model="gpt-test",
    )

    restored = approval_interrupt_from_dict(approval_interrupt_to_dict(interrupt))

    assert tuple(action.tool_call_id for action in restored.actions) == tuple(tool_call_ids)
    assert restored.context_snapshot == context_snapshot
