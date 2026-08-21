"""guardrail 序列化映射器的字面快照等价性测试。

对 ``guardrail_serialization`` 中 3 个 ``to_dict`` 映射与
``guardrail_observation_to_event_payload`` 各写字面快照断言，锁定线格式；
覆盖 ``GuardrailModelPricing`` split/total 互斥、``reason=None``、``metadata``
空/非空、``datetime`` 补 UTC 等边界。
"""

from __future__ import annotations

from datetime import datetime

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailMode,
    GuardrailModelPricing,
    GuardrailObservation,
    GuardrailReason,
    GuardrailRuntimeStats,
    GuardrailSummary,
    ToolRiskLevel,
)
from infrastructure.agent.guardrail_serialization import (
    guardrail_model_pricing_to_dict,
    guardrail_observation_to_event_payload,
    guardrail_runtime_stats_to_dict,
    guardrail_summary_to_dict,
)


def test_model_pricing_split_snapshot() -> None:
    """prompt/completion 分离价格：total 置 None。"""

    pricing = GuardrailModelPricing(prompt_per_1m=1.5, completion_per_1m=2.0)
    assert guardrail_model_pricing_to_dict(pricing) == {
        "prompt_per_1m": 1.5,
        "completion_per_1m": 2.0,
        "total_per_1m": None,
    }


def test_model_pricing_total_snapshot() -> None:
    """total 价格互斥：split 单价置 None。"""

    pricing = GuardrailModelPricing(total_per_1m=3.0)
    assert guardrail_model_pricing_to_dict(pricing) == {
        "prompt_per_1m": None,
        "completion_per_1m": None,
        "total_per_1m": 3.0,
    }


def test_runtime_stats_snapshot() -> None:
    """14 个字段逐一序列化。"""

    stats = GuardrailRuntimeStats(
        total_tokens=100,
        prompt_tokens=60,
        completion_tokens=40,
        elapsed_ms=12.5,
        context_growth_messages=3,
        repeated_tool_call_count=1,
        consecutive_failure_count=2,
        total_model_calls=5,
        total_tool_calls=4,
        estimated_cost=0.5,
        cost_available=True,
        last_tool_name="shell",
        last_tool_risk_level="high",
        last_tool_error=True,
    )
    assert guardrail_runtime_stats_to_dict(stats) == {
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "elapsed_ms": 12.5,
        "context_growth_messages": 3,
        "repeated_tool_call_count": 1,
        "consecutive_failure_count": 2,
        "total_model_calls": 5,
        "total_tool_calls": 4,
        "estimated_cost": 0.5,
        "cost_available": True,
        "last_tool_name": "shell",
        "last_tool_risk_level": "high",
        "last_tool_error": True,
    }


def test_summary_reason_none_empty_metadata_snapshot() -> None:
    """reason=None 且 metadata 为空的摘要序列化。"""

    summary = GuardrailSummary(
        mode=GuardrailMode.OBSERVE,
        action=GuardrailAction.ALLOW,
    )
    assert guardrail_summary_to_dict(summary) == {
        "mode": "observe",
        "action": "allow",
        "reason": None,
        "message": "",
        "estimated_cost": None,
        "metadata": {},
        "evaluation_count": 0,
        "blocked_count": 0,
        "approval_request_count": 0,
        "last_event_cursor": None,
        "updated_at": None,
        "runtime_stats": {},
        "stale": False,
        "stale_reason": None,
    }


