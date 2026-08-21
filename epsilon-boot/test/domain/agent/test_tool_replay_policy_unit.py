from typing import Any

import pytest

from domain.agent.tools import Tool, ToolExecutionResult
from domain.model_access.value_objects import ToolCallRequest
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel


class MinimalTool(Tool):
    @property
    def name(self) -> str:
        return "minimal_tool"

    @property
    def description(self) -> str:
        return "Minimal tool for recovery metadata tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=f"echo:{kwargs['text']}")


def test_tool_default_recovery_metadata_is_conservative() -> None:
    tool = MinimalTool()
    request = ToolCallRequest(
        id="call-1",
        name=tool.name,
        arguments='{"text":"hello"}',
    )

    assert tool.side_effect_level is ToolSideEffectLevel.EXTERNAL_WRITE
    assert tool.replay_policy is ToolReplayPolicy.MANUAL_REVIEW
    assert tool.idempotency_key(request, "execution-key-1") is None


@pytest.mark.asyncio
async def test_minimal_tool_subclass_runs_without_overriding_recovery_metadata() -> None:
    tool = MinimalTool()
    request = ToolCallRequest(
        id="call-1",
        name=tool.name,
        arguments='{"text":"hello"}',
    )

    assert await tool.run(request) == "echo:hello"
