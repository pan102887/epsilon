"""ReAct guardrail 运行时统计累加器与执行链路 ContextVar。

本模块从 ``react_agent_adapter.py`` 逐字迁出 guardrail 运行时统计累加器
``GuardrailRuntimeAccumulator``（原 ``_GuardrailRuntimeAccumulator``）及其配套
的安全数值转换 helper 与执行链路 ContextVar，供门面 ``ReActAgentAdapter``
引用。属基础设施层内部 SRP 拆分产物，逻辑与原实现完全一致（行为等价）。

模块 import 限于 ``domain`` 值对象类型、
``infrastructure.agent.tool_abuse_detector.ToolAbuseDetector`` 与标准库，不引入
新的跨层依赖（ADR-0013 / 分层方向不变）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast

from domain.agent.guardrails import GuardrailRuntimeStats, ToolRiskLevel
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.tool_abuse_detector import ToolAbuseDetector


def _safe_int(value: Any) -> int:
    """把外部统计值安全转换为非负整数。"""

    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    """把外部统计值安全转换为非负浮点数。"""

    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_optional_float(value: Any) -> float | None:
    """把外部统计值安全转换为可选非负浮点数。"""

    try:
        return max(float(value), 0.0) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_optional_str(value: Any) -> str | None:
    """把外部统计值安全转换为可选字符串。"""

    return str(value) if value is not None else None


def _normalize_tool_arguments(arguments: str | None) -> str:
    """把工具参数规范化为用于重复调用统计的稳定字符串。"""

    raw = "" if arguments is None else str(arguments)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw
    return json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


@dataclass
class GuardrailRuntimeAccumulator:
    """在单次 ReAct 执行内累计 guardrail 真实运行时统计。"""

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
    cost_unavailable_seen: bool = False
    last_tool_name: str | None = None
    last_tool_risk_level: str | None = None
    last_tool_error: bool = False
    context_key: tuple[str | None, str | None, int | None] = (None, None, None)
    _last_tool_signature: tuple[str, str] | None = None
    _prepared_tool_stats: dict[str, GuardrailRuntimeStats] = field(
        default_factory=dict[str, GuardrailRuntimeStats]
    )
    _prepared_checkpoint_keys: dict[str, str] = field(default_factory=dict[str, str])

    @classmethod
    def from_summary(
        cls,
        summary: Mapping[str, Any] | None,
        *,
        context_key: tuple[str | None, str | None, int | None] = (None, None, None),
    ) -> GuardrailRuntimeAccumulator:
        """从已持久化 guardrail summary 恢复累计统计基线。"""

        raw_stats = summary.get("runtime_stats") if isinstance(summary, Mapping) else None
        if not isinstance(raw_stats, Mapping):
            return cls(context_key=context_key)
        stats = cast(Mapping[str, Any], raw_stats)
        return cls(
            total_tokens=_safe_int(stats.get("total_tokens")),
            prompt_tokens=_safe_int(stats.get("prompt_tokens")),
            completion_tokens=_safe_int(stats.get("completion_tokens")),
            elapsed_ms=_safe_float(stats.get("elapsed_ms")),
            context_growth_messages=_safe_int(stats.get("context_growth_messages")),
            repeated_tool_call_count=_safe_int(stats.get("repeated_tool_call_count")),
            consecutive_failure_count=_safe_int(stats.get("consecutive_failure_count")),
            total_model_calls=_safe_int(stats.get("total_model_calls")),
            total_tool_calls=_safe_int(stats.get("total_tool_calls")),
            estimated_cost=_safe_optional_float(stats.get("estimated_cost")),
            cost_available=bool(stats.get("cost_available"))
            and _safe_optional_float(stats.get("estimated_cost")) is not None,
            cost_unavailable_seen=not bool(stats.get("cost_available"))
            or _safe_optional_float(stats.get("estimated_cost")) is None,
            last_tool_name=_safe_optional_str(stats.get("last_tool_name")),
            last_tool_risk_level=_safe_optional_str(stats.get("last_tool_risk_level")),
            last_tool_error=bool(stats.get("last_tool_error", False)),
            context_key=context_key,
        )

    def model_completed(
        self,
        *,
        usage: Mapping[str, Any] | None,
        model: str | None,
        model_pricing: Mapping[str, Any] | None,
        elapsed_ms: float,
        context_growth_messages: int,
    ) -> GuardrailRuntimeStats:
        """累计一次真实模型调用 usage、耗时、上下文增长与估算成本。"""

        call_stats = GuardrailRuntimeStats.from_model_usage(
            usage=usage,
            model=model,
            model_pricing=model_pricing,
            elapsed_ms=elapsed_ms,
            context_growth_messages=context_growth_messages,
        )
        self.total_tokens += call_stats.total_tokens
        self.prompt_tokens += call_stats.prompt_tokens
        self.completion_tokens += call_stats.completion_tokens
        self.elapsed_ms += call_stats.elapsed_ms
        self.context_growth_messages += call_stats.context_growth_messages
        self.total_model_calls += 1
        if call_stats.estimated_cost is not None:
            self.estimated_cost = (self.estimated_cost or 0.0) + call_stats.estimated_cost
        else:
            self.cost_unavailable_seen = True
        self.cost_available = self.estimated_cost is not None and not self.cost_unavailable_seen
        return self.snapshot()

    def tool_before(
        self, *, tool_call: ToolCallRequest, risk_level: ToolRiskLevel
    ) -> GuardrailRuntimeStats:
        """在真实工具执行前按模型返回顺序累计重复工具调用统计。"""

        signature = (tool_call.name, _normalize_tool_arguments(tool_call.arguments))
        if self._last_tool_signature == signature:
            self.repeated_tool_call_count += 1
        self._last_tool_signature = signature
        self.last_tool_name = tool_call.name
        self.last_tool_risk_level = risk_level.value
        self.last_tool_error = False
        return self.snapshot()

    def remember_tool_before(
        self,
        *,
        tool_call: ToolCallRequest,
        stats: GuardrailRuntimeStats,
    ) -> None:
        """记录已完成前置记账的工具调用，供执行阶段避免重复统计。"""

        self._prepared_tool_stats[tool_call.id] = stats

    def prepared_tool_before(self, *, tool_call: ToolCallRequest) -> GuardrailRuntimeStats | None:
        """返回准备阶段已产生的工具前置统计快照。"""

        return self._prepared_tool_stats.get(tool_call.id)

    def remember_checkpoint_key(
        self, *, tool_call: ToolCallRequest, tool_execution_key: str
    ) -> None:
        """记录准备阶段已探测的 checkpoint 工具执行键。"""

        self._prepared_checkpoint_keys[tool_call.id] = tool_execution_key

    def prepared_checkpoint_key(self, *, tool_call: ToolCallRequest) -> str | None:
        """返回准备阶段已探测的 checkpoint 工具执行键。"""

        return self._prepared_checkpoint_keys.get(tool_call.id)

    def tool_after(
        self,
        *,
        tool_call: ToolCallRequest,
        risk_level: ToolRiskLevel,
        elapsed_ms: float,
        is_error: bool,
    ) -> GuardrailRuntimeStats:
        """在真实工具执行后累计工具次数、耗时与连续失败统计。"""

        self.total_tool_calls += 1
        self.elapsed_ms += max(float(elapsed_ms), 0.0)
        self.consecutive_failure_count = self.consecutive_failure_count + 1 if is_error else 0
        self.last_tool_name = tool_call.name
        self.last_tool_risk_level = risk_level.value
        self.last_tool_error = is_error
        before_stats = self.prepared_tool_before(tool_call=tool_call)
        if before_stats is None:
            return self.snapshot()
        return GuardrailRuntimeStats(
            total_tokens=self.total_tokens,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            elapsed_ms=self.elapsed_ms,
            context_growth_messages=self.context_growth_messages,
            repeated_tool_call_count=before_stats.repeated_tool_call_count,
            consecutive_failure_count=self.consecutive_failure_count,
            total_model_calls=self.total_model_calls,
            total_tool_calls=self.total_tool_calls,
            estimated_cost=self.estimated_cost,
            cost_available=self.cost_available,
            last_tool_name=tool_call.name,
            last_tool_risk_level=risk_level.value,
            last_tool_error=is_error,
        )

    def snapshot(self) -> GuardrailRuntimeStats:
        """返回当前累计统计的不可变快照。"""

        return GuardrailRuntimeStats(
            total_tokens=self.total_tokens,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            elapsed_ms=self.elapsed_ms,
            context_growth_messages=self.context_growth_messages,
            repeated_tool_call_count=self.repeated_tool_call_count,
            consecutive_failure_count=self.consecutive_failure_count,
            total_model_calls=self.total_model_calls,
            total_tool_calls=self.total_tool_calls,
            estimated_cost=self.estimated_cost,
            cost_available=self.cost_available,
            last_tool_name=self.last_tool_name,
            last_tool_risk_level=self.last_tool_risk_level,
            last_tool_error=self.last_tool_error,
        )


CURRENT_GUARDRAIL_RUNTIME: ContextVar[GuardrailRuntimeAccumulator | None] = ContextVar(
    "react_guardrail_runtime_accumulator",
    default=None,
)
"""当前 ReAct 执行链路的 guardrail 运行时统计累计器。"""

CURRENT_TOOL_ABUSE_DETECTOR: ContextVar[ToolAbuseDetector | None] = ContextVar(
    "react_tool_abuse_detector",
    default=None,
)
"""当前 ReAct 执行链路的工具调用滥用检测器。"""
