"""``ApprovalDefaultLookup`` 领域服务单元测试模块。

覆盖 feature ``ddd-anemic-domain-agent-followups`` 候选 B（需求 6 AC6.3，
Property 3/4）：审批默认查表全分支逐一等价锁定。本测试为脱离运行时单测，
**仅 import ``domain.agent.approval_lookup`` 与 ``domain.agent.value_objects``**，
不依赖 ``application``/``infrastructure`` 或框架运行时。

覆盖矩阵（与上提前 ``StaticApprovalPolicyProvider`` 无 override 默认分支及
``_policy_from_value`` 的 ``value is True`` 分支逐一等价）：

* ``policy_for`` 命中 ``DEFAULT_POLICIES`` 6 工具（区分 5 个 ``APPROVE_REJECT``
  与 ``http_request`` 的 ``APPROVE_EDIT_REJECT``），断言 ``interrupt=True`` /
  ``allowed_decisions`` / ``risk_label`` 逐值；
* ``policy_for`` 命中 ``LOW_RISK_TOOLS`` 4 工具（``interrupt=False`` /
  ``allowed_decisions`` 空 / ``risk_label="低风险工具"``）；
* ``policy_for`` 未命中且非低风险工具（``interrupt=False`` / ``risk_label=""``）；
* ``decisions_for`` 命中与未命中默认元组。
"""

import pytest

from domain.agent.approval_lookup import (
    APPROVE_EDIT_REJECT,
    APPROVE_REJECT,
    DEFAULT_POLICIES,
    LOW_RISK_TOOLS,
    ApprovalDefaultLookup,
)


@pytest.mark.parametrize(
    ("tool_name", "expected_decisions", "expected_risk_label"),
    [
        ("write_file", APPROVE_REJECT, "高风险文件写入"),
        ("edit_file", APPROVE_REJECT, "高风险文件编辑"),
        ("shell_exec", APPROVE_REJECT, "高风险命令执行"),
        ("python_exec", APPROVE_REJECT, "高风险代码执行"),
        ("delegate_to_agent", APPROVE_REJECT, "高风险子 Agent 委派"),
        ("http_request", APPROVE_EDIT_REJECT, "高风险网络请求"),
    ],
)
def test_policy_for_default_policies_hit(
    tool_name: str, expected_decisions: frozenset[str], expected_risk_label: str
) -> None:
    """命中 ``DEFAULT_POLICIES``：``interrupt=True`` + 对应决策集 + 风险标签。"""
    policy = ApprovalDefaultLookup.policy_for(tool_name)
    assert policy.tool_name == tool_name
    assert policy.interrupt is True
    assert policy.allowed_decisions == expected_decisions
    assert policy.risk_label == expected_risk_label


def test_policy_for_http_request_uses_approve_edit_reject() -> None:
    """``http_request`` 专项：决策集为 ``APPROVE_EDIT_REJECT``（含 edit）。"""
    policy = ApprovalDefaultLookup.policy_for("http_request")
    assert policy.allowed_decisions == frozenset({"approve", "edit", "reject"})


@pytest.mark.parametrize("tool_name", sorted(LOW_RISK_TOOLS))
def test_policy_for_low_risk_tools(tool_name: str) -> None:
    """命中 ``LOW_RISK_TOOLS``：不中断、决策集空、风险标签为『低风险工具』。"""
    policy = ApprovalDefaultLookup.policy_for(tool_name)
    assert policy.tool_name == tool_name
    assert policy.interrupt is False
    assert policy.allowed_decisions == frozenset()
    assert policy.risk_label == "低风险工具"


def test_policy_for_unknown_non_low_risk_tool() -> None:
    """未命中且非低风险：不中断、决策集空、风险标签为空串。"""
    policy = ApprovalDefaultLookup.policy_for("unknown_tool")
    assert policy.tool_name == "unknown_tool"
    assert policy.interrupt is False
    assert policy.allowed_decisions == frozenset()
    assert policy.risk_label == ""


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("write_file", (APPROVE_REJECT, "高风险文件写入")),
        ("http_request", (APPROVE_EDIT_REJECT, "高风险网络请求")),
    ],
)
def test_decisions_for_hit(
    tool_name: str, expected: tuple[frozenset[str], str]
) -> None:
    """``decisions_for`` 命中 ``DEFAULT_POLICIES``：返回对应元组。"""
    assert ApprovalDefaultLookup.decisions_for(tool_name) == expected


def test_decisions_for_miss_returns_default_tuple() -> None:
    """``decisions_for`` 未命中：返回默认 ``(APPROVE_REJECT, "用户配置审批工具")``。"""
    assert ApprovalDefaultLookup.decisions_for("unknown") == (
        APPROVE_REJECT,
        "用户配置审批工具",
    )


def test_default_policies_contains_expected_tools() -> None:
    """``DEFAULT_POLICIES`` 键集合与上提前逐一等价（6 工具）。"""
    assert set(DEFAULT_POLICIES) == {
        "write_file",
        "edit_file",
        "shell_exec",
        "python_exec",
        "delegate_to_agent",
        "http_request",
    }
