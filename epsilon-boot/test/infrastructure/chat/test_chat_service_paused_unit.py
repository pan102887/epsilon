"""ChatServiceAdapter 同步暂停翻译测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    AgentTerminationReason,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.chat.value_objects import ChatRequestVO
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _adapter(
    agent: MagicMock, context: ConversationContext
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
    prompt_registry = MagicMock(
        get=MagicMock(
            return_value=loaded_prompt
        )
    )
    return (
        ChatServiceAdapter(
            session_store=session_store,
            model_registry=model_registry,
            prompt_registry=prompt_registry,
            context_builder=MagicMock(),
            agent=agent,
            tool_calling_enabled=True,
            max_tool_rounds=2,
            tool_schemas=tool_schemas,
            **make_chat_adapter_dependencies(
                session_store=session_store,
                model_registry=model_registry,
                loaded_prompt=loaded_prompt,
                agent=agent,
                tool_schemas=tool_schemas,
                max_tool_rounds=2,
            ),
        ),
        session_store,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminated_reason", ["max_rounds", "token_budget_exceeded"])
async def test_chat_returns_paused_and_saves_tool_tail(
    terminated_reason: AgentTerminationReason,
) -> None:
    """验证同步 chat 对阶段边界返回 paused，且保存上下文不追加空 assistant。"""
    context = ConversationContext()

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        ctx.add_tool_result("search", "result", "call-1")
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 10},
            terminated_reason=terminated_reason,
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store = _adapter(agent, context)

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "paused"
    assert response.reply == ""
    assert response.terminated_reason == terminated_reason
    assert response.can_continue is True
    saved_context = session_store.save.call_args.args[1]
    messages = saved_context.get_messages()
    assert isinstance(messages[-1], ToolMessage)
    assert not (isinstance(messages[-1], AssistantMessage) and messages[-1].content == "")


@pytest.mark.asyncio
async def test_chat_paused_can_continue_false_when_tail_is_not_tool() -> None:
    """验证 paused 响应按尾部消息类型计算 can_continue。"""
    context = ConversationContext()
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
        )
    )
    adapter, _ = _adapter(agent, context)

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "paused"
    assert response.can_continue is False


@pytest.mark.asyncio
async def test_chat_approval_required_preserves_approval_state() -> None:
    """验证审批等待状态不被暂停逻辑污染。"""
    action = PendingActionRequest(
        "call-1",
        "search",
        "{}",
        frozenset({"approve", "reject"}),
    )
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            status="approval_required",
            approval=ApprovalRequiredPayload("s1", "a1", (action,), "chat-default@v1"),
        )
    )
    adapter, session_store = _adapter(agent, ConversationContext())

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "approval_required"
    assert response.terminated_reason == "completed"
    assert response.can_continue is False
    session_store.save.assert_not_called()
