"""聊天对话路由模块。

定义聊天对话相关的 HTTP 端点，包括同步聊天、流式聊天（SSE）和会话清除。
通过 DI 容器注入 Chat_Service_Port，将 HTTP 请求转换为领域值对象后
委托给聊天服务处理，再将结果转换为 HTTP 响应返回。

端点列表：
- ``POST /api/chat``：发送聊天消息，支持同步和流式两种响应模式。
- ``DELETE /api/chat/sessions/{session_id}``：清除指定会话的对话历史。
"""

import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import cast

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from common.container import inject
from common.exceptions import BizException
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.ports import ArtifactStorePort
from domain.agent.segmented_execution import SegmentRunMetadata
from domain.agent.value_objects import (
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalDecisionType,
    EditedAction,
    PendingActionRequest,
)
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.ports import ChatServicePort
from domain.chat.value_objects import (
    ApprovalResumeRequestVO,
    ChatContinueRequestVO,
    ChatRequestVO,
    ChatResponseVO,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])
CHAT_SERVICE_DEPENDENCY = Depends(inject(ChatServicePort))
ARTIFACT_STORE_DEPENDENCY = Depends(inject(ArtifactStorePort))


def _empty_action_requests() -> list[dict[str, object]]:
    """创建空审批动作响应列表。"""
    return []


class ChatRequestBody(BaseModel):
    """聊天请求 HTTP 请求体模型。

    用于 FastAPI 自动校验和 OpenAPI 文档生成，与领域层 ChatRequest_VO 分离。

    Attributes:
        session_id: 会话唯一标识符。
        message: 用户消息内容。
        stream: 是否使用流式响应（SSE），默认 False。
        model: 可选的模型名称，未指定时使用系统默认模型。
    """

    session_id: str
    message: str
    stream: bool = False
    model: str | None = None


class BudgetUsageBody(BaseModel):
    """分段预算用量 HTTP 模型。"""

    segment_count: int = 0
    continuation_count: int = 0
    total_tokens: int = 0
    elapsed_ms: float = 0.0
    consecutive_paused_count: int = 0
    no_progress_count: int = 0
    repeated_tool_call_count: int = 0


class ChatResponseBody(BaseModel):
    """同步聊天 HTTP 响应体模型。

    Attributes:
        code: 业务状态码，0 表示成功。
        session_id: 会话唯一标识符。
        reply: 模型回复的文本内容。
        model: 实际使用的模型名称。
        usage: token 用量信息。
        prompt_id: 本次对话使用的 Prompt 标识符。
    """

    code: int = 0
    session_id: str
    reply: str
    model: str
    usage: dict[str, int]
    prompt_id: str
    status: str = "completed"
    approval_id: str | None = None
    action_requests: list[dict[str, object]] = Field(default_factory=_empty_action_requests)
    terminated_reason: str = "completed"
    can_continue: bool = False
    segment_index: int = 1
    segment_count: int = 1
    auto_continue_attempted: bool = False
    segment_stop_reason: str = "completed"
    budget_usage: BudgetUsageBody = Field(default_factory=BudgetUsageBody)
    trace_id: str
    trace_ref: dict[str, object]
    artifact_ids: list[str] = Field(default_factory=list)
    artifact_ref: dict[str, object]


class EditedActionBody(BaseModel):
    """编辑后工具动作 HTTP 模型。"""

    name: str
    arguments: str


class ApprovalDecisionBody(BaseModel):
    """审批决策 HTTP 模型。"""

    type: str
    tool_call_id: str
    edited_action: EditedActionBody | None = None
    message: str = ""


class ApprovalResumeRequestBody(BaseModel):
    """审批恢复 HTTP 请求体模型。"""

    decisions: list[ApprovalDecisionBody]
    model: str | None = None


class ChatContinueRequestBody(BaseModel):
    """聊天继续 HTTP 请求体模型。"""

    stream: bool = False
    model: str | None = None


