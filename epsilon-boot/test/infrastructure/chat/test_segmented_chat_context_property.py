"""Chat 分段执行上下文属性测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatRequestVO
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _adapter(agent: MagicMock, context: ConversationContext) -> ChatServiceAdapter:
    """构造启用自动分段的 ChatServiceAdapter。"""
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
    policy = SegmentExecutionPolicy(
        auto_continue_enabled=True,
        max_continuations=5,
        max_consecutive_paused=10,
    )
    return ChatServiceAdapter(
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
        segment_policy=policy,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=4,
            segment_policy=policy,
        ),
    )


def _append_tool_tail(context: ConversationContext, index: int) -> None:
    """追加一段可继续的工具尾部。"""
    call_id = f"call-{index}"
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=call_id, name="search", arguments=f'{{"i":{index}}}')],
    )
    context.add_tool_result("search", f"result-{index}", call_id)


@pytest.mark.asyncio
@settings(max_examples=12)
@given(paused_segments=st.integers(min_value=0, max_value=3))
async def test_auto_segments_preserve_user_count_and_single_segment_round_limit(
    paused_segments: int,
) -> None:
    """任意有限暂停段后完成时，不追加 user 且每段 max_rounds 固定。"""
    context = ConversationContext()
    user_counts: list[int] = []
    max_rounds: list[int] = []

    async def run(
        ctx: ConversationContext,
        config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        segment_number = len(user_counts) + 1
        user_counts.append(sum(isinstance(message, UserMessage) for message in ctx.get_messages()))
        max_rounds.append(config.max_rounds)
        if segment_number <= paused_segments:
            _append_tool_tail(ctx, segment_number)
            return AgentResult(
                content="",
                model="test-model",
                usage={"total_tokens": 1},
                terminated_reason="max_rounds",
            )
        return AgentResult(
            content="done",
            model="test-model",
            usage={"total_tokens": 1},
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _adapter(agent, context)

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    expected_segments = paused_segments + 1
    assert response.status == "completed"
    assert response.segment_metadata.segment_count == expected_segments
    assert user_counts == [1] * expected_segments
    assert max_rounds == [4] * expected_segments
