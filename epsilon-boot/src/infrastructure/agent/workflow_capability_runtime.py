"""Workflow 角色能力运行时 enforcement helper。

本模块供 ReAct 工具执行、delegation 与 handoff 适配器在真实动作发生前
复用同一套 role capability 判定逻辑。它只依赖领域 ContextVar、领域值对象
和 Run 事件端口，不导入 application 或具体存储实现。
"""

from __future__ import annotations

from typing import Any

from domain.run.ports import RunEventStorePort
from domain.run.runtime_context import get_run_execution_context
from domain.run.value_objects import RunEventType
from domain.run.workflow import (
    WorkflowCapabilityAction,
    WorkflowCapabilityCheck,
    WorkflowCapabilityDecision,
    evaluate_role_capability,
)
from domain.run.workflow_context import get_workflow_collaboration_context


async def enforce_workflow_capability_before_action(
    *,
    event_store: RunEventStorePort | None,
    action: WorkflowCapabilityAction,
    target: str | None,
) -> WorkflowCapabilityDecision | None:
    """在真实运行时动作前执行 workflow role capability 判定。

    Args:
        event_store: Run 事件端口；存在时拒绝会写入 ``ROLE_CAPABILITY_REJECTED``。
        action: 待执行动作类型，例如工具、委派、handoff 或 child run。
        target: 动作目标；工具为工具名，委派或 handoff 为目标 Agent 名称。

    Returns:
        ``None`` 表示当前非 workflow Run、治理未开启或动作被允许；返回
        ``WorkflowCapabilityDecision`` 表示动作被拒绝，调用方必须停止真实执行。
    """

    workflow_context = get_workflow_collaboration_context()
    run_context = get_run_execution_context()
    if workflow_context is None or run_context is None:
        return None
    if not workflow_context.role_capability_enabled:
        return None

    decision = evaluate_role_capability(
        roles=workflow_context.roles,
        check=WorkflowCapabilityCheck(
            action=action,
            role=workflow_context.source_role,
            target=target,
        ),
    )
    if decision.allowed:
        return None

    payload = workflow_capability_rejected_payload(decision)
    payload.update(
        {
            "run_id": run_context.run_id,
            "workflow_name": workflow_context.workflow_name,
            "phase": workflow_context.phase.value if workflow_context.phase is not None else None,
        }
    )
    if event_store is not None:
        await event_store.append_event(
            run_context.run_id,
            RunEventType.ROLE_CAPABILITY_REJECTED,
            payload,
        )
    return decision


def workflow_capability_rejected_payload(
    decision: WorkflowCapabilityDecision,
) -> dict[str, Any]:
    """把 role capability 拒绝结果转换为 JSON-safe 事件载荷。"""

    return {
        "active_role": decision.role,
        "action": decision.action.value,
        "target": decision.target,
        "reason": decision.reason,
        "workflow_run_state": {
            "phase_error_summary": {
                "terminal_reason": "role_capability_rejected",
                "action": decision.action.value,
                "target": decision.target,
                "reason": decision.reason,
            }
        },
    }
