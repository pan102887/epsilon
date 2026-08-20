"""Run 工作流协作上下文模块。

本模块使用 ``ContextVar`` 保存当前 Run 执行窗口内的 workflow 协作治理
上下文，供 delegation / handoff 工具在不改变既有端口签名的情况下读取
当前 phase、role 和协作限制。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from domain.run.workflow import AgentRoleCapability, CollaborationLimit, WorkflowPhase


@dataclass(frozen=True)
class WorkflowCollaborationContext:
    """当前 Run 的协作治理上下文。"""

    run_id: str
    workflow_name: str | None
    phase: WorkflowPhase | None
    source_role: str | None
    limit: CollaborationLimit
    depth: int
    handoff_count: int
    delegation_count: int
    role_capability_enabled: bool = False
    roles: tuple[AgentRoleCapability, ...] = ()


_WORKFLOW_COLLABORATION_CONTEXT: ContextVar[WorkflowCollaborationContext | None] = ContextVar(
    "workflow_collaboration_context", default=None
)


def set_workflow_collaboration_context(
    value: WorkflowCollaborationContext,
) -> Token[WorkflowCollaborationContext | None]:
    """设置当前执行窗口的 workflow 协作上下文并返回 reset token。"""

    return _WORKFLOW_COLLABORATION_CONTEXT.set(value)


def reset_workflow_collaboration_context(
    token: Token[WorkflowCollaborationContext | None],
) -> None:
    """使用 set 返回的 token 恢复上一个 workflow 协作上下文。"""

    _WORKFLOW_COLLABORATION_CONTEXT.reset(token)


def get_workflow_collaboration_context() -> WorkflowCollaborationContext | None:
    """读取当前执行窗口的 workflow 协作上下文；未设置时返回 None。"""

    return _WORKFLOW_COLLABORATION_CONTEXT.get()