def _action_to_dict(action: PendingActionRequest) -> dict[str, object]:
    """把 PendingActionRequest 转成 HTTP 响应 dict。"""
    return {
        "tool_call_id": action.tool_call_id,
        "tool_name": action.tool_name,
        "arguments": action.arguments,
        "allowed_decisions": sorted(action.allowed_decisions),
        "reason": action.reason,
    }


def _budget_usage_body(metadata: SegmentRunMetadata) -> BudgetUsageBody:
    """把分段预算元数据映射为 HTTP body。"""
    usage = metadata.budget_usage
    return BudgetUsageBody(
        segment_count=usage.segment_count,
        continuation_count=usage.continuation_count,
        total_tokens=usage.total_tokens,
        elapsed_ms=usage.elapsed_ms,
        consecutive_paused_count=usage.consecutive_paused_count,
        no_progress_count=usage.no_progress_count,
        repeated_tool_call_count=usage.repeated_tool_call_count,
    )


def _has_explicit_callable(obj: object, name: str) -> bool:
    """判断对象是否显式提供可调用属性，避免 MagicMock 虚假 hasattr。"""
    value = getattr(obj, name, None)
    if not callable(value):
        return False
    if name in getattr(obj, "__dict__", {}):
        return True
    return any(name in cls.__dict__ for cls in type(obj).__mro__)


def _segment_fields_from_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    """从事件 metadata 中提取分段响应字段。"""
    keys = (
        "segment_index",
        "segment_count",
        "auto_continue_attempted",
        "segment_stop_reason",
        "budget_usage",
    )
    return {key: metadata[key] for key in keys if key in metadata}


async def _artifact_ids_for_session(
    session_id: str,
    artifact_store: ArtifactStorePort | None,
) -> list[str]:
    """从 artifact store 提取当前 session 的轻量引用 ID。"""

    if artifact_store is None or not callable(getattr(artifact_store, "list_artifacts", None)):
        return []
    artifacts = await artifact_store.list_artifacts(session_id)
    return [item.logical_path for item in artifacts]


def _resource_refs(session_id: str, artifact_ids: list[str]) -> dict[str, dict[str, object]]:
    """构造工作台统一资源引用字段。"""

    return {
        "trace_ref": {
            "available": True,
            "trace_id": session_id,
            "url": f"/api/traces/{session_id}",
        },
        "artifact_ref": {
            "available": bool(artifact_ids),
            "session_id": session_id,
            "url": f"/api/artifacts/{session_id}",
            "count": len(artifact_ids),
        },
    }


def _chat_response_body(
    response: ChatResponseVO,
    artifact_ids: list[str] | None = None,
) -> ChatResponseBody:
    """把 ChatResponseVO 映射为 HTTP body。"""
    metadata = response.segment_metadata
    artifact_ids = artifact_ids or []
    refs = _resource_refs(response.session_id, artifact_ids)
    return ChatResponseBody(
        session_id=response.session_id,
        reply=response.reply,
        model=response.model,
        usage=response.usage,
        prompt_id=response.prompt_id,
        status=response.status,
        approval_id=response.approval_id,
        action_requests=[_action_to_dict(action) for action in response.action_requests],
        terminated_reason=response.terminated_reason,
        can_continue=response.can_continue,
        segment_index=metadata.segment_index,
        segment_count=metadata.segment_count,
        auto_continue_attempted=metadata.auto_continue_attempted,
        segment_stop_reason=metadata.segment_stop_reason,
        budget_usage=_budget_usage_body(metadata),
        trace_id=response.session_id,
        trace_ref=refs["trace_ref"],
        artifact_ids=artifact_ids,
        artifact_ref=refs["artifact_ref"],
    )


def _biz_error_response(exc: BizException) -> JSONResponse:
    """把审批业务异常映射为 HTTP JSON。"""
    status_code = 400
    if isinstance(exc, ApprovalNotFoundError):
        status_code = 404
    elif isinstance(
        exc, (ApprovalExpiredError, ApprovalConsumedError, ContinuationUnavailableError)
    ):
        status_code = 409
    return JSONResponse(status_code=status_code, content={"code": exc.code, "message": exc.message})


