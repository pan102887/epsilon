"""静态审批策略提供器模块。

根据默认工具风险分级和 ``HITL_INTERRUPT_ON`` JSON 配置生成工具审批策略。
"""

from __future__ import annotations

import json
from typing import Any, get_args

from domain.agent.approval_lookup import ApprovalDefaultLookup
from domain.agent.exceptions import HitlConfigInvalidError
from domain.agent.ports import ApprovalPolicyPort
from domain.agent.value_objects import ApprovalDecisionType, ApprovalPolicy

_VALID_DECISIONS = frozenset(get_args(ApprovalDecisionType))


class StaticApprovalPolicyProvider(ApprovalPolicyPort):
    """基于静态默认策略和 JSON 覆盖的审批策略提供器。"""

    def __init__(self, enabled: bool, interrupt_on: str) -> None:
        """初始化审批策略提供器。

        Args:
            enabled: HITL 是否开启。
            interrupt_on: JSON object 字符串，按工具名覆盖审批策略。
        """
        self._enabled = enabled
        self._overrides = self._parse_interrupt_on(interrupt_on) if enabled else {}

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        """返回指定工具的审批策略。"""
        if not self._enabled:
            return ApprovalPolicy(
                tool_name=tool_name,
                interrupt=False,
                allowed_decisions=frozenset(),
            )

        if tool_name in self._overrides:
            return self._overrides[tool_name]

        return ApprovalDefaultLookup.policy_for(tool_name)

    def _parse_interrupt_on(self, raw: str) -> dict[str, ApprovalPolicy]:
        """解析 HITL_INTERRUPT_ON JSON 配置。"""
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HitlConfigInvalidError("HITL_INTERRUPT_ON 不是合法 JSON") from exc
        if not isinstance(parsed, dict):
            raise HitlConfigInvalidError("HITL_INTERRUPT_ON 必须是 JSON object")

        result: dict[str, ApprovalPolicy] = {}
        for tool_name, value in parsed.items():
            if not isinstance(tool_name, str) or not tool_name:
                raise HitlConfigInvalidError("工具名称必须是非空字符串")
            result[tool_name] = self._policy_from_value(tool_name, value)
        return result

    def _policy_from_value(self, tool_name: str, value: Any) -> ApprovalPolicy:
        """把单个工具配置值转换为 ApprovalPolicy。"""
        if value is True:
            decisions, risk_label = ApprovalDefaultLookup.decisions_for(tool_name)
            return ApprovalPolicy(
                tool_name=tool_name,
                interrupt=True,
                allowed_decisions=frozenset(decisions),
                risk_label=risk_label,
            )
        if value is False:
            return ApprovalPolicy(
                tool_name=tool_name,
                interrupt=False,
                allowed_decisions=frozenset(),
            )
        if isinstance(value, list):
            return ApprovalPolicy(
                tool_name=tool_name,
                interrupt=True,
                allowed_decisions=self._validate_decisions(value),
                risk_label="用户配置审批工具",
            )
        if isinstance(value, dict):
            allowed = value.get("allowed_decisions", ["approve", "reject"])
            risk_label = value.get("risk_label", "")
            if not isinstance(risk_label, str):
                raise HitlConfigInvalidError(f"工具 {tool_name} 的 risk_label 必须是字符串")
            return ApprovalPolicy(
                tool_name=tool_name,
                interrupt=True,
                allowed_decisions=self._validate_decisions(allowed),
                risk_label=risk_label,
            )
        raise HitlConfigInvalidError(f"工具 {tool_name} 的审批配置格式不支持")

    def _validate_decisions(self, values: Any) -> frozenset[ApprovalDecisionType]:
        """校验决策集合。"""
        if not isinstance(values, list) or not values:
            raise HitlConfigInvalidError("allowed_decisions 必须是非空数组")
        invalid = [value for value in values if value not in _VALID_DECISIONS]
        if invalid:
            raise HitlConfigInvalidError(f"非法审批决策: {', '.join(map(str, invalid))}")
        return frozenset(values)
