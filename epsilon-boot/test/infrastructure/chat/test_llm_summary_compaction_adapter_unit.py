"""LLM 摘要上下文压缩适配器单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import AssistantMessage, SystemMessage, UserMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.llm_summary_compaction_adapter import (
    LLMSummaryCompactionAdapter,
)
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)


def _loaded_prompt() -> LoadedPrompt:
    """构造 context-summary@v1 Prompt。"""
    return LoadedPrompt(
        prompt_id="context-summary@v1",
        name="context-summary",
        version="v1",
        content="summary prompt",
    )


def _adapter(
    *, keep_recent: int = 2
) -> tuple[
    LLMSummaryCompactionAdapter,
    MagicMock,
]:
    """构造被测适配器和 prompt registry mock。"""
    prompt_registry = MagicMock()
    prompt_registry.get.return_value = _loaded_prompt()
    adapter = LLMSummaryCompactionAdapter(
        prompt_registry=prompt_registry,
        trigger_tokens=50,
        keep_recent_messages=keep_recent,
        fallback=SlidingWindowCompactionAdapter(max_messages=2),
    )
    return adapter, prompt_registry


def _model_access(
    *, count_tokens: int = 100, chat_response: "LLMResponse | None" = None
) -> MagicMock:
    """构造 model_access mock，带可控 count_tokens 返回值。"""
    ma = MagicMock()
    ma.count_tokens = MagicMock(return_value=count_tokens)
    if chat_response is not None:
        ma.chat = AsyncMock(return_value=chat_response)
    return ma


def test_constructor_loads_context_summary_prompt() -> None:
    """构造期加载 context-summary Prompt。"""
    _, prompt_registry = _adapter()

    prompt_registry.get.assert_called_once_with("context-summary")


@pytest.mark.asyncio
async def test_not_triggered_returns_original_copy_without_model_call() -> None:
    """低于触发阈值时透传原消息拷贝且不调用模型。"""
    adapter, _ = _adapter()
    model_access = _model_access(count_tokens=10)
    messages = [UserMessage(content="hello")]

    result = await adapter.compact(messages, model_access=model_access, model="m1")

    assert result.messages == messages
    assert result.messages is not messages
    assert result.usage == {}
    assert result.summary_created is False
    model_access.chat.assert_not_called()


@pytest.mark.asyncio
async def test_triggered_summary_uses_prompt_and_current_model() -> None:
    """触发摘要时使用 LoadedPrompt.content 和当前请求模型。"""
    adapter, _ = _adapter(keep_recent=2)
    model_access = _model_access(
        count_tokens=100,
        chat_response=LLMResponse(
            content="summary text",
            model="m1",
            usage={"summary_tokens": 7},
        ),
    )
    messages = [
        SystemMessage(content="system-1"),
        UserMessage(content="old-1"),
        AssistantMessage(content="old-2"),
        UserMessage(content="recent-1"),
        AssistantMessage(content="recent-2"),
    ]

    result = await adapter.compact(messages, model_access=model_access, model="m1")

    request = model_access.chat.call_args.args[0]
    assert request.model == "m1"
    assert request.messages[0] == SystemMessage(content="summary prompt")
    assert "old-1" in request.messages[1].content
    assert result.messages == [
        messages[0],
        SystemMessage(content="summary text"),
        messages[3],
        messages[4],
    ]
    assert result.usage == {"summary_tokens": 7}
    assert result.summary_created is True


@pytest.mark.asyncio
async def test_no_earlier_messages_does_not_summarize() -> None:
    """较早非 system 消息为空时不调用摘要模型。"""
    adapter, _ = _adapter(keep_recent=3)
    model_access = _model_access(count_tokens=100)
    messages = [SystemMessage(content="system"), UserMessage(content="recent")]

    result = await adapter.compact(messages, model_access=model_access)

    assert result.messages == messages
    model_access.chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        LLMResponse(content="   ", model="m1"),
        LLMResponse(
            content="summary",
            model="m1",
            tool_calls=[
                ToolCallRequest(id="call-1", name="tool", arguments="{}"),
            ],
        ),
    ],
)
async def test_invalid_summary_response_falls_back(response: LLMResponse) -> None:
    """空摘要或摘要返回 tool_calls 时降级到滑动窗口。"""
    adapter, _ = _adapter(keep_recent=1)
    model_access = _model_access(count_tokens=100)
    model_access.chat = AsyncMock(return_value=response)
    messages = [
        SystemMessage(content="system"),
        UserMessage(content="old"),
        UserMessage(content="recent"),
    ]

    result = await adapter.compact(messages, model_access=model_access)

    assert result.messages == messages
    assert result.summary_created is False
    assert result.usage == {}


@pytest.mark.asyncio
async def test_summary_exception_falls_back_without_raising() -> None:
    """摘要调用异常时降级且不向主流程抛出。"""
    adapter, _ = _adapter(keep_recent=1)
    model_access = _model_access(count_tokens=100)
    model_access.chat = AsyncMock(side_effect=RuntimeError("boom"))
    messages = [
        SystemMessage(content="system"),
        UserMessage(content="old"),
        UserMessage(content="recent"),
    ]

    result = await adapter.compact(messages, model_access=model_access)

    assert result.messages == messages
    assert result.summary_created is False


@pytest.mark.asyncio
async def test_missing_model_access_falls_back() -> None:
    """缺少 model_access 时直接降级到滑动窗口。"""
    adapter, _ = _adapter(keep_recent=1)
    messages = [
        SystemMessage(content="system"),
        UserMessage(content="old"),
        UserMessage(content="recent"),
    ]

    result = await adapter.compact(messages)

    assert result.messages == messages
    assert result.summary_created is False
