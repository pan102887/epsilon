"""domain/agent 审批默认查表领域服务。

承载「工具名 → 默认审批策略」的纯查表领域规则，为零基础设施依赖的领域
服务（Domain_Service）：无 ``json``、无框架、无 I/O、无 logging、无 ContextVar、
无 OTel，可脱离配置字符串单元测试。JSON 配置解析（``HITL_INTERRUPT_ON``）依
ADR-0008 属配置边界技术关注点，保留在
``infrastructure/agent/approval_policy_provider.py``。不变量：查表判据、决策集、
``risk_label`` 取值与上提前逐一等价（Behavior_Equivalent_Refactor）。
"""

from __future__ import annotations

from domain.agent.value_objects import ApprovalDecisionType, ApprovalPolicy

APPROVE_REJECT: frozenset[ApprovalDecisionType] = frozenset({"approve", "reject"})
"""允许 approve / reject 的默认决策集。"""

APPROVE_EDIT_REJECT: frozenset[ApprovalDecisionType] = frozenset(
    {"approve", "edit", "reject"}
)
"""允许 approve / edit / reject 的默认决策集。"""

DEFAULT_POLICIES: dict[str, tuple[frozenset[ApprovalDecisionType], str]] = {
    "write_file": (APPROVE_REJECT, "高风险文件写入"),
    "edit_file": (APPROVE_REJECT, "高风险文件编辑"),
    "shell_exec": (APPROVE_REJECT, "高风险命令执行"),
    "python_exec": (APPROVE_REJECT, "高风险代码执行"),
    "delegate_to_agent": (APPROVE_REJECT, "高风险子 Agent 委派"),
    "http_request": (APPROVE_EDIT_REJECT, "高风险网络请求"),
}
"""默认审批工具查表：工具名 → (允许决策集, 风险标签)。字面自 infrastructure 上提。"""

LOW_RISK_TOOLS = frozenset({"read_file", "list_dir", "web_fetch", "web_search"})
"""默认低风险工具集合。"""


class ApprovalDefaultLookup:
    """审批默认查表领域服务。

    无字段的无状态领域服务，仅承载「工具名 → 默认审批策略」的纯查表这一
    单一职责（对齐 ``srp-principle.md``）。所有判定为纯函数，不触发任何 I/O、
    不 ``raise`` 异常（命中/未命中均返回值对象或元组）；``HITL_INTERRUPT_ON``
    JSON 解析与 ``HitlConfigInvalidError`` 抛出留在 infrastructure（ADR-0008）。
    """

    @staticmethod
    def policy_for(tool_name: str) -> ApprovalPolicy:
        """无 override 时按默认查表返回工具审批策略。

        与 ``StaticApprovalPolicyProvider.policy_for`` 的无 override 默认分支
        逐一等价：

        - 命中 ``DEFAULT_POLICIES``：``interrupt=True``，带对应 ``allowed_decisions``
          与 ``risk_label``；
        - 未命中：``interrupt=False``，``allowed_decisions`` 空，``risk_label`` 依
          「在 ``LOW_RISK_TOOLS`` 则『低风险工具』否则空串」。

        Args:
            tool_name: 工具名称。

        Returns:
            对应的 ``ApprovalPolicy`` 值对象。
        """
        if tool_name in DEFAULT_POLICIES:
            decisions, risk_label = DEFAULT_POLICIES[tool_name]
            return ApprovalPolicy(
                tool_name=tool_name,
                interrupt=True,
                allowed_decisions=frozenset(decisions),
                risk_label=risk_label,
            )
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=False,
            allowed_decisions=frozenset(),
            risk_label="低风险工具" if tool_name in LOW_RISK_TOOLS else "",
        )

    @staticmethod
    def decisions_for(
        tool_name: str,
    ) -> tuple[frozenset[ApprovalDecisionType], str]:
        """返回 ``value is True`` 分支所需的 (决策集, 风险标签) 元组。

        与 ``_policy_from_value`` 中
        ``_DEFAULT_POLICIES.get(tool_name, (_APPROVE_REJECT, "用户配置审批工具"))``
        逐一等价：命中返回对应元组，未命中返回
        ``(APPROVE_REJECT, "用户配置审批工具")``。

        Args:
            tool_name: 工具名称。

        Returns:
            (允许决策集, 风险标签) 元组。
        """
        return DEFAULT_POLICIES.get(tool_name, (APPROVE_REJECT, "用户配置审批工具"))
