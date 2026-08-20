"""结构化 Agent 追踪查询路由模块。

暴露 P0.3 已记录的结构化 trace 的读取侧接口，供 Web 控制台和云平台
复用同一份 trace 数据。写入侧由 ``ReActAgentAdapter`` 通过 ``TraceStorePort``
完成，本模块只做读取和 HTTP DTO 转换，不产生任何领域副作用。

端点列表：
- ``GET /api/traces``：按时间倒序列出最近若干 session trace 摘要。
- ``GET /api/traces/{session_id}``：获取指定 session 的完整 trace（含全部 steps）。

当结构化追踪被配置关闭（``TRACE_ENABLED=false``）时，注入的
``TraceStorePort`` 为 ``None``：列表接口返回空数组，详情接口返回 404。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from common.container import inject
from domain.agent.ports import TraceStorePort
from domain.agent.trace_value_objects import SessionTrace

logger = logging.getLogger(__name__)

router = APIRouter(tags=["traces"])
TRACE_STORE_DEPENDENCY = Depends(inject(TraceStorePort))


def _summary_to_dict(trace: SessionTrace) -> dict[str, Any]:
    """将 session trace 摘要转换为响应字典（不含完整 steps）。

    Args:
        trace: 由 ``list_traces`` 返回的摘要对象，其 ``steps`` 为空、
            ``metadata`` 中含 ``step_count``。

    Returns:
        含 ``session_id``、``started_at_epoch``、``step_count`` 的字典。
    """
    step_count = trace.metadata.get("step_count", len(trace.steps))
    return {
        "session_id": trace.session_id,
        "started_at_epoch": trace.started_at_epoch,
        "step_count": step_count,
    }


def _trace_to_dict(trace: SessionTrace) -> dict[str, Any]:
    """将完整 session trace 转换为响应字典（含全部 steps）。

    Args:
        trace: 由 ``get_session_trace`` 返回的完整聚合对象。

    Returns:
        含会话元数据与逐步 trace 记录的字典，steps 保留各自的 ``kind`` 字段。
    """
    return {
        "session_id": trace.session_id,
        "started_at_epoch": trace.started_at_epoch,
        "step_count": len(trace.steps),
        "steps": [asdict(step) for step in trace.steps],
        "metadata": trace.metadata,
    }


@router.get("/api/traces")
async def list_traces(
    limit: int = Query(default=20, ge=1, le=200),
    trace_store: TraceStorePort | None = TRACE_STORE_DEPENDENCY,
) -> JSONResponse:
    """按时间倒序列出最近的 session trace 摘要。

    Args:
        limit: 最大返回条数，取值范围 [1, 200]，默认 20。
        trace_store: 由 DI 容器注入的 trace 存储端口；追踪关闭时为 None。

    Returns:
        ``{"object": "list", "data": [...]}`` 形式的 JSON 响应；
        追踪关闭时 ``data`` 为空数组。
    """
    if trace_store is None:
        return JSONResponse(content={"object": "list", "data": []})

    traces = await trace_store.list_traces(limit=limit)
    data = [_summary_to_dict(t) for t in traces]
    return JSONResponse(content={"object": "list", "data": data})


@router.get("/api/traces/{session_id}")
async def get_trace(
    session_id: str,
    trace_store: TraceStorePort | None = TRACE_STORE_DEPENDENCY,
) -> JSONResponse:
    """获取指定 session 的完整 trace。

    Args:
        session_id: 会话唯一标识符。
        trace_store: 由 DI 容器注入的 trace 存储端口；追踪关闭时为 None。

    Returns:
        含全部 steps 的 JSON 响应；trace 不存在或追踪关闭时返回 HTTP 404。
    """
    if trace_store is None:
        return JSONResponse(
            content={"detail": f"trace not found: {session_id}"},
            status_code=404,
        )

    trace = await trace_store.get_session_trace(session_id)
    if trace is None:
        return JSONResponse(
            content={"detail": f"trace not found: {session_id}"},
            status_code=404,
        )
    return JSONResponse(content=_trace_to_dict(trace))
