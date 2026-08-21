"""ReactToolExecutionCoordinator 单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from domain.agent.value_objects import AgentConfig, AgentStreamEvent
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import StreamingChunk, ToolCallRequest
from infrastructure.agent.react_tool_execution_coordinator import (
    ReactToolExecutionCoordinator,
)


def _config() -> AgentConfig:
    """构造测试用 AgentConfig。"""

    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "lookup"}},
            {"type": "function", "function": {"name": "write_note"}},
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _tool_calls() -> tuple[ToolCallRequest, ...]:
    """构造同轮多工具调用。"""

    return (
        ToolCallRequest(id="call-a", name="lookup", arguments='{"q": "a"}'),
        ToolCallRequest(id="call-b", name="write_note", arguments='{"text": "b"}'),
        ToolCallRequest(id="call-c", name="lookup", arguments='{"q": "c"}'),
    )


class _FakeRuntime:
    """测试用工具执行运行时。"""

    def __init__(self) -> None:
        """初始化运行时执行记录。"""

        self.release = asyncio.Event()
        self.started: list[str] = []
        self.completed: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.error_call_ids: set[str] = set()
        self.raise_call_ids: set[str] = set()

    async def execute_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
    ) -> None:
        """记录并发执行状态，并把工具结果写入上下文。"""

        assert round_num >= 0
        self.started.append(tool_call.id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await self.release.wait()
        self.in_flight -= 1
        if tool_call.id in self.raise_call_ids:
            raise RuntimeError(f"boom-{tool_call.id}")

        metadata = {"error": True} if tool_call.id in self.error_call_ids else {}
        context.append_message(
            ToolMessage(
                content=f"result-{tool_call.id}",
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                metadata=metadata,
            )
        )
        self.completed.append(tool_call.id)

    def tool_progress_chunk(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
        phase: str,
    ) -> StreamingChunk:
        """构造测试用工具进度分片。"""

        return StreamingChunk(
            delta_content="",
            finished=False,
            metadata={
                "type": "tool_progress",
                "round": round_num,
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "phase": phase,
            },
        )

    def tool_start_event(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
    ) -> AgentStreamEvent:
        """构造测试用工具开始事件。"""

        return AgentStreamEvent(
            kind="tool_start",
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            metadata={"round": round_num},
        )

    def tool_result_event(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
        content: str,
    ) -> AgentStreamEvent:
        """构造测试用工具成功事件。"""

        return AgentStreamEvent(
            kind="tool_result",
            content=content,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            metadata={"round": round_num},
        )

    def tool_error_event(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
        content: str,
    ) -> AgentStreamEvent:
        """构造测试用工具失败事件。"""

        return AgentStreamEvent(
            kind="tool_error",
            content=content,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            metadata={"round": round_num},
        )


@pytest.mark.asyncio
async def test_dispatch_executes_same_round_tools_concurrently() -> None:
    """dispatch 并发调度同轮工具并返回执行数量。"""

    runtime = _FakeRuntime()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()

    task = asyncio.create_task(
        coordinator.dispatch(context=context, tool_calls=_tool_calls(), config=_config())
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if len(runtime.started) == 3:
            break

    assert set(runtime.started) == {"call-a", "call-b", "call-c"}
    assert runtime.max_in_flight == 3

    runtime.release.set()
    result = await task

    assert result.executed_count == 3
    assert runtime.completed == ["call-a", "call-b", "call-c"]


@pytest.mark.asyncio
async def test_stream_progress_emits_adjacent_start_end_pairs() -> None:
    """stream_progress 为每个工具输出相邻 start/end 分片。"""

    runtime = _FakeRuntime()
    runtime.release.set()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()

    chunks = [
        chunk
        async for chunk in coordinator.stream_progress(
            context=context,
            tool_calls=_tool_calls(),
            config=_config(),
            round_num=2,
        )
    ]

    progress = [chunk.metadata for chunk in chunks]
    assert [(item["tool_call_id"], item["phase"]) for item in progress] == [
        ("call-a", "start"),
        ("call-a", "end"),
        ("call-b", "start"),
        ("call-b", "end"),
        ("call-c", "start"),
        ("call-c", "end"),
    ]
    assert all(item["round"] == 2 for item in progress)


@pytest.mark.asyncio
async def test_stream_progress_single_tool_yields_start_before_execution() -> None:
    """单工具 stream_progress 保持先 start、后执行、再 end 的既有时序。"""

    runtime = _FakeRuntime()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()
    tool_call = _tool_calls()[0]
    stream = coordinator.stream_progress(
        context=context,
        tool_calls=(tool_call,),
        config=_config(),
        round_num=2,
    )

    first = await anext(stream)
    assert first.metadata["phase"] == "start"
    assert runtime.started == []

    second_task = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    assert runtime.started == ["call-a"]

    runtime.release.set()
    second = await second_task
    assert second.metadata["phase"] == "end"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_stream_events_emits_adjacent_start_result_and_error_pairs() -> None:
    """stream_events 为成功和失败工具输出相邻事件对。"""

    runtime = _FakeRuntime()
    runtime.error_call_ids.add("call-b")
    runtime.release.set()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()

    events = [
        event
        async for event in coordinator.stream_events(
            context=context,
            tool_calls=_tool_calls(),
            config=_config(),
            round_num=4,
        )
    ]

    assert [(event.tool_call_id, event.kind) for event in events] == [
        ("call-a", "tool_start"),
        ("call-a", "tool_result"),
        ("call-b", "tool_start"),
        ("call-b", "tool_error"),
        ("call-c", "tool_start"),
        ("call-c", "tool_result"),
    ]
    assert events[1].content == "result-call-a"
    assert events[3].content == "result-call-b"
    assert all(event.metadata == {"round": 4} for event in events)


@pytest.mark.asyncio
async def test_stream_events_single_tool_yields_start_before_execution() -> None:
    """单工具 stream_events 保持先 tool_start、后执行、再 result 的既有时序。"""

    runtime = _FakeRuntime()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()
    tool_call = _tool_calls()[0]
    stream = coordinator.stream_events(
        context=context,
        tool_calls=(tool_call,),
        config=_config(),
        round_num=5,
    )

    first = await anext(stream)
    assert first.kind == "tool_start"
    assert runtime.started == []

    second_task = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    assert runtime.started == ["call-a"]

    runtime.release.set()
    second = await second_task
    assert second.kind == "tool_result"
    assert second.content == "result-call-a"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_stream_events_propagates_runtime_exception() -> None:
    """runtime 抛出的基础设施异常应向上传播，不伪装成工具失败事件。"""

    runtime = _FakeRuntime()
    runtime.raise_call_ids.add("call-b")
    runtime.release.set()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()

    with pytest.raises(RuntimeError, match="boom-call-b"):
        _ = [
            event
            async for event in coordinator.stream_events(
                context=context,
                tool_calls=_tool_calls(),
                config=_config(),
                round_num=5,
            )
        ]


@pytest.mark.asyncio
async def test_stream_progress_propagates_runtime_exception() -> None:
    """stream_progress 也不吞掉 runtime 抛出的基础设施异常。"""

    runtime = _FakeRuntime()
    runtime.raise_call_ids.add("call-b")
    runtime.release.set()
    coordinator = ReactToolExecutionCoordinator(runtime)
    context = ConversationContext()

    with pytest.raises(RuntimeError, match="boom-call-b"):
        _ = [
            chunk
            async for chunk in coordinator.stream_progress(
                context=context,
                tool_calls=_tool_calls(),
                config=_config(),
                round_num=5,
            )
        ]
