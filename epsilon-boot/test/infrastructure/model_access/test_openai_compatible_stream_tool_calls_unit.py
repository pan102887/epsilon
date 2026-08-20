"""``OpenAICompatibleAdapter.stream`` 工具调用分片透传单元测试。

覆盖 PR-1 任务 1.7：

* 中间分片 ``StreamingChunk.tool_calls`` 仅携带本片增量；
* 多 ``tool_calls`` 并行（不同 ``index``）分别累积；
* ``finished=True`` 分片携带按 ``index`` 升序的完整列表，且与等价 chat
  一次返回的 ``LLMResponse.tool_calls`` 按 ``(id, name, arguments)`` 三元组相等；
* 纯文本流 ``StreamingChunk.tool_calls`` 全程为 ``None``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import UserMessage
from domain.model_access.value_objects import (
    ChatRequest,
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter() -> OpenAICompatibleAdapter:
    cfg = MagicMock()
    cfg.api_key = "k"
    cfg.api_base = "https://fake/v1"
    cfg.timeout = 30
    cfg.max_retries = 0
    cfg.max_connections = 10
    cfg.max_keepalive_connections = 5
    cfg.provider_name = "test"
    cfg.default_model = "m"
    cfg.temperature = 0.7
    cfg.max_tokens = 4096
    return OpenAICompatibleAdapter(cfg)


def _sdk_chunk(
    *,
    content: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    """构造模拟 OpenAI SDK 流式分片（``chunk.choices[0].delta``）。"""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _sdk_usage_only_chunk(prompt: int, completion: int, total: int) -> SimpleNamespace:
    """构造仅含 usage 的最末分片（``chunk.choices`` 为空）。"""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        ),
    )


def _sdk_tool_call_delta(
    *,
    index: int,
    id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> SimpleNamespace:
    """构造模拟 SDK ``delta.tool_calls[i]``。"""
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=function)


class _MockAsyncStream:
    """异步迭代器，模拟 ``AsyncOpenAI.chat.completions.create(stream=True)``。"""

    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        for c in self._chunks:
            yield c


async def _consume(stream: AsyncIterator[StreamingChunk]) -> list[StreamingChunk]:
    return [c async for c in stream]


@pytest.mark.asyncio
async def test_stream_intermediate_chunks_carry_only_increment() -> None:
    """中间分片 ``tool_calls`` 仅携带本片增量（不携带累积值）。"""
    adapter = _make_adapter()
    chunks = [
        _sdk_chunk(
            tool_calls=[
                _sdk_tool_call_delta(index=0, id="call_1", name="search", arguments=""),
            ]
        ),
        _sdk_chunk(
            tool_calls=[
                _sdk_tool_call_delta(index=0, arguments='{"q":'),
            ]
        ),
        _sdk_chunk(
            tool_calls=[
                _sdk_tool_call_delta(index=0, arguments='"hi"}'),
            ]
        ),
        _sdk_chunk(finish_reason="tool_calls"),
        _sdk_usage_only_chunk(10, 20, 30),
    ]
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_MockAsyncStream(chunks)
    )

    out = await _consume(adapter.stream(ChatRequest(messages=[UserMessage(content="x")])))

    # 中间 3 个 delta 分片：tool_calls 仅含本片增量
    assert out[0].tool_calls == [
        StreamingToolCallDelta(index=0, id="call_1", name="search", arguments_delta=""),
    ]
    assert out[1].tool_calls == [
        StreamingToolCallDelta(index=0, id=None, name=None, arguments_delta='{"q":'),
    ]
    assert out[2].tool_calls == [
        StreamingToolCallDelta(index=0, id=None, name=None, arguments_delta='"hi"}'),
    ]


@pytest.mark.asyncio
async def test_stream_multiple_tool_calls_accumulated_by_index() -> None:
    """多 ``tool_calls`` 按 ``index`` 分别累积。"""
    adapter = _make_adapter()
    chunks = [
        _sdk_chunk(
            tool_calls=[
                _sdk_tool_call_delta(index=0, id="c1", name="a", arguments=""),
                _sdk_tool_call_delta(index=1, id="c2", name="b", arguments=""),
            ]
        ),
        _sdk_chunk(
            tool_calls=[
                _sdk_tool_call_delta(index=0, arguments='{"x":1}'),
            ]
        ),
        _sdk_chunk(
            tool_calls=[
                _sdk_tool_call_delta(index=1, arguments='{"y":2}'),
            ]
        ),
        _sdk_chunk(finish_reason="tool_calls"),
    ]
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_MockAsyncStream(chunks)
    )

    out = await _consume(adapter.stream(ChatRequest(messages=[UserMessage(content="x")])))
    final = next(c for c in out if c.finished)

    assert final.tool_calls is not None
    # 升序按 index 排列；arguments_delta 在 finished=True 分片代表完整 arguments
    assert final.tool_calls == [
        StreamingToolCallDelta(index=0, id="c1", name="a", arguments_delta='{"x":1}'),
        StreamingToolCallDelta(index=1, id="c2", name="b", arguments_delta='{"y":2}'),
    ]


@pytest.mark.asyncio
async def test_stream_finished_chunk_full_tool_calls() -> None:
    """``finished=True`` 分片携带的列表与等价 chat 一次返回的工具调用按
    ``(id, name, arguments)`` 三元组相等。"""
    adapter = _make_adapter()
    chunks = [
        _sdk_chunk(
            tool_calls=[_sdk_tool_call_delta(index=0, id="c1", name="search", arguments="")]
        ),
        _sdk_chunk(tool_calls=[_sdk_tool_call_delta(index=0, arguments='{"q":"a"}')]),
        _sdk_chunk(finish_reason="tool_calls"),
    ]
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_MockAsyncStream(chunks)
    )

    out = await _consume(adapter.stream(ChatRequest(messages=[UserMessage(content="x")])))
    final = out[-1]

    assert final.finished is True
    assert final.tool_calls is not None
    only = final.tool_calls[0]
    assert only.id == "c1"
    assert only.name == "search"
    assert only.arguments_delta == '{"q":"a"}'


@pytest.mark.asyncio
async def test_stream_pure_text_keeps_tool_calls_none() -> None:
    """纯文本流 ``StreamingChunk.tool_calls`` 全程保持 ``None``。"""
    adapter = _make_adapter()
    chunks = [
        _sdk_chunk(content="hello"),
        _sdk_chunk(content=" world"),
        _sdk_chunk(finish_reason="stop"),
        _sdk_usage_only_chunk(1, 2, 3),
    ]
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_MockAsyncStream(chunks)
    )

    out = await _consume(adapter.stream(ChatRequest(messages=[UserMessage(content="x")])))

    assert all(c.tool_calls is None for c in out)
    # 仍按 v2 既有形态产出 delta_content / finished / usage
    assert "".join(c.delta_content for c in out) == "hello world"


@pytest.mark.asyncio
async def test_stream_usage_only_chunk_carries_full_tool_calls() -> None:
    """SDK 末尾仅携带 ``usage`` 的分片（``finish_reason`` 在前一片）也要补出累积态完整列表。"""
    adapter = _make_adapter()
    chunks = [
        _sdk_chunk(
            tool_calls=[_sdk_tool_call_delta(index=0, id="c1", name="t", arguments='{"k":1}')]
        ),
        # 注意：未在前一片 finish_reason，只在 usage_only_chunk 中作为流终止的下游标记
        _sdk_usage_only_chunk(1, 2, 3),
    ]
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_MockAsyncStream(chunks)
    )

    out = await _consume(adapter.stream(ChatRequest(messages=[UserMessage(content="x")])))
    last = out[-1]

    assert last.finished is True
    assert last.tool_calls is not None
    assert last.tool_calls[0].id == "c1"
    assert last.tool_calls[0].name == "t"
    assert last.tool_calls[0].arguments_delta == '{"k":1}'
