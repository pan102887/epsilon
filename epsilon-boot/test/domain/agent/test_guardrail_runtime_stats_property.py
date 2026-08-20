"""Guardrail 运行时统计属性测试。

本模块聚焦 P1 的确定性统计来源：恢复时只能从已持久化
``guardrail_summary.runtime_stats`` 继续累计新增事实，不得把已经提交的
模型 usage、工具调用或失败记录重复记账。
"""

from __future__ import annotations

from dataclasses import replace

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailModelPricing,
    GuardrailObservation,
    GuardrailRuntimeStats,
    estimate_guardrail_model_cost,
    merge_guardrail_summary,
)
from infrastructure.agent.guardrail_serialization import (
    guardrail_runtime_stats_to_dict,
)

_stats_strategy = st.builds(
    GuardrailRuntimeStats,
    total_tokens=st.integers(min_value=0, max_value=1_000_000),
    prompt_tokens=st.integers(min_value=0, max_value=700_000),
    completion_tokens=st.integers(min_value=0, max_value=300_000),
    elapsed_ms=st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False),
    context_growth_messages=st.integers(min_value=0, max_value=10_000),
    repeated_tool_call_count=st.integers(min_value=0, max_value=1_000),
    consecutive_failure_count=st.integers(min_value=0, max_value=1_000),
    total_model_calls=st.integers(min_value=0, max_value=10_000),
    total_tool_calls=st.integers(min_value=0, max_value=10_000),
    estimated_cost=st.one_of(
        st.none(),
        st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
    ),
    cost_available=st.booleans(),
    last_tool_name=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    last_tool_risk_level=st.one_of(
        st.none(), st.sampled_from(["low", "medium", "high", "critical"])
    ),
    last_tool_error=st.booleans(),
)


@settings(max_examples=120, deadline=5000)
@given(
    persisted=_stats_strategy,
    new_prompt_tokens=st.integers(min_value=0, max_value=10_000),
    new_completion_tokens=st.integers(min_value=0, max_value=10_000),
    new_tool_calls=st.integers(min_value=0, max_value=20),
    new_failures=st.integers(min_value=0, max_value=20),
)
def test_recovered_runtime_stats_only_add_new_model_and_tool_facts(
    persisted: GuardrailRuntimeStats,
    new_prompt_tokens: int,
    new_completion_tokens: int,
    new_tool_calls: int,
    new_failures: int,
) -> None:
    """恢复后统计以持久化快照为基线，只增加当前段真实新增事实。"""

    base = guardrail_runtime_stats_to_dict(persisted)
    recovered = GuardrailRuntimeStats(**base)
    usage_stats = GuardrailRuntimeStats.from_model_usage(
        usage={
            "prompt_tokens": new_prompt_tokens,
            "completion_tokens": new_completion_tokens,
        },
        model="priced-model",
        model_pricing={"priced-model": GuardrailModelPricing(total_per_1m=2.0)},
        elapsed_ms=7.0,
        context_growth_messages=1,
    )
    after_model = GuardrailRuntimeStats(
        total_tokens=recovered.total_tokens + usage_stats.total_tokens,
        prompt_tokens=recovered.prompt_tokens + usage_stats.prompt_tokens,
        completion_tokens=recovered.completion_tokens + usage_stats.completion_tokens,
        elapsed_ms=recovered.elapsed_ms + usage_stats.elapsed_ms,
        context_growth_messages=(
            recovered.context_growth_messages + usage_stats.context_growth_messages
        ),
        repeated_tool_call_count=recovered.repeated_tool_call_count,
        consecutive_failure_count=recovered.consecutive_failure_count,
        total_model_calls=recovered.total_model_calls + 1,
        total_tool_calls=recovered.total_tool_calls,
        estimated_cost=(recovered.estimated_cost or 0.0) + (usage_stats.estimated_cost or 0.0),
        cost_available=usage_stats.cost_available and recovered.estimated_cost is not None,
        last_tool_name=recovered.last_tool_name,
        last_tool_risk_level=recovered.last_tool_risk_level,
        last_tool_error=recovered.last_tool_error,
    )
    after_tools = replace(
        after_model,
        total_tool_calls=after_model.total_tool_calls + new_tool_calls,
        consecutive_failure_count=(
            after_model.consecutive_failure_count + new_failures if new_failures else 0
        ),
    )

    assert (
        after_tools.total_tokens
        == persisted.total_tokens + new_prompt_tokens + new_completion_tokens
    )
    assert after_tools.prompt_tokens == persisted.prompt_tokens + new_prompt_tokens
    assert after_tools.completion_tokens == persisted.completion_tokens + new_completion_tokens
    assert after_tools.total_model_calls == persisted.total_model_calls + 1
    assert after_tools.total_tool_calls == persisted.total_tool_calls + new_tool_calls
    if new_failures:
        assert (
            after_tools.consecutive_failure_count
            == persisted.consecutive_failure_count + new_failures
        )
    else:
        assert after_tools.consecutive_failure_count == 0


@settings(max_examples=80, deadline=5000)
@given(stats=_stats_strategy)
def test_summary_merge_uses_event_stats_once_as_single_runtime_snapshot(
    stats: GuardrailRuntimeStats,
) -> None:
    """摘要合并应直接采用当前观测 stats，不回算或累加旧 summary 统计。"""

    current = {
        "mode": "observe",
        "action": "observe",
        "evaluation_count": 3,
        "blocked_count": 1,
        "approval_request_count": 0,
        "runtime_stats": guardrail_runtime_stats_to_dict(
            GuardrailRuntimeStats(total_tokens=999, total_tool_calls=9)
        ),
    }
    observation = GuardrailObservation(
        stage=GuardrailEvaluationStage.MODEL_COMPLETED,
        decision=GuardrailDecision(action=GuardrailAction.ALLOW),
        stats=stats,
        segment_index=1,
    )

    summary = merge_guardrail_summary(current, observation, event_cursor=11)

    assert summary.evaluation_count == 4
    assert summary.last_event_cursor == 11
    assert summary.runtime_stats == guardrail_runtime_stats_to_dict(stats)


def test_missing_model_pricing_returns_none_without_synthetic_zero_cost() -> None:
    """模型价格缺失时成本不可用，不用 0 成本伪装为可估算。"""

    assert (
        estimate_guardrail_model_cost(
            model="missing-model",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            model_pricing={},
        )
        is None
    )

    stats = GuardrailRuntimeStats.from_model_usage(
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        model="missing-model",
        model_pricing={},
    )

    assert stats.estimated_cost is None
    assert stats.cost_available is False
