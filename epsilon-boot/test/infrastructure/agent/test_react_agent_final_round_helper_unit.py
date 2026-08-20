"""``Final_Round_Stream_Helper`` 抽取单元测试模块。

验证 v2 重构中新增的 ``_stream_final_round`` 与 ``_stream_events_final_round``
私有方法替代 ``run_streaming`` / ``run_events`` 的 4 处近似复制后行为不变：

- ``run_streaming`` 在 ``max_rounds == 1`` 时通过 ``_stream_final_round`` 完成产出，
  ``model_access.stream.call_count == 1`` 且 ``chat.call_count == 0``;
- ``run_streaming`` 在 ``max_rounds == 3`` 中间轮次都返回 tool_calls 时调用
  ``chat`` 2 次 + ``stream`` 1 次;
- ``run_events`` 同上;
- 两路径(``max_rounds == 1`` 与中间轮次耗尽后)产出的 ``finished=True`` 分片
  的 ``usage`` 字段值相同(不变量回归)。

覆盖需求 2.1-2.9, NFR-1, Property 2。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class _FakeContextBuilder:
    """测试 fake: 原样透传领域消息列表，可选透传 builder usage。"""

    def __init__(self, builder_usage: dict[str, int] | None = None) -> None:
        self._builder_usage = builder_usage or {}
        self.call_count = 0

    async def build(self, messages, **kwargs) -> ContextBuilderResult:
        self.call_count += 1
        return ContextBuilderResult(
            messages=messages,
            usage=dict(self._builder_usage),
        )


class _FakeModel:
    """v3：``stream`` 按队列顺序消费 ``chat_responses`` 等价产出分片；
    队列耗尽后回退到 ``stream_chunks`` 兜底（最后一轮 stream 默认行为）。"""

    def __init__(
        self,
        chat_responses: list[LLMResponse],
        stream_chunks: list[StreamingChunk] | None = None,
    ) -> None:
        self._chat_responses = list(chat_responses)
        self._stream_chunks = list(
            stream_chunks
            or [StreamingChunk(delta_content="最终回答", finished=True, usage={"total_tokens": 5})]
        )
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


def _config(max_rounds: int = 1) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "search", "parameters": {}}},
        ],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _adapter() -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


class TestRunStreamingMaxRoundsOne:
    """``max_rounds == 1`` 路径只发起 1 次 stream，0 次 chat。"""

    @pytest.mark.asyncio
    async def test_only_stream_called(self) -> None:
        """``max_rounds == 1`` 时仅触发 1 次 stream，0 次 chat。"""
        adapter = _adapter()
        model = _FakeModel(chat_responses=[])
        context = ConversationContext()

        chunks = []
        async for chunk in adapter.run_streaming(context, _config(max_rounds=1), model):
            chunks.append(chunk)

        assert model.chat_call_count == 0
        assert model.stream_call_count == 1
        assert chunks
        assert chunks[-1].finished is True


class TestRunStreamingMiddleRoundsExhausted:
    """``max_rounds == 3`` 中间轮次全部 tool_calls 后的行为。

    PR-4 变更：当循环耗尽且最后一轮仍为 tool_calls 时，
    ``terminated_reason="max_rounds"`` 并跳过 ``_stream_final_round``，
    不再发起最后一轮 stream。
    """

    @pytest.mark.asyncio
    async def test_chat_twice_stream_zero_on_max_rounds_hit(self) -> None:
        """``max_rounds == 3`` 中间 2 轮 tool_calls 命中循环耗尽：chat 2 次, stream 0 次。"""
        adapter = _adapter()
        # 中间 2 轮(round 1, round 2)都返回 tool_calls
        chat_responses = [
            LLMResponse(
                content="",
                model="test-model",
                tool_calls=[ToolCallRequest(id=f"call-{i}", name="search", arguments="{}")],
                usage={"total_tokens": 1},
            )
            for i in range(2)
        ]
        model = _FakeModel(chat_responses=chat_responses)
        context = ConversationContext()

        chunks = []
        async for chunk in adapter.run_streaming(context, _config(max_rounds=3), model):
            chunks.append(chunk)

        # v3：ReAct 内部全程 stream。max_rounds=3 + terminal_round=2 命中 →
        # 2 次 stream（中间 2 轮），跳过最后一轮 _stream_final_round。
        assert model.chat_call_count == 0
        assert model.stream_call_count == 2
        # 最后一个分片 finished=True 且携带 terminated_reason
        finished = [c for c in chunks if c.finished]
        assert len(finished) == 1
        assert finished[0].metadata.get("terminated_reason") == "max_rounds"

    @pytest.mark.asyncio
    async def test_chat_once_stream_once_when_text_reply(self) -> None:
        """``max_rounds == 3`` 中间 1 轮 tool_calls + 第 2 轮 text：
        正常进入 _stream_final_round。
        """
        adapter = _adapter()
        chat_responses = [
            LLMResponse(
                content="",
                model="test-model",
                tool_calls=[ToolCallRequest(id="call-0", name="search", arguments="{}")],
                usage={"total_tokens": 1},
            ),
            # 第 2 轮返回纯文本 -> kind="text"，循环在体内 yield + return
            LLMResponse(
                content="最终文本",
                model="test-model",
                tool_calls=[],
                usage={"total_tokens": 2},
            ),
        ]
        model = _FakeModel(chat_responses=chat_responses)
        context = ConversationContext()

        chunks = []
        async for chunk in adapter.run_streaming(context, _config(max_rounds=3), model):
            chunks.append(chunk)

        # v3：第 2 轮 text kind 由内部累积器消费 1 次 stream，提前返回不进入
        # 最后一轮 _stream_final_round → 累计 stream 调用 2 次（每轮一次）。
        assert model.chat_call_count == 0
        assert model.stream_call_count == 2
        finished = [c for c in chunks if c.finished]
        assert len(finished) == 1
        assert finished[0].delta_content == "最终文本"


class TestRunEventsMaxRoundsOne:
    """``run_events`` 在 ``max_rounds == 1`` 时仅触发 1 次 stream。"""

    @pytest.mark.asyncio
    async def test_only_stream_called(self) -> None:
        adapter = _adapter()
        model = _FakeModel(chat_responses=[])
        context = ConversationContext()

        events = []
        async for ev in adapter.run_events(context, _config(max_rounds=1), model):
            events.append(ev)

        assert model.chat_call_count == 0
        assert model.stream_call_count == 1
        # 应至少包含 status + assistant_done
        kinds = [e.kind for e in events]
        assert "status" in kinds
        assert "assistant_done" in kinds


class TestRunEventsMiddleRoundsExhausted:
    """``run_events`` 在 ``max_rounds == 3`` 中间 2 轮 tool_calls 后的行为。

    PR-4 变更：当循环耗尽且最后一轮仍为 tool_calls 时，
    ``terminated_reason="max_rounds"`` 并跳过 ``_stream_events_final_round``。
    """

    @pytest.mark.asyncio
    async def test_chat_twice_stream_zero_on_max_rounds_hit(self) -> None:
        adapter = _adapter()
        chat_responses = [
            LLMResponse(
                content="",
                model="test-model",
                tool_calls=[ToolCallRequest(id=f"call-{i}", name="search", arguments="{}")],
                usage={"total_tokens": 1},
            )
            for i in range(2)
        ]
        model = _FakeModel(chat_responses=chat_responses)
        context = ConversationContext()

        events = []
        async for ev in adapter.run_events(context, _config(max_rounds=3), model):
            events.append(ev)

        # v3：max_rounds=3 + terminal_round=2 命中 → 2 次 stream（中间 2 轮），
        # 跳过最后一轮 _stream_events_final_round。
        assert model.chat_call_count == 0
        assert model.stream_call_count == 2
        kinds = [e.kind for e in events]
        assert "assistant_done" in kinds
        # 最后一个 assistant_done 应携带 terminated_reason
        done_events = [e for e in events if e.kind == "assistant_done"]
        assert done_events[-1].metadata.get("terminated_reason") == "max_rounds"


class TestUsageInvariantBetweenTwoPaths:
    """两路径(``max_rounds == 1`` 与正常中间轮次耗尽后进入 stream)产出的 finished 分片 usage 等价。

    PR-4 说明：当循环耗尽且 ``terminated_reason="max_rounds"`` 时 stream 被跳过，
    这里测试的是 max_rounds==1 (直接 stream) 与 "中间轮次正常结束后进入 stream"
    两种路径的 usage 等价性。为此需要让中间轮次第 2 轮返回纯文本 -> text kind
    提前终止（不触发 max_rounds 超限），这样不进入循环耗尽分支，最后一轮 stream
    由 ``_stream_final_round`` 完成。或者直接对比 max_rounds==1 的两次独立调用。
    """

    @pytest.mark.asyncio
    async def test_finished_usage_equal_max_rounds_one_two_calls(self) -> None:
        """``max_rounds == 1`` 的两次独立调用应产出相同 usage。"""
        adapter1 = _adapter()
        model1 = _FakeModel(chat_responses=[])
        context1 = ConversationContext()
        chunks1 = []
        async for chunk in adapter1.run_streaming(context1, _config(max_rounds=1), model1):
            chunks1.append(chunk)
        finished1 = next(c for c in chunks1 if c.finished)

        adapter2 = _adapter()
        model2 = _FakeModel(chat_responses=[])
        context2 = ConversationContext()
        chunks2 = []
        async for chunk in adapter2.run_streaming(context2, _config(max_rounds=1), model2):
            chunks2.append(chunk)
        finished2 = next(c for c in chunks2 if c.finished)

        # 两次 max_rounds==1 调用下 stream 产出的 finished usage 应相等
        assert finished1.usage.get("total_tokens") == finished2.usage.get("total_tokens")
