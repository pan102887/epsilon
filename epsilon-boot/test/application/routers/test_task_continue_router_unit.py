"""任务继续路由单元测试。"""

import importlib.util
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentBudgetUsage, SegmentRunMetadata
from domain.chat.exceptions import ContinuationUnavailableError
from domain.task.value_objects import TaskResult, TaskStatus


def _load_task_module():
    """直接加载 task 路由模块。"""
    task_path = pathlib.Path(__file__).resolve().parents[3] / "src/application/routers/task.py"
    spec = importlib.util.spec_from_file_location(
        "test_task_continue_router_module", str(task_path)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TaskContinueRequestBody.model_rebuild()
    module.TaskExecuteResponseBody.model_rebuild()
    module.TraceEntryBody.model_rebuild()
    return module


@pytest.mark.asyncio
async def test_continue_task_returns_paused_fields() -> None:
    """验证任务继续响应透传 paused、terminated_reason 与 can_continue。"""
    module = _load_task_module()
    service = MagicMock()
    service.continue_task = AsyncMock(
        return_value=TaskResult(
            content="",
            status=TaskStatus.PAUSED,
            model="test-model",
            prompt_id="task-template@v1",
            terminated_reason="max_rounds",
            can_continue=True,
            segment_metadata=SegmentRunMetadata(
                segment_index=2,
                segment_count=2,
                auto_continue_attempted=False,
                segment_stop_reason="max_continuations_reached",
                budget_usage=SegmentBudgetUsage(
                    segment_count=2,
                    continuation_count=1,
                    total_tokens=42,
                    elapsed_ms=123.4,
                    consecutive_paused_count=2,
                    no_progress_count=1,
                    repeated_tool_call_count=0,
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
    assert body["status"] == "paused"
    assert body["terminated_reason"] == "max_rounds"
    assert body["can_continue"] is True
    assert body["segment_index"] == 2
    assert body["segment_count"] == 2
    assert body["auto_continue_attempted"] is False
    assert body["segment_stop_reason"] == "max_continuations_reached"
    assert body["budget_usage"] == {
        "segment_count": 2,
        "continuation_count": 1,
        "total_tokens": 42,
        "elapsed_ms": 123.4,
        "consecutive_paused_count": 2,
        "no_progress_count": 1,
        "repeated_tool_call_count": 0,
    }


@pytest.mark.asyncio
async def test_continue_task_unavailable_returns_409() -> None:
    """验证任务继续不可用映射为 HTTP 409。"""
    module = _load_task_module()
    service = MagicMock()
    service.continue_task = AsyncMock(
        side_effect=ContinuationUnavailableError("s1", "缺少可继续的上下文")
    )

    response = await module.continue_task(
        "s1",
        module.TaskContinueRequestBody(),
        service=service,
    )

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == 60041


@pytest.mark.asyncio
async def test_continue_task_empty_session_id_returns_400() -> None:
    """直接验证路由构造空 session_id 继续请求时返回 400。"""
    module = _load_task_module()
    response = await module.continue_task(
        "",
        module.TaskContinueRequestBody(),
        service=MagicMock(),
    )

    assert response.status_code == 400