def test_summary_with_reason_and_metadata_snapshot() -> None:
    """reason 非空、metadata 非空、runtime_stats 为值对象的摘要序列化。"""

    stats = GuardrailRuntimeStats(total_tokens=10, prompt_tokens=6, completion_tokens=4)
    summary = GuardrailSummary(
        mode=GuardrailMode.ENFORCE,
        action=GuardrailAction.STOP,
        reason=GuardrailReason.TOKEN_BUDGET_REACHED,
        message="budget reached",
        estimated_cost=1.25,
        metadata={"source": "run_runtime", "tool_name": "shell"},
        evaluation_count=3,
        blocked_count=1,
        approval_request_count=0,
        last_event_cursor=7,
        updated_at="2026-07-06T00:00:00+00:00",
        runtime_stats=guardrail_runtime_stats_to_dict(stats),
        stale=True,
        stale_reason="checkpoint_recovery",
    )
    assert guardrail_summary_to_dict(summary) == {
        "mode": "enforce",
        "action": "stop",
        "reason": "token_budget_reached",
        "message": "budget reached",
        "estimated_cost": 1.25,
        "metadata": {"source": "run_runtime", "tool_name": "shell"},
        "evaluation_count": 3,
        "blocked_count": 1,
        "approval_request_count": 0,
        "last_event_cursor": 7,
        "updated_at": "2026-07-06T00:00:00+00:00",
        "runtime_stats": {
            "total_tokens": 10,
            "prompt_tokens": 6,
            "completion_tokens": 4,
            "elapsed_ms": 0.0,
            "context_growth_messages": 0,
            "repeated_tool_call_count": 0,
            "consecutive_failure_count": 0,
            "total_model_calls": 0,
            "total_tool_calls": 0,
            "estimated_cost": None,
            "cost_available": False,
            "last_tool_name": None,
            "last_tool_risk_level": None,
            "last_tool_error": False,
        },
        "stale": True,
        "stale_reason": "checkpoint_recovery",
    }


def test_observation_to_event_payload_snapshot_with_naive_datetime() -> None:
    """naive datetime 应补 UTC 后 isoformat，reason=None 输出 None。"""

    decision = GuardrailDecision(
        action=GuardrailAction.ALLOW,
        message="ok",
        mode=GuardrailMode.OBSERVE,
    )
    stats = GuardrailRuntimeStats(total_tokens=5, prompt_tokens=3, completion_tokens=2)
    observation = GuardrailObservation(
        stage=GuardrailEvaluationStage.RUN_START,
        decision=decision,
        stats=stats,
        segment_index=0,
        created_at=datetime(2026, 7, 6, 12, 0, 0),
    )
    assert guardrail_observation_to_event_payload(observation) == {
        "stage": "run_start",
        "action": "allow",
        "reason": None,
        "message": "ok",
        "mode": "observe",
        "segment_index": 0,
        "round_num": None,
        "tool_name": None,
        "tool_call_id": None,
        "tool_risk_level": None,
        "approval_id": None,
        "source": "run_runtime",
        "created_at": "2026-07-06T12:00:00+00:00",
        "stats": {
            "total_tokens": 5,
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "elapsed_ms": 0.0,
            "context_growth_messages": 0,
            "repeated_tool_call_count": 0,
            "consecutive_failure_count": 0,
            "total_model_calls": 0,
            "total_tool_calls": 0,
            "estimated_cost": None,
            "cost_available": False,
            "last_tool_name": None,
            "last_tool_risk_level": None,
            "last_tool_error": False,
        },
    }


def test_observation_to_event_payload_snapshot_with_reason_and_tool() -> None:
    """reason 非空、含工具风险等级与 approval_id 的事件 payload。"""

    decision = GuardrailDecision(
        action=GuardrailAction.REQUIRE_APPROVAL,
        reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
        message="approval needed",
        mode=GuardrailMode.ENFORCE,
    )
    stats = GuardrailRuntimeStats(total_tokens=1)
    observation = GuardrailObservation(
        stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
        decision=decision,
        stats=stats,
        segment_index=2,
        round_num=4,
        tool_name="shell",
        tool_call_id="call-1",
        tool_risk_level=ToolRiskLevel.CRITICAL,
        approval_id="appr-1",
        source="run_runtime",
        created_at=datetime.fromisoformat("2026-07-06T08:30:00+00:00"),
    )
    assert guardrail_observation_to_event_payload(observation) == {
        "stage": "tool_before_execution",
        "action": "require_approval",
        "reason": "tool_risk_gate_required",
        "message": "approval needed",
        "mode": "enforce",
        "segment_index": 2,
        "round_num": 4,
        "tool_name": "shell",
        "tool_call_id": "call-1",
        "tool_risk_level": "critical",
        "approval_id": "appr-1",
        "source": "run_runtime",
        "created_at": "2026-07-06T08:30:00+00:00",
        "stats": {
            "total_tokens": 1,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "elapsed_ms": 0.0,
            "context_growth_messages": 0,
            "repeated_tool_call_count": 0,
            "consecutive_failure_count": 0,
            "total_model_calls": 0,
            "total_tool_calls": 0,
            "estimated_cost": None,
            "cost_available": False,
            "last_tool_name": None,
            "last_tool_risk_level": None,
            "last_tool_error": False,
        },
    }
