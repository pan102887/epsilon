"""ChatServiceAdapter 边界拆分 characterization 测试。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import (
    ApprovalResumeRequestVO,
    ChatContinueRequestVO,
    ChatRequestVO,
    ContextBuilderResult,
)
from domain.model_access.value_objects import LLMResponse
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter


class _BoundaryWorkflow:
    """记录 adapter 与会话 workflow 的交互。"""

    prompt_id = "chat-default@v1"

    def __init__(self, context: ConversationContext) -> None:
        self.context = context
        self.load_chat_calls: list[str] = []
        self.load_continue_calls: list[str] = []
        self.save_calls: list[tuple[str, ConversationContext, str | None]] = []

    async def load_for_chat(self, request: ChatRequestVO) -> ConversationContext:
        """模拟真实 workflow 的聊天加载行为。"""

        self.load_chat_calls.append(request.session_id)
        self.context.session_id = request.session_id
        if not any(message.role == "system" for message in self.context.get_messages()):
            self.context.add_system_message("system")
        self.context.add_user_message(request.message)
        return self.context

    async def load_for_continue(self, request: ChatContinueRequestVO) -> ConversationContext:
        """模拟真实 workflow 的继续加载行为。"""

        self.load_continue_calls.append(request.session_id)
        self.context.session_id = request.session_id
        return self.context

    def ensure_system_prompt(self, context: ConversationContext) -> None:
        """模拟 system prompt 幂等注入。"""

        if not any(message.role == "system" for message in context.get_messages()):
            context.add_system_message("system")

    async def save_context_and_index(
        self,
        session_id: str,
        context: ConversationContext,
        *,
        model: str | None = None,
    ) -> None:
        """记录保存调用。"""

        self.save_calls.append((session_id, context, model))


class _BoundaryApplicationService:
    """记录 adapter 与聊天应用服务的交互。"""

    def __init__(self) -> None:
        self.continue_calls: list[str] = []
        self.resume_calls: list[str] = []
        self.segmented_calls: list[str] = []

    async def continue_chat(
        self,
        request: ChatContinueRequestVO,
        *,
        run_agent: Callable[[ConversationContext, str | None], Awaitable[AgentResult]]
        | None = None,
        run_chat: Callable[[ConversationContext, str | None], Awaitable[object]] | None = None,
    ):
        """执行传入的 agent 回调并返回最小响应。"""

        self.continue_calls.append(request.session_id)
        context = ConversationContext()
        context.session_id = request.session_id
        context.add_system_message("system")
        context.add_user_message("goal")
        from domain.model_access.value_objects import ToolCallRequest

        context.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        context.add_tool_result("search", "result", "call-1")
        if run_chat is not None:
            return await run_chat(context, request.model)
        assert run_agent is not None
        result = await run_agent(context, request.model)
        from domain.chat.value_objects import ChatResponseVO

        return ChatResponseVO(
            session_id=request.session_id,
            reply=result.content,
            model=result.model,
            usage=result.usage,
            prompt_id="chat-default@v1",
        )

    async def run_segmented_chat_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        run_agent: Callable[[ConversationContext, str | None], Awaitable[AgentResult]],
    ):
        """记录分段委托并执行 adapter 提供的 agent 回调。"""

        self.segmented_calls.append(session_id)
        result = await run_agent(context, model)
        from domain.chat.value_objects import ChatResponseVO

        return ChatResponseVO(
            session_id=session_id,
            reply=result.content,
            model=result.model,
            usage=result.usage,
            prompt_id="chat-default@v1",
        )

    async def resume_approval_to_agent_result(
        self,
        request: ApprovalResumeRequestVO,
    ) -> tuple[ConversationContext, AgentResult]:
        """返回恢复后的上下文和 AgentResult。"""

        self.resume_calls.append(request.approval_id)
        context = ConversationContext()
        context.session_id = request.session_id
        return context, AgentResult(content="done", model="test-model", usage={"total_tokens": 1})


def _adapter(
    *,
    workflow: _BoundaryWorkflow,
    app_service: _BoundaryApplicationService | None = None,
    agent: MagicMock | None = None,
) -> ChatServiceAdapter:
    """构造带边界 fake 的 ChatServiceAdapter。"""

    model_access = MagicMock()
    model_access.chat = AsyncMock(return_value=LLMResponse(content="reply", model="actual-model"))
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = model_access
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="hello")],
            usage={},
            environment_injected=False,
        )
    )
    return ChatServiceAdapter(
        session_store=MagicMock(),
        model_registry=model_registry,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=LoadedPrompt(
                    prompt_id="chat-default@v1",
                    name="chat-default",
                    version="v1",
                    content="system",
                )
            )
        ),
        context_builder=context_builder,
        agent=agent or MagicMock(),
        tool_calling_enabled=False,
        max_tool_rounds=3,
        tool_schemas=[],
        session_workflow=workflow,
        chat_application_service=cast(
            Any,
            app_service or _BoundaryApplicationService(),
        ),
    )


@pytest.mark.asyncio
async def test_chat_uses_session_workflow_for_load_prompt_and_user_append() -> None:
    """chat 入口通过 workflow 完成 load/session_id/system/user 边界。"""

    context = ConversationContext()
    workflow = _BoundaryWorkflow(context)
    adapter = _adapter(workflow=workflow)

    await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert workflow.load_chat_calls == ["s1"]
    assert context.session_id == "s1"
    assert sum(isinstance(message, UserMessage) for message in context.get_messages()) == 1
    assert workflow.save_calls[0][0] == "s1"
    assert workflow.save_calls[0][2] == "actual-model"


@pytest.mark.asyncio
async def test_save_context_and_index_delegates_to_session_workflow() -> None:
    """旧私有保存入口仅作为 workflow 转发兼容点。"""

    context = ConversationContext()
    workflow = _BoundaryWorkflow(context)
    adapter = _adapter(workflow=workflow)

    await adapter.save_context_and_index("s1", context, model="m1")

    assert workflow.save_calls == [("s1", context, "m1")]


@pytest.mark.asyncio
async def test_continue_chat_delegates_to_application_service() -> None:
    """continue_chat 通过应用服务执行校验与 Agent 编排。"""

    workflow = _BoundaryWorkflow(ConversationContext())
    app_service = _BoundaryApplicationService()
    agent = MagicMock()
    agent.run = AsyncMock(return_value=AgentResult(content="done", model="test-model"))
    adapter = _adapter(workflow=workflow, app_service=app_service, agent=agent)

    response = await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))

    assert app_service.continue_calls == ["s1"]
    assert response.reply == "done"
    agent.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_and_stream_resume_share_application_service_core() -> None:
    """同步和流式审批恢复都调用同一个应用服务恢复核心。"""

    workflow = _BoundaryWorkflow(ConversationContext())
    app_service = _BoundaryApplicationService()
    adapter = _adapter(workflow=workflow, app_service=app_service)
    request = ApprovalResumeRequestVO(session_id="s1", approval_id="a1", decisions=())

    response = await adapter.resume_approval(request)
    events = [event async for event in adapter.stream_resume_approval(request)]

    assert response.reply == "done"
    assert [event.kind for event in events] == ["assistant_delta", "assistant_done"]
    assert app_service.resume_calls == ["a1", "a1"]
