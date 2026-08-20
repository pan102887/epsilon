"""Agent 审批异常单元测试模块。"""

from common.exceptions import BizException
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
    ApprovalEditInvalidArgumentsError,
    ApprovalEditToolNameMismatchError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalRespondNotAllowedError,
    HitlConfigInvalidError,
)


def test_approval_not_found_error() -> None:
    """验证审批不存在异常。"""
    error = ApprovalNotFoundError("s1", "a1")
    assert isinstance(error, BizException)
    assert error.code == 60020
    assert "不存在" in error.message
    assert error.session_id == "s1"
    assert error.approval_id == "a1"


def test_approval_expired_error() -> None:
    """验证审批过期异常。"""
    error = ApprovalExpiredError("s1", "a1")
    assert error.code == 60021
    assert "过期" in error.message


def test_approval_consumed_error() -> None:
    """验证审批已消费异常。"""
    error = ApprovalConsumedError("s1", "a1")
    assert error.code == 60022
    assert "重复恢复" in error.message


def test_approval_decision_count_mismatch_error() -> None:
    """验证审批数量不匹配异常。"""
    error = ApprovalDecisionCountMismatchError(expected_count=2, actual_count=1)
    assert error.code == 60023
    assert "期望 2 个" in error.message
    assert error.expected_count == 2
    assert error.actual_count == 1


def test_approval_decision_order_mismatch_error() -> None:
    """验证审批顺序不匹配异常。"""
    error = ApprovalDecisionOrderMismatchError("call-1", "call-2")
    assert error.code == 60024
    assert "顺序" in error.message
    assert error.expected_tool_call_id == "call-1"
    assert error.actual_tool_call_id == "call-2"


def test_approval_decision_not_allowed_error() -> None:
    """验证审批决策不允许异常。"""
    error = ApprovalDecisionNotAllowedError("write_file", "edit", frozenset({"approve"}))
    assert error.code == 60025
    assert "write_file" in error.message
    assert "edit" in error.message
    assert error.allowed_decisions == frozenset({"approve"})


def test_approval_edit_tool_name_mismatch_error() -> None:
    """验证编辑工具名不一致异常。"""
    error = ApprovalEditToolNameMismatchError("write_file", "shell_exec")
    assert error.code == 60026
    assert "工具名称" in error.message
    assert error.expected_tool_name == "write_file"
    assert error.actual_tool_name == "shell_exec"


def test_approval_edit_invalid_arguments_error() -> None:
    """验证编辑参数非法异常。"""
    error = ApprovalEditInvalidArgumentsError("http_request", "JSON 格式错误")
    assert error.code == 60027
    assert "http_request" in error.message
    assert "JSON 格式错误" in error.message
    assert error.reason == "JSON 格式错误"


def test_approval_respond_not_allowed_error() -> None:
    """验证人工回复不允许异常。"""
    error = ApprovalRespondNotAllowedError("write_file")
    assert error.code == 60028
    assert "不允许" in error.message
    assert error.tool_name == "write_file"


def test_hitl_config_invalid_error() -> None:
    """验证 HITL 配置非法异常。"""
    error = HitlConfigInvalidError("非法 JSON")
    assert error.code == 60029
    assert "配置非法" in error.message
    assert error.reason == "非法 JSON"
