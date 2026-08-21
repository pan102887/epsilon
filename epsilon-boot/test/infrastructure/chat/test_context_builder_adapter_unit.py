"""上下文构建适配器单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from domain.chat.value_objects import ContextCompactionResult
from infrastructure.chat.context_builder_adapter import ContextBuilderAdapter
from infrastructure.chat.environment_context_provider import (
    EnvironmentContextBuildError,
    UnsafeEnvironmentContextError,
)


class _FakeEnvironmentProvider:
    """返回固定环境上下文文本的测试 provider。"""

    def __init__(self, text: str = "<environment_context>safe</environment_context>"):
        """初始化测试 provider。"""
        self._text = text

    def build(self) -> str:
        """返回固定环境上下文文本。"""
        return self._text


class _FailingEnvironmentProvider:
    """抛出固定异常的测试 provider。"""

    def __init__(self, error: Exception) -> None:
        """初始化测试 provider。"""
        self._error = error

    def build(self) -> str:
        """抛出配置好的异常。"""
        raise self._error


def _build_adapter(
    compaction_result: ContextCompactionResult,
    *,
    environment_text: str = "<environment_context>safe</environment_context>",
) -> tuple[ContextBuilderAdapter, MagicMock]:
    """构建带 AsyncMock compaction 的适配器。"""
    compaction = MagicMock()
    compaction.compact = AsyncMock(return_value=compaction_result)
    adapter = ContextBuilderAdapter(
        compaction=compaction,
        environment_provider=_FakeEnvironmentProvider(environment_text),
    )
    return adapter, compaction


@pytest.mark.asyncio
async def test_build_passes_model_access_and_model_to_compaction() -> None:
    """build 应把 model_access 与 model 原样透传给压缩策略。"""
    messages: list[BaseMessage] = [UserMessage(content="用户输入")]
    model_access = MagicMock()
    adapter, compaction = _build_adapter(
        ContextCompactionResult(messages=messages, usage={}, summary_created=False)
    )

    await adapter.build(messages, model_access=model_access, model="test-model")

    compaction.compact.assert_awaited_once_with(
        messages,
        model_access=model_access,
        model="test-model",
    )


@pytest.mark.asyncio
async def test_build_inserts_environment_after_last_system_message() -> None:
    """存在 system 消息时，环境上下文应插入最后一条 system 消息之后。"""
    adapter, _ = _build_adapter(
        ContextCompactionResult(
            messages=[
                SystemMessage(content="system prompt"),
                SystemMessage(content="summary"),
                UserMessage(content="question"),
            ],
            usage={"prompt_tokens": 3, "total_tokens": 3},
            summary_created=True,
        ),
        environment_text="<environment_context>env</environment_context>",
    )

    result = await adapter.build([UserMessage(content="source")])

    assert result.messages == [
        SystemMessage(content="system prompt"),
        SystemMessage(content="summary"),
        SystemMessage(
            content="<environment_context>env</environment_context>",
            metadata={"context_kind": "environment"},
        ),
        UserMessage(content="question"),
    ]
    assert result.usage == {"prompt_tokens": 3, "total_tokens": 3}
    assert result.summary_created is True
    assert result.environment_injected is True


@pytest.mark.asyncio
async def test_build_inserts_environment_at_head_when_no_system_message() -> None:
    """没有 system 消息时，环境上下文应插入模型消息头部。"""
    adapter, _ = _build_adapter(
        ContextCompactionResult(
            messages=[
                UserMessage(content="question"),
                AssistantMessage(content="answer"),
            ],
            usage={},
            summary_created=False,
        ),
        environment_text="<environment_context>env</environment_context>",
    )

    result = await adapter.build([UserMessage(content="source")])

    assert result.messages == [
        SystemMessage(
            content="<environment_context>env</environment_context>",
            metadata={"context_kind": "environment"},
        ),
        UserMessage(content="question"),
        AssistantMessage(content="answer"),
    ]
    assert result.summary_created is False
    assert result.environment_injected is True


@pytest.mark.asyncio
async def test_build_does_not_mutate_input_or_compaction_result_messages() -> None:
    """build 不应原地修改输入列表或压缩返回列表。"""
    source_messages: list[BaseMessage] = [
        UserMessage(content="source", metadata={"trace": "input"})
    ]
    compacted_messages: list[BaseMessage] = [
        SystemMessage(content="system", metadata={"trace": "compacted-system"}),
        UserMessage(content="question", metadata={"trace": "compacted-user"}),
    ]
    original_source_snapshot = list(source_messages)
    original_compacted_snapshot = list(compacted_messages)
    adapter, _ = _build_adapter(
        ContextCompactionResult(
            messages=compacted_messages,
            usage={"summary_tokens": 2},
            summary_created=False,
        )
    )

    result = await adapter.build(source_messages)

    assert source_messages == original_source_snapshot
    assert compacted_messages == original_compacted_snapshot
    assert all(
        message.content != "<environment_context>safe</environment_context>"
        for message in source_messages + compacted_messages
    )
    assert result.usage == {"summary_tokens": 2}


@pytest.mark.asyncio
async def test_build_preserves_source_metadata_and_injects_environment() -> None:
    """历史消息保留原始 metadata，环境消息携带 context_kind 标记。"""
    adapter, _ = _build_adapter(
        ContextCompactionResult(
            messages=[
                SystemMessage(content="system", metadata={"secret": "hidden"}),
                UserMessage(content="question", metadata={"trace": "hidden"}),
            ],
            usage={},
            summary_created=False,
        )
    )

    result = await adapter.build([UserMessage(content="source")])

    # Source messages preserve their metadata
    assert result.messages[0] == SystemMessage(content="system", metadata={"secret": "hidden"})
    # Environment message carries context_kind metadata
    assert result.messages[1] == SystemMessage(
        content="<environment_context>safe</environment_context>",
        metadata={"context_kind": "environment"},
    )
    assert result.messages[2] == UserMessage(content="question", metadata={"trace": "hidden"})


@pytest.mark.asyncio
async def test_build_propagates_unsafe_environment_context_error() -> None:
    """provider 抛 UnsafeEnvironmentContextError 时，build 应阻断并直接传播。"""
    compaction = MagicMock()
    compaction.compact = AsyncMock(
        return_value=ContextCompactionResult(
            messages=[UserMessage(content="question")],
            usage={},
            summary_created=False,
        )
    )
    adapter = ContextBuilderAdapter(
        compaction=compaction,
        environment_provider=_FailingEnvironmentProvider(UnsafeEnvironmentContextError("unsafe")),
    )

    with pytest.raises(UnsafeEnvironmentContextError):
        await adapter.build([UserMessage(content="source")])

    compaction.compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_wraps_regular_provider_error() -> None:
    """provider 普通异常应包装为 EnvironmentContextBuildError。"""
    original_error = RuntimeError("provider failed")
    compaction = MagicMock()
    compaction.compact = AsyncMock(
        return_value=ContextCompactionResult(
            messages=[UserMessage(content="question")],
            usage={},
            summary_created=False,
        )
    )
    adapter = ContextBuilderAdapter(
        compaction=compaction,
        environment_provider=_FailingEnvironmentProvider(original_error),
    )

    with pytest.raises(EnvironmentContextBuildError) as exc_info:
        await adapter.build([UserMessage(content="source")])

    assert exc_info.value.__cause__ is original_error
    compaction.compact.assert_awaited_once()