@router.post("/api/chat", response_model=None)
async def chat(
    request: ChatRequestBody,
    service: ChatServicePort = CHAT_SERVICE_DEPENDENCY,
    artifact_store: ArtifactStorePort | None = ARTIFACT_STORE_DEPENDENCY,
) -> ChatResponseBody | EventSourceResponse | JSONResponse:
    """聊天端点，支持同步和流式两种响应模式。

    根据请求体中的 ``stream`` 字段决定响应方式：
    - ``stream=false``：调用同步对话，返回完整 JSON 响应。
    - ``stream=true``：调用流式对话，返回 SSE 事件流。

    ChatRequestBody 到 ChatRequest_VO 的转换在此处完成，
    转换时的 ValueError 会被 FastAPI 异常处理器捕获并返回 HTTP 400。

    Args:
        request: 聊天请求体，由 FastAPI 自动解析和校验。
        service: 聊天服务实例，由 DI 容器注入。

    Returns:
        同步模式返回 ChatResponseBody JSON，流式模式返回 EventSourceResponse。
    """
    try:
        chat_request = ChatRequestVO(
            session_id=request.session_id,
            message=request.message,
            stream=request.stream,
            model=request.model,
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": str(e)},
        )

    if not request.stream:
        try:
            response = await service.chat(chat_request)
        except BizException as exc:
            return _biz_error_response(exc)
        artifact_ids = await _artifact_ids_for_session(response.session_id, artifact_store)
        return _chat_response_body(response, artifact_ids)

    async def _event_generator() -> AsyncIterator[dict[str, str]]:
        """生成 SSE 事件流。

        逐个产出 StreamingChunk，序列化为 JSON 格式的 SSE data 事件。
        最后一个分片到达后，先发送一条 ``{"prompt_id": "..."}`` 事件再发送
        ``[DONE]`` 标记通知客户端流结束（设计决策 #2，需求 4.6 / 7.3）。

        当流式调用过程中发生异常时，将错误信息作为 SSE 事件发送给客户端，
        避免异常冒泡到 sse_starlette 的 TaskGroup 导致 ExceptionGroup 错误；
        异常分支降级为不写 ``prompt_id`` 事件。
        """
        try:
            approval_required = False
            if _has_explicit_callable(
                service, "stream_segmented_chat_events"
            ) or _has_explicit_callable(service, "stream_chat_events"):
                event_stream = (
                    service.stream_segmented_chat_events
                    if _has_explicit_callable(service, "stream_segmented_chat_events")
                    else service.stream_chat_events
                )
                async for event in event_stream(chat_request):
                    if event.kind == "assistant_delta":
                        data = json.dumps(
                            {
                                "event_type": "assistant_delta",
                                "delta_content": event.content,
                                "finished": False,
                            },
                            ensure_ascii=False,
                        )
                        yield {"data": data}
                    elif event.kind in {"tool_start", "tool_result", "tool_error"}:
                        data = json.dumps(
                            {
                                "event_type": event.kind,
                                "content": event.content,
                                "finished": False,
                                **event.metadata,
                            },
                            ensure_ascii=False,
                        )
                        yield {"event": event.kind, "data": data}
                    elif event.kind == "assistant_done":
                        if event.metadata.get("event_type") == "segment_done":
                            payload: dict[str, object] = {
                                "event_type": "segment_done",
                                "finished": False,
                            }
                            payload.update(_segment_fields_from_metadata(event.metadata))
                            yield {"data": json.dumps(payload, ensure_ascii=False)}
                            continue
                        payload = {
                            "event_type": "assistant_done",
                            "delta_content": "",
                            "finished": True,
                        }
                        payload.update(_segment_fields_from_metadata(event.metadata))
                        if event.metadata.get("status") == "paused":
                            payload["status"] = "paused"
                            payload["terminated_reason"] = event.metadata.get(
                                "terminated_reason", "completed"
                            )
                            payload["can_continue"] = bool(
                                event.metadata.get("can_continue", False)
                            )
                        yield {
                            "data": json.dumps(
                                payload,
                                ensure_ascii=False,
                            )
                        }
                    elif event.kind == "approval_required":
                        approval_required = True
                        data = json.dumps(
                            {
                                "event_type": "approval_required",
                                "status": "approval_required",
                                "session_id": event.metadata.get("session_id"),
                                "approval_id": event.metadata.get("approval_id"),
                                "action_requests": event.metadata.get("actions", []),
                            },
                            ensure_ascii=False,
                        )
                        yield {"event": "approval_required", "data": data}
            else:
                async for chunk in service.stream_chat(chat_request):
                    data = json.dumps(
                        {"delta_content": chunk.delta_content, "finished": chunk.finished},
                        ensure_ascii=False,
                    )
                    yield {"data": data}
            if not approval_required:
                prompt_id_event = json.dumps(
                    {"prompt_id": service.prompt_id},
                    ensure_ascii=False,
                )
                yield {"data": prompt_id_event}
            yield {"data": "[DONE]"}
        except Exception as exc:
            logger.error("流式对话异常: %s", exc, exc_info=True)
            error_data = json.dumps(
                {"error": True, "message": str(exc), "finished": True},
                ensure_ascii=False,
            )
            yield {"data": error_data}
            yield {"data": "[DONE]"}

    return EventSourceResponse(_event_generator())


