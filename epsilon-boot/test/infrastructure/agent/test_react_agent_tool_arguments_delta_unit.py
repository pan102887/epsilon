"""``run_events`` ``tool_arguments_delta`` 事件单元测试模块。

覆盖 PR-2 任务 2.11 / Property 2：

(a) 收到 ≥1 条 ``tool_arguments_delta`` 事件；
(b) 各 ``tool_arguments_delta.arguments`` 顺序拼接 = 完整 ``arguments`` JSON；
(c) 末尾仍产出 ``assistant_done`` 事件；
(d) ``tool_call_id`` / ``tool_name`` 仅首个 delta 携带非 ``None``，后续可能为 ``None``；
(e) ``tool_arguments_delta.usage == None`` 且 ``content == ""``；
(f) 中间轮次累积期间不产出 ``tool_arguments_delta``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig, AgentStreamEvent
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class _FakeContextBuilder:
    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del model_access, model
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


def _adapter() -> ReActAgentAdapter:
    return ReActAgentAdapter(
        tool_registry=MagicMock(execute=AsyncMock(return_value=ToolExecutionResult(content="ok"))),
        context_builder=_FakeContextBuilder(),
    )


def _config(max_rounds: int = 1) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        model="m",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


# ── 测试用 stream 序列：最后一轮分多个分片产出 tool_calls.arguments JSON ──


def _final_round_stream_chunks() -> list[StreamingChunk]:
    """最后一轮：模型返回工具调用 + 文本，arguments 分 3 片产出。"""
    return [
        StreamingChunk(
            delta_content="thinking",
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="search", arguments_delta=""),
            ],
        ),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='{"q":')]),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='"hi"')]),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta="}")]),
        StreamingChunk(
            finished=True,
            usage={"total_tokens": 7},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0, id="c1", name="search", arguments_delta='{"q":"hi"}'
                )
            ],
        ),
    ]


class _FakeModelAccess:
    """``stream`` 在最后一轮（max_rounds=1）产出预设的 tool_calls 分片序列。"""

    def __init__(self, chunks: list[StreamingChunk]) -> None:
        self._chunks = chunks
        self.stream_call_count = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:  # pragma: no cover
        raise AssertionError("chat 不应被 ReAct 调用")

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        self.stream_call_count += 1
        for c in self._chunks:
            yield c

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return len(messages)


@pytest.mark.asyncio
async def test_tool_arguments_delta_emitted_with_correct_payload() -> None:
    """(a) ≥1 条 tool_arguments_delta；(b) arguments 拼接 = 完整 JSON；
    (c) 末尾产出 assistant_done；(d) id/name 仅首片携带非 None；
    (e) tool_arguments_delta usage=None 且 content=""。"""
    model = _FakeModelAccess(_final_round_stream_chunks())
    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("hi")

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, _config(max_rounds=1), model):
        events.append(ev)

    delta_events = [e for e in events if e.kind == "tool_arguments_delta"]
    # (a)
    assert len(delta_events) >= 1
    # (b) 拼接为完整 arguments
    concat = "".join(e.arguments or "" for e in delta_events)
    assert concat == '{"q":"hi"}'
    # (c)
    assert any(e.kind == "assistant_done" for e in events)
    # (d) 首个 delta 携带 id/name，后续可能 None
    assert delta_events[0].tool_call_id == "c1"
    assert delta_events[0].tool_name == "search"
    if len(delta_events) > 1:
        # 后续 delta 的 id/name 在 SDK 协议下通常为 None
        # 这里通过累积器机制传递，本 mock 中后续 delta 显式为 None
        assert any(e.tool_call_id is None or e.tool_name is None for e in delta_events[1:])
    # (e)
    for e in delta_events:
        assert e.content == ""
        assert e.usage is None


@pytest.mark.asyncio
async def test_intermediate_round_emits_no_tool_arguments_delta() -> None:
    """(f) 中间轮次累积期间不产出 ``tool_arguments_delta``。"""
    # 中间轮：通过累积器静默消费的分片中包含 tool_calls 增量；
    # 第 2 轮：text 自然终止（所以 max_rounds=2，无最后一轮 stream）。
    intermediate_chunks = [
        StreamingChunk(
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="search", arguments_delta=""),
            ]
        ),
        StreamingChunk(tool_calls=[StreamingToolCallDelta(index=0, arguments_delta='{"q":"x"}')]),
        StreamingChunk(
            finished=True,
            usage={"total_tokens": 3},
            tool_calls=[
                StreamingToolCallDelta(index=0, id="c1", name="search", arguments_delta='{"q":"x"}')
            ],
        ),
    ]
    final_text_chunks = [
        StreamingChunk(delta_content="done"),
        StreamingChunk(finished=True, usage={"total_tokens": 1}),
    ]

    class _ModelTwoRounds:
        def __init__(self) -> None:
            self.queue = [intermediate_chunks, final_text_chunks]

        async def chat(self, request: ChatRequest) -> LLMResponse:  # pragma: no cover
            raise AssertionError("chat 不应被 ReAct 调用")

        async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
            chunks = self.queue.pop(0)
            for c in chunks:
                yield c

        def count_tokens(self, messages: list[BaseMessage]) -> int:
            return len(messages)

    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("ask")

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, _config(max_rounds=2), _ModelTwoRounds()):
        events.append(ev)

    # 中间轮次有 tool_calls 增量分片，但 ReAct 累积器静默消费 → 0 条 tool_arguments_delta
    delta_events = [e for e in events if e.kind == "tool_arguments_delta"]
    assert len(delta_events) == 0
    # 第 2 轮 text 终止 → assistant_done 仍存在
    assert any(e.kind == "assistant_done" for e in events)
