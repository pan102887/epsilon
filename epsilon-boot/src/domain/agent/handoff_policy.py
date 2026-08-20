"""Handoff 前置限制的领域判定策略。

本模块只承载 handoff 动作发生前的深度与 workflow handoff 次数判定。
调用方负责读取运行时上下文、执行委派端口、构造工具返回值以及记录协作事件。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.task.policy import DelegationDepthPolicy

if TYPE_CHECKING:
    from domain.run.workflow_context import WorkflowCollaborationContext


@dataclass(frozen=True)
class HandoffDecision:
    """Handoff 限制判定结果。"""

    allowed: bool
    next_depth: int
    effective_max_depth: int
    reason: str | None = None


def decide_handoff(
    *,
    current_depth: int,
    max_delegation_depth: int,
    workflow_context: WorkflowCollaborationContext | None,
) -> HandoffDecision:
    """判定 handoff 是否满足深度与 workflow handoff 次数限制。

    Args:
        current_depth: 当前 Agent 所处委派深度。
        max_delegation_depth: Agent 配置侧允许的最大委派深度。
        workflow_context: 可选的 workflow 协作上下文；未提供时只使用配置侧深度上限。

    Returns:
        HandoffDecision：包含是否允许、下一层深度、有效深度上限以及拒绝原因。
    """

    next_depth = current_depth + 1
    effective_max_depth = max_delegation_depth
    if workflow_context is not None:
        effective_max_depth = min(
            effective_max_depth,
            workflow_context.limit.max_recursion_depth,
        )

    if DelegationDepthPolicy.exceeds_for_next_depth(current_depth, effective_max_depth):
        return HandoffDecision(
            allowed=False,
            next_depth=next_depth,
            effective_max_depth=effective_max_depth,
            reason="handoff_depth_exceeded",
        )

    if workflow_context is not None:
        next_handoff_count = workflow_context.handoff_count + 1
        max_handoff_count = workflow_context.limit.max_handoff_count
        if next_handoff_count > max_handoff_count:
            return HandoffDecision(
                allowed=False,
                next_depth=next_depth,
                effective_max_depth=effective_max_depth,
                reason=f"handoff_count_exceeded:{next_handoff_count}>{max_handoff_count}",
            )

    return HandoffDecision(
        allowed=True,
        next_depth=next_depth,
        effective_max_depth=effective_max_depth,
    )