@router.post(
    "/api/chat/sessions/{session_id}/approvals/{approval_id}/resume",
    response_model=None,
)
async def resume_approval(
    session_id: str,
    approval_id: str,
    request: ApprovalResumeRequestBody,
    service: ChatServicePort = CHAT_SERVICE_DEPENDENCY,
    artifact_store: ArtifactStorePort | None = ARTIFACT_STORE_DEPENDENCY,
) -> ChatResponseBody | JSONResponse:
    """提交审批决策并恢复 Agent 执行。"""
    try:
        decisions = tuple(
            ApprovalDecision(
                type=cast(ApprovalDecisionType, decision.type),
                tool_call_id=decision.tool_call_id,
                edited_action=(
                    EditedAction(
                        name=decision.edited_action.name,
                        arguments=decision.edited_action.arguments,
                    )
                    if decision.edited_action is not None
                    else None
                ),
                message=decision.message,
            )
            for decision in request.decisions
        )
        response = await service.resume_approval(
            ApprovalResumeRequestVO(
                session_id=session_id,
                approval_id=approval_id,
                decisions=decisions,
                model=request.model,
            )
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc)})
    except BizException as exc:
        return _biz_error_response(exc)

    artifact_ids = await _artifact_ids_for_session(response.session_id, artifact_store)
    return _chat_response_body(response, artifact_ids)


