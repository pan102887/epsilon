"""ReActAgentAdapter 同轮工具并发执行单元测试。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    StreamingToolCallDelta,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class _FakeContextBuilder:
    async def build(self, messages, **kwargs) -> ContextBuilderResult:
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


def _response_to_chunks(response: LLMResponse) -> list[StreamingChunk]:
    chunks: list[StreamingChunk] = []
    if response.content:
        chunks.append(StreamingChunk(delta_content=response.content, finished=False))
    if response.tool_calls:
        full = [
            StreamingToolCallDelta(index=i, id=tc.id, name=tc.name, arguments_delta=tc.arguments)
            for i, tc in enumerate(response.tool_calls)
        ]
        chunks.append(
            StreamingChunk(delta_content="", finished=True, usage=response.usage, tool_calls=full)
        )
    else:
        chunks.append(StreamingChunk(delta_content="", finished=True, usage=response.usage))
    return chunks


class _FakeModel:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    async def chat(self, request: ChatRequest) -> LLMResponse:
        return self._responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        if self._responses:
            response = self._responses.pop(0)
            for chunk in _response_to_chunks(response):
                yield chunk
            return
        yield StreamingChunk(delta_content="done", finished=True, usage={"total_tokens": 5})


def _config(max_rounds=3) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[{"type": "function", "function": {"name": "slow_tool"}}],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _adapter_with_slow_tool(sleep_time=0.5):
    """创建带延迟执行工具的适配器。"""
    tool_registry = MagicMock()

    async def slow_execute(tool_call):
        await asyncio.sleep(sleep_time)
        return ToolExecutionResult(content=f"result-{tool_call.id}")

    tool_registry.execute = AsyncMock(side_effect=slow_execute)
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_single_tool_call_fast_path_equivalence():
    """Property 1: 单工具 fast path 与 v3 串行等价。"""
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="single-result"))
    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[ToolCallRequest(id="tc1", name="slow_tool", arguments="{}")],
        ),
        LLMResponse(content="answer", model="m", usage={"total_tokens": 20}),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("hi")

    result = await adapter.run(context, _config(max_rounds=3), model)  # type: ignore[arg-type]
    assert result.content == "answer"
    tool_msgs = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"


@pytest.mark.asyncio
async def test_run_three_concurrent_tools_total_elapsed_under_threshold():
    """Property 2: 3 工具各 sleep(0.5)，并发总耗时 < 1.2s。"""
    adapter = _adapter_with_slow_tool(sleep_time=0.5)

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="tc1", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc2", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc3", name="slow_tool", arguments="{}"),
            ],
        ),
        LLMResponse(content="done", model="m", usage={"total_tokens": 20}),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("go")

    start = time.monotonic()
    result = await adapter.run(context, _config(max_rounds=3), model)  # type: ignore[arg-type]
    elapsed = time.monotonic() - start

    assert result.content == "done"
    assert elapsed < 1.2, f"Elapsed {elapsed:.2f}s exceeds 1.2s threshold"
    tool_msgs = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 3


@pytest.mark.asyncio
async def test_run_partial_failure_does_not_affect_others():
    """Property 4: 3 工具 (deny, raise, ok)，互不影响。"""
    from domain.agent.exceptions import ToolPermissionDeniedError

    call_results = {
        "tc-deny": ToolPermissionDeniedError("slow_tool", "deny"),
        "tc-raise": RuntimeError("boom"),
        "tc-ok": ToolExecutionResult(content="success"),
    }

    tool_registry = MagicMock()

    async def execute_tool(tool_call):
        r = call_results[tool_call.id]
        if isinstance(r, Exception):
            raise r
        return r  # ToolExecutionResult

    tool_registry.execute = AsyncMock(side_effect=execute_tool)
    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="tc-deny", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc-raise", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc-ok", name="slow_tool", arguments="{}"),
            ],
        ),
        LLMResponse(content="recovered", model="m", usage={"total_tokens": 20}),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("go")

    result = await adapter.run(context, _config(max_rounds=3), model)  # type: ignore[arg-type]
    assert result.content == "recovered"

    tool_msgs = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 3
    tool_ids = {m.tool_call_id for m in tool_msgs}
    assert tool_ids == {"tc-deny", "tc-raise", "tc-ok"}

    error_msgs = [m for m in tool_msgs if m.metadata.get("error")]
    assert len(error_msgs) == 2
    ok_msgs = [m for m in tool_msgs if not m.metadata.get("error")]
    assert len(ok_msgs) == 1
    assert ok_msgs[0].tool_call_id == "tc-ok"


@pytest.mark.asyncio
async def test_run_streaming_tool_progress_pair_adjacency_three_tools():
    """Property 3: streaming 事件配对相邻。"""
    adapter = _adapter_with_slow_tool(sleep_time=0.05)

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="tc1", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc2", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc3", name="slow_tool", arguments="{}"),
            ],
        ),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("go")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=2), model):  # type: ignore[arg-type]
        chunks.append(chunk)

    progress_chunks = [
        c for c in chunks if c.metadata and c.metadata.get("type") == "tool_progress"
    ]
    # 验证配对相邻：start/end 按 tool_call_id 分组连续
    i = 0
    while i < len(progress_chunks):
        start_chunk = progress_chunks[i]
        assert start_chunk.metadata["phase"] == "start"
        tc_id = start_chunk.metadata["tool_call_id"]
        i += 1
        assert i < len(progress_chunks)
        end_chunk = progress_chunks[i]
        assert end_chunk.metadata["phase"] == "end"
        assert end_chunk.metadata["tool_call_id"] == tc_id
        i += 1


@pytest.mark.asyncio
async def test_run_events_tool_start_result_pair_adjacency_three_tools():
    """Property 3: events 事件配对相邻。"""
    from domain.agent.value_objects import AgentStreamEvent

    adapter = _adapter_with_slow_tool(sleep_time=0.05)

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="tc1", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc2", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc3", name="slow_tool", arguments="{}"),
            ],
        ),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("go")

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, _config(max_rounds=2), model):  # type: ignore[arg-type]
        events.append(ev)

    tool_events = [e for e in events if e.kind in ("tool_start", "tool_result", "tool_error")]
    # 验证配对相邻
    i = 0
    while i < len(tool_events):
        start_ev = tool_events[i]
        assert start_ev.kind == "tool_start"
        tc_id = start_ev.tool_call_id
        i += 1
        assert i < len(tool_events)
        result_ev = tool_events[i]
        assert result_ev.kind in ("tool_result", "tool_error")
        assert result_ev.tool_call_id == tc_id
        i += 1


@pytest.mark.asyncio
async def test_concurrent_timeout_keeps_pair_semantics():
    """Property 7: 1 超时 + 2 正常，事件配对语义不变。"""
    from domain.agent.value_objects import AgentStreamEvent

    call_counts = {}

    async def tool_execute(tool_call):
        call_counts[tool_call.id] = True
        if tool_call.id == "tc-timeout":
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(0.01)
        return ToolExecutionResult(content=f"result-{tool_call.id}")

    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=tool_execute)
    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="tc-timeout", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc-ok1", name="slow_tool", arguments="{}"),
                ToolCallRequest(id="tc-ok2", name="slow_tool", arguments="{}"),
            ],
        ),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("go")

    config = AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[{"type": "function", "function": {"name": "slow_tool"}}],
        model="test-model",
        max_rounds=2,
        prompt_id="chat-default@v1",
        tool_timeout_seconds=0.5,
    )

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, config, model):  # type: ignore[arg-type]
        events.append(ev)

    tool_events = [e for e in events if e.kind in ("tool_start", "tool_result", "tool_error")]
    # 验证配对相邻
    i = 0
    while i < len(tool_events):
        start_ev = tool_events[i]
        assert start_ev.kind == "tool_start"
        tc_id = start_ev.tool_call_id
        i += 1
        assert i < len(tool_events)
        result_ev = tool_events[i]
        assert result_ev.kind in ("tool_result", "tool_error")
        assert result_ev.tool_call_id == tc_id
        i += 1

    # 超时工具应标记为 error
    timeout_results = [
        e for e in tool_events if e.tool_call_id == "tc-timeout" and e.kind == "tool_error"
    ]
    assert len(timeout_results) == 1


@pytest.mark.asyncio
async def test_concurrent_tools_dont_share_arguments_state():
    """R1.9: 每个 task 收到的 tool_call.arguments 引用唯一。"""
    received_args = []

    async def tool_execute(tool_call):
        received_args.append(tool_call.arguments)
        await asyncio.sleep(0.01)
        return ToolExecutionResult(content="ok")

    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=tool_execute)
    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )

    responses = [
        LLMResponse(
            content="",
            model="m",
            usage={"total_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="tc1", name="slow_tool", arguments='{"a": 1}'),
                ToolCallRequest(id="tc2", name="slow_tool", arguments='{"b": 2}'),
                ToolCallRequest(id="tc3", name="slow_tool", arguments='{"c": 3}'),
            ],
        ),
        LLMResponse(content="done", model="m", usage={"total_tokens": 20}),
    ]
    model = _FakeModel(responses)
    context = ConversationContext()
    context.add_user_message("go")

    await adapter.run(context, _config(max_rounds=3), model)  # type: ignore[arg-type]

    # 每个 tool_call 的 arguments 应该不同
    assert len(received_args) == 3
    assert len(set(received_args)) == 3
