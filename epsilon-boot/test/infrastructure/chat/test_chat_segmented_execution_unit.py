"""ChatServiceAdapter 同步分段执行测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, ToolMessage, UserMessage
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies

_TOOL_SCHEMA = {"type": "function", "function": {"name": "search"}}


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
            tool_schemas=[_TOOL_SCHEMA],
            segment_policy=policy,
            **make_chat_adapter_dependencies(
                session_store=session_store,
                model_registry=model_registry,
                loaded_prompt=loaded_prompt,
                agent=agent,
                tool_schemas=[_TOOL_SCHEMA],
                max_tool_rounds=4,
                segment_policy=policy,
            ),
        ),
        session_store,
    )


def _append_tool_tail(context: ConversationContext, call_id: str = "call-1") -> None:
    """向上下文追加可继续的 assistant tool_calls + tool result 尾部。"""
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=call_id, name="search", arguments='{"q":"x"}')],
    )
    context.add_tool_result("search", "result", call_id)


@pytest.mark.asyncio
async def test_segmented_chat_auto_disabled_returns_paused_metadata() -> None:
    """自动续跑关闭时首段暂停，并用 auto_disabled 标记停止原因。"""
    context = ConversationContext()

    async def run(
        ctx: ConversationContext,
        config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        assert config.max_rounds == 4
        _append_tool_tail(ctx)
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 7},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _ = _adapter(agent, context, policy=SegmentExecutionPolicy())

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "paused"
    assert response.segment_metadata.segment_count == 1
    assert response.segment_metadata.auto_continue_attempted is False
    assert response.segment_metadata.segment_stop_reason == "auto_disabled"
    assert response.segment_metadata.budget_usage.total_tokens == 7
    assert agent.run.await_count == 1


@pytest.mark.asyncio
async def test_segmented_chat_auto_continue_completes_without_new_user_message() -> None:
    """自动续跑第二段完成，不追加额外 user message 且每段 max_rounds 不变。"""
    context = ConversationContext()
    observed_user_counts: list[int] = []
    observed_max_rounds: list[int] = []

    async def run(
        ctx: ConversationContext,
        config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        observed_user_counts.append(
            sum(isinstance(message, UserMessage) for message in ctx.get_messages())
        )
        observed_max_rounds.append(config.max_rounds)
        if len(observed_user_counts) == 1:
            _append_tool_tail(ctx)
            return AgentResult(
                content="",
                model="test-model",
                usage={"total_tokens": 5},
                terminated_reason="max_rounds",
            )
        return AgentResult(
            content="done",
            model="test-model",
            usage={"total_tokens": 3},
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "completed"
    assert response.reply == "done"
    assert response.segment_metadata.segment_count == 2
    assert response.segment_metadata.auto_continue_attempted is True
    assert response.segment_metadata.segment_stop_reason == "completed"
    assert response.segment_metadata.budget_usage.total_tokens == 8
    assert observed_user_counts == [1, 1]
    assert observed_max_rounds == [4, 4]
    saved_context = session_store.save.call_args.args[1]
    assert sum(isinstance(message, UserMessage) for message in saved_context.get_messages()) == 1


@pytest.mark.asyncio
async def test_segmented_continue_auto_continue_completes_without_new_user_message() -> None:
    """手动继续后自动续跑第二段完成，不追加额外 user message。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    _append_tool_tail(context)
    observed_user_counts: list[int] = []

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        observed_user_counts.append(
            sum(isinstance(message, UserMessage) for message in ctx.get_messages())
        )
        if len(observed_user_counts) == 1:
            _append_tool_tail(ctx, "call-2")
            return AgentResult(
                content="",
                model="test-model",
                usage={"total_tokens": 4},
                terminated_reason="max_rounds",
            )
        return AgentResult(
            content="done",
            model="test-model",
            usage={"total_tokens": 2},
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    response = await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))

    assert response.status == "completed"
    assert response.reply == "done"
    assert response.segment_metadata.segment_count == 2
    assert response.segment_metadata.auto_continue_attempted is True
    assert response.segment_metadata.segment_stop_reason == "completed"
    assert observed_user_counts == [1, 1]
    saved_context = session_store.save.call_args.args[1]
    assert sum(isinstance(message, UserMessage) for message in saved_context.get_messages()) == 1


@pytest.mark.asyncio
async def test_segmented_chat_stops_on_max_continuations() -> None:
    """自动续跑次数达到策略上限时返回 max_continuations_reached。"""
    context = ConversationContext()

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        _append_tool_tail(ctx, call_id=f"call-{agent.run.await_count + 1}")
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 1},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _ = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=1),
    )

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "paused"
    assert response.segment_metadata.segment_count == 2
    assert response.segment_metadata.auto_continue_attempted is True
    assert response.segment_metadata.segment_stop_reason == "max_continuations_reached"
    assert agent.run.await_count == 2


@pytest.mark.asyncio
async def test_segmented_chat_stops_on_total_token_budget() -> None:
    """累计 token 达到预算时不再自动续跑。"""
    context = ConversationContext()

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        _append_tool_tail(ctx)
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 5},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _ = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_continuations=3,
            max_total_tokens=5,
        ),
    )

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "paused"
    assert response.segment_metadata.segment_count == 1
    assert response.segment_metadata.segment_stop_reason == "total_token_budget_reached"
    assert agent.run.await_count == 1


@pytest.mark.asyncio
async def test_segmented_chat_stops_on_approval_required() -> None:
    """审批中断不会被自动续跑吞掉。"""
    context = ConversationContext()
    approval = ApprovalRequiredPayload(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        prompt_id="chat-default@v1",
        metadata={
            "source": "guardrail",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": True,
        },
    )
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            status="approval_required",
            approval=approval,
        )
    )
    adapter, session_store = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=3),
    )

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "approval_required"
    assert response.segment_metadata.segment_count == 1
    assert response.segment_metadata.segment_stop_reason == "approval_required"
    assert response.segment_metadata.auto_continue_attempted is False
    assert response.segment_metadata.risk_gate_required is True
    assert response.segment_metadata.guardrail_reason == "tool_risk_gate_required"
    assert agent.run.await_count == 1
    session_store.save.assert_not_called()


@pytest.mark.asyncio
async def test_segmented_chat_stops_on_guardrail_stop_metadata() -> None:
    """guardrail stop 写入稳定 metadata 时分段门禁应阻止自动续跑。"""
    context = ConversationContext()

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        _append_tool_tail(ctx)
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
            usage={"total_tokens": 2},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _ = _adapter(
        agent,
        context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "paused"
    assert response.segment_metadata.segment_count == 1
    assert response.segment_metadata.segment_stop_reason == "risk_gate_required"
    assert response.segment_metadata.risk_gate_required is True
    assert response.segment_metadata.guardrail_reason == "tool_risk_gate_required"
    assert agent.run.await_count == 1
