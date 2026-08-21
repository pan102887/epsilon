"""聊天继续路由单元测试。"""

import importlib.util
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentStreamEvent
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.value_objects import ChatContinueRequestVO, ChatResponseVO


def _load_chat_module() -> Any:
    """直接加载 chat 路由模块。"""
    chat_path = pathlib.Path(__file__).resolve().parents[3] / "src/application/routers/chat.py"
    spec = importlib.util.spec_from_file_location(
        "test_chat_continue_router_module", str(chat_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SseResponse(Protocol):
    body_iterator: AsyncIterator[dict[str, object] | bytes | str]


async def _read_sse_events(response: _SseResponse) -> list[str]:
    """从 EventSourceResponse 提取 data 事件。"""
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
async def test_continue_chat_json_returns_continuation_fields() -> None:
    """验证聊天继续 JSON 响应透传终止原因与可继续标记。"""
    module = _load_chat_module()
    service = MagicMock()
    service.continue_chat = AsyncMock(
        return_value=ChatResponseVO(
            session_id="s1",
            reply="",
            model="test-model",
            usage={},
            prompt_id="chat-default@v1",
            status="paused",
            terminated_reason="max_rounds",
            can_continue=True,
        )
    )

    response = await module.continue_chat(
        "s1",
        module.ChatContinueRequestBody(stream=False),
        service=service,
    )

    body = response.model_dump()
    assert body["status"] == "paused"
    assert body["terminated_reason"] == "max_rounds"
    assert body["can_continue"] is True


@pytest.mark.asyncio
async def test_continue_chat_stream_paused_done_payload() -> None:
    """验证聊天继续 SSE paused final payload。"""
    module = _load_chat_module()

    class FakeService:
        """fake chat service。"""

        @property
        def prompt_id(self) -> str:
            return "chat-default@v1"

        def stream_continue_chat_events(
            self, _request: ChatContinueRequestVO
        ) -> AsyncIterator[AgentStreamEvent]:
            async def gen() -> AsyncIterator[AgentStreamEvent]:
                yield AgentStreamEvent(
                    kind="assistant_done",
                    metadata={
                        "status": "paused",
                        "terminated_reason": "max_rounds",
                        "can_continue": True,
                    },
                )

            return gen()

    response = await module.continue_chat(
        "s1",
        module.ChatContinueRequestBody(stream=True),
        service=FakeService(),
    )

    events = await _read_sse_events(response)
    payload = cast(dict[str, object], json.loads(events[0]))
    assert payload["finished"] is True
    assert payload["status"] == "paused"
    assert payload["terminated_reason"] == "max_rounds"
    assert payload["can_continue"] is True


@pytest.mark.asyncio
async def test_continue_chat_unavailable_returns_409() -> None:
    """验证继续不可用映射为 HTTP 409。"""
    module = _load_chat_module()
    service = MagicMock()
    service.continue_chat = AsyncMock(
        side_effect=ContinuationUnavailableError("s1", "最新消息不是工具结果")
    )

    response = await module.continue_chat(
        "s1",
        module.ChatContinueRequestBody(),
        service=service,
    )

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == 60041


@pytest.mark.asyncio
async def test_continue_chat_stream_unavailable_returns_409_before_sse() -> None:
    """验证流式继续前置失败在创建 SSE 前映射为 HTTP 409。"""
    module = _load_chat_module()
    service = MagicMock()

    def stream_continue_chat_events(
        _request: ChatContinueRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        async def gen() -> AsyncIterator[AgentStreamEvent]:
            raise ContinuationUnavailableError("s1", "最新消息不是工具结果")
            yield AgentStreamEvent(kind="assistant_done")

        return gen()

    service.stream_continue_chat_events = stream_continue_chat_events

    response = await module.continue_chat(
        "s1",
        module.ChatContinueRequestBody(stream=True),
        service=service,
    )

    assert response.status_code == 409
    body = json.loads(response.body)
    assert body["code"] == 60041
    assert "最新消息不是工具结果" in body["message"]
