"""ChatSessionContextWorkflow 单元测试。"""

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from collections.abc import Awaitable, Callable
from typing import TypeVar
from hypothesis import given, settings

from application.chat.session_context_workflow import ChatSessionContextWorkflow
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO, SessionMetadata

_MESSAGE_CONTENT_ST = st.text(min_size=0, max_size=200)
_NON_SYSTEM_MESSAGE_ST = st.one_of(
    st.builds(UserMessage, content=_MESSAGE_CONTENT_ST),
    st.builds(AssistantMessage, content=_MESSAGE_CONTENT_ST),
    st.builds(ToolMessage, content=_MESSAGE_CONTENT_ST, tool_name=st.just("unknown")),
)
_SYSTEM_MESSAGE_ST = st.builds(SystemMessage, content=_MESSAGE_CONTENT_ST)
_ANY_MESSAGE_ST = st.one_of(_NON_SYSTEM_MESSAGE_ST, _SYSTEM_MESSAGE_ST)
_SYSTEM_PROMPT_ST = st.text(min_size=1, max_size=200).filter(lambda value: value.strip() != "")
_T = TypeVar("_T")


class _MemorySessionStore:
    """测试用内存会话存储。"""

    def __init__(self, context: ConversationContext | None = None) -> None:
        self.context = context or ConversationContext()
        self.saved: list[tuple[str, ConversationContext]] = []

    async def load(self, session_id: str) -> ConversationContext:
        """返回预置上下文。"""

        return self.context

    async def save(self, session_id: str, context: ConversationContext) -> None:
        """记录保存调用。"""

        self.saved.append((session_id, context))

    async def delete(self, session_id: str) -> None:
        self.context = ConversationContext()

    async def exists(self, session_id: str) -> bool:
        return bool(self.context.get_messages())

    async def compare_and_swap(
        self,
        session_id: str,
        mutator: Callable[[ConversationContext], Awaitable[_T]],
    ) -> _T:
        result = await mutator(self.context)
        await self.save(session_id, self.context)
        return result


class _MemorySessionIndex:
    """测试用内存会话索引。"""

    def __init__(self, existing: SessionMetadata | None = None) -> None:
        self.existing = existing
        self.upserts: list[SessionMetadata] = []

    async def get(self, session_id: str) -> SessionMetadata | None:
        """返回预置索引元数据。"""

        return self.existing

    async def upsert(self, metadata: SessionMetadata) -> None:
        """记录 upsert 调用。"""

        self.upserts.append(metadata)

    async def list_recent(self, limit: int = 20) -> list[SessionMetadata]:
        return ([self.existing] if self.existing is not None else [])[:limit]

    async def delete(self, session_id: str) -> None:
        if self.existing is not None and self.existing.session_id == session_id:
            self.existing = None


def _build_context(messages: list[BaseMessage]) -> ConversationContext:
    """按消息角色构造测试用会话上下文。"""

    context = ConversationContext()
    for message in messages:
        if message.role == "system":
            context.add_system_message(message.content)
        elif message.role == "user":
            context.add_user_message(message.content)
        elif message.role == "assistant":
            context.add_assistant_message(message.content)
        elif message.role == "tool":
            context.add_tool_result(getattr(message, "tool_name", "unknown"), message.content)
    return context


def _system_messages(context: ConversationContext) -> list[BaseMessage]:
    """返回上下文中的 system 消息列表。"""

    return [message for message in context.get_messages() if message.role == "system"]


def _workflow_for(context: ConversationContext, system_prompt: str) -> ChatSessionContextWorkflow:
    """为指定上下文构造测试 workflow。"""

    return ChatSessionContextWorkflow(
        _MemorySessionStore(context),
        None,
        system_prompt,
        "chat-default@v1",
    )


@pytest.mark.asyncio
async def test_load_for_chat_sets_session_injects_system_and_appends_user() -> None:
    """新聊天加载上下文时写入 session_id、注入系统提示词并追加用户消息。"""

    store = _MemorySessionStore()
    workflow = ChatSessionContextWorkflow(store, None, "system prompt", "chat-default@v1")

    context = await workflow.load_for_chat(ChatRequestVO(session_id="s1", message="hello"))

    messages = context.get_messages()
    assert context.session_id == "s1"
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "system prompt"
    assert isinstance(messages[1], UserMessage)
    assert messages[1].content == "hello"


@pytest.mark.asyncio
async def test_load_for_continue_sets_session_without_appending_user_or_system() -> None:
    """继续执行只加载既有上下文并写入 session_id。"""

    context = ConversationContext()
    store = _MemorySessionStore(context)
    workflow = ChatSessionContextWorkflow(store, None, "system prompt", "chat-default@v1")

    loaded = await workflow.load_for_continue(ChatContinueRequestVO(session_id="s1"))

    assert loaded is context
    assert loaded.session_id == "s1"
    assert loaded.get_messages() == []


