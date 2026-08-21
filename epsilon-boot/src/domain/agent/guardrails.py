"""Agent 智能调度与护栏领域值对象。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, cast


class TaskExecutionClass(StrEnum):
    """确定性任务类型分类。"""

    SHORT_QA = "short_qa"
    TOOL_TASK = "tool_task"
    LONG_TASK = "long_task"
    BATCH_TASK = "batch_task"


class GuardrailMode(StrEnum):
    """护栏执行模式。"""

    OBSERVE = "observe"
    ENFORCE = "enforce"


class GuardrailAction(StrEnum):
    """护栏决策动作。"""

    ALLOW = "allow"
    OBSERVE = "observe"
    REQUIRE_APPROVAL = "require_approval"
    STOP = "stop"


class GuardrailReason(StrEnum):
    """护栏命中原因。"""

    TOKEN_BUDGET_REACHED = "token_budget_reached"
    DURATION_BUDGET_REACHED = "duration_budget_reached"
    CONTEXT_GROWTH_LIMIT = "context_growth_limit"
    NO_PROGRESS = "no_progress"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    REPEATED_FAILURE = "repeated_failure"
    TOOL_RISK_GATE_REQUIRED = "tool_risk_gate_required"
    UNSAFE_TOOL_INPUT = "unsafe_tool_input"


class ToolRiskLevel(StrEnum):
    """工具风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardrailEvaluationStage(StrEnum):
    """护栏评估发生的运行时阶段。"""

    RUN_START = "run_start"
    MODEL_COMPLETED = "model_completed"
    TOOL_BEFORE_EXECUTION = "tool_before_execution"
    TOOL_AFTER_EXECUTION = "tool_after_execution"
    RECOVERY_RESTORED = "recovery_restored"


