"""ReActAgentAdapter guardrail 接入单元测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.guardrails import GuardrailMode, GuardrailPolicy, ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy


class _CriticalTool(Tool):
    @property
    def name(self) -> str:
        return "critical_tool"

    @property
    def description(self) -> str:
        return "critical"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.CRITICAL

    async def execute(self, **kwargs: Any) -> str:
        return "should not run"


def _config() -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[
            {"type": "function", "function": {"name": "critical_tool", "parameters": {}}}
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _tool_call() -> ToolCallRequest:
    return ToolCallRequest(id="call-1", name="critical_tool", arguments="{}")


@pytest.mark.asyncio
async def test_observe_mode_does_not_block_critical_tool() -> None:
    registry = MagicMock()
    registry.get.return_value = _CriticalTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE)),
    )
    ctx = ConversationContext()

    result, is_error = await adapter._execute_tool_call(ctx, _tool_call(), _config())

    assert result.content == "tool ok"
    assert is_error is False
    registry.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_mode_blocks_critical_tool_before_execution() -> None:
    registry = MagicMock()
    registry.get.return_value = _CriticalTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE)),
    )
    ctx = ConversationContext()

    executable, approval = await adapter._prepare_tool_calls_for_execution(
        context=ctx,
        config=_config(),
        tool_calls=(_tool_call(),),
        round_num=1,
        model="test-model",
        usage_so_far={},
    )

    assert executable == ()
    assert approval is None
    registry.execute.assert_not_awaited()
    last = ctx.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.metadata["error"] is True
    assert last.metadata["guardrail_blocked"] is True
    assert last.metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert last.metadata["guardrail_action"] == "stop"
    assert last.metadata["risk_gate_required"] is True
