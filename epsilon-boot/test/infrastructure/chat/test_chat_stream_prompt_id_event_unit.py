"""SSE 流式路径中 ``prompt_id`` 事件位置测试。

# Validates: Property 8 / Requirement 4.6, 7.3

构造 fake ``ChatServicePort``：``stream_chat`` 产生 N 个 ``StreamingChunk``，
``prompt_id`` 属性返回 ``"chat-default@v3"``。通过 FastAPI ``TestClient``
发起 ``POST /api/chat`` 流式请求，读取 SSE 原始文本，断言事件序列：

    最后一个 content chunk → ``data: {"prompt_id": "chat-default@v3"}`` →
    ``data: [DONE]``

且 ``[DONE]`` 之前**有且仅有**一条 ``prompt_id`` 事件（决策 #2）。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest

from domain.model_access.value_objects import StreamingChunk

# mock prometheus_client，避免 chat 路由模块加载链触发的副作用
if "prometheus_client" not in sys.modules:
    _mock_prom = MagicMock()
    _mock_prom.CONTENT_TYPE_LATEST = "text/plain"
    _mock_prom.generate_latest = MagicMock(return_value=b"")
    sys.modules["prometheus_client"] = _mock_prom


def _load_chat_module():
    """绕过 application 包初始化，直接加载 chat 路由模块。"""
    chat_path = (
        pathlib.Path(__file__).resolve().parents[3] / "src" / "application" / "routers" / "chat.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_chat_prompt_id_event_module", str(chat_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeChatService:
    """模拟 ``ChatServicePort``：仅实现路由测试所需的最小接口。

    - ``stream_chat`` 异步迭代器产出预定义的 chunks
    - ``prompt_id`` 属性返回固定值
    - 其余接口路由层不会调用，留空即可
    """

    def __init__(self, chunks: list[StreamingChunk], prompt_id: str) -> None:
        self._chunks = chunks
        self._prompt_id = prompt_id

    async def chat(self, request):  # pragma: no cover - 流式路径不会调用
        raise AssertionError("同步路径不应被调用")

    def stream_chat(self, request) -> AsyncIterator[StreamingChunk]:
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()

    @property
    def prompt_id(self) -> str:
        return self._prompt_id

    async def clear_session(self, session_id: str) -> None:  # pragma: no cover
        return None


async def _read_sse_events(response) -> list[str]:
    """从 ``EventSourceResponse`` 中按出现顺序提取 ``data`` 事件。"""
    events: list[str] = []
    async for item in response.body_iterator:
        if isinstance(item, dict) and "data" in item:
            events.append(str(item["data"]))
            continue
        text = item.decode("utf-8") if isinstance(item, bytes) else str(item)
        for line in text.splitlines():
            if line.startswith("data:"):
                events.append(line[len("data:") :].strip())
    return events


@pytest.mark.asyncio
async def test_sse_prompt_id_event_appears_immediately_before_done() -> None:
    """断言 ``prompt_id`` 事件位于最后一个 chunk 之后、``[DONE]`` 之前。"""
    chat_module = _load_chat_module()

    chunks = [
        StreamingChunk(delta_content="你好", finished=False, usage=None),
        StreamingChunk(delta_content="，", finished=False, usage=None),
        StreamingChunk(
            delta_content="世界",
            finished=True,
            usage={"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
        ),
    ]
    fake_service = _FakeChatService(chunks=chunks, prompt_id="chat-default@v3")

    response = await chat_module.chat(
        chat_module.ChatRequestBody(
            session_id="s-stream",
            message="hi",
            stream=True,
        ),
        service=fake_service,
    )

    assert response.status_code == 200
    events = await _read_sse_events(response)

    # 必须包含 [DONE] 事件
    assert "[DONE]" in events
    done_index = events.index("[DONE]")

    # [DONE] 紧邻前一条事件应为 prompt_id 事件
    assert done_index >= 1
    prompt_id_event_text = events[done_index - 1]
    parsed = json.loads(prompt_id_event_text)
    assert parsed == {"prompt_id": "chat-default@v3"}

    # 全流程中 prompt_id 事件**有且仅有**一条
    prompt_id_count = 0
    for ev in events:
        try:
            payload = json.loads(ev)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "prompt_id" in payload and len(payload) == 1:
            prompt_id_count += 1
    assert prompt_id_count == 1

    # prompt_id 事件之前的最后一个 content chunk 必须 finished=True
    last_content_event_text = events[done_index - 2]
    last_content_payload = json.loads(last_content_event_text)
    assert last_content_payload["finished"] is True
