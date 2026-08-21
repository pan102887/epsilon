"""Run HTTP 路由模块。

本模块是阶段三后台 Run runtime 的可选 FastAPI 薄 adapter。它只负责
HTTP DTO 转换、输入校验、业务异常到 HTTP 响应映射和 SSE 包装，所有
业务语义均委托给 ``RunApplicationService``。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from application.run.run_application_service import RunApplicationService
from common.container import inject
from common.exceptions import BizException
from domain.agent.value_objects import ApprovalDecision, EditedAction
from domain.run.exceptions import (
    RunCancelUnavailableError,
    RunCheckpointStoreUnavailableError,
    RunContinuationUnavailableError,
    RunEventReplayExpiredError,
    RunIdempotencyConflictError,
    RunNotFoundError,
    RunPayloadValidationError,
    RunQueueFullError,
    RunRecoveryUnavailableError,
    RunToolReplayBlockedError,
)
from domain.run.value_objects import (
    RunCreateRequest,
    RunEvent,
    RunKind,
    RunPayload,
    RunSnapshot,
)
from domain.run.workflow import canonicalize_collaboration_summary

router = APIRouter(tags=["runs"])
RUN_APPLICATION_SERVICE_DEPENDENCY = Depends(inject(RunApplicationService))


class ChatRunCreateBody(BaseModel):
    """聊天 Run 创建请求体。"""

    session_id: str
    message: str
    model: str | None = None


class TaskRunCreateBody(BaseModel):
    """任务 Run 创建请求体。"""

    goal: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    output_format: str | None = None
    model: str | None = None
    session_id: str | None = None


class RunCreateRequestBody(BaseModel):
    """Run 创建 HTTP 请求体。"""

    kind: str
    client_request_id: str | None = None
    workflow_name: str | None = None
    chat: ChatRunCreateBody | None = None
    task: TaskRunCreateBody | None = None
    model: str | None = None
    created_by: str | None = None


class RunContinueRequestBody(BaseModel):
    """Run 继续请求体。"""

    model: str | None = None


class EditedActionBody(BaseModel):
    """审批编辑动作请求体。"""

    name: str
    arguments: str


class ApprovalDecisionBody(BaseModel):
    """审批决策请求体。"""

    type: str
    tool_call_id: str
    edited_action: EditedActionBody | None = None
    message: str = ""


class ApprovalResumeRequestBody(BaseModel):
    """Run 审批恢复请求体。"""

    decisions: list[ApprovalDecisionBody]
    model: str | None = None


class CollaborationSummaryBody(BaseModel):
    """Run 协作摘要 HTTP 响应体。"""

    model_config = ConfigDict(extra="allow")

    latest_steps: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    child_links: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    delegation_count: int = 0
    handoff_count: int = 0
    max_depth_seen: int = 0
    limit_hit_reason: str | None = None


class RunSnapshotBody(BaseModel):
    """Run 快照 HTTP 响应体。"""

    code: int = 0
    run_id: str
    kind: str
    status: str
    client_request_id: str | None
    payload_hash: str | None
    latest_event_cursor: int | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    approval_id: str | None
    can_continue: bool
    terminal_reason: str | None
    segment_metadata: dict[str, Any] | None
    latest_checkpoint_id: str | None = None
    recoverable: bool = False
    recovery_attempt_count: int = 0
    last_recovery_error: dict[str, Any] | None = None
    task_classification: str | None = None
    guardrail_summary: dict[str, Any] | None = None
    workflow_name: str | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: CollaborationSummaryBody | None = None
    created_at: datetime
    updated_at: datetime
    version: int


class RunEventBody(BaseModel):
    """Run 事件 HTTP 响应体。"""

    run_id: str
    cursor: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class RunEventsResponseBody(BaseModel):
    """Run 事件轮询响应体。"""

    code: int = 0
    events: list[RunEventBody]
    latest_cursor: int | None


def _model_dump(model: BaseModel) -> dict[str, Any]:
    """兼容 Pydantic v2 的 dict 转换。"""

    return model.model_dump(exclude_none=True)


def _collaboration_summary_body(
    value: dict[str, Any] | None,
) -> CollaborationSummaryBody | None:
    """把 canonical 协作摘要转换为结构化 HTTP DTO。"""

    if value is None:
        return None
    return CollaborationSummaryBody.model_validate(value)


def _snapshot_body(snapshot: RunSnapshot) -> RunSnapshotBody:
    """把领域 RunSnapshot 映射为 HTTP body。"""

    return RunSnapshotBody(
        run_id=snapshot.run_id,
        kind=snapshot.kind.value,
        status=snapshot.status.value,
        client_request_id=snapshot.client_request_id,
        payload_hash=snapshot.payload_hash,
        latest_event_cursor=snapshot.latest_event_cursor,
        result=snapshot.result,
        error=snapshot.error,
        approval_id=snapshot.approval_id,
        can_continue=snapshot.can_continue,
        terminal_reason=snapshot.terminal_reason,
        segment_metadata=snapshot.segment_metadata,
        latest_checkpoint_id=snapshot.latest_checkpoint_id,
        recoverable=snapshot.recoverable,
        recovery_attempt_count=snapshot.recovery_attempt_count,
        last_recovery_error=snapshot.last_recovery_error,
        task_classification=snapshot.task_classification,
        guardrail_summary=snapshot.guardrail_summary,
        workflow_name=snapshot.workflow_name,
        workflow_run_state=snapshot.workflow_run_state,
        collaboration_summary=_collaboration_summary_body(
            canonicalize_collaboration_summary(snapshot.collaboration_summary)
        ),
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        version=snapshot.version,
    )


def _event_body(event: RunEvent) -> RunEventBody:
    """把领域 RunEvent 映射为 HTTP body。"""

    return RunEventBody(
        run_id=event.run_id,
        cursor=event.cursor,
        event_type=event.event_type.value,
        payload=event.payload,
        created_at=event.created_at,
    )


def snapshot_body(snapshot: RunSnapshot) -> RunSnapshotBody:
    """把领域 RunSnapshot 映射为公开 HTTP body。"""
    return _snapshot_body(snapshot)


def event_body(event: RunEvent) -> RunEventBody:
    """把领域 RunEvent 映射为公开 HTTP body。"""
    return _event_body(event)


def _run_create_request(body: RunCreateRequestBody) -> RunCreateRequest:
    """把 HTTP 创建请求转换为领域请求。"""

    try:
        kind = RunKind(body.kind)
    except ValueError as exc:
        raise RunPayloadValidationError("kind 只支持 chat 或 task") from exc

    if kind is RunKind.CHAT:
        if body.chat is None:
            raise RunPayloadValidationError("chat run 必须提供 chat")
        message = body.chat.message.strip()
        if not body.chat.session_id.strip() or not message:
            raise RunPayloadValidationError("chat.session_id 和 chat.message 必填")
        model = body.model or body.chat.model
        payload = RunPayload(
            kind=kind,
            session_id=body.chat.session_id,
            chat={"message": message},
            model=model,
        )
    else:
        if body.task is None:
            raise RunPayloadValidationError("task run 必须提供 task")
        goal = body.task.goal.strip()
        if not goal:
            raise RunPayloadValidationError("task.goal 必填")
        model = body.model or body.task.model
        payload = RunPayload(
            kind=kind,
            session_id=body.task.session_id,
            task={**_model_dump(body.task), "goal": goal, "model": model},
            model=model,
        )

    return RunCreateRequest(
        payload=payload,
        client_request_id=body.client_request_id,
        created_by=body.created_by,
        workflow_name=body.workflow_name,
    )


def _approval_decisions(body: ApprovalResumeRequestBody) -> list[ApprovalDecision]:
    """把审批恢复请求体转换为领域审批决策。"""

    decisions: list[ApprovalDecision] = []
    for item in body.decisions:
        edited_action = None
        if item.edited_action is not None:
            edited_action = EditedAction(
                name=item.edited_action.name,
                arguments=item.edited_action.arguments,
            )
        decisions.append(
            ApprovalDecision(
                type=item.type,  # type: ignore[arg-type]
                tool_call_id=item.tool_call_id,
                edited_action=edited_action,
                message=item.message,
            )
        )
    return decisions


def _biz_error_response(exc: BizException) -> JSONResponse:
    """把 Run 业务异常映射为 HTTP JSON。"""

    status_code = 400
    if isinstance(exc, RunNotFoundError):
        status_code = 404
    elif isinstance(exc, RunQueueFullError):
        status_code = 429
    elif isinstance(
        exc,
        (
            RunIdempotencyConflictError,
            RunContinuationUnavailableError,
            RunCancelUnavailableError,
            RunEventReplayExpiredError,
            RunRecoveryUnavailableError,
            RunToolReplayBlockedError,
        ),
    ):
        status_code = 409
    elif isinstance(exc, RunCheckpointStoreUnavailableError):
        status_code = 503
    return JSONResponse(
        status_code=status_code,
        content={"code": exc.code, "message": exc.message},
    )


@router.post("/api/runs", response_model=None)
async def create_run(
    request: RunCreateRequestBody,
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> RunSnapshotBody | JSONResponse:
    """创建后台 Run。"""

    try:
        snapshot = await service.create_run(_run_create_request(request))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc)})
    except BizException as exc:
        return _biz_error_response(exc)
    return _snapshot_body(snapshot)


@router.get("/api/runs/{run_id}", response_model=None)
async def get_run(
    run_id: str,
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> RunSnapshotBody | JSONResponse:
    """查询 Run 快照。"""

    try:
        snapshot = await service.get_run(run_id)
    except BizException as exc:
        return _biz_error_response(exc)
    return _snapshot_body(snapshot)


@router.get("/api/runs/{run_id}/events", response_model=None)
async def get_run_events(
    run_id: str,
    after_cursor: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> RunEventsResponseBody | JSONResponse:
    """轮询查询 Run 事件。"""

    try:
        events = await service.list_events(run_id, after_cursor, limit)
    except BizException as exc:
        return _biz_error_response(exc)
    bodies = [_event_body(event) for event in events]
    return RunEventsResponseBody(
        events=bodies,
        latest_cursor=bodies[-1].cursor if bodies else after_cursor,
    )


@router.get("/api/runs/{run_id}/events/stream", response_model=None)
async def stream_run_events(
    run_id: str,
    after_cursor: int | None = Query(default=None),
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> EventSourceResponse:
    """通过 SSE 订阅 Run 事件。"""

    async def event_generator():
        try:
            async for event in service.stream_events(run_id, after_cursor):
                body = _event_body(event).model_dump(mode="json")
                yield {"event": event.event_type.value, "data": json.dumps(body)}
        except RunEventReplayExpiredError as exc:
            yield {
                "event": "replay_expired",
                "data": json.dumps(
                    {
                        "run_id": exc.run_id,
                        "cursor": after_cursor,
                        "after_cursor": exc.after_cursor,
                        "message": exc.message,
                        "fallback": "polling",
                    },
                    ensure_ascii=False,
                ),
            }
        except BizException as exc:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"code": exc.code, "message": exc.message},
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())


@router.post("/api/runs/{run_id}/cancel", response_model=None)
async def cancel_run(
    run_id: str,
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> RunSnapshotBody | JSONResponse:
    """请求取消 Run。"""

    try:
        snapshot = await service.request_cancel(run_id)
    except BizException as exc:
        return _biz_error_response(exc)
    return _snapshot_body(snapshot)


@router.post("/api/runs/{run_id}/continue", response_model=None)
async def continue_run(
    run_id: str,
    request: RunContinueRequestBody,
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> RunSnapshotBody | JSONResponse:
    """继续 paused Run。"""

    try:
        snapshot = await service.continue_run(run_id, request.model)
    except BizException as exc:
        return _biz_error_response(exc)
    return _snapshot_body(snapshot)


@router.post("/api/runs/{run_id}/approve", response_model=None)
async def approve_run(
    run_id: str,
    request: ApprovalResumeRequestBody,
    service: RunApplicationService = RUN_APPLICATION_SERVICE_DEPENDENCY,
) -> RunSnapshotBody | JSONResponse:
    """恢复 awaiting_approval Run。"""

    try:
        snapshot = await service.resume_approval_run(
            run_id,
            _approval_decisions(request),
            request.model,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc)})
    except BizException as exc:
        return _biz_error_response(exc)
    return _snapshot_body(snapshot)
