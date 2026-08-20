"""HITL 聊天值对象属性测试模块。"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.agent.value_objects import PendingActionRequest
from domain.chat.value_objects import ChatResponseVO


@settings(max_examples=100, deadline=5000)
@given(tool_call_ids=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=10))
def test_chat_response_action_request_order_is_preserved(tool_call_ids: list[str]) -> None:
    """验证 action_requests 在 ChatResponseVO 中保持原始顺序。"""
    actions = tuple(
        PendingActionRequest(
            tool_call_id=tool_call_id,
            tool_name="write_file",
            arguments="{}",
            allowed_decisions=frozenset({"approve", "reject"}),
        )
        for tool_call_id in tool_call_ids
    )

    response = ChatResponseVO(
        session_id="s1",
        reply="",
        model="gpt-test",
        usage={},
        prompt_id="chat-default@v1",
        status="approval_required",
        approval_id="approval-1",
        action_requests=actions,
    )

    assert tuple(action.tool_call_id for action in response.action_requests) == tuple(tool_call_ids)
