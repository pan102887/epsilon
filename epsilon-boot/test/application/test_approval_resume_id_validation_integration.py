"""审批恢复路径前置校验集成回归测试（Task 2.4）。

对应 design 测试矩阵 T16：``ApprovalResume(decisions=(ApprovalDecision(tool_call_id="", ...),))``
在 ``ApprovalDecision`` 构造时即抛 ``InvalidApprovalActionError``，错误不
延迟到 ``react_agent_adapter.py`` 的 ``ToolCallRequest(...)`` 构造点。

注：design §修改文件清单未列 ``react_agent_adapter.py``，本测试仅做"前置
校验拦截在 application 入口"的黑盒断言：构造审批恢复请求时即抛错。
"""

from __future__ import annotations

import pytest

from domain.agent.exceptions import InvalidApprovalActionError
from domain.agent.value_objects import ApprovalDecision, ApprovalResume


def test_t16_approval_resume_construction_blocks_empty_id_at_decision() -> None:
    """T16: 构造 ApprovalDecision(tool_call_id="") 时即抛，不进入 ApprovalResume。"""
    with pytest.raises(InvalidApprovalActionError) as exc_info:
        # 元组推导式内部即触发 ApprovalDecision 构造，前置校验先于 ApprovalResume
        ApprovalResume(
            session_id="sess-1",
            approval_id="appr-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id=""),),
        )
    exc = exc_info.value
    assert exc.value_object == "ApprovalDecision"
    assert exc.field == "tool_call_id"
    assert exc.raw_value == ""


def test_t16_approval_resume_with_valid_decisions_succeeds() -> None:
    """回归保护：所有 decision tool_call_id 合法时正常构造。"""
    resume = ApprovalResume(
        session_id="sess-1",
        approval_id="appr-1",
        decisions=(
            ApprovalDecision(type="approve", tool_call_id="call_a"),
            ApprovalDecision(type="reject", tool_call_id="call_b"),
        ),
    )
    assert len(resume.decisions) == 2
    assert resume.decisions[0].tool_call_id == "call_a"
    assert resume.decisions[1].tool_call_id == "call_b"
