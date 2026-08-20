"""聊天暂停响应属性测试模块。"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.chat.value_objects import ChatResponseVO


@settings(max_examples=50, deadline=5000)
@given(
    terminated_reason=st.sampled_from(["max_rounds", "token_budget_exceeded"]),
    can_continue=st.booleans(),
)
def test_paused_response_preserves_termination_reason(
    terminated_reason: str,
    can_continue: bool,
) -> None:
    """验证暂停态响应保留终止原因并可表达继续标记。"""
    response = ChatResponseVO(
        session_id="s1",
        reply="",
        model="gpt-test",
        usage={"total_tokens": 1},
        prompt_id="chat-default@v1",
        status="paused",
        terminated_reason=terminated_reason,  # type: ignore[arg-type]
        can_continue=can_continue,
    )

    assert response.status == "paused"
    assert response.terminated_reason == terminated_reason
    assert response.can_continue is can_continue
