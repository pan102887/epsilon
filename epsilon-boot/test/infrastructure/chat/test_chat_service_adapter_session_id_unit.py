"""ChatServiceAdapter session_id 写入回归测试模块。

验证 v2 重构中 ``ChatServiceAdapter`` 的 4 处 ``session_id`` 写入由
``setattr(context, "session_id", request.session_id)`` 替换为对正式字段
``context.session_id = request.session_id`` 的直接赋值后，行为等价：

- ``chat`` 入口写入后 ``context.session_id == request.session_id``;
- ``stream_chat`` 入口写入后 ``context.session_id == request.session_id``;
- ``stream_chat_events`` 入口写入后 ``context.session_id == request.session_id``;
- ``resume_approval`` 入口写入后 ``context.session_id == request.session_id``。

测试聚焦于 "入口直接赋值生效" 这一行为契约，不涉及 Agent Loop 内部
模型调用次数的回归。

覆盖需求 5.8 / 5.9。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatRequestVO, ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, StreamingChunk
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _make_adapter(
    *,
    session_store: MagicMock,
    model_access: MagicMock,
    context_builder: MagicMock,
    agent: MagicMock | None = None,
    tool_calling_enabled: bool = False,
    tool_schemas: list[dict] | None = None,
    approval_store: MagicMock | None = None,
) -> ChatServiceAdapter:
    """构造一个用于 session_id 测试的 ChatServiceAdapter。"""
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = model_access
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="你是助手",
    )
    effective_agent = agent or MagicMock()
    effective_tool_schemas = tool_schemas or []
    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=loaded_prompt
            )
        ),
        context_builder=context_builder,
        agent=effective_agent,
        tool_calling_enabled=tool_calling_enabled,
        max_tool_rounds=5,
        tool_schemas=effective_tool_schemas,
        approval_store=approval_store,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=effective_agent,
            tool_schemas=effective_tool_schemas,
            max_tool_rounds=5,
            approval_store=approval_store,
        ),
    )


def _builder_result() -> ContextBuilderResult:
    """构造一个最小可用的 ContextBuilderResult。"""
    return ContextBuilderResult(
        messages=[
            UserMessage(content="hello"),
        ],
        usage={},
        environment_injected=False,
    )


class TestChatSessionIdWrite:
    """``chat`` 入口直接赋值 session_id。"""

    @pytest.mark.asyncio
    async def test_chat_assigns_session_id(self) -> None:
        """``chat`` 入口加载上下文后应直接赋值 ``context.session_id``。"""
        context = ConversationContext()
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=context)
        session_store.save = AsyncMock()
        model_access = MagicMock()
        model_access.chat = AsyncMock(
            return_value=LLMResponse(content="ok", model="test-model", usage={})
        )
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())

        adapter = _make_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        await adapter.chat(ChatRequestVO(session_id="sess-chat", message="hi"))

        assert context.session_id == "sess-chat"


class TestStreamChatSessionIdWrite:
    """``stream_chat`` 入口直接赋值 session_id。"""

    @pytest.mark.asyncio
    async def test_stream_chat_assigns_session_id(self) -> None:
        """``stream_chat`` 入口加载上下文后应直接赋值 ``context.session_id``。"""
        context = ConversationContext()
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=context)
        session_store.save = AsyncMock()

        async def _stream(_request):
            yield StreamingChunk(delta_content="ok", finished=True, usage={})

        model_access = MagicMock()
        model_access.stream = _stream
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())

        adapter = _make_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        request = ChatRequestVO(session_id="sess-stream", message="hi", stream=True)
        async for _ in adapter.stream_chat(request):
            pass

        assert context.session_id == "sess-stream"


class TestStreamChatEventsSessionIdWrite:
    """``stream_chat_events`` 入口直接赋值 session_id。"""

    @pytest.mark.asyncio
    async def test_stream_chat_events_assigns_session_id(self) -> None:
        """``stream_chat_events`` 入口加载上下文后应直接赋值 ``context.session_id``。"""
        context = ConversationContext()
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=context)
        session_store.save = AsyncMock()

        async def _stream(_request):
            yield StreamingChunk(delta_content="ok", finished=True, usage={})

        model_access = MagicMock()
        model_access.stream = _stream
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())

        adapter = _make_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        request = ChatRequestVO(session_id="sess-events", message="hi", stream=True)
        async for _ in adapter.stream_chat_events(request):
            pass

        assert context.session_id == "sess-events"


class TestResumeApprovalSessionIdWrite:
    """``resume_approval`` 入口直接赋值 session_id。"""

    @pytest.mark.asyncio
    async def test_resume_approval_assigns_session_id(self) -> None:
        """``resume_approval`` 入口反序列化上下文后应直接赋值 ``context.session_id``。"""
        from domain.agent.value_objects import (
            ApprovalDecision,
            ApprovalInterrupt,
            PendingActionRequest,
        )

        original_ctx = ConversationContext()
        original_ctx.add_user_message("hello")
        snapshot = original_ctx.to_dict()

        action = PendingActionRequest(
            tool_call_id="call-1",
            tool_name="echo",
            arguments="{}",
            allowed_decisions=frozenset({"approve"}),
        )
        interrupt = ApprovalInterrupt(
            session_id="sess-resume",
            approval_id="appr-1",
            actions=(action,),
            context_snapshot=snapshot,
            round_num=1,
            model="test-model",
        )

        approval_store = MagicMock()
        approval_store.load = AsyncMock(return_value=interrupt)
        approval_store.consume = AsyncMock(return_value=interrupt)

        captured_ctx: list[ConversationContext] = []

        async def _resume(context, _config, _model_access, _interrupt, _decisions):
            captured_ctx.append(context)
            return AgentResult(content="done", model="test-model", usage={})

        agent = MagicMock()
        agent.resume = _resume

        session_store = MagicMock()
        session_store.save = AsyncMock()

        model_access = MagicMock()
        context_builder = MagicMock()

        adapter = _make_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
            agent=agent,
            tool_calling_enabled=True,
            tool_schemas=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            approval_store=approval_store,
        )

        decision = ApprovalDecision(type="approve", tool_call_id="call-1")
        await adapter.resume_approval(
            ApprovalResumeRequestVO(
                session_id="sess-resume",
                approval_id="appr-1",
                decisions=(decision,),
            )
        )

        assert len(captured_ctx) == 1
        assert captured_ctx[0].session_id == "sess-resume"
