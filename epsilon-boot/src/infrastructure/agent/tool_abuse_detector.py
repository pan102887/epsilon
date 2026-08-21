"""Agent 工具调用滥用检测。

本模块提供单个 Agent run 内的轻量级工具调用统计，用于在工具真正执行前
发现高频重复调用同一工具等失控迹象。
"""

from dataclasses import dataclass, field
from typing import Any


def _count_dict() -> dict[str, int]:
    return {}


@dataclass(frozen=True)
class ToolAbuseVerdict:
    """单次工具调用滥用检测结果。"""

    abuse_detected: bool
    reason: str | None = None


@dataclass
class ToolAbuseDetector:
    """统计单个 Agent run 内的工具调用模式并识别滥用迹象。"""

    max_same_tool_calls: int = 5
    _counts: dict[str, int] = field(default_factory=_count_dict)

    def record_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> ToolAbuseVerdict:
        """记录一次工具调用并返回是否命中滥用策略。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数；当前策略只统计调用频次，保留参数入口便于后续扩展。

        Returns:
            当前工具调用的滥用检测结果。
        """

        del arguments
        count = self._counts.get(tool_name, 0) + 1
        self._counts[tool_name] = count
        if count > self.max_same_tool_calls:
            return ToolAbuseVerdict(True, "same_tool_call_limit_exceeded")
        return ToolAbuseVerdict(False)
