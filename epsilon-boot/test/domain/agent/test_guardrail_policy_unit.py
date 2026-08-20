"""领域护栏策略 guardrail_policy 单元测试（脱离运行时）。

仅 import domain.*，覆盖 StaticAgentGuardrailPolicy 全部纯判定分支：
classify_run / classify_payload 任务分类、_budget_decision 五类阈值、
evaluate_* 委托与风险门、_risk_decision metadata 归一、_looks_batch /
_segment_count 启发式边界，逐一锁定上提前后行为等价（Behavior_Equivalent_Refactor）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.agent.guardrail_policy import (
    StaticAgentGuardrailPolicy,
    _looks_batch,
    _segment_count,
)
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


def _snapshot(
    *,
    payload: RunPayload,
    latest_checkpoint_id: str | None = None,
    can_continue: bool = False,
    segment_metadata: dict | None = None,
) -> RunSnapshot:
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
        can_continue=can_continue,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
        latest_checkpoint_id=latest_checkpoint_id,
    )


def _chat_payload(chat: dict | None = None) -> RunPayload:
    return RunPayload(kind=RunKind.CHAT, session_id="s1", chat=chat or {"message": "hi"})


def _task_payload(task: dict | None = None) -> RunPayload:
    return RunPayload(kind=RunKind.TASK, session_id="s1", task=task or {"goal": "g"})


# --------------------------------------------------------------------------- #
# classify_run（Property 1）
# --------------------------------------------------------------------------- #
def test_classify_run_checkpoint_triggers_long_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    snapshot = _snapshot(payload=_chat_payload(), latest_checkpoint_id="chk-1")

    assert policy.classify_run(snapshot) is TaskExecutionClass.LONG_TASK


def test_classify_run_can_continue_triggers_long_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    snapshot = _snapshot(payload=_chat_payload(), can_continue=True)

    assert policy.classify_run(snapshot) is TaskExecutionClass.LONG_TASK


def test_classify_run_segment_count_gt_one_triggers_long_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    snapshot = _snapshot(payload=_chat_payload(), segment_metadata={"segment_count": 2})

    assert policy.classify_run(snapshot) is TaskExecutionClass.LONG_TASK


def test_classify_run_no_trigger_delegates_to_classify_payload() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    # 无 checkpoint / 不可续 / segment_count<=1 时委托 classify_payload(payload, has_tools=True)。
    snapshot = _snapshot(
        payload=_chat_payload({"message": "hi"}),
        segment_metadata={"segment_count": 1},
    )

    assert policy.classify_run(snapshot) is TaskExecutionClass.TOOL_TASK


# --------------------------------------------------------------------------- #
# classify_payload（Property 1）
# --------------------------------------------------------------------------- #
def test_classify_payload_batch_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())
    payload = _task_payload({"goal": "process", "items": ["a", "b"]})

    assert policy.classify_payload(payload, has_tools=True) is TaskExecutionClass.BATCH_TASK


def test_classify_payload_task_with_tools_is_tool_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())

    assert policy.classify_payload(_task_payload(), has_tools=True) is TaskExecutionClass.TOOL_TASK


def test_classify_payload_task_without_tools_is_long_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())

    assert policy.classify_payload(_task_payload(), has_tools=False) is TaskExecutionClass.LONG_TASK


def test_classify_payload_chat_with_tools_is_tool_task() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())

    assert policy.classify_payload(_chat_payload(), has_tools=True) is TaskExecutionClass.TOOL_TASK


def test_classify_payload_chat_without_tools_is_short_qa() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy())

    assert policy.classify_payload(_chat_payload(), has_tools=False) is TaskExecutionClass.SHORT_QA


# --------------------------------------------------------------------------- #
# _budget_decision 五类阈值命中 / 未命中（Property 2）
# --------------------------------------------------------------------------- #
def test_budget_token_reached_enforce_stops() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_total_tokens=10)
    )

    decision = policy.evaluate_model_completed(GuardrailEvaluationContext(total_tokens=10))

    assert decision.action is GuardrailAction.STOP
    assert decision.reason is GuardrailReason.TOKEN_BUDGET_REACHED


def test_budget_token_none_short_circuits() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_total_tokens=None)
    )

    decision = policy.evaluate_model_completed(GuardrailEvaluationContext(total_tokens=10_000))

    assert decision.action is GuardrailAction.ALLOW


def test_budget_duration_reached_uses_seconds_times_1000() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_duration_seconds=2)
    )

    # 2 秒 = 2000ms，恰好命中 >= 边界。
    decision = policy.evaluate_run_start(GuardrailEvaluationContext(elapsed_ms=2000))

    assert decision.action is GuardrailAction.STOP
    assert decision.reason is GuardrailReason.DURATION_BUDGET_REACHED


def test_budget_duration_below_threshold_allows() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_duration_seconds=2)
    )

    decision = policy.evaluate_run_start(GuardrailEvaluationContext(elapsed_ms=1999))

    assert decision.action is GuardrailAction.ALLOW


def test_budget_duration_none_short_circuits() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_duration_seconds=None)
    )

    decision = policy.evaluate_run_start(GuardrailEvaluationContext(elapsed_ms=10_000_000))

    assert decision.action is GuardrailAction.ALLOW


def test_budget_context_growth_reached_stops() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_context_growth_messages=3)
    )

    decision = policy.evaluate_model_completed(
        GuardrailEvaluationContext(context_growth_messages=3)
    )

    assert decision.action is GuardrailAction.STOP
    assert decision.reason is GuardrailReason.CONTEXT_GROWTH_LIMIT


def test_budget_context_growth_none_short_circuits() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_context_growth_messages=None)
    )

    decision = policy.evaluate_model_completed(
        GuardrailEvaluationContext(context_growth_messages=999)
    )

    assert decision.action is GuardrailAction.ALLOW


def test_budget_repeated_tool_reached_stops() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_repeated_tool_calls=2)
    )

    decision = policy.evaluate_tool_after_execution(
        GuardrailEvaluationContext(repeated_tool_call_count=2)
    )

    assert decision.action is GuardrailAction.STOP
    assert decision.reason is GuardrailReason.REPEATED_TOOL_CALL


def test_budget_repeated_tool_below_threshold_allows() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_repeated_tool_calls=2)
    )

    decision = policy.evaluate_tool_after_execution(
        GuardrailEvaluationContext(repeated_tool_call_count=1)
    )

    assert decision.action is GuardrailAction.ALLOW


def test_budget_consecutive_failure_reached_stops() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_consecutive_failures=3)
    )

    decision = policy.evaluate_tool_after_execution(
        GuardrailEvaluationContext(consecutive_failure_count=3)
    )

    assert decision.action is GuardrailAction.STOP
    assert decision.reason is GuardrailReason.REPEATED_FAILURE


def test_budget_consecutive_failure_below_threshold_allows() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_consecutive_failures=3)
    )

    decision = policy.evaluate_tool_after_execution(
        GuardrailEvaluationContext(consecutive_failure_count=2)
    )

    assert decision.action is GuardrailAction.ALLOW


def test_budget_observe_mode_records_instead_of_stop() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.OBSERVE, max_total_tokens=10)
    )

    decision = policy.evaluate_model_completed(GuardrailEvaluationContext(total_tokens=10))

    assert decision.action is GuardrailAction.OBSERVE
    assert decision.reason is GuardrailReason.TOKEN_BUDGET_REACHED


def test_budget_check_order_token_takes_precedence() -> None:
    # token 与 consecutive_failure 同时满足时，token 检查排在首位胜出。
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(
            mode=GuardrailMode.ENFORCE,
            max_total_tokens=10,
            max_consecutive_failures=1,
        )
    )

    decision = policy.evaluate_model_completed(
        GuardrailEvaluationContext(total_tokens=100, consecutive_failure_count=100)
    )

    assert decision.reason is GuardrailReason.TOKEN_BUDGET_REACHED


def test_budget_no_match_allows() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE))

    decision = policy.evaluate_run_start(GuardrailEvaluationContext())

    assert decision.action is GuardrailAction.ALLOW


def test_delegating_evaluate_methods_agree_with_budget() -> None:
    # evaluate_run_start / evaluate_model_completed / evaluate_tool_after_execution
    # 在同一 context 下与 _budget_decision 结果一致。
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_total_tokens=10)
    )
    context = GuardrailEvaluationContext(total_tokens=20)

    start = policy.evaluate_run_start(context)
    completed = policy.evaluate_model_completed(context)
    after = policy.evaluate_tool_after_execution(context)

    assert start.action is completed.action is after.action is GuardrailAction.STOP
    assert start.reason is completed.reason is after.reason is GuardrailReason.TOKEN_BUDGET_REACHED


# --------------------------------------------------------------------------- #
# evaluate_tool_before_execution 风险门（Property 3）
# --------------------------------------------------------------------------- #
def test_before_execution_budget_short_circuits_risk_gate() -> None:
    # 预算非 ALLOW 时直接返回预算决策，不进入风险门。
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, max_total_tokens=10)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(
            total_tokens=100,
            tool_name="shell_exec",
            tool_risk_level=ToolRiskLevel.CRITICAL,
        )
    )

    assert decision.reason is GuardrailReason.TOKEN_BUDGET_REACHED


def test_before_execution_critical_enforced_stops() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_critical_tools=True)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.STOP


def test_before_execution_critical_not_enforced_allows() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_critical_tools=False)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.ALLOW


def test_before_execution_high_enforced_requires_approval() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=True)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="http_request", tool_risk_level=ToolRiskLevel.HIGH)
    )

    assert decision.action is GuardrailAction.REQUIRE_APPROVAL


def test_before_execution_high_not_enforced_allows() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=False)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="http_request", tool_risk_level=ToolRiskLevel.HIGH)
    )

    assert decision.action is GuardrailAction.ALLOW


def test_before_execution_observe_mode_downgrades_to_observe() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE))

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.OBSERVE
    assert decision.reason is GuardrailReason.TOOL_RISK_GATE_REQUIRED


# --------------------------------------------------------------------------- #
# _risk_decision metadata 归一专项（Property 3 / T-1.3 / AC3.4）
# --------------------------------------------------------------------------- #
def test_risk_metadata_critical_enforce_stop() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_critical_tools=True)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.STOP
    assert decision.metadata == {"tool_name": "shell_exec", "risk_level": "critical"}


def test_risk_metadata_high_enforce_require_approval() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=True)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="http_request", tool_risk_level=ToolRiskLevel.HIGH)
    )

    assert decision.action is GuardrailAction.REQUIRE_APPROVAL
    assert decision.metadata == {"tool_name": "http_request", "risk_level": "high"}


def test_risk_metadata_observe_downgrade() -> None:
    policy = StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE))

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name="shell_exec", tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.action is GuardrailAction.OBSERVE
    assert decision.metadata == {"tool_name": "shell_exec", "risk_level": "critical"}


def test_risk_metadata_tool_name_none_passthrough() -> None:
    policy = StaticAgentGuardrailPolicy(
        GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_critical_tools=True)
    )

    decision = policy.evaluate_tool_before_execution(
        GuardrailEvaluationContext(tool_name=None, tool_risk_level=ToolRiskLevel.CRITICAL)
    )

    assert decision.metadata == {"tool_name": None, "risk_level": "critical"}


# --------------------------------------------------------------------------- #
# _looks_batch 边界（Property 4）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["items", "batch", "targets", "inputs"])
def test_looks_batch_list_len_two_is_batch(key: str) -> None:
    assert _looks_batch({key: ["a", "b"]}) is True


@pytest.mark.parametrize("key", ["items", "batch", "targets", "inputs"])
def test_looks_batch_list_len_one_is_not_batch(key: str) -> None:
    assert _looks_batch({key: ["a"]}) is False


def test_looks_batch_empty_list_is_not_batch() -> None:
    assert _looks_batch({"items": []}) is False


def test_looks_batch_non_list_value_is_not_batch() -> None:
    assert _looks_batch({"items": "a,b,c"}) is False


def test_looks_batch_constraints_with_keyword_is_batch() -> None:
    assert _looks_batch({"constraints": ["请批量处理"]}) is True


def test_looks_batch_constraints_without_keyword_is_not_batch() -> None:
    assert _looks_batch({"constraints": ["逐条处理"]}) is False


def test_looks_batch_empty_dict_is_not_batch() -> None:
    assert _looks_batch({}) is False


# --------------------------------------------------------------------------- #
# _segment_count 边界（Property 4）
# --------------------------------------------------------------------------- #
def test_segment_count_none_metadata_returns_zero() -> None:
    assert _segment_count(None) == 0


def test_segment_count_non_dict_returns_zero() -> None:
    assert _segment_count("not-a-dict") == 0  # type: ignore[arg-type]


def test_segment_count_missing_key_returns_zero() -> None:
    assert _segment_count({}) == 0


def test_segment_count_valid_int() -> None:
    assert _segment_count({"segment_count": 3}) == 3


def test_segment_count_numeric_string_coerced() -> None:
    assert _segment_count({"segment_count": "4"}) == 4


def test_segment_count_non_numeric_returns_zero() -> None:
    assert _segment_count({"segment_count": "abc"}) == 0


def test_segment_count_none_value_returns_zero() -> None:
    assert _segment_count({"segment_count": None}) == 0
