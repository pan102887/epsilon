"""``ReactFinalRoundStreamer`` 单元测试模块。

测试直接实例化最终轮流式协作者，使用 fake context builder 与 fake model
access 验证 chunk/event 映射，不导入具体 ReAct adapter。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from domain.agent.value_objects import AgentConfig
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.agent.react_final_round_streamer import ReactFinalRoundStreamer


class _FakeContextBuilder:
    """测试用上下文构建器，记录调用并返回固定 usage。"""

    def __init__(self, *, usage: dict[str, int] | None = None) -> None:
        """初始化 fake builder。"""
        self.usage = usage or {}
        self.calls: list[tuple[list[BaseMessage], ModelAccessPort | None, str | None]] = []

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        """返回原始消息与预设 usage。"""
        self.calls.append((messages, model_access, model))
        return ContextBuilderResult(messages=messages, usage=dict(self.usage))


class _FakeModelAccess:
    """测试用模型访问端口，按顺序吐出预设 stream 分片。"""

    def __init__(self, chunks: list[StreamingChunk]) -> None:
        """初始化 fake model。"""
        self._chunks = chunks
        self.stream_calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """本测试不应调用同步 chat。"""
        raise AssertionError("最终轮 streamer 不应调用 chat")

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """记录请求并吐出预设分片。"""
        self.stream_calls.append(request)
        for chunk in self._chunks:
            yield chunk

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        """本测试不依赖 token 估算。"""
        return len(messages)


def _config() -> AgentConfig:
    """构造测试用 Agent 配置。"""
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _context() -> ConversationContext:
    """构造带用户消息的对话上下文。"""
    context = ConversationContext()
    context.add_user_message("hi")
    return context


async def _collect_chunks(stream: AsyncIterator[StreamingChunk]) -> list[StreamingChunk]:
    """收集异步 chunk 流。"""
    return [chunk async for chunk in stream]


async def _collect_events(stream) -> list:
    """收集异步 event 流。"""
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_stream_chunks_passes_text_deltas_and_builds_chat_request() -> None:
    """文本中间分片原样透出，并使用 builder 结果构造 ChatRequest。"""
    builder = _FakeContextBuilder(usage={"prompt_tokens": 2})
    model = _FakeModelAccess(
        [
            StreamingChunk(delta_content="你"),
            StreamingChunk(delta_content="好"),
            StreamingChunk(delta_content="", finished=True, usage={"completion_tokens": 3}),
        ]
    )
    streamer = ReactFinalRoundStreamer(context_builder=builder)
    context = _context()

    chunks = await _collect_chunks(
        streamer.stream_chunks(
            context=context,
            config=_config(),
            model_access=model,
            round_num=3,
            initial_usage={"total_tokens": 1},
        )
    )

    assert [chunk.delta_content for chunk in chunks] == ["你", "好", ""]
    assert chunks[0] is model._chunks[0]
    assert len(builder.calls) == 1
    assert builder.calls[0][1] is model
    assert builder.calls[0][2] == "test-model"
    assert len(model.stream_calls) == 1
    assert model.stream_calls[0].model == "test-model"
    assert model.stream_calls[0].tools == _config().tool_schemas


@pytest.mark.asyncio
async def test_stream_chunks_merges_usage_and_preserves_finished_metadata() -> None:
    """finished 分片合并 initial/builder/chunk usage，并保留末尾 metadata。"""
    builder = _FakeContextBuilder(usage={"prompt_tokens": 2, "total_tokens": 2})
    model = _FakeModelAccess(
        [
            StreamingChunk(
                delta_content="done",
                finished=True,
                usage={"completion_tokens": 3, "total_tokens": 3},
                metadata={"provider": "fake"},
            )
        ]
    )
    streamer = ReactFinalRoundStreamer(context_builder=builder)

    chunks = await _collect_chunks(
        streamer.stream_chunks(
            context=_context(),
            config=_config(),
            model_access=model,
            round_num=1,
            initial_usage={"prompt_tokens": 5, "total_tokens": 5},
        )
    )

    assert len(chunks) == 1
    finished = chunks[0]
    assert finished.finished is True
    assert finished.delta_content == "done"
    assert finished.usage == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    assert finished.metadata == {"provider": "fake"}


@pytest.mark.asyncio
async def test_stream_events_maps_text_delta_and_finished_done() -> None:
    """event 路径映射 assistant_delta 与带 round/usage 的 assistant_done。"""
    builder = _FakeContextBuilder(usage={"prompt_tokens": 2})
    model = _FakeModelAccess(
        [
            StreamingChunk(delta_content="hello"),
            StreamingChunk(delta_content="", finished=True, usage={"completion_tokens": 4}),
        ]
    )
    streamer = ReactFinalRoundStreamer(context_builder=builder)

    events = await _collect_events(
        streamer.stream_events(
            context=_context(),
            config=_config(),
            model_access=model,
            round_num=7,
            initial_usage={"total_tokens": 1},
        )
    )

    assert [event.kind for event in events] == ["assistant_delta", "assistant_done"]
    assert events[0].content == "hello"
    assert events[1].usage == {
        "prompt_tokens": 2,
        "completion_tokens": 4,
        "total_tokens": 1,
    }
    assert events[1].metadata == {"round": 7}


@pytest.mark.asyncio
async def test_stream_events_maps_tool_arguments_delta_only_before_finished() -> None:
    """非 finished tool_calls 分片映射为 tool_arguments_delta，末尾只产出 done。"""
    builder = _FakeContextBuilder()
    model = _FakeModelAccess(
        [
            StreamingChunk(
                tool_calls=[
                    StreamingToolCallDelta(
                        index=0,
                        id="call-1",
                        name="search",
                        arguments_delta="",
                    )
                ]
            ),
            StreamingChunk(
                tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='{"q":')]
            ),
            StreamingChunk(
                tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='"hi"}')]
            ),
            StreamingChunk(
                finished=True,
                usage={"total_tokens": 5},
                tool_calls=[
                    StreamingToolCallDelta(
                        index=0,
                        id="call-1",
                        name="search",
                        arguments_delta='{"q":"hi"}',
                    )
                ],
            ),
        ]
    )
    streamer = ReactFinalRoundStreamer(context_builder=builder)

    events = await _collect_events(
        streamer.stream_events(
            context=_context(),
            config=_config(),
            model_access=model,
            round_num=2,
            initial_usage=None,
        )
    )

    delta_events = [event for event in events if event.kind == "tool_arguments_delta"]
    assert len(delta_events) == 3
    assert delta_events[0].tool_call_id == "call-1"
    assert delta_events[0].tool_name == "search"
    assert "".join(event.arguments or "" for event in delta_events) == '{"q":"hi"}'
    assert all(event.content == "" for event in delta_events)
    assert all(event.usage is None for event in delta_events)
    assert all(event.metadata == {"round": 2} for event in delta_events)
    assert [event.kind for event in events][-1] == "assistant_done"


@pytest.mark.asyncio
async def test_custom_usage_merger_is_constructor_injected() -> None:
    """构造注入的 usage 合并函数会同时参与初始合并与 finished 合并。"""
    calls: list[tuple[dict[str, int] | None, dict[str, int] | None]] = []

    def merge_usage(left: dict[str, int] | None, right: dict[str, int] | None) -> dict[str, int]:
        calls.append((left, right))
        merged: dict[str, int] = {}
        for usage in (left, right):
            if usage:
                for key, value in usage.items():
                    merged[key] = merged.get(key, 0) + value
        return merged

    builder = _FakeContextBuilder(usage={"prompt_tokens": 2})
    model = _FakeModelAccess([StreamingChunk(finished=True, usage={"completion_tokens": 3})])
    streamer = ReactFinalRoundStreamer(context_builder=builder, merge_usage=merge_usage)

    chunks = await _collect_chunks(
        streamer.stream_chunks(
            context=_context(),
            config=_config(),
            model_access=model,
            round_num=1,
            initial_usage={"total_tokens": 1},
        )
    )

    assert chunks[-1].usage == {
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 1,
    }
    assert calls == [
        ({"total_tokens": 1}, {"prompt_tokens": 2}),
        ({"total_tokens": 1, "prompt_tokens": 2}, {"completion_tokens": 3}),
    ]
