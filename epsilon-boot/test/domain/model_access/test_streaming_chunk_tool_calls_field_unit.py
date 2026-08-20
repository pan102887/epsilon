"""``StreamingChunk.tool_calls`` 字段与 :class:`StreamingToolCallDelta` 单元测试。

覆盖 PR-1 任务 1.6：

* ``StreamingToolCallDelta`` 字段约束（``index`` 必填、其余三字段默认 ``None``）；
* ``StreamingChunk`` 默认 ``tool_calls=None``（**非空元组**）；
* ``frozen=True`` 不可变；
* 末尾追加可选字段不破坏既有位置参数构造。
"""

from dataclasses import FrozenInstanceError

import pytest

from domain.model_access.value_objects import StreamingChunk, StreamingToolCallDelta


def test_streaming_tool_call_delta_index_required() -> None:
    """``index`` 必填，其余三字段默认为 ``None``。"""
    delta = StreamingToolCallDelta(index=0)

    assert delta.index == 0
    assert delta.id is None
    assert delta.name is None
    assert delta.arguments_delta is None


def test_streaming_tool_call_delta_index_missing_raises() -> None:
    """缺少 ``index`` 必填字段时构造失败。"""
    with pytest.raises(TypeError):
        StreamingToolCallDelta()  # type: ignore[call-arg]


def test_streaming_tool_call_delta_full_fields() -> None:
    """携带四个字段的完整构造。"""
    delta = StreamingToolCallDelta(
        index=2,
        id="call_abc",
        name="search",
        arguments_delta='{"q": "x"}',
    )

    assert delta.index == 2
    assert delta.id == "call_abc"
    assert delta.name == "search"
    assert delta.arguments_delta == '{"q": "x"}'


def test_streaming_tool_call_delta_is_frozen() -> None:
    """``StreamingToolCallDelta`` ``frozen=True``，禁止修改字段。"""
    delta = StreamingToolCallDelta(index=0, id="call_1")

    with pytest.raises(FrozenInstanceError):
        delta.id = "call_2"  # type: ignore[misc]


def test_streaming_chunk_tool_calls_defaults_to_none() -> None:
    """``StreamingChunk.tool_calls`` 默认是 ``None``，**非空列表/元组**。"""
    chunk = StreamingChunk()

    assert chunk.tool_calls is None


def test_streaming_chunk_with_tool_calls_field() -> None:
    """显式赋值 ``tool_calls`` 字段。"""
    deltas = [
        StreamingToolCallDelta(index=0, id="call_1", name="echo"),
        StreamingToolCallDelta(index=0, arguments_delta='{"x":1}'),
    ]

    chunk = StreamingChunk(delta_content="", finished=False, tool_calls=deltas)

    assert chunk.tool_calls is deltas
    assert chunk.tool_calls[0].id == "call_1"
    assert chunk.tool_calls[1].arguments_delta == '{"x":1}'


def test_streaming_chunk_is_frozen_with_tool_calls() -> None:
    """``StreamingChunk`` 仍 ``frozen=True``，禁止覆盖 ``tool_calls`` 字段。"""
    chunk = StreamingChunk(tool_calls=None)

    with pytest.raises(FrozenInstanceError):
        chunk.tool_calls = []  # type: ignore[misc]


def test_streaming_chunk_legacy_positional_args_compatible() -> None:
    """末尾追加 ``tool_calls`` 字段不破坏既有位置参数构造（覆盖 NFR-2）。

    注意：原 4 个字段（``delta_content`` / ``finished`` / ``usage`` / ``metadata``）
    位置不变，``tool_calls`` 作为末尾追加的可选字段，默认 ``None``。
    """
    chunk = StreamingChunk("hello", True, {"total_tokens": 1}, {"k": "v"})

    assert chunk.delta_content == "hello"
    assert chunk.finished is True
    assert chunk.usage == {"total_tokens": 1}
    assert chunk.metadata == {"k": "v"}
    assert chunk.tool_calls is None


def test_streaming_chunk_legacy_kwargs_compatible() -> None:
    """既有关键字构造仍可用，且 ``tool_calls`` 默认 ``None``。"""
    chunk = StreamingChunk(delta_content="x", finished=True, usage={"total_tokens": 1})

    assert chunk.tool_calls is None
