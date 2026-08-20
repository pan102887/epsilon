"""Guardrail 护栏领域值对象的线格式序列化映射模块。

集中承载 ``domain/agent/guardrails.py`` 中原 ``GuardrailModelPricing`` /
``GuardrailRuntimeStats`` / ``GuardrailSummary`` 的 ``to_dict`` 以及
``GuardrailObservation.to_event_payload`` 的对外序列化产出，使领域值对象只保留
业务语义与领域行为（``from_raw`` / ``from_model_usage`` / ``estimate_cost`` /
``to_summary`` 等），序列化这一基础设施关注点集中到本层。

本模块属于基础设施层内部 helper，仅被 ``infrastructure/`` 与 ``application/``
消费，不向 ``domain/`` 反向暴露任何符号，依赖方向为
``infrastructure → domain``，遵循 `docs/steering/ddd-architecture.md`。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from domain.agent.guardrails import (
    GuardrailModelPricing,
    GuardrailObservation,
    GuardrailRuntimeStats,
    GuardrailSummary,
)


def _resolve_datetime(value: datetime | None) -> datetime:
    """归一化观测时间，缺失时取当前 UTC，无时区时补 UTC。"""

    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _json_safe(value: Any) -> Any:
    """递归转换为 JSON 安全值（guardrails 版排序与 fallback 语义）。"""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _resolve_datetime(value).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def guardrail_model_pricing_to_dict(
    value: GuardrailModelPricing,
) -> dict[str, float | None]:
    """把模型价格值对象序列化为 JSON-safe 价格字典。

    照搬 split / total 互斥输出规则：当 ``total_per_1m`` 存在时仅暴露总价、
    其余置 ``None``；否则暴露 prompt/completion 单价、总价置 ``None``。
    """

    if value.total_per_1m is not None:
        return {
            "prompt_per_1m": None,
            "completion_per_1m": None,
            "total_per_1m": value.total_per_1m,
        }
    return {
        "prompt_per_1m": value.prompt_per_1m,
        "completion_per_1m": value.completion_per_1m,
        "total_per_1m": None,
    }


def guardrail_runtime_stats_to_dict(
    value: GuardrailRuntimeStats,
) -> dict[str, Any]:
    """把 guardrail 运行时累计统计逐字段序列化为 JSON-safe 字典。"""

    return {
        "total_tokens": value.total_tokens,
        "prompt_tokens": value.prompt_tokens,
        "completion_tokens": value.completion_tokens,
        "elapsed_ms": value.elapsed_ms,
        "context_growth_messages": value.context_growth_messages,
        "repeated_tool_call_count": value.repeated_tool_call_count,
        "consecutive_failure_count": value.consecutive_failure_count,
        "total_model_calls": value.total_model_calls,
        "total_tool_calls": value.total_tool_calls,
        "estimated_cost": value.estimated_cost,
        "cost_available": value.cost_available,
        "last_tool_name": value.last_tool_name,
        "last_tool_risk_level": value.last_tool_risk_level,
        "last_tool_error": value.last_tool_error,
    }


def _coerce_runtime_stats_payload(value: Any) -> dict[str, Any]:
    """把摘要内 ``runtime_stats`` 归一为 JSON-safe 子字典。

    ``GuardrailRuntimeStats`` 走本模块逐字段序列化；普通 dict 经 ``_json_safe``
    递归归一；其余类型回退为空字典，与领域旧实现语义等价。
    """

    if isinstance(value, GuardrailRuntimeStats):
        return guardrail_runtime_stats_to_dict(value)
    if isinstance(value, dict):
        return _json_safe(value)
    return {}


def guardrail_summary_to_dict(value: GuardrailSummary) -> dict[str, Any]:
    """把对外护栏摘要值对象逐字段序列化为 JSON-safe 字典。

    ``reason`` 为 ``None`` 时输出 ``None``，否则输出其 ``.value``；``metadata``
    经 ``_json_safe`` 递归归一；``runtime_stats`` 子字典改调本模块
    ``guardrail_runtime_stats_to_dict`` 对应逻辑保持等价。
    """

    return {
        "mode": value.mode.value,
        "action": value.action.value,
        "reason": value.reason.value if value.reason is not None else None,
        "message": value.message,
        "estimated_cost": value.estimated_cost,
        "metadata": _json_safe(value.metadata),
        "evaluation_count": value.evaluation_count,
        "blocked_count": value.blocked_count,
        "approval_request_count": value.approval_request_count,
        "last_event_cursor": value.last_event_cursor,
        "updated_at": value.updated_at,
        "runtime_stats": _coerce_runtime_stats_payload(value.runtime_stats),
        "stale": value.stale,
        "stale_reason": value.stale_reason,
    }


def guardrail_observation_to_event_payload(
    value: GuardrailObservation,
) -> dict[str, Any]:
    """把一次护栏观测记录序列化为 Run 事件 payload。

    逐字段照搬领域旧 ``to_event_payload`` 的产出形态，内部 ``stats`` 子字典改调
    本模块 ``guardrail_runtime_stats_to_dict`` 保持等价；``created_at`` 已在领域
    构造时补齐时区，此处直接 ``.isoformat()``。
    """

    created_at = _resolve_datetime(value.created_at)
    return {
        "stage": value.stage.value,
        "action": value.decision.action.value,
        "reason": (
            value.decision.reason.value if value.decision.reason is not None else None
        ),
        "message": value.decision.message,
        "mode": value.decision.mode.value,
        "segment_index": value.segment_index,
        "round_num": value.round_num,
        "tool_name": value.tool_name,
        "tool_call_id": value.tool_call_id,
        "tool_risk_level": (
            value.tool_risk_level.value if value.tool_risk_level is not None else None
        ),
        "approval_id": value.approval_id,
        "source": value.source,
        "created_at": created_at.isoformat(),
        "stats": guardrail_runtime_stats_to_dict(value.stats),
    }
