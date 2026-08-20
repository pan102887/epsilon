"""桩 ``ScriptedModelAccess`` 自测。

覆盖点：
- 按序返回预设脚本；
- 脚本耗尽后返回一个空 ``content`` 的 :class:`LLMResponse`（兜底，
  避免评测样本在异常路径下死锁）；
- :meth:`stream` 产出与 ``chat`` 等价的 StreamingChunk 序列（v3 适配）。

对应 ``docs/spec/spec-ai-evaluation/design.md`` 中的设计决策
"ScriptedModelAccess 是否需要支持 stream — v3 要求全程 stream"。
"""

from __future__ import annotations

import asyncio

import pytest

from domain.model_access.value_objects import ChatRequest, LLMResponse, ToolCallRequest
from tests.evaluation.stubs.model_access import ScriptedModelAccess


def _make_request() -> ChatRequest:
    """构造一个最小合法 :class:`ChatRequest`，供 chat 调用使用。"""

    return ChatRequest(messages=[{"role": "user", "content": "hi"}])


def test_chat_returns_scripted_responses_in_order() -> None:
    """按 FIFO 顺序返回脚本内容，``calls`` 累加。"""

    r1 = LLMResponse(content="a", model="m1")
    r2 = LLMResponse(
        content="",
        model="m2",
        tool_calls=[ToolCallRequest(id="1", name="echo", arguments="{}")],
    )
    stub = ScriptedModelAccess(scripted_responses=[r1, r2])

    req = _make_request()

    got1 = asyncio.run(stub.chat(req))
    got2 = asyncio.run(stub.chat(req))

    assert got1 is r1
    assert got2 is r2
    assert stub.calls == 2
    assert stub.scripted_responses == []


def test_chat_returns_empty_response_when_script_exhausted() -> None:
    """脚本耗尽后返回空 content 的兜底响应，便于样本优雅收尾。"""

    stub = ScriptedModelAccess(scripted_responses=[])

    resp = asyncio.run(stub.chat(_make_request()))

    assert isinstance(resp, LLMResponse)
    assert resp.content == ""
    assert resp.tool_calls == []
    # 默认兜底模型名应可识别，便于在聚合结果中过滤。
    assert resp.model == "scripted-exhausted"
    assert stub.calls == 1


def test_stream_yields_equivalent_chunk() -> None:
    """stream() 产出与脚本响应等价的 finished=True 分片。"""

    r1 = LLMResponse(
        content="hello",
        model="m",
        tool_calls=[ToolCallRequest(id="t1", name="echo", arguments='{"a":1}')],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    )
    stub = ScriptedModelAccess(scripted_responses=[r1])

    async def _collect():
        chunks = []
        async for chunk in stub.stream(_make_request()):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_collect())

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.finished is True
    assert chunk.delta_content == "hello"
    assert chunk.usage == r1.usage
    assert chunk.tool_calls is not None
    assert len(chunk.tool_calls) == 1
    assert chunk.tool_calls[0].name == "echo"
    assert chunk.tool_calls[0].arguments_delta == '{"a":1}'
    assert stub.calls == 1
    assert stub.scripted_responses == []