@router.post("/api/chat/sessions/{session_id}/continue", response_model=None)
async def continue_chat(
    session_id: str,
    request: ChatContinueRequestBody,
    service: ChatServicePort = CHAT_SERVICE_DEPENDENCY,
    artifact_store: ArtifactStorePort | None = ARTIFACT_STORE_DEPENDENCY,
) -> ChatResponseBody | EventSourceResponse | JSONResponse:
    """基于已有会话上下文继续聊天执行。"""
    try:
        continue_request = ChatContinueRequestVO(
            session_id=session_id,
            stream=request.stream,
            model=request.model,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"code": 400, "message": str(exc)})

    if not request.stream:
        try:
            response = await service.continue_chat(continue_request)
        except BizException as exc:
            return _biz_error_response(exc)
        artifact_ids = await _artifact_ids_for_session(response.session_id, artifact_store)
        return _chat_response_body(response, artifact_ids)

    try:
        event_source = (
            service.stream_segmented_continue_chat_events(continue_request)
            if _has_explicit_callable(service, "stream_segmented_continue_chat_events")
            else service.stream_continue_chat_events(continue_request)
        )
        try:
            first_event = await anext(event_source)
        except StopAsyncIteration:
            first_event = None
    except BizException as exc:
        return _biz_error_response(exc)
    except Exception as exc:
        logger.error("继续流式对话预打开异常: %s", exc, exc_info=True)
        error_message = str(exc)

        async def _error_generator() -> AsyncIterator[dict[str, str]]:
            """生成继续聊天 SSE 预打开失败事件。"""
            yield {
                "data": json.dumps(
                    {"error": True, "message": error_message, "finished": True},
                    ensure_ascii=False,
                )
            }
            yield {"data": "[DONE]"}

        return EventSourceResponse(_error_generator())

    async def _event_generator() -> AsyncIterator[dict[str, str]]:
        """生成继续聊天 SSE 事件流。"""

        async def _emit_event(event: AgentStreamEvent) -> AsyncIterator[dict[str, str]]:
            """把结构化聊天事件转换为 SSE data。"""
            if event.kind == "assistant_delta":
                yield {
                    "data": json.dumps(
                        {
                            "event_type": "assistant_delta",
                            "delta_content": event.content,
                            "finished": False,
                        },
                        ensure_ascii=False,
                    )
                }
            elif event.kind in {"tool_start", "tool_result", "tool_error"}:
                yield {
                    "event": event.kind,
                    "data": json.dumps(
                        {
                            "event_type": event.kind,
                            "content": event.content,
                            "finished": False,
                            **event.metadata,
                        },
                        ensure_ascii=False,
                    ),
                }
            elif event.kind == "assistant_done":
                if event.metadata.get("event_type") == "segment_done":
                    payload: dict[str, object] = {
                        "event_type": "segment_done",
                        "finished": False,
                    }
                    payload.update(_segment_fields_from_metadata(event.metadata))
                    yield {"data": json.dumps(payload, ensure_ascii=False)}
                    return
                payload = {
                    "event_type": "assistant_done",
                    "delta_content": "",
                    "finished": True,
                }
                payload.update(_segment_fields_from_metadata(event.metadata))
                if event.metadata.get("status") == "paused":
                    payload["status"] = "paused"
                    payload["terminated_reason"] = event.metadata.get(
                        "terminated_reason", "completed"
                    )
                    payload["can_continue"] = bool(
                        event.metadata.get("can_continue", False)
                    )
                yield {"data": json.dumps(payload, ensure_ascii=False)}
            elif event.kind == "approval_required":
                data = json.dumps(
                    {
                        "event_type": "approval_required",
                        "status": "approval_required",
                        "session_id": event.metadata.get("session_id"),
                        "approval_id": event.metadata.get("approval_id"),
                        "action_requests": event.metadata.get("actions", []),
                    },
                    ensure_ascii=False,
                )
                yield {"event": "approval_required", "data": data}

        try:
            if first_event is not None:
                async for item in _emit_event(first_event):
                    yield item
            async for event in event_source:
                async for item in _emit_event(event):
                    yield item
            yield {
                "data": json.dumps(
                    {"prompt_id": service.prompt_id},
                    ensure_ascii=False,
                )
            }
            yield {"data": "[DONE]"}
        except Exception as exc:
            logger.error("继续流式对话异常: %s", exc, exc_info=True)
            yield {
                "data": json.dumps(
                    {"error": True, "message": str(exc), "finished": True},
                    ensure_ascii=False,
                )
            }
            yield {"data": "[DONE]"}

    return EventSourceResponse(_event_generator())


@router.delete("/api/chat/sessions/{session_id}")
async def clear_session(
    session_id: str,
    service: ChatServicePort = CHAT_SERVICE_DEPENDENCY,
) -> JSONResponse:
    """清除指定会话的对话历史。

    删除该会话的全部对话上下文，使用户可以开始新的对话。

    Args:
        session_id: 会话唯一标识符，从 URL 路径参数获取。
        service: 聊天服务实例，由 DI 容器注入。

    Returns:
        包含操作结果的 JSON 响应。
    """
    await service.clear_session(session_id)
    return JSONResponse(content={"code": 0, "message": "会话已清除"})
