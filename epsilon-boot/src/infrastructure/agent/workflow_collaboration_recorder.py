"""Workflow 协作事件记录 helper。

本模块供 delegation / handoff 工具在不改变领域端口签名的情况下记录
workflow 协作步骤与限制命中事件。无 workflow context 或 event store 时
保持空操作，避免影响非 Run Agent loop。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any
from uuid import uuid4

from domain.run.ports import RunEventStorePort
from domain.run.runtime_context import (
    get_run_execution_context,
    set_run_execution_context,
)
from domain.run.value_objects import RunEventType
from domain.run.workflow import (
    CollaborationAction,
    CollaborationStepTraceLink,
    canonicalize_collaboration_summary,
)
from domain.run.workflow_context import get_workflow_collaboration_context
from infrastructure.run.workflow_serialization import (
    collaboration_step_trace_link_to_dict,
)


async def record_collaboration_step(
    *,
    event_store: RunEventStorePort | None,
    action: CollaborationAction,
    target_agent: str | None,
    task_summary: str,
    result_summary: str | None,
    target_role: str | None = None,
    depth: int | None = None,
    collaboration_summary: dict[str, Any] | None = None,
    recent_limit: int = 5,
) -> dict[str, Any]:
    """记录一次 workflow 协作步骤并返回更新后的摘要。"""

    context = get_workflow_collaboration_context()
    if context is None:
        return dict(collaboration_summary or {})

    step = CollaborationStepTraceLink(
        link_id=f"collab_{uuid4().hex}",
        run_id=context.run_id,
        phase=context.phase,
        source_role=context.source_role,
        target_role=target_role,
        target_agent=target_agent,
        action=action,
        task_summary=_safe_text(task_summary),
        result_summary=_safe_text(result_summary),
        depth=depth if depth is not None else context.depth,
        created_at=datetime.now(UTC),
    )
    payload = collaboration_step_trace_link_to_dict(step)
    if event_store is not None:
        await event_store.append_event(
            context.run_id,
            RunEventType.COLLABORATION_STEP_RECORDED,
            payload,
        )
    return _updated_summary(
        collaboration_summary,
        step_payload=payload,
        action=action,
        depth=step.depth,
        recent_limit=recent_limit,
    )


def summarize_workflow_handoff_state(
    *,
    workflow_run_state: dict[str, Any] | None,
    collaboration_summary: dict[str, Any] | None = None,
    recent_limit: int = 5,
) -> dict[str, Any]:
    """把 workflow_run_state.handoff_state 映射为 canonical collaboration summary。

    本函数只消费后端已经判定并持久化的 handoff_state，不重新计算 workflow
    策略或限额；展示层可同时读取 workflow_run_state 与 latest_steps。
    """

    if not isinstance(workflow_run_state, dict):
        return _base_summary(collaboration_summary)
    handoff_state = workflow_run_state.get("handoff_state")
    if not isinstance(handoff_state, dict):
        return _base_summary(collaboration_summary)
    payload = {
        "link_id": f"handoff_{uuid4().hex}",
        "run_id": workflow_run_state.get("run_id"),
        "phase": workflow_run_state.get("current_phase"),
        "source_role": handoff_state.get("source_role"),
        "target_role": handoff_state.get("target_role"),
        "target_agent": handoff_state.get("target_agent"),
        "action": CollaborationAction.HANDOFF.value,
        "task_summary": _safe_text(handoff_state.get("reason")) or "workflow handoff",
        "result_summary": _safe_text(handoff_state.get("status")),
        "depth": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    return _updated_summary(
        collaboration_summary,
        step_payload=_json_safe(payload),
        action=CollaborationAction.HANDOFF,
        depth=0,
        recent_limit=recent_limit,
    )


async def record_workflow_handoff(
    *,
    event_store: RunEventStorePort | None,
    target_agent: str | None,
    reason: str,
    target_role: str | None = None,
    workflow_run_state: dict[str, Any] | None = None,
    collaboration_summary: dict[str, Any] | None = None,
    recent_limit: int = 5,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """记录真实 handoff_to_agent 成功后的 workflow 级状态与事件。

    本函数只在 RunExecutionContext 与 WorkflowCollaborationContext 同时存在时
    生效；非 Run 路径或未启用 workflow 的普通 Agent handoff 保持空操作。
    """

    context = get_workflow_collaboration_context()
    run_context = get_run_execution_context()
    if context is None or run_context is None:
        return workflow_run_state, collaboration_summary

    source_state = workflow_run_state or run_context.workflow_run_state or {}
    source_role = context.source_role
    resolved_target_role = target_role or _target_role_from_agent(
        target_agent=target_agent,
        roles=context.roles,
    )
    handoff_state = {
        "status": "completed",
        "source_role": source_role,
        "target_role": resolved_target_role,
        "target_agent": target_agent,
        "reason": _safe_text(reason) or "handoff_to_agent",
    }
    updated_state = {
        **source_state,
        "run_id": run_context.run_id,
        "workflow_name": context.workflow_name or source_state.get("workflow_name"),
        "current_phase": (
            context.phase.value if context.phase is not None else source_state.get("current_phase")
        ),
        "active_role": resolved_target_role or source_state.get("active_role") or source_role,
        "handoff_state": handoff_state,
    }
    payload = {
        "workflow_name": updated_state.get("workflow_name"),
        "phase": updated_state.get("current_phase"),
        "source_role": source_role,
        "target_role": resolved_target_role,
        "target_agent": target_agent,
        "reason": handoff_state["reason"],
        "workflow_run_state": updated_state,
    }
    if event_store is not None:
        await event_store.append_event(
            run_context.run_id,
            RunEventType.WORKFLOW_HANDOFF_RECORDED,
            _json_safe(payload),
        )

    updated_summary = summarize_workflow_handoff_state(
        workflow_run_state=updated_state,
        collaboration_summary=collaboration_summary or run_context.collaboration_summary,
        recent_limit=recent_limit,
    )
    set_run_execution_context(
        replace(
            run_context,
            workflow_run_state=_json_safe(updated_state),
            collaboration_summary=_json_safe(updated_summary),
        )
    )
    return _json_safe(updated_state), _json_safe(updated_summary)


async def record_collaboration_limit_hit(
    *,
    event_store: RunEventStorePort | None,
    reason: str,
    action: CollaborationAction | None = None,
    target_agent: str | None = None,
    depth: int | None = None,
    collaboration_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """记录一次 workflow 协作限制命中并返回更新后的摘要。"""

    context = get_workflow_collaboration_context()
    if context is None:
        return dict(collaboration_summary or {})

    payload = {
        "run_id": context.run_id,
        "workflow_name": context.workflow_name,
        "phase": context.phase.value if context.phase is not None else None,
        "source_role": context.source_role,
        "action": action.value if action is not None else None,
        "target_agent": target_agent,
        "depth": depth if depth is not None else context.depth,
        "reason": _safe_text(reason),
    }
    if event_store is not None:
        await event_store.append_event(
            context.run_id,
            RunEventType.COLLABORATION_LIMIT_HIT,
            _json_safe(payload),
        )

    summary = _base_summary(collaboration_summary)
    summary["limit_hit_reason"] = _safe_text(reason)
    summary["max_depth_seen"] = max(
        int(summary.get("max_depth_seen", 0) or 0),
        int(payload["depth"] or 0),
    )
    return summary


def _updated_summary(
    collaboration_summary: dict[str, Any] | None,
    *,
    step_payload: dict[str, Any],
    action: CollaborationAction,
    depth: int,
    recent_limit: int,
) -> dict[str, Any]:
    """更新 collaboration summary 并裁剪 latest_steps。"""

    summary = _base_summary(collaboration_summary)
    latest_steps = [item for item in summary.get("latest_steps", []) if isinstance(item, dict)]
    latest_steps.append(step_payload)
    keep = max(int(recent_limit), 1)
    summary["latest_steps"] = latest_steps[-keep:]
    if action is CollaborationAction.DELEGATION:
        summary["delegation_count"] = int(summary.get("delegation_count", 0) or 0) + 1
    if action is CollaborationAction.HANDOFF:
        summary["handoff_count"] = int(summary.get("handoff_count", 0) or 0) + 1
    summary["max_depth_seen"] = max(
        int(summary.get("max_depth_seen", 0) or 0),
        depth,
    )
    return summary


def _target_role_from_agent(*, target_agent: str | None, roles: tuple[Any, ...]) -> str | None:
    """根据目标 Agent 名称反查 workflow 角色；无法确定时返回 None。"""

    if not target_agent:
        return None
    for role in roles:
        agent_names = getattr(role, "agent_names", ())
        if target_agent in agent_names:
            value = getattr(role, "role", None)
            return str(value) if value is not None else None
    return None


def _base_summary(collaboration_summary: dict[str, Any] | None) -> dict[str, Any]:
    """返回只包含规范 ``latest_steps`` 写路径的 summary 副本。"""

    summary = canonicalize_collaboration_summary(collaboration_summary) or {}
    summary.setdefault("latest_steps", [])
    summary.setdefault("child_links", [])
    summary.setdefault("delegation_count", 0)
    summary.setdefault("handoff_count", 0)
    summary.setdefault("max_depth_seen", 0)
    summary.setdefault("limit_hit_reason", None)
    summary.pop("recent_steps", None)
    return summary


def _safe_text(value: Any, *, max_length: int = 200) -> str | None:
    """返回安全短文本。"""

    if value is None:
        return None
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _json_safe(value: Any) -> Any:
    """递归转换 JSON-safe 值。"""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (StrEnum, Enum)):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)