def test_ensure_system_prompt_is_idempotent_when_system_exists() -> None:
    """已有 system 消息时不重复插入，也不改写原内容。"""

    context = ConversationContext()
    context.add_system_message("existing")
    workflow = ChatSessionContextWorkflow(
        _MemorySessionStore(context),
        None,
        "system prompt",
        "chat-default@v1",
    )

    workflow.ensure_system_prompt(context)
    workflow.ensure_system_prompt(context)

    system_messages = [message for message in context.get_messages() if message.role == "system"]
    assert len(system_messages) == 1
    assert system_messages[0].content == "existing"


@settings(max_examples=100)
@given(
    messages=st.lists(_NON_SYSTEM_MESSAGE_ST, min_size=0, max_size=20),
    system_prompt=_SYSTEM_PROMPT_ST,
)
def test_ensure_system_prompt_adds_one_when_absent_property(
    messages: list[BaseMessage],
    system_prompt: str,
) -> None:
    """无 system 消息时 workflow 恰好新增一条系统提示词。"""

    context = _build_context(messages)
    assert _system_messages(context) == []

    _workflow_for(context, system_prompt).ensure_system_prompt(context)

    system_messages = _system_messages(context)
    assert len(system_messages) == 1
    assert system_messages[0].content == system_prompt


@settings(max_examples=100)
@given(
    non_system_messages=st.lists(_NON_SYSTEM_MESSAGE_ST, min_size=0, max_size=15),
    system_messages=st.lists(_SYSTEM_MESSAGE_ST, min_size=1, max_size=5),
    system_prompt=_SYSTEM_PROMPT_ST,
)
def test_ensure_system_prompt_preserves_existing_system_messages_property(
    non_system_messages: list[BaseMessage],
    system_messages: list[BaseMessage],
    system_prompt: str,
) -> None:
    """已有 system 消息时 workflow 不增删也不改写原内容。"""

    context = _build_context([*system_messages, *non_system_messages])
    original_contents = [message.content for message in _system_messages(context)]

    _workflow_for(context, system_prompt).ensure_system_prompt(context)

    current_contents = [message.content for message in _system_messages(context)]
    assert current_contents == original_contents


@settings(max_examples=100)
@given(
    messages=st.lists(_ANY_MESSAGE_ST, min_size=0, max_size=20),
    system_prompt=_SYSTEM_PROMPT_ST,
)
def test_ensure_system_prompt_is_idempotent_property(
    messages: list[BaseMessage],
    system_prompt: str,
) -> None:
    """workflow 连续注入系统提示词后消息角色与内容保持不变。"""

    context = _build_context(messages)
    workflow = _workflow_for(context, system_prompt)

    workflow.ensure_system_prompt(context)
    snapshot_after_first = [(message.role, message.content) for message in context.get_messages()]

    workflow.ensure_system_prompt(context)
    snapshot_after_second = [(message.role, message.content) for message in context.get_messages()]

    assert snapshot_after_first == snapshot_after_second


@pytest.mark.asyncio
async def test_save_context_and_index_upserts_metadata_with_prompt_preview_and_model() -> None:
    """保存上下文后刷新索引，保留既有 created_at 并写入预览和模型。"""

    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("hello\n   world")
    store = _MemorySessionStore(context)
    index = _MemorySessionIndex(
        SessionMetadata(
            session_id="s1",
            updated_at_epoch_ms=2000,
            message_count=1,
            preview="old",
            created_at_epoch_ms=1000,
        )
    )
    workflow = ChatSessionContextWorkflow(store, index, "system", "chat-default@v1")

    await workflow.save_context_and_index("s1", context, model="m1")

    assert store.saved == [("s1", context)]
    metadata = index.upserts[0]
    assert metadata.session_id == "s1"
    assert metadata.message_count == 2
    assert metadata.preview == "hello world"
    assert metadata.created_at_epoch_ms == 1000
    assert metadata.updated_at_epoch_ms >= 1000
    assert metadata.model == "m1"


@pytest.mark.asyncio
async def test_save_context_and_index_uses_empty_preview_for_system_only() -> None:
    """只有 system 消息时沿用既有空会话预览格式。"""

    context = ConversationContext()
    context.add_system_message("system")
    store = _MemorySessionStore(context)
    index = _MemorySessionIndex()
    workflow = ChatSessionContextWorkflow(store, index, "system", "chat-default@v1")

    await workflow.save_context_and_index("s1", context)

    assert index.upserts[0].preview == "(空会话)"
