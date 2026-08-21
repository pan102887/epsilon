"""``Final_Round_Stream_Helper`` 等价性属性测试模块。

PR-4 更新：当 ``terminated_reason="max_rounds"`` 时流式入口跳过
``_stream_*_final_round``，因此"中间轮次全部 tool_calls 后的最后一轮
stream"路径不再适用于等价性断言。本测试聚焦于 ``max_rounds == 1``
路径的确定性行为：多次调用应产出相同的 finished 分片特征。

覆盖需求 2.7 与 Property 2（限于 max_rounds==1 路径）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig, AgentStreamEvent
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class _FakeContextBuilder:
    """测试用上下文构建器(空 builder usage,聚焦 stream 路径)。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


class _FakeModel:
    """随机分片序列 + 可控 chat 响应的模型 fake。"""

    def __init__(
        self,
        chat_responses: list[LLMResponse],
        stream_chunks: list[StreamingChunk],
    ) -> None:
        self._chat_responses = list(chat_responses)
        self._stream_chunks = list(stream_chunks)
        self.chat_call_count = 0
        self.stream_call_count = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:
        self.chat_call_count += 1
        return self._chat_responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        self.stream_call_count += 1
        if self._chat_responses:
            response = self._chat_responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        for chunk in self._stream_chunks:
            yield chunk

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return 0


def _make_adapter() -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


def _make_config(max_rounds: int) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "search", "parameters": {}}},
        ],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


# ── Hypothesis 策略 ──

# 1 ~ 5 个 stream 分片
delta_st = st.text(min_size=0, max_size=20)
# 最后一片 finished=True 携带 usage
final_chunk_usage_st = st.fixed_dictionaries(
    {"total_tokens": st.integers(min_value=0, max_value=100)}
)


@st.composite
def stream_chunk_seq_st(draw: st.DrawFn) -> list[StreamingChunk]:
    """构造一个非空的 stream 分片序列,最后一个 finished=True。"""
    n = draw(st.integers(min_value=1, max_value=4))
    chunks: list[StreamingChunk] = []
    for _i in range(n - 1):
        chunks.append(
            StreamingChunk(
                delta_content=draw(delta_st),
                finished=False,
                usage={},
            )
        )
    chunks.append(
        StreamingChunk(
            delta_content=draw(delta_st),
            finished=True,
            usage=draw(final_chunk_usage_st),
        )
    )
    return chunks


@settings(max_examples=20, deadline=None)
@given(stream_chunks=stream_chunk_seq_st())
@pytest.mark.asyncio
async def test_run_streaming_max_rounds_one_deterministic(
    stream_chunks: list[StreamingChunk],
) -> None:
    """``run_streaming`` 在 ``max_rounds == 1`` 路径下两次独立调用产出等价 finished 分片。

    验证 ``_stream_final_round`` 抽取后 max_rounds==1 路径的确定性行为：
    相同 stream_chunks 输入应产出相同 finished 分片 (delta_content / metadata / usage)。
    """
    adapter_a = _make_adapter()
    model_a = _FakeModel(chat_responses=[], stream_chunks=list(stream_chunks))
    ctx_a = ConversationContext()
    ctx_a.add_user_message("hi")
    chunks_a: list[StreamingChunk] = []
    async for chunk in adapter_a.run_streaming(ctx_a, _make_config(max_rounds=1), model_a):
        chunks_a.append(chunk)

    adapter_b = _make_adapter()
    model_b = _FakeModel(chat_responses=[], stream_chunks=list(stream_chunks))
    ctx_b = ConversationContext()
    ctx_b.add_user_message("hi")
    chunks_b: list[StreamingChunk] = []
    async for chunk in adapter_b.run_streaming(ctx_b, _make_config(max_rounds=1), model_b):
        chunks_b.append(chunk)

    finished_a = [c for c in chunks_a if c.finished]
    finished_b = [c for c in chunks_b if c.finished]
    assert len(finished_a) == 1
    assert len(finished_b) == 1

    fa = finished_a[0]
    fb = finished_b[0]

    assert fa.delta_content == fb.delta_content
    assert dict(fa.metadata) == dict(fb.metadata)
    assert dict(fa.usage or {}) == dict(fb.usage or {})


@settings(max_examples=20, deadline=None)
@given(stream_chunks=stream_chunk_seq_st())
@pytest.mark.asyncio
async def test_run_events_max_rounds_one_deterministic(
    stream_chunks: list[StreamingChunk],
) -> None:
    """``run_events`` 在 ``max_rounds == 1`` 路径下两次独立调用产出等价 assistant_done 事件。"""
    adapter_a = _make_adapter()
    model_a = _FakeModel(chat_responses=[], stream_chunks=list(stream_chunks))
    ctx_a = ConversationContext()
    ctx_a.add_user_message("hi")
    events_a: list[AgentStreamEvent] = []
    async for ev in adapter_a.run_events(ctx_a, _make_config(max_rounds=1), model_a):
        events_a.append(ev)

    adapter_b = _make_adapter()
    model_b = _FakeModel(chat_responses=[], stream_chunks=list(stream_chunks))
    ctx_b = ConversationContext()
    ctx_b.add_user_message("hi")
    events_b: list[AgentStreamEvent] = []
    async for ev in adapter_b.run_events(ctx_b, _make_config(max_rounds=1), model_b):
        events_b.append(ev)

    done_a = [e for e in events_a if e.kind == "assistant_done"]
    done_b = [e for e in events_b if e.kind == "assistant_done"]
    assert len(done_a) == 1
    assert len(done_b) == 1

    assert dict(done_a[0].usage or {}) == dict(done_b[0].usage or {})

    deltas_a = "".join(e.content for e in events_a if e.kind == "assistant_delta")
    deltas_b = "".join(e.content for e in events_b if e.kind == "assistant_delta")
    assert deltas_a == deltas_b


@settings(max_examples=20, deadline=None)
@given(
    middle_rounds=st.integers(min_value=1, max_value=3),
    stream_chunks=stream_chunk_seq_st(),
)
@pytest.mark.asyncio
async def test_run_streaming_max_rounds_hit_skips_stream(
    middle_rounds: int, stream_chunks: list[StreamingChunk]
) -> None:
    """当中间轮次全部 tool_calls 命中 max_rounds 时, stream 被跳过。

    验证 PR-4 Property 4: terminated_reason="max_rounds" 时 stream_call_count == 0
    且 finished 分片携带 metadata.terminated_reason="max_rounds"。
    """
    adapter = _make_adapter()
    chat_responses = [
        LLMResponse(
            content="",
            model="test-model",
            tool_calls=[ToolCallRequest(id=f"call-{i}", name="search", arguments="{}")],
            usage={},
        )
        for i in range(middle_rounds)
    ]
    model = _FakeModel(chat_responses=chat_responses, stream_chunks=list(stream_chunks))
    ctx = ConversationContext()
    ctx.add_user_message("hi")
    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(
        ctx, _make_config(max_rounds=middle_rounds + 1), model
    ):
        chunks.append(chunk)

    # v3：ReAct 全程 stream。max_rounds 命中时跳过最后一轮 _stream_final_round，
    # 中间轮次累计 ``middle_rounds`` 次 stream 调用。
    assert model.stream_call_count == middle_rounds
    assert model.chat_call_count == 0
    # finished 分片携带 terminated_reason
    finished = [c for c in chunks if c.finished]
    assert len(finished) == 1
    assert finished[0].metadata.get("terminated_reason") == "max_rounds"
