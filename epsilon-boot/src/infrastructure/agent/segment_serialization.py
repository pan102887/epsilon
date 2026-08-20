"""Agent 分段执行值对象的共享序列化模块。

抽取 ``SegmentBudgetUsage`` / ``SegmentRunMetadata`` 的 HTTP 响应线格式产出
逻辑，作为基础设施层内部 helper，避免在领域值对象中承载序列化职责，符合
`docs/steering/ddd-tactical-modeling.md` 第 9 节的关注点分离约束。

本模块属于基础设施层内部 helper，仅被 ``infrastructure/`` 与 ``application/``
的分段执行调用点复用，依赖方向为 infrastructure→domain，不向 ``domain/``
反向暴露任何符号，遵循 `docs/steering/ddd-architecture.md` 的依赖方向。
"""

from __future__ import annotations

from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentRunMetadata,
)


def segment_budget_usage_to_dict(value: SegmentBudgetUsage) -> dict[str, int | float]:
    """把 ``SegmentBudgetUsage`` 序列化为 HTTP 响应友好的字典。

    逐字段照搬领域值对象原 ``to_dict`` 的输出形态，确保对外线格式字面等价。

    Args:
        value: 待序列化的分段运行累计预算用量。

    Returns:
        以字段名为键、值为原生 ``int`` / ``float`` 的字典。
    """
    return {
        "segment_count": value.segment_count,
        "continuation_count": value.continuation_count,
        "total_tokens": value.total_tokens,
        "elapsed_ms": value.elapsed_ms,
        "consecutive_paused_count": value.consecutive_paused_count,
        "no_progress_count": value.no_progress_count,
        "repeated_tool_call_count": value.repeated_tool_call_count,
    }


def segment_run_metadata_to_http_dict(value: SegmentRunMetadata) -> dict[str, object]:
    """把 ``SegmentRunMetadata`` 序列化为 HTTP 响应字段字典。

    逐字段照搬领域值对象原 ``to_http_dict`` 的输出形态，其中 ``budget_usage``
    子字典改调本模块 ``segment_budget_usage_to_dict`` 替代原
    ``value.budget_usage.to_dict()``，确保对外线格式字面等价。

    Args:
        value: 待序列化的分段响应元数据。

    Returns:
        以字段名为键的 HTTP 响应字典，``budget_usage`` 为嵌套字典。
    """
    return {
        "segment_index": value.segment_index,
        "segment_count": value.segment_count,
        "auto_continue_attempted": value.auto_continue_attempted,
        "segment_stop_reason": value.segment_stop_reason,
        "budget_usage": segment_budget_usage_to_dict(value.budget_usage),
        "risk_gate_required": value.risk_gate_required,
        "guardrail_reason": value.guardrail_reason,
    }
