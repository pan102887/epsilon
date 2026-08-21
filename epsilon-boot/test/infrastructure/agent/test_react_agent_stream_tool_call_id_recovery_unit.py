"""ReActAgentAdapter 消费流式工具调用 id 恢复结果的集成测试。

本测试把 ``OpenAICompatibleAdapter`` 作为 ``ModelAccessPort`` 使用，并 mock
其底层 SDK stream。这样可以覆盖真实链路：Provider 流式分片缺失 id →
模型适配器生成合成 id → ReAct Agent 累积为 ``ToolCallRequest`` → 工具执行
与 ``ToolMessage.tool_call_id`` 使用同一个 id。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import AssistantMessage, BaseMessage, ConversationContext, ToolMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import StreamingChunk, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


class _FakeContextBuilder:
    """测试用上下文构建器，直接透传当前消息列表。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del model_access, model
        return ContextBuilderResult(messages=messages, usage={})


class _RecordingToolRegistry:
    """记录执行入参的工具注册表 fake。"""

    def __init__(self) -> None:
        self.executed: list[ToolCallRequest] = []

    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        """记录工具调用并返回固定结果。"""
        self.executed.append(request)
        return ToolExecutionResult(content="tool result")


def _agent_config(max_rounds: int = 3) -> AgentConfig:
    """构造允许调用 ``search`` 的 AgentConfig。"""
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "search", "parameters": {}},
            }
        ],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _make_model_access() -> OpenAICompatibleAdapter:
    """构造启用 recover 策略的 OpenAI 兼容模型适配器。"""
    cfg = MagicMock()
    cfg.api_key = "k"
    cfg.api_base = "https://fake/v1"
    cfg.timeout = 30
    cfg.max_retries = 0
    cfg.max_connections = 10
    cfg.max_keepalive_connections = 5
    cfg.provider_name = "test-provider"
    cfg.default_model = "test-model"
    cfg.temperature = 0.7
    cfg.max_tokens = 4096
    cfg.stream_tool_call_id_strategy = "recover"
    return OpenAICompatibleAdapter(cfg)


def _sdk_chunk(
    *,
    content: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    """构造模拟 OpenAI SDK choices 分片。"""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _tool_delta(
    *,
    index: int,
    id: str | None,
    name: str | None,
    arguments: str | None,
) -> SimpleNamespace:
    """构造模拟 SDK ``delta.tool_calls[i]``。"""
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _MockAsyncStream:
    """模拟 SDK 异步 stream。"""

    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        for chunk in self._chunks:
            yield chunk


def _tool_call_stream() -> _MockAsyncStream:
    """第一轮：缺失 id 的流式工具调用。"""
    return _MockAsyncStream(
        [
            _sdk_chunk(tool_calls=[_tool_delta(index=0, id="", name="search", arguments='{"q":')]),
            _sdk_chunk(tool_calls=[_tool_delta(index=0, id=None, name=None, arguments='"x"}')]),
            _sdk_chunk(finish_reason="tool_calls"),
        ]
    )


def _text_stream() -> _MockAsyncStream:
    """第二轮：模型给出最终文本。"""
    return _MockAsyncStream(
        [
            _sdk_chunk(content="done", finish_reason="stop"),
        ]
    )


@pytest.mark.asyncio
async def test_run_keeps_recovered_tool_call_id_consistent() -> None:
    """run(...) 中 assistant/tool/tool registry 三处使用同一个合成 id。"""
    model_access = _make_model_access()
    model_access.client.chat.completions.create = AsyncMock(
        side_effect=[_tool_call_stream(), _text_stream()]
    )
    registry = _RecordingToolRegistry()
    adapter = ReActAgentAdapter(
        tool_registry=registry,  # type: ignore[arg-type]
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )
    context = ConversationContext()
    context.add_user_message("搜索 x")

    result = await adapter.run(context, _agent_config(max_rounds=3), model_access)

    assert result.content == "done"
    assert len(registry.executed) == 1
    executed_id = registry.executed[0].id
    assert executed_id.startswith("call_synthetic_")

    assistant_messages = [
        msg
        for msg in context.get_messages()
        if isinstance(msg, AssistantMessage) and msg.tool_calls
    ]
    tool_messages = [msg for msg in context.get_messages() if isinstance(msg, ToolMessage)]
    assert assistant_messages
    assert tool_messages
    assert assistant_messages[0].tool_calls[0].id == executed_id
    assert tool_messages[0].tool_call_id == executed_id


@pytest.mark.asyncio
async def test_run_streaming_tool_progress_uses_recovered_id() -> None:
    """run_streaming(...) 的工具进度 metadata 使用恢复后的合成 id。"""
    model_access = _make_model_access()
    model_access.client.chat.completions.create = AsyncMock(
        side_effect=[_tool_call_stream(), _text_stream()]
    )
    registry = _RecordingToolRegistry()
    adapter = ReActAgentAdapter(
        tool_registry=registry,  # type: ignore[arg-type]
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )
    context = ConversationContext()
    context.add_user_message("搜索 x")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(
        context,
        _agent_config(max_rounds=2),
        model_access,
    ):
        chunks.append(chunk)

    progress_chunks = [chunk for chunk in chunks if chunk.metadata.get("type") == "tool_progress"]
    assert progress_chunks
    ids = {chunk.metadata["tool_call_id"] for chunk in progress_chunks}
    assert len(ids) == 1
    only_id = next(iter(ids))
    assert only_id.startswith("call_synthetic_")
    assert registry.executed[0].id == only_id


@pytest.mark.asyncio
async def test_run_streaming_final_round_propagates_recovery_metadata() -> None:
    """max_rounds=1 直接流式最终轮时透传适配器恢复 metadata。"""
    model_access = _make_model_access()
    model_access.client.chat.completions.create = AsyncMock(return_value=_tool_call_stream())
    registry = _RecordingToolRegistry()
    adapter = ReActAgentAdapter(
        tool_registry=registry,  # type: ignore[arg-type]
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )
    context = ConversationContext()
    context.add_user_message("搜索 x")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(
        context,
        _agent_config(max_rounds=1),
        model_access,
    ):
        chunks.append(chunk)

    final = chunks[-1]
    assert final.finished is True
    assert final.metadata == {
        "tool_call_id_recovered": True,
        "synthetic_tool_call_count": 1,
    }
