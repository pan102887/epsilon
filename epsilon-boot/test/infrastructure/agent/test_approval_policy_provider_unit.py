"""审批策略提供器单元测试模块。"""

import pytest

from domain.agent.exceptions import HitlConfigInvalidError
from infrastructure.agent.approval_policy_provider import StaticApprovalPolicyProvider


def test_default_sensitive_tool_requires_approval() -> None:
    """验证默认敏感工具需要审批。"""
    policy = StaticApprovalPolicyProvider(True, "").policy_for("write_file")

    assert policy.interrupt is True
    assert policy.allowed_decisions == frozenset({"approve", "reject"})


def test_default_low_risk_tool_does_not_require_approval() -> None:
    """验证默认低风险工具不审批。"""
    policy = StaticApprovalPolicyProvider(True, "").policy_for("read_file")

    assert policy.interrupt is False
    assert policy.allowed_decisions == frozenset()


def test_http_request_allows_edit_by_default() -> None:
    """验证 http_request 默认允许 edit。"""
    policy = StaticApprovalPolicyProvider(True, "").policy_for("http_request")

    assert policy.interrupt is True
    assert policy.allowed_decisions == frozenset({"approve", "edit", "reject"})


def test_disabled_provider_never_interrupts() -> None:
    """验证 HITL 关闭时不审批。"""
    policy = StaticApprovalPolicyProvider(False, '{"write_file": true}').policy_for("write_file")

    assert policy.interrupt is False
    assert policy.allowed_decisions == frozenset()


def test_user_override_array_and_risk_label() -> None:
    """验证用户覆盖决策集合和风险说明。"""
    provider = StaticApprovalPolicyProvider(
        True,
        (
            '{"web_fetch": ["approve", "reject"], '
            '"http_request": {"allowed_decisions": ["approve"], '
            '"risk_label": "外部网络"}}'
        ),
    )

    web_policy = provider.policy_for("web_fetch")
    http_policy = provider.policy_for("http_request")
    assert web_policy.interrupt is True
    assert web_policy.allowed_decisions == frozenset({"approve", "reject"})
    assert http_policy.allowed_decisions == frozenset({"approve"})
    assert http_policy.risk_label == "外部网络"


def test_false_override_disables_default_sensitive_tool() -> None:
    """验证 false 覆盖可关闭默认敏感工具审批。"""
    policy = StaticApprovalPolicyProvider(True, '{"write_file": false}').policy_for("write_file")

    assert policy.interrupt is False


@pytest.mark.parametrize("raw", ["[1]", "{bad", '{"write_file": ["delete"]}'])
def test_invalid_interrupt_on_fails_fast(raw: str) -> None:
    """验证非法 JSON 或非法决策 fail-fast。"""
    with pytest.raises(HitlConfigInvalidError):
        StaticApprovalPolicyProvider(True, raw)
