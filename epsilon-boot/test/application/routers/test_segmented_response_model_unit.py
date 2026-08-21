"""HTTP 分段响应模型测试。"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any, Protocol
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentBudgetUsage, SegmentRunMetadata
from domain.agent.value_objects import AgentStreamEvent
from domain.chat.value_objects import ChatRequestVO, ChatResponseVO
from domain.task.value_objects import TaskResult, TaskStatus

_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load_module(relative: str, name: str) -> Any:
    path = _ROOT / "src" / relative
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_chat_sync_response_includes_segment_fields() -> None:
    """同步 Chat 响应透传分段元数据。"""
    module = _load_module("application/routers/chat.py", "segmented_chat_router_module")
    service = MagicMock()
    service.chat = AsyncMock(
        return_value=ChatResponseVO(
            session_id="s1",
            reply="done",
            model="test-model",
            usage={"total_tokens": 8},
            prompt_id="chat-default@v1",
            segment_metadata=SegmentRunMetadata(
                segment_index=2,
                segment_count=2,
                auto_continue_attempted=True,
                segment_stop_reason="completed",
                budget_usage=SegmentBudgetUsage(
                    segment_count=2, continuation_count=1, total_tokens=8
                ),
            ),
        )
    )

    response = await module.chat(
        module.ChatRequestBody(session_id="s1", message="hi"),
        service=service,
    )

    body = response.model_dump()
    assert body["segment_index"] == 2
    assert body["segment_count"] == 2
    assert body["auto_continue_attempted"] is True
    assert body["segment_stop_reason"] == "completed"
    assert body["budget_usage"]["continuation_count"] == 1
    assert body["budget_usage"]["total_tokens"] == 8


@pytest.mark.asyncio
async def test_task_execute_response_includes_segment_fields() -> None:
    """Task execute 响应透传分段元数据。"""
    module = _load_module("application/routers/task.py", "segmented_task_router_module")
    module.TaskExecuteResponseBody.model_rebuild()
    service = MagicMock()
    service.execute = AsyncMock(
        return_value=TaskResult(
            content="done",
            status=TaskStatus.SUCCESS,
            model="test-model",
            prompt_id="task-template@v1",
            usage={"total_tokens": 6},
            segment_metadata=SegmentRunMetadata(
                segment_index=2,
                segment_count=2,
                auto_continue_attempted=True,
                segment_stop_reason="completed",
                budget_usage=SegmentBudgetUsage(
                    segment_count=2,
                    continuation_count=1,
                    total_tokens=6,
                    elapsed_ms=12.5,
                    consecutive_paused_count=1,
                    no_progress_count=2,
                    repeated_tool_call_count=3,
                ),
            ),
        )
    )

    response = await module.execute_task(
        module.TaskExecuteRequestBody(goal="do it"),
        service=service,
    )

    body = response.model_dump()
    assert body["segment_index"] == 2
    assert body["segment_count"] == 2
    assert body["auto_continue_attempted"] is True
    assert body["segment_stop_reason"] == "completed"
    assert body["budget_usage"] == {
        "segment_count": 2,
        "continuation_count": 1,
        "total_tokens": 6,
        "elapsed_ms": 12.5,
        "consecutive_paused_count": 1,
        "no_progress_count": 2,
        "repeated_tool_call_count": 3,
    }


class _SseResponse(Protocol):
    body_iterator: AsyncIterator[dict[str, object] | bytes | str]


async def _read_sse_data(response: _SseResponse) -> list[str]:
    """读取 EventSourceResponse 的 data payload。"""
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
async def test_chat_stream_uses_segmented_events_and_emits_segment_done_payload() -> None:
    """Chat SSE 使用分段流并输出 segment_done 控制 payload。"""
    module = _load_module("application/routers/chat.py", "segmented_chat_stream_router_module")

    class FakeService:
        @property
        def prompt_id(self) -> str:
            return "chat-default@v1"

        def stream_segmented_chat_events(
            self, _request: ChatRequestVO
        ) -> AsyncIterator[AgentStreamEvent]:
            async def gen() -> AsyncIterator[AgentStreamEvent]:
                yield AgentStreamEvent(kind="assistant_delta", content="hi")
                yield AgentStreamEvent(
                    kind="assistant_done",
                    metadata={
                        "segment_index": 2,
                        "segment_count": 2,
                        "auto_continue_attempted": True,
                        "segment_stop_reason": "completed",
                        "budget_usage": {
                            "segment_count": 2,
                            "continuation_count": 1,
                            "total_tokens": 8,
                        },
                    },
                )
                yield AgentStreamEvent(
                    kind="assistant_done",
                    metadata={
                        "event_type": "segment_done",
                        "finished": False,
                        "segment_index": 2,
                        "segment_count": 2,
                        "auto_continue_attempted": True,
                        "segment_stop_reason": "completed",
                        "budget_usage": {
                            "segment_count": 2,
                            "continuation_count": 1,
                            "total_tokens": 8,
                        },
                    },
                )

            return gen()

    response = await module.chat(
        module.ChatRequestBody(session_id="s1", message="hi", stream=True),
        service=FakeService(),
    )

    raw_events = await _read_sse_data(response)
    events = [json.loads(item) for item in raw_events if item != "[DONE]"]
    segment_payload = next(item for item in events if item.get("event_type") == "segment_done")
    final_payload = next(item for item in events if item.get("finished") is True)
    assert segment_payload["finished"] is False
    assert segment_payload["segment_count"] == 2
    assert final_payload["segment_count"] == 2
    assert final_payload["auto_continue_attempted"] is True


@pytest.mark.asyncio
async def test_task_continue_response_includes_segment_fields() -> None:
    """Task continue 响应透传分段元数据。"""
    module = _load_module("application/routers/task.py", "segmented_task_continue_router_module")
    module.TaskExecuteResponseBody.model_rebuild()
    service = MagicMock()
    service.continue_task = AsyncMock(
        return_value=TaskResult(
            content="done",
            status=TaskStatus.SUCCESS,
            model="test-model",
            prompt_id="task-template@v1",
            usage={"total_tokens": 6},
            segment_metadata=SegmentRunMetadata(
                segment_index=2,
                segment_count=2,
                auto_continue_attempted=True,
                segment_stop_reason="completed",
                budget_usage=SegmentBudgetUsage(
                    segment_count=2,
                    continuation_count=1,
                    total_tokens=6,
                    elapsed_ms=12.5,
                    consecutive_paused_count=1,
                    no_progress_count=2,
                    repeated_tool_call_count=3,
                ),
            ),
        )
    )

    response = await module.continue_task(
        "s1",
        module.TaskContinueRequestBody(),
        service=service,
    )

    body = response.model_dump()
    assert body["segment_count"] == 2
    assert body["auto_continue_attempted"] is True
    assert body["budget_usage"] == {
        "segment_count": 2,
        "continuation_count": 1,
        "total_tokens": 6,
        "elapsed_ms": 12.5,
        "consecutive_paused_count": 1,
        "no_progress_count": 2,
        "repeated_tool_call_count": 3,
    }
