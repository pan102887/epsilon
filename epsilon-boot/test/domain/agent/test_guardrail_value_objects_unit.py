"""Agent guardrail 值对象单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailMode,
    GuardrailModelPricing,
    GuardrailObservation,
    GuardrailPolicy,
    GuardrailReason,
    GuardrailRuntimeStats,
    GuardrailSummary,
    TaskExecutionClass,
    ToolRiskLevel,
    mark_guardrail_summary_stale,
    merge_guardrail_summary,
)
from infrastructure.agent.guardrail_serialization import (
    guardrail_observation_to_event_payload,
    guardrail_runtime_stats_to_dict,
    guardrail_summary_to_dict,
)


def test_guardrail_enums_are_stable() -> None:
    assert {item.value for item in TaskExecutionClass} == {
        "short_qa",
        "tool_task",
        "long_task",
        "batch_task",
    }
    assert {item.value for item in GuardrailMode} == {"observe", "enforce"}
    assert {item.value for item in GuardrailAction} == {
        "allow",
        "observe",
        "require_approval",
        "stop",
    }
    assert {item.value for item in GuardrailEvaluationStage} == {
        "run_start",
        "model_completed",
        "tool_before_execution",
        "tool_after_execution",
        "recovery_restored",
    }
    assert ToolRiskLevel.HIGH.value == "high"
    assert GuardrailReason.TOOL_RISK_GATE_REQUIRED.value == "tool_risk_gate_required"


def test_guardrail_policy_defaults_to_observe() -> None:
    policy = GuardrailPolicy()

    assert policy.enabled is True
    assert policy.mode is GuardrailMode.OBSERVE
    assert policy.enforce_critical_tools is True
    assert policy.enforce_high_risk_tools is False
    assert policy.max_total_tokens is None


def test_guardrail_policy_normalizes_legacy_and_object_model_pricing() -> None:
    policy = GuardrailPolicy(
        model_pricing={
            "legacy": 1.5,
            "split": {"prompt_per_1m": 0.8, "completion_per_1m": 2.0},
            "total": GuardrailModelPricing(total_per_1m=1.2),
        }
    )

    assert policy.model_pricing == {
        "legacy": GuardrailModelPricing(total_per_1m=1.5),
        "split": GuardrailModelPricing(prompt_per_1m=0.8, completion_per_1m=2.0),
        "total": GuardrailModelPricing(total_per_1m=1.2),
    }


def test_guardrail_runtime_stats_from_usage_estimates_split_and_total_costs() -> None:
    split_stats = GuardrailRuntimeStats.from_model_usage(
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        model="split",
        model_pricing={"split": GuardrailModelPricing(prompt_per_1m=1.0, completion_per_1m=3.0)},
        elapsed_ms=12.5,
        context_growth_messages=2,
    )
    total_stats = GuardrailRuntimeStats.from_model_usage(
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        model="total",
        model_pricing={"total": 2.0},
    )

    assert guardrail_runtime_stats_to_dict(split_stats) == {
        "total_tokens": 1500,
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "elapsed_ms": 12.5,
        "context_growth_messages": 2,
        "repeated_tool_call_count": 0,
        "consecutive_failure_count": 0,
        "total_model_calls": 1,
        "total_tool_calls": 0,
        "estimated_cost": 0.0025,
        "cost_available": True,
        "last_tool_name": None,
        "last_tool_risk_level": None,
        "last_tool_error": False,
    }
    assert total_stats.estimated_cost == 0.003
    assert total_stats.cost_available is True


def test_guardrail_runtime_stats_missing_price_marks_cost_unavailable() -> None:
    stats = GuardrailRuntimeStats.from_model_usage(
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
        model="missing",
        model_pricing={"other": GuardrailModelPricing(total_per_1m=1.0)},
    )

    assert stats.total_tokens == 1500
    assert stats.estimated_cost is None
    assert stats.cost_available is False


def test_guardrail_decision_blocked_summary_uses_single_public_reason() -> None:
    decision = GuardrailDecision.require_approval(
        reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
        message="需要人工确认",
        metadata={"tool_name": "shell_exec"},
    )

    assert decision.action is GuardrailAction.REQUIRE_APPROVAL
    assert decision.public_terminal_reason == "guardrail_blocked"
    assert (
        guardrail_summary_to_dict(decision.to_summary())["reason"]
        == "tool_risk_gate_required"
    )


def test_guardrail_summary_round_trips_to_dict() -> None:
    summary = GuardrailSummary(
        mode=GuardrailMode.OBSERVE,
        action=GuardrailAction.OBSERVE,
        reason=GuardrailReason.REPEATED_TOOL_CALL,
        message="重复工具调用",
        estimated_cost=None,
        metadata={"count": 2},
        evaluation_count=3,
        blocked_count=1,
        approval_request_count=0,
        last_event_cursor=7,
        updated_at="2026-06-10T15:02:31.123456+00:00",
        runtime_stats={"total_tokens": 32, "cost_available": False},
        stale=False,
        stale_reason=None,
    )

    assert guardrail_summary_to_dict(summary) == {
        "mode": "observe",
        "action": "observe",
        "reason": "repeated_tool_call",
        "message": "重复工具调用",
        "estimated_cost": None,
        "metadata": {"count": 2},
        "evaluation_count": 3,
        "blocked_count": 1,
        "approval_request_count": 0,
        "last_event_cursor": 7,
        "updated_at": "2026-06-10T15:02:31.123456+00:00",
        "runtime_stats": {"total_tokens": 32, "cost_available": False},
        "stale": False,
        "stale_reason": None,
    }


def test_guardrail_runtime_stats_to_dict_is_json_safe() -> None:
    stats = GuardrailRuntimeStats(
        total_tokens=11,
        prompt_tokens=7,
        completion_tokens=4,
        elapsed_ms=123.4,
        context_growth_messages=2,
        repeated_tool_call_count=1,
        consecutive_failure_count=0,
        total_model_calls=1,
        total_tool_calls=3,
        estimated_cost=0.0214,
        cost_available=True,
        last_tool_name="shell_exec",
        last_tool_risk_level="high",
        last_tool_error=True,
    )

    assert guardrail_runtime_stats_to_dict(stats) == {
        "total_tokens": 11,
        "prompt_tokens": 7,
        "completion_tokens": 4,
        "elapsed_ms": 123.4,
        "context_growth_messages": 2,
        "repeated_tool_call_count": 1,
        "consecutive_failure_count": 0,
        "total_model_calls": 1,
        "total_tool_calls": 3,
        "estimated_cost": 0.0214,
        "cost_available": True,
        "last_tool_name": "shell_exec",
        "last_tool_risk_level": "high",
        "last_tool_error": True,
    }


def test_guardrail_observation_to_event_payload_is_json_safe() -> None:
    created_at = datetime(2026, 6, 10, 15, 3, 1, 100000, tzinfo=UTC)
    observation = GuardrailObservation(
        stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
        decision=GuardrailDecision.require_approval(
            reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
            message="高风险工具需要人工确认",
            mode=GuardrailMode.ENFORCE,
        ),
        stats=GuardrailRuntimeStats(total_tokens=18420, estimated_cost=0.0219, cost_available=True),
        segment_index=2,
        round_num=5,
        tool_name="shell_exec",
        tool_call_id="call_123",
        tool_risk_level=ToolRiskLevel.HIGH,
        approval_id="approval_abc",
        created_at=created_at,
    )

    assert guardrail_observation_to_event_payload(observation) == {
        "stage": "tool_before_execution",
        "action": "require_approval",
        "reason": "tool_risk_gate_required",
        "message": "高风险工具需要人工确认",
        "mode": "enforce",
        "segment_index": 2,
        "round_num": 5,
        "tool_name": "shell_exec",
        "tool_call_id": "call_123",
        "tool_risk_level": "high",
        "approval_id": "approval_abc",
        "source": "run_runtime",
        "created_at": "2026-06-10T15:03:01.100000+00:00",
        "stats": {
            "total_tokens": 18420,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "elapsed_ms": 0.0,
            "context_growth_messages": 0,
            "repeated_tool_call_count": 0,
            "consecutive_failure_count": 0,
            "total_model_calls": 0,
            "total_tool_calls": 0,
            "estimated_cost": 0.0219,
            "cost_available": True,
            "last_tool_name": None,
            "last_tool_risk_level": None,
            "last_tool_error": False,
        },
    }


def test_merge_guardrail_summary_accumulates_counts_and_runtime_stats() -> None:
    first = GuardrailObservation(
        stage=GuardrailEvaluationStage.MODEL_COMPLETED,
        decision=GuardrailDecision.observe(
            reason=GuardrailReason.CONTEXT_GROWTH_LIMIT,
            message="上下文增长已达到上限",
        ),
        stats=GuardrailRuntimeStats(total_tokens=100, total_model_calls=1),
        segment_index=1,
        round_num=3,
        created_at=datetime(2026, 6, 10, 15, 2, 31, 123456, tzinfo=UTC),
    )

    first_summary = merge_guardrail_summary(None, first, event_cursor=47)

    assert guardrail_summary_to_dict(first_summary) == {
        "mode": "observe",
        "action": "observe",
        "reason": "context_growth_limit",
        "message": "上下文增长已达到上限",
        "estimated_cost": None,
        "metadata": {"source": "run_runtime"},
        "evaluation_count": 1,
        "blocked_count": 0,
        "approval_request_count": 0,
        "last_event_cursor": 47,
        "updated_at": "2026-06-10T15:02:31.123456+00:00",
        "runtime_stats": {
            "total_tokens": 100,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "elapsed_ms": 0.0,
            "context_growth_messages": 0,
            "repeated_tool_call_count": 0,
            "consecutive_failure_count": 0,
            "total_model_calls": 1,
            "total_tool_calls": 0,
            "estimated_cost": None,
            "cost_available": False,
            "last_tool_name": None,
            "last_tool_risk_level": None,
            "last_tool_error": False,
        },
        "stale": False,
        "stale_reason": None,
    }

    second = GuardrailObservation(
        stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
        decision=GuardrailDecision.require_approval(
            reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
            message="高风险工具需要人工确认",
            mode=GuardrailMode.ENFORCE,
            metadata={"extra": "kept"},
        ),
        stats=GuardrailRuntimeStats(
            total_tokens=120,
            total_model_calls=1,
            total_tool_calls=1,
            estimated_cost=0.0214,
            cost_available=True,
            last_tool_name="shell_exec",
            last_tool_risk_level="high",
            last_tool_error=False,
        ),
        segment_index=1,
        round_num=4,
        tool_name="shell_exec",
        tool_call_id="call_123",
        tool_risk_level=ToolRiskLevel.HIGH,
        approval_id="approval_1",
        created_at=datetime(2026, 6, 10, 15, 3, 1, 100000, tzinfo=UTC),
    )

    second_summary = merge_guardrail_summary(first_summary, second, event_cursor=48)

    assert guardrail_summary_to_dict(second_summary) == {
        "mode": "enforce",
        "action": "require_approval",
        "reason": "tool_risk_gate_required",
        "message": "高风险工具需要人工确认",
        "estimated_cost": 0.0214,
        "metadata": {
            "extra": "kept",
            "source": "run_runtime",
            "tool_name": "shell_exec",
            "tool_call_id": "call_123",
            "tool_risk_level": "high",
            "approval_id": "approval_1",
        },
        "evaluation_count": 2,
        "blocked_count": 1,
        "approval_request_count": 1,
        "last_event_cursor": 48,
        "updated_at": "2026-06-10T15:03:01.100000+00:00",
        "runtime_stats": {
            "total_tokens": 120,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "elapsed_ms": 0.0,
            "context_growth_messages": 0,
            "repeated_tool_call_count": 0,
            "consecutive_failure_count": 0,
            "total_model_calls": 1,
            "total_tool_calls": 1,
            "estimated_cost": 0.0214,
            "cost_available": True,
            "last_tool_name": "shell_exec",
            "last_tool_risk_level": "high",
            "last_tool_error": False,
        },
        "stale": False,
        "stale_reason": None,
    }


def test_merge_guardrail_summary_accepts_dict_current_value() -> None:
    observation = GuardrailObservation(
        stage=GuardrailEvaluationStage.TOOL_AFTER_EXECUTION,
        decision=GuardrailDecision.stop(
            reason=GuardrailReason.REPEATED_FAILURE,
            message="连续失败已达到上限",
            mode=GuardrailMode.ENFORCE,
        ),
        stats=GuardrailRuntimeStats(consecutive_failure_count=3),
        segment_index=0,
        created_at=datetime(2026, 6, 10, 15, 4, tzinfo=UTC),
    )

    summary = merge_guardrail_summary(
        {
            "mode": "observe",
            "action": "observe",
            "evaluation_count": 4,
            "blocked_count": 1,
            "approval_request_count": 1,
            "metadata": {"source": "run_runtime"},
            "runtime_stats": {"total_tokens": 50},
        },
        observation,
        event_cursor=49,
    )

    assert summary.evaluation_count == 5
    assert summary.blocked_count == 2
    assert summary.approval_request_count == 1
    assert summary.last_event_cursor == 49
    assert summary.runtime_stats["consecutive_failure_count"] == 3


def test_guardrail_observation_without_created_at_reuses_one_timestamp() -> None:
    observation = GuardrailObservation(
        stage=GuardrailEvaluationStage.MODEL_COMPLETED,
        decision=GuardrailDecision.observe(
            reason=GuardrailReason.CONTEXT_GROWTH_LIMIT,
            message="上下文增长已达到上限",
        ),
        stats=GuardrailRuntimeStats(total_tokens=100, total_model_calls=1),
        segment_index=1,
        round_num=3,
    )

    event_payload = guardrail_observation_to_event_payload(observation)
    summary = merge_guardrail_summary(None, observation, event_cursor=47)

    assert observation.created_at is not None
    assert event_payload["created_at"] == summary.updated_at


def test_mark_guardrail_summary_stale_preserves_existing_counts() -> None:
    stale = mark_guardrail_summary_stale(
        GuardrailSummary(
            mode=GuardrailMode.ENFORCE,
            action=GuardrailAction.STOP,
            reason=GuardrailReason.REPEATED_FAILURE,
            message="连续失败已达到上限",
            estimated_cost=0.3,
            metadata={"source": "run_runtime"},
            evaluation_count=9,
            blocked_count=2,
            approval_request_count=1,
            last_event_cursor=128,
            updated_at="2026-06-10T15:02:31.123456+00:00",
            runtime_stats={"total_tokens": 18234},
        ),
        reason="recovered_without_persisted_guardrail_summary",
        updated_at=datetime(2026, 6, 10, 15, 10, tzinfo=UTC),
    )

    assert guardrail_summary_to_dict(stale) == {
        "mode": "enforce",
        "action": "stop",
        "reason": "repeated_failure",
        "message": "连续失败已达到上限",
        "estimated_cost": 0.3,
        "metadata": {"source": "run_runtime"},
        "evaluation_count": 9,
        "blocked_count": 2,
        "approval_request_count": 1,
        "last_event_cursor": 128,
        "updated_at": "2026-06-10T15:10:00+00:00",
        "runtime_stats": {"total_tokens": 18234},
        "stale": True,
        "stale_reason": "recovered_without_persisted_guardrail_summary",
    }


def test_mark_guardrail_summary_stale_builds_conservative_default_when_missing() -> None:
    stale = mark_guardrail_summary_stale(
        None,
        reason="recovered_without_persisted_guardrail_summary",
        updated_at=datetime(2026, 6, 10, 15, 10, tzinfo=UTC),
    )

    assert guardrail_summary_to_dict(stale) == {
        "mode": "observe",
        "action": "observe",
        "reason": None,
        "message": "guardrail summary recovered conservatively",
        "estimated_cost": None,
        "metadata": {"source": "checkpoint_recovery"},
        "evaluation_count": 0,
        "blocked_count": 0,
        "approval_request_count": 0,
        "last_event_cursor": None,
        "updated_at": "2026-06-10T15:10:00+00:00",
        "runtime_stats": {},
        "stale": True,
        "stale_reason": "recovered_without_persisted_guardrail_summary",
    }
