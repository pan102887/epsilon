"""任务路由单元测试。

验证 POST /api/task/execute 端点的核心行为：
1. 正常请求：mock TaskAgentPort.execute() 返回成功 TaskResult，验证响应体字段正确。
2. goal 为空字符串：验证返回 HTTP 400。
3. goal 为纯空白字符：验证返回 HTTP 400。

通过 importlib 直接加载 task 路由模块，避免触发 application 包的
__init__.py 初始化副作用。
"""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.exceptions import BizException
from domain.agent.segmented_execution import SegmentBudgetUsage, SegmentRunMetadata
from domain.task.value_objects import TaskResult, TaskStatus, TraceEntry

# mock prometheus_client 以避免 Windows 平台兼容问题
if "prometheus_client" not in sys.modules:
    _mock_prom = MagicMock()
    _mock_prom.CONTENT_TYPE_LATEST = "text/plain"
    _mock_prom.generate_latest = MagicMock(return_value=b"")
    sys.modules["prometheus_client"] = _mock_prom


def _load_task_module():
    """直接加载 task 路由模块，绕过 application 包的 __init__.py。

    使用 importlib 从文件路径加载 ``src/application/routers/task.py``，
    避免触发 ``application/__init__.py`` 中 server_app 的完整初始化链。

    Returns:
        task 路由模块对象
    """
    task_path = (
        pathlib.Path(__file__).resolve().parents[3] / "src" / "application" / "routers" / "task.py"
    )
    mod_name = "test_task_router_module"
    spec = importlib.util.spec_from_file_location(mod_name, str(task_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    # Pydantic 需要在 sys.modules 中找到模块才能正确解析类型引用，
    # 加载完成后调用 model_rebuild 确保 Pydantic 模型完全定义。
    module.TaskExecuteRequestBody.model_rebuild()
    module.TaskExecuteResponseBody.model_rebuild()
    module.TraceEntryBody.model_rebuild()
    return module


def _load_exception_handlers_module():
    """直接加载异常处理模块，用于注册统一异常处理器。

    Returns:
        exception_handlers 模块对象
    """
    handlers_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "application"
        / "exception_handlers.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_exception_handlers_module", str(handlers_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_task_module = _load_task_module()
_router = _task_module.router


# ---------------------------------------------------------------------------
# 成功场景测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_task_success() -> None:
    """正常请求：mock TaskAgentPort.execute() 返回成功 TaskResult，验证响应体字段正确。

    验证 code=0、content、status="success"、model、usage、trace、latency_ms 均正确映射。

    Requirements: 7.1, 7.2, 7.3, 7.4
    """
    mock_result = TaskResult(
        content="分析完成",
        status=TaskStatus.SUCCESS,
        model="gpt-4",
        prompt_id="task-template@v1",
        usage={"total_tokens": 150},
        trace=[
            TraceEntry(
                step=1,
                action="tool_call",
                detail="read_file",
                timestamp_ms=1000.0,
            )
        ],
        latency_ms=500.0,
        segment_metadata=SegmentRunMetadata(
            segment_index=2,
            segment_count=3,
            auto_continue_attempted=True,
            segment_stop_reason="completed",
            budget_usage=SegmentBudgetUsage(
                segment_count=3,
                continuation_count=2,
                total_tokens=150,
                elapsed_ms=500.0,
                consecutive_paused_count=1,
                no_progress_count=2,
                repeated_tool_call_count=3,
            ),
        ),
    )
    mock_service = AsyncMock()
    mock_service.execute.return_value = mock_result

    response = await _task_module.execute_task(
        _task_module.TaskExecuteRequestBody(goal="分析代码质量"),
        service=mock_service,
    )

    body = response.model_dump()
    assert body["code"] == 0
    assert body["content"] == "分析完成"
    assert body["status"] == "success"
    assert body["model"] == "gpt-4"
    assert body["usage"] == {"total_tokens": 150}
    assert len(body["trace"]) == 1
    assert body["trace"][0]["step"] == 1
    assert body["trace"][0]["action"] == "tool_call"
    assert body["trace"][0]["detail"] == "read_file"
    assert body["trace"][0]["timestamp_ms"] == 1000.0
    assert body["latency_ms"] == 500.0
    # 任务 9.6：响应体应透传 TaskResult.prompt_id（需求 7.4）
    assert body["prompt_id"] == "task-template@v1"
    assert body["terminated_reason"] == "completed"
    assert body["can_continue"] is False
    assert body["segment_index"] == 2
    assert body["segment_count"] == 3
    assert body["auto_continue_attempted"] is True
    assert body["segment_stop_reason"] == "completed"
    assert body["budget_usage"] == {
        "segment_count": 3,
        "continuation_count": 2,
        "total_tokens": 150,
        "elapsed_ms": 500.0,
        "consecutive_paused_count": 1,
        "no_progress_count": 2,
        "repeated_tool_call_count": 3,
    }


@pytest.mark.asyncio
async def test_execute_task_approval_response_shape() -> None:
    """验证任务需人工介入时响应结构保持不变。"""
    mock_result = TaskResult(
        content="需要审批",
        status=TaskStatus.HUMAN_INTERVENTION_REQUIRED,
        model="gpt-4",
        prompt_id="task-template@v1",
        terminated_reason="completed",
        can_continue=False,
        approval_id="approval-1",
    )
    mock_service = AsyncMock()
    mock_service.execute.return_value = mock_result

    response = await _task_module.execute_task(
        _task_module.TaskExecuteRequestBody(goal="需要审批的任务"),
        service=mock_service,
    )

    body = response.model_dump()
    assert body["code"] == 0
    assert body["content"] == "需要审批"
    assert body["status"] == "human_intervention_required"
    assert body["terminated_reason"] == "completed"
    assert body["can_continue"] is False
    assert "approval_id" not in body


@pytest.mark.asyncio
async def test_execute_task_biz_exception_returns_400_body() -> None:
    """验证普通 BizException 映射为 HTTP 400 与标准错误体。"""
    mock_service = AsyncMock()
    mock_service.execute.side_effect = BizException(70001, "任务参数不合法")

    response = await _task_module.execute_task(
        _task_module.TaskExecuteRequestBody(goal="触发业务错误"),
        service=mock_service,
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {"code": 70001, "message": "任务参数不合法"}


# ---------------------------------------------------------------------------
# 400 错误场景测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_task_goal_empty_returns_400() -> None:
    """goal 为空字符串时返回 HTTP 400。

    Requirements: 7.5
    """
    response = await _task_module.execute_task(
        _task_module.TaskExecuteRequestBody(goal=""),
        service=AsyncMock(),
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_execute_task_goal_whitespace_returns_400() -> None:
    """goal 为纯空白字符时返回 HTTP 400。

    Requirements: 7.5
    """
    response = await _task_module.execute_task(
        _task_module.TaskExecuteRequestBody(goal="   "),
        service=AsyncMock(),
    )

    assert response.status_code == 400
