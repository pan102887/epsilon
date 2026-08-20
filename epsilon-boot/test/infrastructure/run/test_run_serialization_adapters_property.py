"""Run 序列化 delegating adapter 的等价性单测。

对构造的各值对象，断言 ``adapter.method(v)`` 与对应 serializer 自由函数
``<free_function>(v)`` 结果完全相等，锁定 adapter 只做委托、输出逐字节等价
（Property 4）。serializer 自由函数模块保持不动（ADR-0008）。
"""

from __future__ import annotations

from datetime import datetime

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailMode,
    GuardrailObservation,
    GuardrailReason,
    GuardrailRuntimeStats,
    GuardrailSummary,
    ToolRiskLevel,
)
from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentRunMetadata,
)
from domain.run.workflow import (
    ChildRunOrchestrationState,
    WorkflowCapabilityAction,
    WorkflowCapabilityDecision,
    WorkflowPhase,
    WorkflowPhaseRecord,
    WorkflowRunState,
)
from infrastructure.agent.guardrail_serialization import (
    guardrail_observation_to_event_payload,
    guardrail_summary_to_dict,
)
from infrastructure.agent.segment_serialization import (
    segment_run_metadata_to_http_dict,
)
from infrastructure.run.run_serialization_adapters import (
    GuardrailSerializerAdapter,
    SegmentSerializerAdapter,
    WorkflowSerializerAdapter,
)
from infrastructure.run.workflow_serialization import (
    child_run_orchestration_state_to_dict,
    workflow_capability_decision_to_dict,
    workflow_run_state_to_dict,
)


def test_workflow_run_state_adapter_delegates() -> None:
    """WorkflowSerializerAdapter.workflow_run_state_to_dict 与自由函数等价。"""
    record = WorkflowPhaseRecord(
        phase=WorkflowPhase.PLAN,
        status="completed",
        started_at=datetime(2026, 7, 6, 7, 0, 0),
        completed_at=None,
        summary={},
        error=None,
        revise_count=0,
    )
    value = WorkflowRunState(
        workflow_name="research",
        current_phase=WorkflowPhase.EXECUTE,
        phase_started_at=datetime(2026, 7, 6, 7, 10, 0),
        phase_history=(record,),
        phase_result_summary={"ok": True},
        phase_error_summary=None,
        revise_counts={"execute": 1},
        active_role="worker",
        handoff_state=None,
    )
    adapter = WorkflowSerializerAdapter()

    assert adapter.workflow_run_state_to_dict(value) == workflow_run_state_to_dict(value)


def test_workflow_capability_decision_adapter_delegates() -> None:
    """WorkflowSerializerAdapter.workflow_capability_decision_to_dict 与自由函数等价。"""
    value = WorkflowCapabilityDecision(
        allowed=True,
        action=WorkflowCapabilityAction.DELEGATION,
        role="planner",
        target="worker",
        reason="allowed",
    )
    adapter = WorkflowSerializerAdapter()

    assert adapter.workflow_capability_decision_to_dict(
        value
    ) == workflow_capability_decision_to_dict(value)


def test_child_run_orchestration_state_adapter_delegates() -> None:
    """WorkflowSerializerAdapter.child_run_orchestration_state_to_dict 与自由函数等价。"""
    value = ChildRunOrchestrationState(
        parent_run_id="p-1",
        child_run_id="c-1",
        phase=WorkflowPhase.EVALUATE,
        role=None,
        ownership_status="owned",
        reconciliation_status="pending",
        reason="await",
        updated_at=datetime(2026, 7, 6, 10, 15, 30),
    )
    adapter = WorkflowSerializerAdapter()

    assert adapter.child_run_orchestration_state_to_dict(
        value
    ) == child_run_orchestration_state_to_dict(value)


def test_guardrail_summary_adapter_delegates() -> None:
    """GuardrailSerializerAdapter.guardrail_summary_to_dict 与自由函数等价。"""
    stats = GuardrailRuntimeStats(total_tokens=10, prompt_tokens=6, completion_tokens=4)
    value = GuardrailSummary(
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
        runtime_stats=stats,
        stale=True,
        stale_reason="checkpoint_recovery",
    )
    adapter = GuardrailSerializerAdapter()

    assert adapter.guardrail_summary_to_dict(value) == guardrail_summary_to_dict(value)


def test_guardrail_observation_adapter_delegates() -> None:
    """GuardrailSerializerAdapter.guardrail_observation_to_event_payload 与自由函数等价。"""
    decision = GuardrailDecision(
        action=GuardrailAction.REQUIRE_APPROVAL,
        reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
        message="approval needed",
        mode=GuardrailMode.ENFORCE,
    )
    stats = GuardrailRuntimeStats(total_tokens=1)
    value = GuardrailObservation(
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
    adapter = GuardrailSerializerAdapter()

    assert adapter.guardrail_observation_to_event_payload(
        value
    ) == guardrail_observation_to_event_payload(value)


def test_segment_run_metadata_adapter_delegates() -> None:
    """SegmentSerializerAdapter.segment_run_metadata_to_http_dict 与自由函数等价。"""
    value = SegmentRunMetadata(
        segment_index=2,
        segment_count=4,
        auto_continue_attempted=True,
        segment_stop_reason="max_continuations_reached",
        budget_usage=SegmentBudgetUsage(
            segment_count=4,
            continuation_count=3,
            total_tokens=2048,
            elapsed_ms=987.0,
            consecutive_paused_count=0,
            no_progress_count=1,
            repeated_tool_call_count=0,
        ),
        risk_gate_required=True,
        guardrail_reason="risk_gate",
    )
    adapter = SegmentSerializerAdapter()

    assert adapter.segment_run_metadata_to_http_dict(
        value
    ) == segment_run_metadata_to_http_dict(value)
