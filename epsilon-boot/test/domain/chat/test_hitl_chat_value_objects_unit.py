"""HITL 聊天值对象单元测试模块。"""

import pytest

from domain.agent.value_objects import ApprovalDecision, PendingActionRequest
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatResponseVO


def _action(tool_call_id: str = "call-1") -> PendingActionRequest:
    """构造待审批动作。"""
    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name="write_file",
        arguments="{}",
        allowed_decisions=frozenset({"approve", "reject"}),
    )


def test_chat_response_default_completed_compatible() -> None:
    """验证 ChatResponseVO 默认 completed，旧构造方式兼容。"""
    response = ChatResponseVO(
        session_id="s1",
        reply="hello",
        model="gpt-test",
        usage={},
        prompt_id="chat-default@v1",
    )

    assert response.status == "completed"
    assert response.approval_id is None
    assert response.action_requests == ()


def test_chat_response_approval_required_fields() -> None:
    """验证 ChatResponseVO 可表达 approval_required 状态。"""
    action = _action()
    response = ChatResponseVO(
        session_id="s1",
        reply="",
        model="gpt-test",
        usage={"total_tokens": 3},
        prompt_id="chat-default@v1",
        status="approval_required",
        approval_id="approval-1",
        action_requests=(action,),
    )

    assert response.status == "approval_required"
    assert response.approval_id == "approval-1"
    assert response.action_requests == (action,)


@pytest.mark.parametrize(
    ("session_id", "approval_id", "message"),
    [
        ("", "approval-1", "session_id"),
        ("session-1", "", "approval_id"),
    ],
)
def test_approval_resume_request_rejects_empty_ids(
    session_id: str,
    approval_id: str,
    message: str,
) -> None:
    """验证 ApprovalResumeRequestVO 拒绝空 session_id / approval_id。"""
    with pytest.raises(ValueError, match=message):
        ApprovalResumeRequestVO(
            session_id=session_id,
            approval_id=approval_id,
            decisions=(),
        )


def test_approval_resume_request_accepts_decisions() -> None:
    """验证 ApprovalResumeRequestVO 保留决策 tuple。"""
    decision = ApprovalDecision(type="approve", tool_call_id="call-1")
    request = ApprovalResumeRequestVO(
        session_id="s1",
        approval_id="approval-1",
        decisions=(decision,),
        model="gpt-test",
    )

    assert request.decisions == (decision,)
    assert request.model == "gpt-test"
