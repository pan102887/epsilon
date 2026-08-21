"""ChatServiceAdapter 分段风险门禁稳定标记测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import ConversationContext, ToolMessage
from domain.chat.value_objects import ChatContinueRequestVO
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _adapter(
    agent: MagicMock, context: ConversationContext
) -> tuple[ChatServiceAdapter, MagicMock]:
    """构造启用分段续跑的聊天适配器。"""
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context)
    session_store.save = AsyncMock()
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="system",
    )
    tool_schemas: list[dict[str, Any]] = [
        {"type": "function", "function": {"name": "search"}}
    ]
    return (
        ChatServiceAdapter(
            session_store=session_store,
            model_registry=model_registry,
            prompt_registry=MagicMock(
                get=MagicMock(
                    return_value=loaded_prompt
                )
            ),
            context_builder=MagicMock(),
            agent=agent,
            tool_calling_enabled=True,
            max_tool_rounds=4,
            tool_schemas=tool_schemas,
            **make_chat_adapter_dependencies(
                session_store=session_store,
                model_registry=model_registry,
                loaded_prompt=loaded_prompt,
                agent=agent,
                tool_schemas=tool_schemas,
                max_tool_rounds=4,
            ),
        ),
        session_store,
    )


def _continuable_context() -> ConversationContext:
    """构造可继续的聊天上下文。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")
    return context


@pytest.mark.asyncio
async def test_continue_chat_reads_risk_gate_required_from_new_tool_metadata() -> None:
    """continue_chat 应从新增 ToolMessage.metadata 读取风险门禁。"""
    context = _continuable_context()

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-2", name="search", arguments="{}")],
        )
        ctx.add_tool_result("search", "blocked", "call-2")
        tool_message = ctx.get_messages()[-1]
        assert isinstance(tool_message, ToolMessage)
        tool_message.metadata.update(
            {
                "guardrail_action": "stop",
                "guardrail_reason": "tool_risk_gate_required",
                "risk_gate_required": True,
            }
        )
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 1},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _ = _adapter(agent, context)

    response = await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))

    assert response.status == "paused"
    assert response.segment_metadata.segment_stop_reason == "risk_gate_required"
    assert response.segment_metadata.risk_gate_required is True
    assert response.segment_metadata.guardrail_reason == "tool_risk_gate_required"


def test_segment_risk_gate_required_ignores_observe_marker() -> None:
    """observe 模式仅暴露原因，不应误置 risk_gate_required。"""
    context = _continuable_context()
    tool_message = context.get_messages()[-1]
    assert isinstance(tool_message, ToolMessage)
    tool_message.metadata.update(
        {
            "guardrail_action": "observe",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": False,
        }
    )

    required, reason = ChatServiceAdapter.segment_risk_gate_required(
        context=context,
        pre_message_count=3,
    )

    assert required is False
    assert reason == "tool_risk_gate_required"


def test_segment_risk_gate_required_uses_guardrail_approval_metadata() -> None:
    """guardrail 来源审批在无 ToolMessage 时也应触发风险门禁。"""
    required, reason = ChatServiceAdapter.segment_risk_gate_required(
        context=ConversationContext(),
        pre_message_count=0,
        approval_required=True,
        approval_metadata={
            "source": "guardrail",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": True,
        },
    )

    assert required is True
    assert reason == "tool_risk_gate_required"
