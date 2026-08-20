"""``_RoundStreamAccumulator`` 单元测试模块。

覆盖 PR-2 任务 2.8：

(a) 纯文本累积：``delta_content`` 顺序拼接 → ``LLMResponse.content``；
(b) 单 tool_call 累积：多分片 ``arguments_delta`` 拼接 → ``LLMResponse.tool_calls[0].arguments``；
(c) 多 tool_calls 并行（不同 ``index``）累积；
(d) ``usage`` 取 ``finished=True`` 分片，缺失视为 ``{}``；
(e) ``latency_ms`` 为非负 float；
(f) ``finished=True`` 分片携带的"完整 arguments"优先覆盖增量拼接结果（决策 11）；
(g) ``build_response()`` 与等价 chat 一次返回值按 ``(content, tool_calls.id,
    tool_calls.name, tool_calls.arguments, usage)`` 全等。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from domain.model_access.value_objects import (
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.agent.round_stream_accumulator import _RoundStreamAccumulator


async def _async_iter(chunks: list[StreamingChunk]) -> AsyncIterator[StreamingChunk]:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_pure_text_accumulation() -> None:
    """(a) 纯文本累积。"""
    acc = _RoundStreamAccumulator(model="m")
    chunks = [
        StreamingChunk(delta_content="hello "),
        StreamingChunk(delta_content="world"),
        StreamingChunk(delta_content="", finished=True, usage={"total_tokens": 7}),
    ]
    await acc.consume(_async_iter(chunks))

    response = acc.build_response()
    assert response.content == "hello world"
    assert response.model == "m"
    assert response.usage == {"total_tokens": 7}
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_single_tool_call_increment_accumulation() -> None:
    """(b) 单 tool_call 多分片 arguments 拼接。"""
    acc = _RoundStreamAccumulator(model="m")
    chunks = [
        StreamingChunk(
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="search", arguments_delta=""),
            ]
        ),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='{"q":')]),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='"x"}')]),
        # 末尾不携带 finished 完整列表，纯靠增量拼接
        StreamingChunk(finished=True, usage={"total_tokens": 1}),
    ]
    await acc.consume(_async_iter(chunks))

    response = acc.build_response()
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc.id == "c1"
    assert tc.name == "search"
    assert tc.arguments == '{"q":"x"}'


@pytest.mark.asyncio
async def test_multiple_tool_calls_parallel_accumulation() -> None:
    """(c) 多 tool_calls 按 index 并行累积。"""
    acc = _RoundStreamAccumulator(model="m")
    chunks = [
        StreamingChunk(
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="a", arguments_delta=""),
                StreamingToolCallDelta(index=1, id="c2", name="b", arguments_delta=""),
            ]
        ),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='{"x":1}')]),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=1, arguments_delta='{"y":2}')]),
        StreamingChunk(finished=True, usage={"total_tokens": 1}),
    ]
    await acc.consume(_async_iter(chunks))

    response = acc.build_response()
    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].id == "c1"
    assert response.tool_calls[0].arguments == '{"x":1}'
    assert response.tool_calls[1].id == "c2"
    assert response.tool_calls[1].arguments == '{"y":2}'


@pytest.mark.asyncio
async def test_usage_taken_from_finished_chunk_or_empty() -> None:
    """(d) ``usage`` 取 ``finished=True`` 分片，缺失视为 ``{}``。"""
    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(
        _async_iter(
            [
                StreamingChunk(delta_content="x"),
                StreamingChunk(finished=True),  # 无 usage
            ]
        )
    )
    assert acc.build_response().usage == {}


@pytest.mark.asyncio
async def test_latency_ms_non_negative_float() -> None:
    """(e) ``latency_ms`` 为非负 float。"""
    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(_async_iter([StreamingChunk(finished=True, usage={"total_tokens": 1})]))
    response = acc.build_response()
    assert isinstance(response.latency_ms, float)
    assert response.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_finished_full_tool_calls_overrides_increment() -> None:
    """(f) ``finished=True`` 分片携带"完整 arguments"优先覆盖增量。"""
    acc = _RoundStreamAccumulator(model="m")
    chunks = [
        # 增量拼接结果会得到 '{"q":' + ...，但末尾分片直接给完整 arguments
        StreamingChunk(
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="t", arguments_delta=""),
            ]
        ),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='{"q":')]),
        # finished=True 分片携带完整重组结果
        StreamingChunk(
            finished=True,
            usage={"total_tokens": 1},
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="t", arguments_delta='{"q":"final"}')
            ],
        ),
    ]
    await acc.consume(_async_iter(chunks))

    response = acc.build_response()
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].arguments == '{"q":"final"}'


@pytest.mark.asyncio
async def test_build_response_full_equivalence() -> None:
    """(g) ``build_response()`` 与等价 chat 一次返回值的全字段相等。"""
    acc = _RoundStreamAccumulator(model="my-model")
    chunks = [
        StreamingChunk(delta_content="answer "),
        StreamingChunk(delta_content="text"),
        StreamingChunk(
            finished=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0, id="c1", name="search", arguments_delta='{"q":"y"}'
                ),
            ],
        ),
    ]
    await acc.consume(_async_iter(chunks))

    response = acc.build_response()
    assert response.content == "answer text"
    assert response.model == "my-model"
    assert response.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert (tc.id, tc.name, tc.arguments) == ("c1", "search", '{"q":"y"}')


@pytest.mark.asyncio
async def test_consume_disallows_double_call() -> None:
    """``consume`` 仅支持一次。"""
    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(_async_iter([StreamingChunk(finished=True)]))

    with pytest.raises(RuntimeError):
        await acc.consume(_async_iter([StreamingChunk(finished=True)]))
