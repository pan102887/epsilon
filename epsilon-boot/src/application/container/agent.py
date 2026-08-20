"""Agent 相关组合根注册。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from common.container_models import Scope
from domain.agent.ports import (
    AgentGuardrailPolicyPort,
    AgentPort,
    AgentRegistryPort,
    ApprovalPolicyPort,
    DelegationPort,
)
from domain.agent.tools import ToolRegistry
from domain.task.ports import TaskAgentPort


def register_agent_components(
    container: Any,
    *,
    create_approval_policy: Callable[[], ApprovalPolicyPort],
    create_guardrail_policy: Callable[[], AgentGuardrailPolicyPort],
    create_agent: Callable[[], Awaitable[AgentPort]],
    create_agent_registry: Callable[[], AgentRegistryPort],
    create_delegation_adapter: Callable[[], Awaitable[DelegationPort]],
    register_delegate_tool: Callable[[], Awaitable[None]],
    noop_cleanup: Callable[[], Awaitable[None]],
) -> None:
    """注册 Agent、Delegation 与审批策略组件。"""
    container.register(ApprovalPolicyPort, create_approval_policy, Scope.SINGLETON)
    container.register(AgentGuardrailPolicyPort, create_guardrail_policy, Scope.SINGLETON)
    container.register(AgentPort, create_agent, Scope.SINGLETON)
    container.register(AgentRegistryPort, create_agent_registry, Scope.SINGLETON)
    container.register(DelegationPort, create_delegation_adapter, Scope.SINGLETON)
    container.register_async_resource(
        "delegate_tool_registration",
        register_delegate_tool,
        noop_cleanup,
    )

    # 仅表达延迟注册依赖关系，避免导入被优化工具误判为未用。
    _ = (ToolRegistry, TaskAgentPort)
