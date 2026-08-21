"""Agent 审批值对象单元测试模块。

验证 HITL 审批相关值对象的默认值、载荷构造和过期边界语义。
"""

import pytest

from domain.agent.value_objects import (
    AgentResult,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalRequiredPayload,
    PendingActionRequest,
)


def _pending_action(tool_call_id: str = "call-1") -> PendingActionRequest:
    """构造默认待审批动作测试对象。"""
    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name="write_file",
        arguments='{"path":"README.md"}',
        allowed_decisions=frozenset({"approve", "reject"}),
        reason="高风险写入工具",
    )


def test_agent_result_default_status_remains_completed() -> None:
    """验证 AgentResult 默认仍表示完成状态，保持旧构造兼容。"""
    result = AgentResult(content="ok", model="gpt-test")

    assert result.status == "completed"
    assert result.approval is None


def test_agent_result_can_carry_approval_required_payload() -> None:
    """验证 AgentResult 可携带 approval_required 审批载荷。"""
    action = _pending_action()
    payload = ApprovalRequiredPayload(
        session_id="session-1",
        approval_id="approval-1",
        actions=(action,),
        prompt_id="chat-default@v1",
        metadata={"round": 1},
    )
    result = AgentResult(
        content="",
        model="gpt-test",
        status="approval_required",
        approval=payload,
    )

    assert result.status == "approval_required"
    assert result.approval == payload
    result_approval = result.approval
    assert result_approval is not None
    assert result_approval.actions == (action,)


def test_approval_interrupt_is_expired_boundary() -> None:
    """验证 ApprovalInterrupt.is_expired(...) 在边界时刻的语义。"""
    interrupt = ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=(_pending_action(),),
        context_snapshot={"messages": []},
        round_num=1,
        model="gpt-test",
        expires_at_epoch=100.0,
    )

    assert interrupt.is_expired(99.999) is False
    assert interrupt.is_expired(100.0) is True
    assert interrupt.is_expired(100.001) is True


def test_approval_interrupt_without_expiry_never_expires() -> None:
    """验证 expires_at_epoch 未设置时不判定为过期。"""
    interrupt = ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=(_pending_action(),),
        context_snapshot={},
        round_num=1,
        model="gpt-test",
    )

    assert interrupt.is_expired(0.0) is False
    assert interrupt.is_expired(999999.0) is False


def test_approval_interrupt_summary_accepts_valid_payload() -> None:
    """验证审批中断摘要可承载恢复提示所需字段。"""
    summary = ApprovalInterruptSummary(
        session_id="session-1",
        approval_id="approval-1",
        action_count=2,
        created_at_epoch=10.0,
        expires_at_epoch=20.0,
        expired=False,
        tool_names=("write_file", "shell_exec"),
    )

    assert summary.session_id == "session-1"
    assert summary.approval_id == "approval-1"
    assert summary.action_count == 2
    assert summary.tool_names == ("write_file", "shell_exec")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_id": ""},
        {"approval_id": ""},
        {"action_count": -1},
        {"action_count": 1.5},
        {"created_at_epoch": True},
        {"expires_at_epoch": False},
        {"expired": "false"},
    ],
)
def test_approval_interrupt_summary_rejects_invalid_fields(
    kwargs: dict[str, object],
) -> None:
    """验证审批中断摘要非法字段会被拒绝。"""
    payload: dict[str, object] = {
        "session_id": "session-1",
        "approval_id": "approval-1",
        "action_count": 1,
        "created_at_epoch": 10.0,
        "expires_at_epoch": 20.0,
        "expired": False,
    }
    payload.update(kwargs)

    with pytest.raises(ValueError):
        ApprovalInterruptSummary(**payload)  # type: ignore[arg-type]
