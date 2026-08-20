"""Agent 分段执行值对象模块。

本模块定义阶段二请求内有限分段执行所需的纯领域值对象。对象只表达
策略、预算、进展快照和响应元数据，不依赖 FastAPI、配置框架或任何
基础设施实现，确保领域层仍符合六边形架构依赖方向。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SegmentStopReason = Literal[
    "completed",
    "auto_disabled",
    "approval_required",
    "max_continuations_reached",
    "total_token_budget_reached",
    "total_duration_budget_reached",
    "consecutive_paused_limit",
    "no_progress",
    "repeated_tool_call",
    "tool_boundary_unavailable",
    "continue_precondition_failed",
    "risk_gate_required",
]
"""分段运行停止原因。"""


@dataclass(frozen=True)
class SegmentExecutionPolicy:
    """分段执行策略。

    Attributes:
        auto_continue_enabled: 是否允许服务端自动进入下一段。
        max_continuations: 单次分段运行最多自动续跑次数，0 表示只执行首段。
        max_total_tokens: 跨段累计 token 上限，None 表示不限制。
        max_duration_seconds: 跨段累计耗时上限，None 表示不限制。
        max_consecutive_paused: 连续暂停段停止阈值，必须大于 0。
        max_no_progress_segments: 连续无进展段停止阈值，必须大于 0。
        max_repeated_tool_calls: 连续重复工具调用停止阈值，必须大于 0。
    """

    auto_continue_enabled: bool = False
    max_continuations: int = 3
    max_total_tokens: int | None = None
    max_duration_seconds: float | None = None
    max_consecutive_paused: int = 2
    max_no_progress_segments: int = 2
    max_repeated_tool_calls: int = 2

    def __post_init__(self) -> None:
        """校验分段策略阈值。"""
        if self.max_continuations < 0:
            raise ValueError("max_continuations 必须 >= 0")
        if self.max_total_tokens is not None and self.max_total_tokens <= 0:
            raise ValueError("max_total_tokens 必须为 None 或大于 0")
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds 必须为 None 或大于 0")
        if self.max_consecutive_paused <= 0:
            raise ValueError("max_consecutive_paused 必须大于 0")
        if self.max_no_progress_segments <= 0:
            raise ValueError("max_no_progress_segments 必须大于 0")
        if self.max_repeated_tool_calls <= 0:
            raise ValueError("max_repeated_tool_calls 必须大于 0")


@dataclass(frozen=True)
class SegmentBudgetUsage:
    """分段运行累计预算用量。

    Attributes:
        segment_count: 已执行段数。
        continuation_count: 已执行续跑段数，不包含首段。
        total_tokens: 跨段累计 token。
        elapsed_ms: 跨段累计耗时毫秒。
        consecutive_paused_count: 当前连续暂停段计数。
        no_progress_count: 当前连续无进展段计数。
        repeated_tool_call_count: 当前连续重复工具调用计数。
    """

    segment_count: int = 0
    continuation_count: int = 0
    total_tokens: int = 0
    elapsed_ms: float = 0.0
    consecutive_paused_count: int = 0
    no_progress_count: int = 0
    repeated_tool_call_count: int = 0

    def __post_init__(self) -> None:
        """校验预算用量不可为负。"""
        if self.segment_count < 0:
            raise ValueError("segment_count 必须 >= 0")
        if self.continuation_count < 0:
            raise ValueError("continuation_count 必须 >= 0")
        if self.total_tokens < 0:
            raise ValueError("total_tokens 必须 >= 0")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms 必须 >= 0")
        if self.consecutive_paused_count < 0:
            raise ValueError("consecutive_paused_count 必须 >= 0")
        if self.no_progress_count < 0:
            raise ValueError("no_progress_count 必须 >= 0")
        if self.repeated_tool_call_count < 0:
            raise ValueError("repeated_tool_call_count 必须 >= 0")

    def plus_segment(
        self,
        *,
        total_tokens_delta: int,
        elapsed_ms_delta: float,
        paused: bool,
        has_progress: bool,
        repeated_tool_call: bool,
    ) -> SegmentBudgetUsage:
        """返回追加一个执行段后的累计预算用量。"""
        if total_tokens_delta < 0:
            raise ValueError("total_tokens_delta 必须 >= 0")
        if elapsed_ms_delta < 0:
            raise ValueError("elapsed_ms_delta 必须 >= 0")

        next_segment_count = self.segment_count + 1
        return SegmentBudgetUsage(
            segment_count=next_segment_count,
            continuation_count=max(0, next_segment_count - 1),
            total_tokens=self.total_tokens + total_tokens_delta,
            elapsed_ms=self.elapsed_ms + elapsed_ms_delta,
            consecutive_paused_count=(self.consecutive_paused_count + 1 if paused else 0),
            no_progress_count=self.no_progress_count + 1 if not has_progress else 0,
            repeated_tool_call_count=(
                self.repeated_tool_call_count + 1 if repeated_tool_call else 0
            ),
        )


@dataclass(frozen=True)
class SegmentProgressSnapshot:
    """单段执行前后的进展快照。"""

    pre_message_count: int
    post_message_count: int
    new_tool_message_count: int = 0
    new_trace_count: int = 0
    token_delta: int = 0
    final_content_present: bool = False
    repeated_tool_call: bool = False

    def __post_init__(self) -> None:
        """校验快照计数不可为负。"""
        if self.pre_message_count < 0:
            raise ValueError("pre_message_count 必须 >= 0")
        if self.post_message_count < 0:
            raise ValueError("post_message_count 必须 >= 0")
        if self.new_tool_message_count < 0:
            raise ValueError("new_tool_message_count 必须 >= 0")
        if self.new_trace_count < 0:
            raise ValueError("new_trace_count 必须 >= 0")
        if self.token_delta < 0:
            raise ValueError("token_delta 必须 >= 0")

    @property
    def has_progress(self) -> bool:
        """判断本段是否产生任一保守进展信号。"""
        return (
            self.new_tool_message_count > 0
            or self.new_trace_count > 0
            or self.token_delta > 0
            or self.final_content_present
        )


@dataclass(frozen=True)
class SegmentRunMetadata:
    """返回给上层响应的分段元数据。"""

    segment_index: int = 1
    segment_count: int = 1
    auto_continue_attempted: bool = False
    segment_stop_reason: SegmentStopReason = "completed"
    budget_usage: SegmentBudgetUsage = field(default_factory=SegmentBudgetUsage)
    risk_gate_required: bool = False
    guardrail_reason: str | None = None

    def __post_init__(self) -> None:
        """校验分段响应元数据。"""
        if self.segment_index <= 0:
            raise ValueError("segment_index 必须大于 0")
        if self.segment_count <= 0:
            raise ValueError("segment_count 必须大于 0")
        if self.segment_index > self.segment_count:
            raise ValueError("segment_index 不可大于 segment_count")