@dataclass(frozen=True)
class GuardrailModelPricing:
    """单个模型按每 1M token 计费的 guardrail 成本配置。"""

    prompt_per_1m: float | None = None
    completion_per_1m: float | None = None
    total_per_1m: float | None = None

    def __post_init__(self) -> None:
        """校验价格字段组合并归一为非负浮点数。"""

        prompt_price = _coerce_optional_price(self.prompt_per_1m, "prompt_per_1m")
        completion_price = _coerce_optional_price(
            self.completion_per_1m,
            "completion_per_1m",
        )
        total_price = _coerce_optional_price(self.total_per_1m, "total_per_1m")
        has_split_prices = prompt_price is not None or completion_price is not None
        if total_price is not None and has_split_prices:
            raise ValueError("total_per_1m 不得与 prompt/completion 单价同时配置")
        if has_split_prices and (prompt_price is None or completion_price is None):
            raise ValueError("prompt_per_1m 与 completion_per_1m 必须同时配置")
        object.__setattr__(self, "prompt_per_1m", prompt_price)
        object.__setattr__(self, "completion_per_1m", completion_price)
        object.__setattr__(self, "total_per_1m", total_price)

    @property
    def available(self) -> bool:
        """返回该模型价格是否足以执行成本估算。"""

        return self.total_per_1m is not None or (
            self.prompt_per_1m is not None and self.completion_per_1m is not None
        )

    def estimate_cost(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> float | None:
        """按 token usage 估算成本；价格缺失时返回 None。"""

        if self.total_per_1m is not None:
            return max(total_tokens, 0) * self.total_per_1m / 1_000_000
        if self.prompt_per_1m is None or self.completion_per_1m is None:
            return None
        return (
            max(prompt_tokens, 0) * self.prompt_per_1m
            + max(completion_tokens, 0) * self.completion_per_1m
        ) / 1_000_000

    @classmethod
    def from_raw(cls, value: Any) -> GuardrailModelPricing:
        """从旧标量或新对象格式构造模型价格。"""

        if isinstance(value, GuardrailModelPricing):
            return value
        if isinstance(value, Mapping):
            pricing = cast(Mapping[str, Any], value)
            return cls(
                prompt_per_1m=pricing.get("prompt_per_1m"),
                completion_per_1m=pricing.get("completion_per_1m"),
                total_per_1m=pricing.get("total_per_1m"),
            )
        return cls(total_per_1m=value)


@dataclass(frozen=True)
class GuardrailPolicy:
    """护栏策略阈值。"""

    enabled: bool = True
    mode: GuardrailMode = GuardrailMode.OBSERVE
    enforce_critical_tools: bool = True
    enforce_high_risk_tools: bool = False
    max_total_tokens: int | None = None
    max_duration_seconds: float | None = None
    max_context_growth_messages: int | None = None
    max_repeated_tool_calls: int = 2
    max_consecutive_failures: int = 3
    model_pricing: dict[str, GuardrailModelPricing] = field(
        default_factory=dict[str, GuardrailModelPricing]
    )

    def __post_init__(self) -> None:
        """校验策略阈值并归一化模型价格配置。"""

        object.__setattr__(self, "mode", _coerce_guardrail_mode(self.mode))
        object.__setattr__(self, "model_pricing", _coerce_model_pricing_map(self.model_pricing))
        if self.max_total_tokens is not None and self.max_total_tokens <= 0:
            raise ValueError("max_total_tokens 必须为 None 或大于 0")
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds 必须为 None 或大于 0")
        if self.max_context_growth_messages is not None and self.max_context_growth_messages <= 0:
            raise ValueError("max_context_growth_messages 必须为 None 或大于 0")
        if self.max_repeated_tool_calls <= 0:
            raise ValueError("max_repeated_tool_calls 必须大于 0")
        if self.max_consecutive_failures <= 0:
            raise ValueError("max_consecutive_failures 必须大于 0")


@dataclass(frozen=True)
class GuardrailRuntimeStats:
    """Guardrail 运行时累计统计。"""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: float = 0.0
    context_growth_messages: int = 0
    repeated_tool_call_count: int = 0
    consecutive_failure_count: int = 0
    total_model_calls: int = 0
    total_tool_calls: int = 0
    estimated_cost: float | None = None
    cost_available: bool = False
    last_tool_name: str | None = None
    last_tool_risk_level: str | None = None
    last_tool_error: bool = False

    def __post_init__(self) -> None:
        """归一化累计统计，确保公开字段保持非负且成本状态一致。"""

        for field_name in (
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "context_growth_messages",
            "repeated_tool_call_count",
            "consecutive_failure_count",
            "total_model_calls",
            "total_tool_calls",
        ):
            object.__setattr__(
                self,
                field_name,
                _coerce_non_negative_int(getattr(self, field_name)),
            )
        object.__setattr__(self, "elapsed_ms", max(_coerce_float(self.elapsed_ms), 0.0))
        estimated_cost = _coerce_optional_float(self.estimated_cost)
        if estimated_cost is not None:
            estimated_cost = max(estimated_cost, 0.0)
        object.__setattr__(self, "estimated_cost", estimated_cost)
        object.__setattr__(
            self, "cost_available", bool(self.cost_available and estimated_cost is not None)
        )

    @classmethod
    def from_model_usage(
        cls,
        *,
        usage: Mapping[str, Any] | None,
        model: str | None,
        model_pricing: Mapping[str, Any] | None,
        elapsed_ms: float = 0.0,
        context_growth_messages: int = 0,
        total_model_calls: int = 1,
        total_tool_calls: int = 0,
        repeated_tool_call_count: int = 0,
        consecutive_failure_count: int = 0,
    ) -> GuardrailRuntimeStats:
        """基于真实模型 usage 与价格表构造模型调用统计。

        价格缺失或模型未配置时仅把成本标记为不可用，不改变调用方的
        guardrail 决策动作，满足长任务运行时的保守降级语义。
        """

        usage_data = dict(usage or {})
        prompt_tokens = _coerce_non_negative_int(usage_data.get("prompt_tokens"))
        completion_tokens = _coerce_non_negative_int(usage_data.get("completion_tokens"))
        total_tokens = _coerce_non_negative_int(usage_data.get("total_tokens"))
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        estimated_cost = estimate_guardrail_model_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model_pricing=model_pricing,
        )
        return cls(
            total_tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_ms=elapsed_ms,
            context_growth_messages=context_growth_messages,
            repeated_tool_call_count=repeated_tool_call_count,
            consecutive_failure_count=consecutive_failure_count,
            total_model_calls=total_model_calls,
            total_tool_calls=total_tool_calls,
            estimated_cost=estimated_cost,
            cost_available=estimated_cost is not None,
        )


