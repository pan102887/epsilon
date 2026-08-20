"""审批日志工具单元测试模块。"""

from infrastructure.agent.approval_logging import approval_log_extra, redact_approval_value


def test_redact_approval_value_truncates_long_output() -> None:
    """验证日志值输出会按 max_length 截断。"""
    text = redact_approval_value({"message": "x" * 100}, max_length=20)

    assert len(text) > 20
    assert text.endswith("...(truncated)")


def test_approval_log_extra_fields() -> None:
    """验证 approval_log_extra 字段完整。"""
    extra = approval_log_extra(
        session_id="s1",
        approval_id="a1",
        tool_names=["write_file"],
        action_count=1,
        round_num=2,
        decision_types=["approve"],
    )

    assert extra == {
        "session_id": "s1",
        "approval_id": "a1",
        "tool_names": ["write_file"],
        "action_count": 1,
        "round_num": 2,
        "decision_types": ["approve"],
    }
