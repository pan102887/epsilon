"""``OpenAICompatibleAdapter.stream`` 流式工具调用 id 策略单元测试。

覆盖场景：
- 默认 recover：id 为空时生成本地合成 id
- raise 策略：id 为空时抛 InvalidToolCallIdError
- 正常 id 不抛（回归保护）
- 异常携带完整诊断字段集
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import UserMessage
from domain.model_access.exceptions import InvalidToolCallIdError
from domain.model_access.value_objects import ChatRequest, StreamingChunk
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter(strategy: str = "recover") -> OpenAICompatibleAdapter:
    """构造测试用 adapter。"""
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
    cfg.stream_tool_call_id_strategy = strategy
    return OpenAICompatibleAdapter(cfg)


class _StreamWithEmptyIdFinished:
    """模拟流：首分片携带 id='' 的 tool_call，末分片 finished=True。"""

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        # 首分片：tool_call delta 带空 id
        tc = SimpleNamespace(
            index=0, id="", function=SimpleNamespace(name="web_search", arguments='{"q":')
        )
        delta = SimpleNamespace(content=None, tool_calls=[tc])
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        yield SimpleNamespace(choices=[choice], usage=None)

        # 第二分片：arguments 增量
        tc2 = SimpleNamespace(
            index=0, id=None, function=SimpleNamespace(name=None, arguments='"hi"}')
        )
        delta2 = SimpleNamespace(content=None, tool_calls=[tc2])
        choice2 = SimpleNamespace(delta=delta2, finish_reason=None)
        yield SimpleNamespace(choices=[choice2], usage=None)

        # 末分片：finished=True
        delta3 = SimpleNamespace(content=None, tool_calls=None)
        choice3 = SimpleNamespace(delta=delta3, finish_reason="tool_calls")
        yield SimpleNamespace(choices=[choice3], usage=None)


class _StreamWithEmptyIdUsageOnly:
    """模拟流：tool_call id=''，末尾仅含 usage（无 choices）。"""

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        # 首分片：tool_call delta 带空 id
        tc = SimpleNamespace(
            index=0, id="", function=SimpleNamespace(name="calculator", arguments="{}")
        )
        delta = SimpleNamespace(content=None, tool_calls=[tc])
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        yield SimpleNamespace(choices=[choice], usage=None)

        # finished 分片（无 tool_calls delta）
        delta2 = SimpleNamespace(content=None, tool_calls=None)
        choice2 = SimpleNamespace(delta=delta2, finish_reason="tool_calls")
        yield SimpleNamespace(choices=[choice2], usage=None)

        # usage-only 分片（choices 为空）
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        yield SimpleNamespace(choices=[], usage=usage)


class _StreamWithValidId:
    """模拟流：合法 id，正常完成。"""

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        tc = SimpleNamespace(
            index=0,
            id="call_abc123",
            function=SimpleNamespace(name="web_search", arguments='{"q":"hi"}'),
        )
        delta = SimpleNamespace(content=None, tool_calls=[tc])
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        yield SimpleNamespace(choices=[choice], usage=None)

        delta2 = SimpleNamespace(content=None, tool_calls=None)
        choice2 = SimpleNamespace(delta=delta2, finish_reason="tool_calls")
        yield SimpleNamespace(choices=[choice2], usage=None)


# ---------------------------------------------------------------------------
# 测试：默认 recover 分支 id 为空 → 合成 id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_finished_empty_id_recovers_by_default() -> None:
    """finished 分支中 tool_call.id 为空时默认生成合成 id。"""
    adapter = _make_adapter("recover")
    adapter.client.chat.completions.create = AsyncMock(return_value=_StreamWithEmptyIdFinished())

    request = ChatRequest(messages=[UserMessage(content="x")])
    chunks = [chunk async for chunk in adapter.stream(request)]

    finished_chunk = chunks[-1]
    assert finished_chunk.finished is True
    assert finished_chunk.tool_calls is not None
    recovered_id = finished_chunk.tool_calls[0].id
    assert recovered_id is not None
    assert recovered_id.startswith("call_synthetic_")
    assert finished_chunk.metadata["tool_call_id_recovered"] is True


# ---------------------------------------------------------------------------
# 测试：raise 策略 id 为空 → InvalidToolCallIdError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_empty_id_raises_invalid_tool_call_id_when_strategy_raise() -> None:
    """strict raise 策略下 tool_call.id 为空时抛出 InvalidToolCallIdError。"""
    adapter = _make_adapter("raise")
    adapter.client.chat.completions.create = AsyncMock(return_value=_StreamWithEmptyIdFinished())

    request = ChatRequest(messages=[UserMessage(content="x")])
    with pytest.raises(InvalidToolCallIdError) as exc_info:
        async for _ in adapter.stream(request):
            pass

    exc = exc_info.value
    assert exc.details["source"] == "stream_finished"
    assert exc.details["provider"] == "test-provider"
    assert exc.details["model"] == "test-model"
    assert exc.details["tool_name"] == "web_search"
    assert exc.details["tool_call_index"] == 0
    assert exc.details["raw_id_value"] is None


@pytest.mark.asyncio
async def test_stream_usage_only_empty_id_reuses_recovered_id() -> None:
    """usage-only 末尾分片使用同一套恢复结果，不生成第二个不同 id。"""
    adapter = _make_adapter("recover")
    adapter.client.chat.completions.create = AsyncMock(return_value=_StreamWithEmptyIdUsageOnly())

    request = ChatRequest(messages=[UserMessage(content="x")])
    chunks = [chunk async for chunk in adapter.stream(request)]

    finished_chunk = chunks[1]
    usage_chunk = chunks[2]
    assert finished_chunk.tool_calls is not None
    assert usage_chunk.tool_calls is not None
    assert finished_chunk.tool_calls[0].id == usage_chunk.tool_calls[0].id
    recovered_id = finished_chunk.tool_calls[0].id
    assert recovered_id is not None
    assert recovered_id.startswith("call_synthetic_")


# ---------------------------------------------------------------------------
# 测试：合法 id 不抛异常（回归保护）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_valid_id_does_not_raise() -> None:
    """合法 id 时正常 yield chunks，不抛异常。"""
    adapter = _make_adapter("recover")
    adapter.client.chat.completions.create = AsyncMock(return_value=_StreamWithValidId())

    request = ChatRequest(messages=[UserMessage(content="x")])
    chunks: list[StreamingChunk] = []
    async for chunk in adapter.stream(request):
        chunks.append(chunk)

    # 应有 2 个 chunk（一个增量 + 一个 finished）
    assert len(chunks) == 2
    finished_chunk = chunks[-1]
    assert finished_chunk.finished is True
    assert finished_chunk.tool_calls is not None
    assert finished_chunk.tool_calls[0].id == "call_abc123"