@dataclass(frozen=True)
class GuardrailSummary:
    """对外展示的护栏摘要。"""

    mode: GuardrailMode
    action: GuardrailAction
    reason: GuardrailReason | None = None
    message: str = ""
    estimated_cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    evaluation_count: int = 0
    blocked_count: int = 0
    approval_request_count: int = 0
    last_event_cursor: int | None = None
    updated_at: str | None = None
    runtime_stats: dict[str, Any] = field(default_factory=dict[str, Any])
    stale: bool = False
    stale_reason: str | None = None


@dataclass(frozen=True)
class GuardrailDecision:
    """单次护栏评估决策。"""

    action: GuardrailAction
    reason: GuardrailReason | None = None
    message: str = ""
    mode: GuardrailMode = GuardrailMode.OBSERVE
    estimated_cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])

    @property
    def public_terminal_reason(self) -> str | None:
        """返回对外稳定终止原因。"""

        if self.action in {GuardrailAction.REQUIRE_APPROVAL, GuardrailAction.STOP}:
            return "guardrail_blocked"
        return None

    @classmethod
    def allow(cls) -> GuardrailDecision:
        """允许继续执行。"""

        return cls(action=GuardrailAction.ALLOW)

    @classmethod
    def observe(
        cls,
        *,
        reason: GuardrailReason,
        message: str,
        mode: GuardrailMode = GuardrailMode.OBSERVE,
        metadata: dict[str, Any] | None = None,
    ) -> GuardrailDecision:
        """记录观察事件但不阻断。"""

        return cls(
            action=GuardrailAction.OBSERVE,
            reason=reason,
            message=message,
            mode=mode,
            metadata=metadata or {},
        )

    @classmethod
    def require_approval(
        cls,
        *,
        reason: GuardrailReason,
        message: str,
        mode: GuardrailMode = GuardrailMode.ENFORCE,
        metadata: dict[str, Any] | None = None,
    ) -> GuardrailDecision:
        """要求人工确认。"""

        return cls(
            action=GuardrailAction.REQUIRE_APPROVAL,
            reason=reason,
            message=message,
            mode=mode,
            metadata=metadata or {},
        )

    @classmethod
    def stop(
        cls,
        *,
        reason: GuardrailReason,
        message: str,
        mode: GuardrailMode = GuardrailMode.ENFORCE,
        metadata: dict[str, Any] | None = None,
    ) -> GuardrailDecision:
        """停止继续执行。"""

        return cls(
            action=GuardrailAction.STOP,
            reason=reason,
            message=message,
            mode=mode,
            metadata=metadata or {},
        )

    def to_summary(self) -> GuardrailSummary:
        """转换为最近一次动作的摘要基底。"""

        return GuardrailSummary(
            mode=self.mode,
            action=self.action,
            reason=self.reason,
            message=self.message,
            estimated_cost=self.estimated_cost,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class GuardrailObservation:
    """一次可持久化的护栏观测记录。"""

    stage: GuardrailEvaluationStage
    decision: GuardrailDecision
    stats: GuardrailRuntimeStats
    segment_index: int
    round_num: int | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_risk_level: ToolRiskLevel | None = None
    approval_id: str | None = None
    source: str = "run_runtime"
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """归一化观测时间，确保同一观测共享同一运行时事实时间戳。"""

        object.__setattr__(self, "created_at", _resolve_datetime(self.created_at))


@dataclass(frozen=True)
class GuardrailEvaluationContext:
    """护栏评估输入上下文。"""

    task_classification: TaskExecutionClass | None = None
    tool_name: str | None = None
    tool_risk_level: ToolRiskLevel | None = None
    total_tokens: int = 0
    elapsed_ms: float = 0.0
    context_growth_messages: int = 0
    repeated_tool_call_count: int = 0
    consecutive_failure_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


def estimate_guardrail_model_cost(
    *,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    model_pricing: Mapping[str, Any] | None,
) -> float | None:
    """按模型价格表估算成本；缺失价格时返回 None。

    该函数只负责确定性成本估算，不参与阻断决策；调用方应把 None
    映射为 ``cost_available=false`` 并继续保持原有运行语义。
    """

    if not model:
        return None
    pricing = _coerce_model_pricing_map(model_pricing).get(model)
    if pricing is None or not pricing.available:
        return None
    return pricing.estimate_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def merge_guardrail_summary(
    current: dict[str, Any] | GuardrailSummary | None,
    observation: GuardrailObservation,
    *,
    event_cursor: int,
) -> GuardrailSummary:
    """基于既有摘要与新观测计算下一版 Guardrail_Summary。"""

    previous = _coerce_guardrail_summary(current)
    latest = observation.decision.to_summary()
    blocked_increment = int(
        observation.decision.action in {GuardrailAction.REQUIRE_APPROVAL, GuardrailAction.STOP}
    )
    approval_increment = int(observation.decision.action is GuardrailAction.REQUIRE_APPROVAL)
    estimated_cost = latest.estimated_cost
    if estimated_cost is None:
        estimated_cost = observation.stats.estimated_cost
    return GuardrailSummary(
        mode=latest.mode,
        action=latest.action,
        reason=latest.reason,
        message=latest.message,
        estimated_cost=estimated_cost,
        metadata=_build_observation_metadata(latest.metadata, observation),
        evaluation_count=(previous.evaluation_count if previous is not None else 0) + 1,
        blocked_count=(previous.blocked_count if previous is not None else 0) + blocked_increment,
        approval_request_count=(
            (previous.approval_request_count if previous is not None else 0) + approval_increment
        ),
        last_event_cursor=event_cursor,
        updated_at=(observation.created_at or datetime.now(UTC)).isoformat(),
        runtime_stats=_runtime_stats_payload(observation.stats),
        stale=False,
        stale_reason=None,
    )


def mark_guardrail_summary_stale(
    current: dict[str, Any] | GuardrailSummary | None,
    *,
    reason: str,
    updated_at: datetime,
) -> GuardrailSummary:
    """把恢复后的摘要显式标记为保守过期状态。"""

    previous = _coerce_guardrail_summary(current)
    if previous is None:
        previous = GuardrailSummary(
            mode=GuardrailMode.OBSERVE,
            action=GuardrailAction.OBSERVE,
            message="guardrail summary recovered conservatively",
            metadata={"source": "checkpoint_recovery"},
        )
    return GuardrailSummary(
        mode=previous.mode,
        action=previous.action,
        reason=previous.reason,
        message=previous.message,
        estimated_cost=previous.estimated_cost,
        metadata=dict(previous.metadata),
        evaluation_count=previous.evaluation_count,
        blocked_count=previous.blocked_count,
        approval_request_count=previous.approval_request_count,
        last_event_cursor=previous.last_event_cursor,
        updated_at=updated_at.isoformat(),
        runtime_stats=_coerce_runtime_stats_payload(previous.runtime_stats),
        stale=True,
        stale_reason=reason,
    )


def _build_observation_metadata(
    base_metadata: dict[str, Any],
    observation: GuardrailObservation,
) -> dict[str, Any]:
    metadata = _coerce_mapping(base_metadata)
    metadata["source"] = observation.source
    if observation.tool_name is not None:
        metadata["tool_name"] = observation.tool_name
    if observation.tool_call_id is not None:
        metadata["tool_call_id"] = observation.tool_call_id
    if observation.tool_risk_level is not None:
        metadata["tool_risk_level"] = observation.tool_risk_level.value
    if observation.approval_id is not None:
        metadata["approval_id"] = observation.approval_id
    return metadata


def _coerce_guardrail_summary(
    current: dict[str, Any] | GuardrailSummary | None,
) -> GuardrailSummary | None:
    if current is None:
        return None
    if isinstance(current, GuardrailSummary):
        return current
    return GuardrailSummary(
        mode=_coerce_enum(current.get("mode"), GuardrailMode, GuardrailMode.OBSERVE),
        action=_coerce_enum(
            current.get("action"),
            GuardrailAction,
            GuardrailAction.OBSERVE,
        ),
        reason=_coerce_enum(current.get("reason"), GuardrailReason, None),
        message=_coerce_optional_str(current.get("message")) or "",
        estimated_cost=_coerce_optional_float(current.get("estimated_cost")),
        metadata=_coerce_mapping(current.get("metadata")),
        evaluation_count=_coerce_non_negative_int(current.get("evaluation_count")),
        blocked_count=_coerce_non_negative_int(current.get("blocked_count")),
        approval_request_count=_coerce_non_negative_int(current.get("approval_request_count")),
        last_event_cursor=_coerce_optional_int(current.get("last_event_cursor")),
        updated_at=_coerce_optional_str(current.get("updated_at")),
        runtime_stats=_coerce_runtime_stats_payload(current.get("runtime_stats")),
        stale=bool(current.get("stale", False)),
        stale_reason=_coerce_optional_str(current.get("stale_reason")),
    )


def _runtime_stats_payload(value: GuardrailRuntimeStats) -> dict[str, Any]:
    """领域内部用：把运行时累计统计逐字段序列化为 JSON-safe 子字典。

    仅供 guardrails 领域内部编排（如 ``merge_guardrail_summary`` 与
    ``_coerce_runtime_stats_payload``）生成 ``runtime_stats`` 子字典使用；
    对外的线格式序列化由 ``infrastructure/agent/guardrail_serialization.py``
    的 ``guardrail_runtime_stats_to_dict`` 承载，两者产出保持等价。
    """

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
    if isinstance(value, GuardrailRuntimeStats):
        return _runtime_stats_payload(value)
    if isinstance(value, dict):
        return _json_safe(value)
    return {}


def _coerce_model_pricing_map(value: object) -> dict[str, GuardrailModelPricing]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, GuardrailModelPricing] = {}
    for model_name, pricing in cast(Mapping[object, object], value).items():
        if not isinstance(model_name, str) or not model_name:
            continue
        result[model_name] = GuardrailModelPricing.from_raw(pricing)
    return result


def _coerce_guardrail_mode(value: object) -> GuardrailMode:
    """把外部策略模式收窄为 ``GuardrailMode``。"""

    if isinstance(value, GuardrailMode):
        return value
    return GuardrailMode(str(value))


def _coerce_optional_price(value: Any, field_name: str) -> float | None:
    price = _coerce_optional_float(value)
    if price is None:
        return None
    if price < 0:
        raise ValueError(f"{field_name} 不得小于 0")
    return price


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _json_safe(value)


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _coerce_optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_enum(value: Any, enum_type: type[Enum], default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError:
        return default


def _resolve_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _resolve_datetime(value).isoformat()
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        items = cast(list[object] | tuple[object, ...], value)
        return [_json_safe(item) for item in items]
    if isinstance(value, (set, frozenset)):
        items = cast(set[object] | frozenset[object], value)
        return [_json_safe(item) for item in sorted(items, key=str)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def json_safe(value: Any) -> Any:
    """将护栏元数据转换为可安全写入 JSON 的值。"""
    return _json_safe(value)
