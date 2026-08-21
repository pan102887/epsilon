"""domain/agent 护栏策略领域服务。

承载 Agent 护栏的任务类型分类与预算/风险护栏判定，为零基础设施依赖的
领域服务（Domain_Service）：仅依赖 domain.agent.guardrails 与 domain.run
的领域类型，无 I/O、无 ContextVar、无 OTel、无 logging、无 Pydantic，可
脱离运行时单元测试。本类结构化实现 domain.agent.ports.AgentGuardrailPolicyPort
（Protocol，无需继承）；不变量：所有判据、检查顺序、比较运算符、None 短路
语义、OBSERVE/ENFORCE 分支与上提前逐一等价（Behavior_Equivalent_Refactor）。
"""

from __future__ import annotations

from typing import Any, cast

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationContext,
    GuardrailMode,
    GuardrailPolicy,
    GuardrailReason,
    TaskExecutionClass,
    ToolRiskLevel,
    json_safe,
)
from domain.run import RunKind, RunPayload, RunSnapshot


class StaticAgentGuardrailPolicy:
    """基于确定性规则和静态配置的护栏策略领域服务。"""

    def __init__(self, policy: GuardrailPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> GuardrailPolicy:
        """返回当前领域策略。"""

        return self._policy

    def classify_run(self, snapshot: RunSnapshot) -> TaskExecutionClass:
        """根据 Run 快照确定任务类型。"""

        if (
            snapshot.latest_checkpoint_id is not None
            or snapshot.can_continue
            or segment_count(snapshot.segment_metadata) > 1
        ):
            return TaskExecutionClass.LONG_TASK
        return self.classify_payload(snapshot.payload, has_tools=True)

    def classify_payload(
        self,
        payload: RunPayload,
        *,
        has_tools: bool,
    ) -> TaskExecutionClass:
        """根据 payload 与工具可用性确定任务类型。"""

        data = payload.task if payload.kind is RunKind.TASK else payload.chat
        if looks_batch(data or {}):
            return TaskExecutionClass.BATCH_TASK
        if payload.kind is RunKind.TASK:
            return TaskExecutionClass.TOOL_TASK if has_tools else TaskExecutionClass.LONG_TASK
        return TaskExecutionClass.TOOL_TASK if has_tools else TaskExecutionClass.SHORT_QA

    def evaluate_run_start(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """Run/segment 开始前评估。"""

        return self._budget_decision(context)

    def evaluate_model_completed(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """模型调用后评估预算与上下文增长。"""

        return self._budget_decision(context)

    def evaluate_tool_before_execution(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """工具执行前评估风险与反循环条件。"""

        budget_decision = self._budget_decision(context)
        if budget_decision.action is not GuardrailAction.ALLOW:
            return budget_decision

        risk = context.tool_risk_level
        if risk is ToolRiskLevel.CRITICAL and self._policy.enforce_critical_tools:
            return self._risk_decision(
                action=GuardrailAction.STOP,
                context=context,
                message="critical 工具需要护栏阻断",
            )
        if risk is ToolRiskLevel.HIGH and self._policy.enforce_high_risk_tools:
            return self._risk_decision(
                action=GuardrailAction.REQUIRE_APPROVAL,
                context=context,
                message="高风险工具需要人工确认",
            )
        return GuardrailDecision.allow()

    def evaluate_tool_after_execution(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """工具执行后评估失败计数；不尝试撤销副作用。"""

        return self._budget_decision(context)

    def _risk_decision(
        self,
        *,
        action: GuardrailAction,
        context: GuardrailEvaluationContext,
        message: str,
    ) -> GuardrailDecision:
        """根据模式返回风险决策。"""

        metadata = {"tool_name": context.tool_name, "risk_level": context.tool_risk_level}
        if self._policy.mode is GuardrailMode.OBSERVE:
            return GuardrailDecision.observe(
                reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
                message=message,
                mode=self._policy.mode,
                metadata=json_safe(metadata),
            )
        if action is GuardrailAction.REQUIRE_APPROVAL:
            return GuardrailDecision.require_approval(
                reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
                message=message,
                mode=self._policy.mode,
                metadata=json_safe(metadata),
            )
        return GuardrailDecision.stop(
            reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
            message=message,
            mode=self._policy.mode,
            metadata=json_safe(metadata),
        )

    def _budget_decision(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        checks = [
            (
                self._policy.max_total_tokens is not None
                and context.total_tokens >= self._policy.max_total_tokens,
                GuardrailReason.TOKEN_BUDGET_REACHED,
                "token 预算已达到上限",
                {"total_tokens": context.total_tokens},
            ),
            (
                self._policy.max_duration_seconds is not None
                and context.elapsed_ms >= self._policy.max_duration_seconds * 1000,
                GuardrailReason.DURATION_BUDGET_REACHED,
                "耗时预算已达到上限",
                {"elapsed_ms": context.elapsed_ms},
            ),
            (
                self._policy.max_context_growth_messages is not None
                and context.context_growth_messages >= self._policy.max_context_growth_messages,
                GuardrailReason.CONTEXT_GROWTH_LIMIT,
                "上下文增长已达到上限",
                {"context_growth_messages": context.context_growth_messages},
            ),
            (
                context.repeated_tool_call_count >= self._policy.max_repeated_tool_calls,
                GuardrailReason.REPEATED_TOOL_CALL,
                "重复工具调用已达到上限",
                {"repeated_tool_call_count": context.repeated_tool_call_count},
            ),
            (
                context.consecutive_failure_count >= self._policy.max_consecutive_failures,
                GuardrailReason.REPEATED_FAILURE,
                "连续失败已达到上限",
                {"consecutive_failure_count": context.consecutive_failure_count},
            ),
        ]
        for matched, reason, message, metadata in checks:
            if not matched:
                continue
            if self._policy.mode is GuardrailMode.OBSERVE:
                return GuardrailDecision.observe(
                    reason=reason,
                    message=message,
                    mode=self._policy.mode,
                    metadata=metadata,
                )
            return GuardrailDecision.stop(
                reason=reason,
                message=message,
                mode=self._policy.mode,
                metadata=metadata,
            )
        return GuardrailDecision.allow()


def looks_batch(data: dict[str, Any]) -> bool:
    """启发式判定 payload 是否为批量任务。

    当 items/batch/targets/inputs 中任一为长度大于 1 的 list，或
    constraints 为含「批量」子串的 list 时判为批量任务。
    """

    for key in ("items", "batch", "targets", "inputs"):
        value = data.get(key)
        if isinstance(value, list) and len(cast(list[object], value)) > 1:
            return True
    constraints = data.get("constraints")
    return isinstance(constraints, list) and any(
        "批量" in str(item) for item in cast(list[object], constraints)
    )


def segment_count(metadata: object) -> int:
    """从 segment_metadata 容错读取 segment_count（转 int，异常归 0）。"""

    if not isinstance(metadata, dict):
        return 0
    typed_metadata = cast(dict[object, object], metadata)
    try:
        return int(str(typed_metadata.get("segment_count", 0)))
    except (TypeError, ValueError):
        return 0
