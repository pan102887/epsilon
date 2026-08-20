"""工具风险等级单元测试。"""

from __future__ import annotations

from typing import Any

from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool


class _MinimalTool(Tool):
    @property
    def name(self) -> str:
        return "minimal"

    @property
    def description(self) -> str:
        return "minimal tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_tool_default_risk_level_is_high() -> None:
    assert _MinimalTool().risk_level is ToolRiskLevel.HIGH
