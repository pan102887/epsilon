"""ChatServiceAdapter 手动继续测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.value_objects import ChatContinueRequestVO
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _valid_context() -> ConversationContext:
    """构造 system/user/assistant(tool_calls)/tool 的可继续上下文。"""
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


@pytest.mark.asyncio
async def test_continue_chat_does_not_append_user_and_completes() -> None:
    """验证继续请求不追加 user message，完成后返回 completed。"""
    context = _valid_context()
    captured_user_counts: list[int] = []

    async def run(
        ctx: ConversationContext,
        config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        captured_user_counts.append(
            sum(1 for message in ctx.get_messages() if isinstance(message, UserMessage))
        )
        assert config.max_rounds == 4
        return AgentResult(content="done", model="test-model")

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store = _adapter(agent, context)

    response = await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))

    assert captured_user_counts == [1]
    assert response.status == "completed"
    assert response.can_continue is False
    assert session_store.save.call_args.args[1].get_messages()[-1].content == "done"


@pytest.mark.asyncio
async def test_continue_chat_can_pause_again() -> None:
    """验证继续后再次命中阶段边界时返回 paused。"""
    context = _valid_context()
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
        )
    )
    adapter, _ = _adapter(agent, context)

    response = await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))

    assert response.status == "paused"
    assert response.terminated_reason == "max_rounds"
    assert response.can_continue is True


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [ConversationContext(), None])
async def test_continue_chat_rejects_empty_or_missing_context(
    context: ConversationContext | None,
) -> None:
    """验证空会话或不存在会话不可继续。"""
    agent = MagicMock()
    adapter, _ = _adapter(agent, context or ConversationContext())

    with pytest.raises(ContinuationUnavailableError):
        await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))


@pytest.mark.asyncio
async def test_continue_chat_rejects_non_tool_tail() -> None:
    """验证最新消息不是 ToolMessage 时不可继续。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    agent = MagicMock()
    adapter, _ = _adapter(agent, context)

    with pytest.raises(ContinuationUnavailableError):
        await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))
