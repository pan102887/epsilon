"""LLM 摘要压缩适配器属性测试。"""

from unittest.mock import AsyncMock, MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.chat.context import SystemMessage, UserMessage
from domain.model_access.value_objects import LLMResponse
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.llm_summary_compaction_adapter import (
    LLMSummaryCompactionAdapter,
)
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)


def _adapter(keep_recent: int) -> LLMSummaryCompactionAdapter:
    """构造属性测试适配器。"""
    prompt_registry = MagicMock()
    prompt_registry.get.return_value = LoadedPrompt(
        prompt_id="context-summary@v1",
        name="context-summary",
        version="v1",
        content="summary prompt",
    )
    return LLMSummaryCompactionAdapter(
        prompt_registry=prompt_registry,
        trigger_tokens=50,
        keep_recent_messages=keep_recent,
        fallback=SlidingWindowCompactionAdapter(max_messages=keep_recent),
    )


def _model_access(
    *, count_tokens: int = 100, chat_response: "LLMResponse | None" = None
) -> MagicMock:
    """构造 model_access mock，带可控 count_tokens 返回值。"""
    ma = MagicMock()
    ma.count_tokens = MagicMock(return_value=count_tokens)
    if chat_response is not None:
        ma.chat = AsyncMock(return_value=chat_response)
    return ma


@settings(max_examples=20)
@given(st.lists(st.text(min_size=1, max_size=20), max_size=8))
async def test_not_triggered_never_calls_summary_model(contents: list[str]) -> None:
    """未达到触发阈值时不调用摘要模型。"""
    adapter = _adapter(keep_recent=2)
    model_access = _model_access(count_tokens=10)
    messages = [UserMessage(content=content) for content in contents]

    await adapter.compact(messages, model_access=model_access)

    model_access.chat.assert_not_called()


@settings(max_examples=20)
@given(st.lists(st.text(min_size=1, max_size=20), min_size=3, max_size=8))
async def test_triggered_preserves_system_and_recent_order(contents: list[str]) -> None:
    """触发后保留全部 system 和最近 N 条非 system，顺序不变。"""
    keep_recent = 2
    adapter = _adapter(keep_recent=keep_recent)
    model_access = _model_access(
        count_tokens=100,
        chat_response=LLMResponse(content="summary", model="m1"),
    )
    systems = [SystemMessage(content="system-a"), SystemMessage(content="system-b")]
    non_system = [UserMessage(content=content) for content in contents]
    messages = [systems[0], *non_system, systems[1]]

    result = await adapter.compact(messages, model_access=model_access)

    assert result.messages[:2] == systems
    assert result.messages[-keep_recent:] == non_system[-keep_recent:]


@settings(max_examples=20)
@given(st.lists(st.text(min_size=1, max_size=20), min_size=3, max_size=8))
async def test_compaction_does_not_mutate_input_list(contents: list[str]) -> None:
    """压缩不会修改原输入消息列表。"""
    adapter = _adapter(keep_recent=1)
    model_access = _model_access(
        count_tokens=100,
        chat_response=LLMResponse(content="summary", model="m1"),
    )
    messages = [UserMessage(content=content) for content in contents]
    before = list(messages)

    await adapter.compact(messages, model_access=model_access)

    assert messages == before


@settings(max_examples=20)
@given(st.lists(st.text(min_size=1, max_size=20), min_size=3, max_size=8))
async def test_summary_failure_falls_back_without_raising(contents: list[str]) -> None:
    """摘要失败时降级且不抛出。"""
    adapter = _adapter(keep_recent=1)
    model_access = _model_access(count_tokens=100)
    model_access.chat = AsyncMock(side_effect=RuntimeError("boom"))
    messages = [UserMessage(content=content) for content in contents]

    result = await adapter.compact(messages, model_access=model_access)

    assert result.summary_created is False
