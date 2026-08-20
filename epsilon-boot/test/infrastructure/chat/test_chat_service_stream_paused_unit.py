"""ChatServiceAdapter 流式暂停与继续测试。"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentStreamEvent
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO
from domain.model_access.value_objects import StreamingChunk, ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _valid_context() -> ConversationContext:
    """构造可继续上下文。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")
    return context


def _adapter(
    agent: MagicMock, context: ConversationContext
) -> tuple[ChatServiceAdapter, MagicMock]:
    """构造测试用聊天适配器。"""
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
    tool_schemas = [{"type": "function", "function": {"name": "search"}}]
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
            max_tool_rounds=3,
            tool_schemas=tool_schemas,
            **make_chat_adapter_dependencies(
                session_store=session_store,
                model_registry=model_registry,
                loaded_prompt=loaded_prompt,
                agent=agent,
                tool_schemas=tool_schemas,
                max_tool_rounds=3,
            ),
        ),
        session_store,
    )


@pytest.mark.asyncio
async def test_stream_chat_events_paused_done_metadata_and_save() -> None:
    """验证结构化流 assistant_done 暂停 metadata 与保存规则。"""
    context = ConversationContext()

    async def run_events(_ctx, _config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        context.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        context.add_tool_result("search", "result", "call-1")
        yield AgentStreamEvent(
            kind="assistant_done",
            usage={"total_tokens": 3},
            metadata={"terminated_reason": "max_rounds"},
        )

    agent = MagicMock()
    agent.run_events = run_events
    adapter, session_store = _adapter(agent, context)

    events = [
        event
        async for event in adapter.stream_chat_events(
            ChatRequestVO(session_id="s1", message="hello")
        )
    ]

    done = events[-1]
    assert done.metadata["status"] == "paused"
    assert done.metadata["terminated_reason"] == "max_rounds"
    assert done.metadata["can_continue"] is True
    saved_context = session_store.save.call_args.args[1]
    assert isinstance(saved_context.get_messages()[-1], ToolMessage)


@pytest.mark.asyncio
async def test_stream_continue_chat_events_does_not_append_user() -> None:
    """验证继续结构化流不追加 user message，并保存 completed final。"""
    context = _valid_context()

    async def run_events(ctx, _config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        user_count = sum(1 for message in ctx.get_messages() if message.role == "user")
        assert user_count == 1
        yield AgentStreamEvent(kind="assistant_delta", content="done")
        yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 1})

    agent = MagicMock()
    agent.run_events = run_events
    adapter, session_store = _adapter(agent, context)

    events = [
        event
        async for event in adapter.stream_continue_chat_events(
            ChatContinueRequestVO(session_id="s1")
        )
    ]

    assert [event.kind for event in events] == ["assistant_delta", "assistant_done"]
    assert session_store.save.call_args.args[1].get_messages()[-1].content == "done"


@pytest.mark.asyncio
async def test_stream_continue_chat_events_rejects_invalid_context() -> None:
    """验证无效上下文在进入流前抛继续不可用。"""
    agent = MagicMock()
    adapter, _ = _adapter(agent, ConversationContext())

    with pytest.raises(ContinuationUnavailableError):
        async for _ in adapter.stream_continue_chat_events(ChatContinueRequestVO(session_id="s1")):
            pass


@pytest.mark.asyncio
async def test_stream_chat_paused_chunk_does_not_append_empty_assistant() -> None:
    """验证兼容文本流暂停 final chunk 不追加空助手消息。"""
    context = ConversationContext()

    async def run_streaming(_ctx, _config, _model_access) -> AsyncIterator[StreamingChunk]:
        context.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        context.add_tool_result("search", "result", "call-1")
        yield StreamingChunk(
            delta_content="",
            finished=True,
            usage={"total_tokens": 2},
            metadata={"terminated_reason": "max_rounds"},
        )

    agent = MagicMock()
    agent.run_streaming = run_streaming
    adapter, session_store = _adapter(agent, context)

    chunks = [
        chunk
        async for chunk in adapter.stream_chat(ChatRequestVO(session_id="s1", message="hello"))
    ]

    assert chunks[-1].metadata["status"] == "paused"
    assert chunks[-1].metadata["can_continue"] is True
    messages = session_store.save.call_args.args[1].get_messages()
    assert isinstance(messages[-1], ToolMessage)
    assert not (isinstance(messages[-1], AssistantMessage) and messages[-1].content == "")
