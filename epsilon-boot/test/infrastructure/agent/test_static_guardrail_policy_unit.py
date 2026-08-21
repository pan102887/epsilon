"""静态 Agent guardrail 策略单元测试。"""

from __future__ import annotations

from typing import Any

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailEvaluationContext,
    GuardrailMode,
    GuardrailPolicy,
    GuardrailReason,
    TaskExecutionClass,
    ToolRiskLevel,
)
from domain.run import RunKind, RunPayload, RunSnapshot, RunStatus
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy


def _snapshot(
    *,
    payload: RunPayload,
    latest_checkpoint_id: str | None = None,
    segment_metadata: dict[str, Any] | None = None,
) -> RunSnapshot:
    from datetime import UTC, datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return RunSnapshot(
        run_id="run-1",
        kind=payload.kind,
        status=RunStatus.QUEUED,
        payload=payload,
        client_request_id=None,
        payload_hash=None,
        result=None,
        error=None,
        approval_id=None,
        segment_metadata=segment_metadata,
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
        latest_checkpoint_id=latest_checkpoint_id,
    )


def test_classifies_run_with_checkpoint_as_long_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    snapshot = _snapshot(
        payload=RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"}),
        latest_checkpoint_id="chk-1",
    )

    assert policy.classify_run(snapshot) is TaskExecutionClass.LONG_TASK


def test_classifies_batch_payload_before_tool_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="s1",
        task={"goal": "process", "items": ["a", "b"]},
    )

    assert policy.classify_payload(payload, has_tools=True) is TaskExecutionClass.BATCH_TASK


def test_observe_mode_records_critical_tool_without_blocking() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE))

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.OBSERVE
    assert decision.reason is GuardrailReason.TOOL_RISK_GATE_REQUIRED


def test_enforce_mode_stops_critical_tool_before_execution() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE))

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.STOP
    assert decision.public_terminal_reason == "guardrail_blocked"


def test_high_risk_tool_requires_approval_only_when_configured() -> None:
    default_policy = StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE))
    strict_policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=True)
    )
    context = GuardrailEvaluationContext(
        tool_name="http_request",
        tool_risk_level=ToolRiskLevel.HIGH,
    )

    assert default_policy.evaluate_tool_before_execution(context).action is GuardrailAction.ALLOW
    assert (
        strict_policy.evaluate_tool_before_execution(context).action
        is GuardrailAction.REQUIRE_APPROVAL
    )


def test_enforce_mode_stops_when_token_budget_reached() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_total_tokens=10)
    )

    decision = policy.evaluate_model_completed(GuardrailEvaluationContext(total_tokens=11))

    assert decision.action is GuardrailAction.STOP
    assert decision.reason is GuardrailReason.TOKEN_BUDGET_REACHED
