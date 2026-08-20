"""OpenAICompatibleAdapter 流式工具调用 id 恢复单元测试。

覆盖 Provider 在 OpenAI-compatible 流式 ``tool_calls`` 分片中缺失 ``id`` 时，
适配器按 ``recover`` 策略生成本地合成 id、写入 finished chunk metadata，
并输出不含敏感正文的结构化 WARN 日志。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import UserMessage
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter(strategy: str = "recover") -> OpenAICompatibleAdapter:
    """构造测试用 OpenAI 兼容适配器。"""
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


def _sdk_chunk(
    *,
    tool_calls: list[SimpleNamespace] | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    """构造模拟 OpenAI SDK 流式 choices 分片。"""
    delta = SimpleNamespace(content=None, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _usage_chunk() -> SimpleNamespace:
    """构造仅含 usage 的末尾分片。"""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


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
    """模拟 ``AsyncOpenAI.chat.completions.create(stream=True)`` 返回的异步流。"""

    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        for chunk in self._chunks:
            yield chunk


async def _consume(adapter: OpenAICompatibleAdapter) -> list:
    """消费 adapter.stream(...) 并返回所有 StreamingChunk。"""
    return [
        chunk async for chunk in adapter.stream(ChatRequest(messages=[UserMessage(content="x")]))
    ]


@pytest.mark.asyncio
async def test_finished_missing_none_id_recovers_with_metadata_and_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """finished 分支中 ``id=None`` 时生成合成 id、metadata 和 WARN 日志。"""
    adapter = _make_adapter("recover")
    chunks = [
        _sdk_chunk(
            tool_calls=[_tool_delta(index=0, id=None, name="web_search", arguments='{"q":')]
        ),
        _sdk_chunk(tool_calls=[_tool_delta(index=0, id=None, name=None, arguments='"hi"}')]),
        _sdk_chunk(finish_reason="tool_calls"),
    ]
    adapter._client.chat.completions.create = AsyncMock(return_value=_MockAsyncStream(chunks))
    caplog.set_level(
        logging.WARNING, logger="infrastructure.model_access.openai_compatible_adapter"
    )

    out = await _consume(adapter)
    final = out[-1]

    assert final.finished is True
    assert final.tool_calls is not None
    recovered_id = final.tool_calls[0].id
    assert recovered_id is not None
    assert recovered_id.startswith("call_synthetic_")
    assert final.tool_calls[0].name == "web_search"
    assert final.tool_calls[0].arguments_delta == '{"q":"hi"}'
    assert final.metadata == {
        "tool_call_id_recovered": True,
        "synthetic_tool_call_count": 1,
    }

    record = next(r for r in caplog.records if "已生成本地合成 id" in r.getMessage())
    assert record.source == "stream_finished"
    assert record.provider == "test-provider"
    assert record.model == "test-model"
    assert record.tool_name == "web_search"
    assert record.tool_call_index == 0
    assert record.raw_id_value is None
    assert record.synthetic_id == recovered_id
    assert record.recovery_strategy == "recover"
    assert '{"q"' not in record.getMessage()


@pytest.mark.asyncio
async def test_finished_empty_string_id_recovers() -> None:
    """finished 分支中 ``id=''`` 时同样恢复为合成 id。"""
    adapter = _make_adapter("recover")
    chunks = [
        _sdk_chunk(tool_calls=[_tool_delta(index=0, id="", name="calculator", arguments="{}")]),
        _sdk_chunk(finish_reason="tool_calls"),
    ]
    adapter._client.chat.completions.create = AsyncMock(return_value=_MockAsyncStream(chunks))

    out = await _consume(adapter)
    final = out[-1]

    assert final.tool_calls is not None
    assert final.tool_calls[0].id is not None
    assert final.tool_calls[0].id.startswith("call_synthetic_")
    assert final.metadata["synthetic_tool_call_count"] == 1


@pytest.mark.asyncio
async def test_usage_only_reuses_same_recovered_id() -> None:
    """usage-only 末尾分片复用 finished 分片已写回的同一个合成 id。"""
    adapter = _make_adapter("recover")
    chunks = [
        _sdk_chunk(tool_calls=[_tool_delta(index=0, id="", name="calculator", arguments="{}")]),
        _sdk_chunk(finish_reason="tool_calls"),
        _usage_chunk(),
    ]
    adapter._client.chat.completions.create = AsyncMock(return_value=_MockAsyncStream(chunks))

    out = await _consume(adapter)
    finished = out[1]
    usage_only = out[2]

    assert finished.tool_calls is not None
    assert usage_only.tool_calls is not None
    assert finished.tool_calls[0].id == usage_only.tool_calls[0].id
    assert finished.metadata["tool_call_id_recovered"] is True
    assert usage_only.metadata == {}
