"""Guardrail 摘要属性测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailMode,
    GuardrailObservation,
    GuardrailReason,
    GuardrailRuntimeStats,
    merge_guardrail_summary,
)


def _decision_strategy() -> st.SearchStrategy[GuardrailDecision]:
    """构造覆盖 allow/observe/require_approval/stop 的决策策略。"""

    reason_st = st.sampled_from(tuple(GuardrailReason))
    mode_st = st.sampled_from(tuple(GuardrailMode))
    message_st = st.text(max_size=40)
    metadata_st = st.dictionaries(
        st.text(min_size=1, max_size=10),
        st.one_of(st.none(), st.text(max_size=20), st.integers(min_value=0, max_value=10)),
        max_size=3,
    )

    allow_st = st.builds(GuardrailDecision.allow)
    observe_st = st.builds(
        GuardrailDecision.observe,
        reason=reason_st,
        message=message_st,
        mode=mode_st,
        metadata=metadata_st,
    )
    require_approval_st = st.builds(
        GuardrailDecision.require_approval,
        reason=reason_st,
        message=message_st,
        mode=mode_st,
        metadata=metadata_st,
    )
    stop_st = st.builds(
        GuardrailDecision.stop,
        reason=reason_st,
        message=message_st,
        mode=mode_st,
        metadata=metadata_st,
    )
    return st.one_of(allow_st, observe_st, require_approval_st, stop_st)


@settings(max_examples=120, deadline=5000)
@given(decisions=st.lists(_decision_strategy(), min_size=1, max_size=50))
def test_merge_guardrail_summary_preserves_counter_ordering(
    decisions: list[GuardrailDecision],
) -> None:
    """任意观测序列下计数都必须满足评估数>=阻断数>=审批请求数。"""

    summary = None
    blocked_count = 0
    approval_request_count = 0

    for index, decision in enumerate(decisions, start=1):
        if decision.action in {
            GuardrailAction.REQUIRE_APPROVAL,
            GuardrailAction.STOP,
        }:
            blocked_count += 1
        if decision.action is GuardrailAction.REQUIRE_APPROVAL:
            approval_request_count += 1

        observation = GuardrailObservation(
            stage=GuardrailEvaluationStage.MODEL_COMPLETED,
            decision=decision,
            stats=GuardrailRuntimeStats(
                total_tokens=index,
                total_model_calls=index,
                estimated_cost=float(index) / 100,
                cost_available=True,
            ),
            segment_index=1,
            round_num=index,
            created_at=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        summary = merge_guardrail_summary(summary, observation, event_cursor=index)

        assert summary is not None
        assert summary.evaluation_count == index
        assert summary.blocked_count == blocked_count
        assert summary.approval_request_count == approval_request_count
        assert summary.evaluation_count >= summary.blocked_count
        assert summary.blocked_count >= summary.approval_request_count
        assert summary.last_event_cursor == index
