"""ChatServiceAdapter 分段结构化流测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import AgentStreamEvent
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _adapter(
    agent: MagicMock,
    context: ConversationContext,
    *,
    policy: SegmentExecutionPolicy,
) -> tuple[ChatServiceAdapter, MagicMock]:
    """构造启用工具调用的聊天适配器。"""
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
            segment_policy=policy,
            **make_chat_adapter_dependencies(
                session_store=session_store,
                model_registry=model_registry,
                loaded_prompt=loaded_prompt,
                agent=agent,
                tool_schemas=tool_schemas,
                max_tool_rounds=3,
                segment_policy=policy,
            ),
        ),
        session_store,
    )


def _append_tool_tail(context: ConversationContext, index: int = 1) -> None:
    """追加一段可继续的工具尾部。"""
    call_id = f"call-{index}"
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=call_id, name="search", arguments=f'{{"i":{index}}}')],
    )
    context.add_tool_result("search", f"result-{index}", call_id)


@pytest.mark.asyncio
async def test_segment_done_control_event_contains_budget_metadata() -> None:
    """单段暂停后产出 segment_done 控制事件。"""
    context = ConversationContext()

    async def run_events(ctx, _config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        _append_tool_tail(ctx)
        yield AgentStreamEvent(
            kind="assistant_done",
            usage={"total_tokens": 3},
            metadata={"terminated_reason": "max_rounds"},
        )

    agent = MagicMock()
    agent.run_events = run_events
    adapter, _ = _adapter(agent, context, policy=SegmentExecutionPolicy())

    events = [
        event
        async for event in adapter.stream_segmented_chat_events(
            ChatRequestVO(session_id="s1", message="hello")
        )
    ]

    segment_done = next(
        event for event in events if event.metadata.get("event_type") == "segment_done"
    )
    assert segment_done.kind == "assistant_done"
    assert segment_done.metadata["finished"] is False
    assert segment_done.metadata["segment_index"] == 1
    assert segment_done.metadata["segment_count"] == 1
    assert segment_done.metadata["segment_stop_reason"] == "auto_disabled"
    assert segment_done.metadata["budget_usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_segmented_stream_auto_continue_yields_two_boundaries_and_final_metadata() -> None:
    """max_rounds 后自动续跑，第二段 completed 带最终分段元数据。"""
    context = ConversationContext()
    user_counts: list[int] = []

    async def run_events(ctx, config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        user_counts.append(sum(isinstance(message, UserMessage) for message in ctx.get_messages()))
        assert config.max_rounds == 3
        if len(user_counts) == 1:
            _append_tool_tail(ctx, 1)
            yield AgentStreamEvent(
                kind="assistant_done",
                usage={"total_tokens": 2},
                metadata={"terminated_reason": "max_rounds"},
            )
        else:
            yield AgentStreamEvent(kind="assistant_delta", content="done")
            yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 4})

    agent = MagicMock()
    agent.run_events = run_events
    adapter, session_store = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    events = [
        event
        async for event in adapter.stream_segmented_chat_events(
            ChatRequestVO(session_id="s1", message="hello")
        )
    ]

    boundaries = [event for event in events if event.metadata.get("event_type") == "segment_done"]
    final_done_events = [
        event
        for event in events
        if event.kind == "assistant_done" and event.metadata.get("event_type") != "segment_done"
    ]
    assert len(boundaries) == 2
    assert len(final_done_events) == 1
    final_done = final_done_events[0]
    assert boundaries[-1].metadata["segment_stop_reason"] == "completed"
    assert final_done.metadata["segment_count"] == 2
    assert final_done.metadata["segment_stop_reason"] == "completed"
    assert final_done.metadata["auto_continue_attempted"] is True
    assert user_counts == [1, 1]
    assert (
        sum(
            isinstance(message, UserMessage)
            for message in session_store.save.call_args.args[1].get_messages()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_segmented_stream_approval_required_stops_without_auto_continue() -> None:
    """审批事件停止自动续跑，不被映射为 paused。"""
    context = ConversationContext()

    async def run_events(_ctx, _config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        yield AgentStreamEvent(
            kind="approval_required",
            metadata={"approval_id": "approval-1"},
        )

    agent = MagicMock()
    agent.run_events = run_events
    adapter, session_store = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    events = [
        event
        async for event in adapter.stream_segmented_chat_events(
            ChatRequestVO(session_id="s1", message="hello")
        )
    ]

    assert [event.kind for event in events] == ["approval_required", "assistant_done"]
    assert events[-1].metadata["event_type"] == "segment_done"
    assert events[-1].metadata["segment_stop_reason"] == "approval_required"
    assert events[-1].metadata["risk_gate_required"] is False
    assert "status" not in events[0].metadata
    session_store.save.assert_not_called()


@pytest.mark.asyncio
async def test_segmented_stream_guardrail_approval_sets_risk_gate_required() -> None:
    """guardrail 来源审批事件应把风险门禁透传到 segment_done。"""
    context = ConversationContext()

    async def run_events(_ctx, _config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        yield AgentStreamEvent(
            kind="approval_required",
            metadata={
                "approval_id": "approval-1",
                "source": "guardrail",
                "guardrail_reason": "tool_risk_gate_required",
                "risk_gate_required": True,
            },
        )

    agent = MagicMock()
    agent.run_events = run_events
    adapter, _ = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    events = [
        event
        async for event in adapter.stream_segmented_chat_events(
            ChatRequestVO(session_id="s1", message="hello")
        )
    ]

    segment_done = next(
        event for event in events if event.metadata.get("event_type") == "segment_done"
    )
    assert segment_done.metadata["segment_stop_reason"] == "approval_required"
    assert segment_done.metadata["risk_gate_required"] is True
    assert segment_done.metadata["guardrail_reason"] == "tool_risk_gate_required"


@pytest.mark.asyncio
async def test_segmented_continue_stream_does_not_append_user() -> None:
    """分段继续流复用上下文，不追加 user message。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    _append_tool_tail(context)

    async def run_events(ctx, _config, _model_access) -> AsyncIterator[AgentStreamEvent]:
        assert sum(isinstance(message, UserMessage) for message in ctx.get_messages()) == 1
        yield AgentStreamEvent(kind="assistant_delta", content="done")
        yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 1})

    agent = MagicMock()
    agent.run_events = run_events
    adapter, session_store = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    events = [
        event
        async for event in adapter.stream_segmented_continue_chat_events(
            ChatContinueRequestVO(session_id="s1")
        )
    ]

    assert events[-2].metadata["segment_stop_reason"] == "completed"
    assert (
        sum(
            isinstance(message, UserMessage)
            for message in session_store.save.call_args.args[1].get_messages()
        )
        == 1
    )
