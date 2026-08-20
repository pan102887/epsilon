"""Agent 审批异常安全属性测试模块。"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.agent.exceptions import (
    ApprovalEditInvalidArgumentsError,
    ApprovalNotFoundError,
    HitlConfigInvalidError,
)

secret_text_st = st.sampled_from(
    [
        "/tmp/private/config.properties",
        "C:\\secret\\token.txt",
        "token=raw-token-value",
        "password=raw-password-value",
        "secret=raw-secret-value",
    ]
)


@settings(max_examples=100, deadline=5000)
@given(session_id=secret_text_st, approval_id=secret_text_st)
def test_state_lookup_error_does_not_expose_storage_identifiers(
    session_id: str,
    approval_id: str,
) -> None:
    """验证状态查询类异常不把传入标识拼进 message。"""
    error = ApprovalNotFoundError(session_id, approval_id)

    assert session_id not in error.message
    assert approval_id not in error.message


@settings(max_examples=100, deadline=5000)
@given(tool_name=secret_text_st)
def test_invalid_arguments_error_does_not_expose_tool_secret_values(tool_name: str) -> None:
    """验证参数异常不会因为工具名包含敏感片段而泄露原始值。"""
    error = ApprovalEditInvalidArgumentsError(tool_name)

    if "/" not in tool_name and "\\" not in tool_name and "=" not in tool_name:
        assert tool_name in error.message
    assert "raw-token-value" not in error.message
    assert "raw-password-value" not in error.message
    assert "raw-secret-value" not in error.message
    assert "/tmp/private" not in error.message
    assert "C:\\secret" not in error.message


@settings(max_examples=100, deadline=5000)
@given(reason=st.text(min_size=1, max_size=40))
def test_hitl_config_error_message_contains_reason_without_traceback(reason: str) -> None:
    """验证 HITL 配置异常只包含业务原因，不包含堆栈字段。"""
    error = HitlConfigInvalidError(reason)

    if "/" not in reason and "\\" not in reason:
        assert reason in error.message
    assert "Traceback" not in error.message
    assert "/tmp/" not in error.message
