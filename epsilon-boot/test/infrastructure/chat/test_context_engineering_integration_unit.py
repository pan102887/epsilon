"""Chat 上下文工程集成测试。

本模块验证直接聊天路径中 ``ChatServiceAdapter`` 与真实
``ContextBuilderAdapter`` 的协作边界：环境上下文进入模型输入，但不会
污染会话持久化历史，且 builder usage 会与主模型 usage 合并。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ChatRequestVO, ContextCompactionResult
from domain.model_access.value_objects import ChatRequest, LLMResponse
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.chat.context_builder_adapter import ContextBuilderAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


class FakeCompaction:
    """返回完整消息快照的压缩 fake，并暴露固定 builder usage。"""

    def __init__(self) -> None:
        """初始化 fake 的调用记录。"""
        self.calls: list[tuple[list[BaseMessage], object | None, str | None]] = []

    async def compact(
        self,
        messages: list[BaseMessage],
        *,
        model_access: object | None = None,
        model: str | None = None,
    ) -> ContextCompactionResult:
        """记录调用参数并返回复制后的消息列表。"""
        self.calls.append((list(messages), model_access, model))
        return ContextCompactionResult(
            messages=list(messages),
            usage={"prompt_tokens": 3, "summary_tokens": 2},
            summary_created=True,
        )


class FakeEnvironmentProvider:
    """生成固定的安全环境上下文。"""

    def build(self) -> str:
        """返回 Codex 风格环境上下文文本。"""
        return "\n".join(
            (
                "<environment_context>",
                "current_date: 2026-06-02",
                "workspace: workspace:/",
                "path_policy: Use workspace-relative POSIX paths.",
                "</environment_context>",
            )
        )


class FakeModelAccess:
    """捕获直接聊天请求并返回固定主模型 usage。"""

    def __init__(self) -> None:
        """初始化模型调用记录。"""
        self.chat_requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """记录模型请求并返回测试回复。"""
        self.chat_requests.append(request)
        return LLMResponse(
            content="assistant reply",
            model=request.model or "test-model",
            usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        )


@pytest.mark.asyncio
async def test_chat_context_engineering_injects_environment_without_persisting_it() -> None:
    """直接聊天路径应注入环境上下文、隔离历史并合并 usage。"""
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()

    model_access = FakeModelAccess()
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = model_access

    prompt_registry = MagicMock()
    prompt_registry.get.return_value = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="你是助手",
    )
    agent = MagicMock()

    fake_compaction = FakeCompaction()
    context_builder = ContextBuilderAdapter(
        compaction=fake_compaction,
        environment_provider=FakeEnvironmentProvider(),
    )
    adapter = ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=context_builder,
        agent=agent,
        tool_calling_enabled=False,
        max_tool_rounds=5,
        tool_schemas=[],
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=prompt_registry.get.return_value,
            agent=agent,
            tool_schemas=[],
            max_tool_rounds=5,
        ),
    )

    response = await adapter.chat(
        ChatRequestVO(session_id="session-1", message="hello", model="test-model")
    )

    assert len(model_access.chat_requests) == 1
    chat_request = model_access.chat_requests[0]
    environment_messages = [
        message
        for message in chat_request.messages
        if message.role == "system" and "<environment_context>" in str(message.content)
    ]
    assert len(environment_messages) == 1
    assert "workspace: workspace:/" in environment_messages[0].content

    assert fake_compaction.calls
    assert fake_compaction.calls[0][1] is model_access
    assert fake_compaction.calls[0][2] == "test-model"

    session_store.save.assert_awaited_once()
    saved_context = session_store.save.call_args.args[1]
    saved_payload = str(saved_context.to_dict())
    assert "<environment_context>" not in saved_payload
    assert "workspace:/" not in saved_payload
    assert "context_kind=environment" not in saved_payload

    assert response.usage == {
        "prompt_tokens": 8,
        "summary_tokens": 2,
        "completion_tokens": 7,
        "total_tokens": 12,
    }
