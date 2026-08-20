"""任务执行路由模块。

定义面向任务的 HTTP 端点，将结构化的任务请求转换为领域层 Task 值对象，
通过 DI 容器注入 TaskAgentPort 执行任务，再将 TaskResult 转换为 HTTP 响应返回。

端点列表：
- ``POST /api/task/execute``：提交任务执行请求，返回结构化的执行结果。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from application.api.presenters.task_presenter import segment_budget_usage_to_response_body
from common.container import inject
from common.exceptions import BizException
from domain.agent.ports import ArtifactStorePort
from domain.agent.segmented_execution import SegmentRunMetadata
from domain.chat.exceptions import ContinuationUnavailableError
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import Task, TaskContinueRequest, TaskResult

router = APIRouter(tags=["task"])
TASK_AGENT_DEPENDENCY = Depends(inject(TaskAgentPort))
ARTIFACT_STORE_DEPENDENCY = Depends(inject(ArtifactStorePort))


class TaskExecuteRequestBody(BaseModel):
    """任务执行 HTTP 请求体模型。

    用于 FastAPI 自动校验和 OpenAPI 文档生成，与领域层 Task 值对象分离。

    Attributes:
        goal: 任务目标描述，必填。
        input_data: 输入数据字典，可选，默认空字典。
        constraints: 约束条件列表，可选，默认空列表。
        output_format: 期望输出格式描述，可选。
        model: 模型名称，可选，未指定时使用系统默认模型。
        session_id: 会话标识，可选，用于关联已有对话上下文。
    """

    goal: str
    input_data: dict[str, Any] = {}
    constraints: list[str] = []
    output_format: str | None = None
    model: str | None = None
    session_id: str | None = None


class TraceEntryBody(BaseModel):
    """执行轨迹条目 HTTP 响应体模型。

    Attributes:
        step: 步骤序号，从 1 开始。
        action: 操作类型，如 "tool_call"、"tool_result"。
        detail: 操作详情描述。
        timestamp_ms: 时间戳（毫秒）。
    """

    step: int
    action: str
    detail: str
    timestamp_ms: float


class BudgetUsageBody(BaseModel):
    """分段预算用量 HTTP 模型。"""

    segment_count: int = 0
    continuation_count: int = 0
    total_tokens: int = 0
    elapsed_ms: float = 0.0
    consecutive_paused_count: int = 0
    no_progress_count: int = 0
    repeated_tool_call_count: int = 0


class TaskExecuteResponseBody(BaseModel):
    """任务执行 HTTP 响应体模型。

    Attributes:
        code: 业务状态码，0 表示成功。
        content: 执行结果内容。
        status: 执行状态枚举值。
        model: 实际使用的模型名称。
        usage: token 用量信息。
        trace: 执行轨迹列表。
        latency_ms: 总执行耗时（毫秒）。
        prompt_id: 本次任务执行所用的 Prompt 标识符（形如 ``task-template@v1``）。
    """

    code: int = 0
    content: str
    status: str
    model: str
    usage: dict[str, int]
    trace: list[TraceEntryBody]
    latency_ms: float
    prompt_id: str
    terminated_reason: str = "completed"
    can_continue: bool = False
    segment_index: int = 1
    segment_count: int = 1
    auto_continue_attempted: bool = False
    segment_stop_reason: str = "completed"
    budget_usage: BudgetUsageBody = BudgetUsageBody()
    trace_id: str | None = None
    trace_ref: dict[str, Any] | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    artifact_ref: dict[str, Any] | None = None


class TaskContinueRequestBody(BaseModel):
    """任务继续 HTTP 请求体模型。"""

    model: str | None = None


def _budget_usage_body(metadata: SegmentRunMetadata) -> BudgetUsageBody:
    """把分段预算元数据映射为 HTTP body。"""
    data = segment_budget_usage_to_response_body(metadata.budget_usage)
    return BudgetUsageBody(
        segment_count=int(data["segment_count"]),
        continuation_count=int(data["continuation_count"]),
        total_tokens=int(data["total_tokens"]),
        elapsed_ms=float(data["elapsed_ms"]),
        consecutive_paused_count=int(data["consecutive_paused_count"]),
        no_progress_count=int(data["no_progress_count"]),
        repeated_tool_call_count=int(data["repeated_tool_call_count"]),
    )


async def _artifact_ids_for_session(
    session_id: str | None,
    artifact_store: ArtifactStorePort | None,
) -> list[str]:
    """从 artifact store 提取当前 session 的轻量引用 ID。"""

    if (
        session_id is None
        or artifact_store is None
        or not callable(getattr(artifact_store, "list_artifacts", None))
    ):
        return []
    artifacts = await artifact_store.list_artifacts(session_id)
    return [item.logical_path for item in artifacts]


def _resource_refs(
    session_id: str | None,
    artifact_ids: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """构造任务响应的 trace/artifact 引用。"""

    if session_id is None:
        return None, None
    return (
        {
            "available": True,
            "trace_id": session_id,
            "url": f"/api/traces/{session_id}",
        },
        {
            "available": bool(artifact_ids),
            "session_id": session_id,
            "url": f"/api/artifacts/{session_id}",
            "count": len(artifact_ids),
        },
    )


def _task_response_body(
    result: TaskResult,
    *,
    session_id: str | None = None,
    artifact_ids: list[str] | None = None,
) -> TaskExecuteResponseBody:
    """把 TaskResult 映射为 HTTP body。"""
    metadata = result.segment_metadata
    artifact_ids = artifact_ids or []
    trace_ref, artifact_ref = _resource_refs(session_id, artifact_ids)
    return TaskExecuteResponseBody(
        content=result.content,
        status=result.status.value,
        model=result.model,
        usage=result.usage,
        trace=[
            TraceEntryBody(
                step=entry.step,
                action=entry.action,
                detail=entry.detail,
                timestamp_ms=entry.timestamp_ms,
            )
            for entry in result.trace
        ],
        latency_ms=result.latency_ms,
        prompt_id=result.prompt_id,
        terminated_reason=result.terminated_reason,
        can_continue=result.can_continue,
        segment_index=metadata.segment_index,
        segment_count=metadata.segment_count,
        auto_continue_attempted=metadata.auto_continue_attempted,
        segment_stop_reason=metadata.segment_stop_reason,
        budget_usage=_budget_usage_body(metadata),
        trace_id=session_id,
        trace_ref=trace_ref,
        artifact_ids=artifact_ids,
        artifact_ref=artifact_ref,
    )


def _biz_error_response(exc: BizException) -> JSONResponse:
    """把任务业务异常映射为 HTTP JSON。"""
    status_code = 409 if isinstance(exc, ContinuationUnavailableError) else 400
    return JSONResponse(
        status_code=status_code,
        content={"code": exc.code, "message": exc.message},
    )


@router.post("/api/task/execute", response_model=None)
async def execute_task(
    request: TaskExecuteRequestBody,
    service: TaskAgentPort = TASK_AGENT_DEPENDENCY,
    artifact_store: ArtifactStorePort | None = ARTIFACT_STORE_DEPENDENCY,
) -> TaskExecuteResponseBody | JSONResponse:
    """任务执行端点。

    将 HTTP 请求体转换为 Task 值对象，通过 DI 容器注入 TaskAgentPort，
    调用 execute 方法并将 TaskResult 转换为 HTTP 响应返回。

    Task 构造时 goal 校验失败返回 HTTP 400。

    Args:
        request: 任务执行请求体，由 FastAPI 自动解析和校验。
        service: TaskAgentPort 实例，由 DI 容器注入。

    Returns:
        成功时返回 TaskExecuteResponseBody JSON，goal 校验失败时返回 HTTP 400 JSONResponse。
    """
    try:
        task = Task(
            goal=request.goal,
            input_data=request.input_data,
            constraints=request.constraints,
            output_format=request.output_format,
            model=request.model,
            session_id=request.session_id,
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": str(e)},
        )

    try:
        result = await service.execute(task)
    except BizException as exc:
        return _biz_error_response(exc)

    artifact_ids = await _artifact_ids_for_session(request.session_id, artifact_store)
    return _task_response_body(result, session_id=request.session_id, artifact_ids=artifact_ids)


@router.post("/api/task/sessions/{session_id}/continue", response_model=None)
async def continue_task(
    session_id: str,
    request: TaskContinueRequestBody,
    service: TaskAgentPort = TASK_AGENT_DEPENDENCY,
    artifact_store: ArtifactStorePort | None = ARTIFACT_STORE_DEPENDENCY,
) -> TaskExecuteResponseBody | JSONResponse:
    """基于已有任务会话上下文继续执行。"""
    try:
        continue_request = TaskContinueRequest(
            session_id=session_id,
            model=request.model,
        )
        result = await service.continue_task(continue_request)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc)})
    except BizException as exc:
        return _biz_error_response(exc)

    artifact_ids = await _artifact_ids_for_session(session_id, artifact_store)
    return _task_response_body(result, session_id=session_id, artifact_ids=artifact_ids)
