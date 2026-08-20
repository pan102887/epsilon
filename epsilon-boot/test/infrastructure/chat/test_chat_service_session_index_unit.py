"""ChatServiceAdapter session index 同步测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatRequestVO, ContextBuilderResult, SessionMetadata
from domain.model_access.value_objects import LLMResponse
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _adapter(
    *,
    session_store: MagicMock,
    session_index: MagicMock | None = None,
    model_access: MagicMock | None = None,
    context_builder: MagicMock | None = None,
    approval_store: MagicMock | None = None,
) -> ChatServiceAdapter:
    registry = MagicMock()
    registry.get_default_model.return_value = "default-model"
    registry.get_adapter_for_model.return_value = model_access or MagicMock()
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="system",
    )
    prompt_registry = MagicMock(
        get=MagicMock(
            return_value=loaded_prompt
        )
    )
    agent = MagicMock()
    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=registry,
        prompt_registry=prompt_registry,
        context_builder=context_builder or MagicMock(),
        agent=agent,
        tool_calling_enabled=False,
        max_tool_rounds=5,
        tool_schemas=[],
        approval_store=approval_store,
        session_index=session_index,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=[],
            max_tool_rounds=5,
            approval_store=approval_store,
            session_index=session_index,
        ),
    )


@pytest.mark.asyncio
async def test_chat_direct_path_upserts_session_metadata() -> None:
    """直接 LLM 聊天保存上下文后刷新 session index。"""
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()
    session_index = MagicMock()
    session_index.get = AsyncMock(return_value=None)
    session_index.upsert = AsyncMock()
    model_access = MagicMock()
    model_access.chat = AsyncMock(return_value=LLMResponse(content="reply", model="actual-model"))
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="hello")],
            usage={},
            environment_injected=False,
        )
    )
    adapter = _adapter(
        session_store=session_store,
        session_index=session_index,
        model_access=model_access,
        context_builder=context_builder,
    )

    await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    metadata = session_index.upsert.call_args.args[0]
    assert isinstance(metadata, SessionMetadata)
    assert metadata.session_id == "s1"
    assert metadata.message_count == 3
    assert metadata.preview == "reply"
    assert metadata.model == "actual-model"
    assert metadata.created_at_epoch_ms == metadata.updated_at_epoch_ms


@pytest.mark.asyncio
async def test_save_context_and_index_keeps_existing_created_at() -> None:
    """刷新索引时保留首次 indexed 时间。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("hello\n   world")
    session_store = MagicMock()
    session_store.save = AsyncMock()
    session_index = MagicMock()
    session_index.get = AsyncMock(
        return_value=SessionMetadata(
            session_id="s1",
            updated_at_epoch_ms=2000,
            message_count=1,
            preview="old",
            created_at_epoch_ms=1000,
        )
    )
    session_index.upsert = AsyncMock()
    adapter = _adapter(session_store=session_store, session_index=session_index)

    await adapter._save_context_and_index("s1", context, model="m1")

    metadata = session_index.upsert.call_args.args[0]
    assert metadata.created_at_epoch_ms == 1000
    assert metadata.updated_at_epoch_ms >= 1000
    assert metadata.preview == "hello world"
    assert metadata.model == "m1"


@pytest.mark.asyncio
async def test_save_context_failure_does_not_touch_index() -> None:
    """上下文保存失败时不写 session index。"""
    session_store = MagicMock()
    session_store.save = AsyncMock(side_effect=RuntimeError("save failed"))
    session_index = MagicMock()
    session_index.get = AsyncMock()
    session_index.upsert = AsyncMock()
    adapter = _adapter(session_store=session_store, session_index=session_index)

    with pytest.raises(RuntimeError, match="save failed"):
        await adapter._save_context_and_index("s1", ConversationContext())

    session_index.get.assert_not_awaited()
    session_index.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_index_failure_propagates_after_context_saved() -> None:
    """索引写入失败向上传播，但上下文已先保存。"""
    session_store = MagicMock()
    session_store.save = AsyncMock()
    session_index = MagicMock()
    session_index.get = AsyncMock(return_value=None)
    session_index.upsert = AsyncMock(side_effect=RuntimeError("index failed"))
    adapter = _adapter(session_store=session_store, session_index=session_index)

    with pytest.raises(RuntimeError, match="index failed"):
        await adapter._save_context_and_index("s1", ConversationContext())

    session_store.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_session_deletes_context_index_and_approvals() -> None:
    """显式删除会话时同步删除上下文、索引和审批状态。"""
    session_store = MagicMock()
    session_store.delete = AsyncMock()
    session_index = MagicMock()
    session_index.delete = AsyncMock()
    approval_store = MagicMock()
    approval_store.delete_session = AsyncMock()
    adapter = _adapter(
        session_store=session_store,
        session_index=session_index,
        approval_store=approval_store,
    )

    await adapter.clear_session("s1")

    session_store.delete.assert_awaited_once_with("s1")
    session_index.delete.assert_awaited_once_with("s1")
    approval_store.delete_session.assert_awaited_once_with("s1")
