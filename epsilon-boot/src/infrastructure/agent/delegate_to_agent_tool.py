"""Agent 委派工具模块。

本模块提供 DelegateToAgentTool，继承 Tool ABC，允许当前 Agent 将子任务
委派给其他命名 Agent 执行。通过 DelegationPort 领域层抽象完成实际委派，
工具本身仅负责深度校验和结果路由，不再直接依赖 TaskAgentPort。

核心流程：
1. 校验委派深度是否超限
2. 调用 DelegationPort.delegate() 执行委派
3. 根据 DelegationResult.success 返回结果内容或错误信息
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from domain.agent.exceptions import DelegationDepthExceededError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.workflow import CollaborationAction
from domain.run.workflow_context import get_workflow_collaboration_context
from domain.task.policy import DelegationDepthPolicy
from infrastructure.agent.workflow_collaboration_recorder import (
    record_collaboration_limit_hit,
    record_collaboration_step,
)

if TYPE_CHECKING:
    from domain.agent.ports import AgentRegistryPort, DelegationPort
from domain.run.ports import RunEventAppenderPort

logger = logging.getLogger(__name__)


class DelegateToAgentTool(Tool):
    """Agent 委派工具，继承 Tool ABC。

    允许当前 Agent 将子任务委派给其他命名 Agent 执行。
    通过 DelegationPort 领域层抽象完成实际委派，工具本身仅负责
    深度校验和结果路由，不再感知 TaskAgentPort 等底层细节。

    Attributes:
        _agent_registry: Agent 注册表端口，用于生成动态工具描述
        _delegation: 委派能力端口，用于执行实际委派
        _current_delegation_depth: 当前 Agent 执行所处的委派深度
        _max_delegation_depth: 最大允许委派深度
    """

    def __init__(
        self,
        agent_registry: AgentRegistryPort,
        delegation: DelegationPort,
        current_delegation_depth: int = 0,
        max_delegation_depth: int = 3,
        event_store: RunEventAppenderPort | None = None,
        recent_collaboration_summary_limit: int = 5,
    ) -> None:
        """初始化委派工具。

        Args:
            agent_registry: Agent 注册表端口实例，用于生成动态工具描述
            delegation: 委派能力端口实例，用于执行实际委派
            current_delegation_depth: 当前委派深度，默认 0（根 Agent）
            max_delegation_depth: 最大允许委派深度，默认 3
        """
        self._agent_registry = agent_registry
        self._delegation = delegation
        self._current_delegation_depth = current_delegation_depth
        self._max_delegation_depth = max_delegation_depth
        self._event_store = event_store
        self._recent_collaboration_summary_limit = recent_collaboration_summary_limit
        self._collaboration_summary: dict[str, Any] = {}

    @property
    def max_delegation_depth(self) -> int:
        return self._max_delegation_depth

    @property
    def name(self) -> str:
        """工具唯一名称。"""
        return "delegate_to_agent"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """委派工具为中风险。"""
        return ToolRiskLevel.MEDIUM

    @property
    def description(self) -> str:
        """工具功能描述，动态包含已注册 Agent 列表信息。

        Returns:
            包含当前已注册 Agent 名称列表的描述字符串
        """
        registered = self._agent_registry.list_names()
        if registered:
            agent_list = ", ".join(registered)
            return (
                "Delegate a bounded subtask to a named agent and wait for its result. "
                "Use this when another agent has a better role, tool scope, or context "
                f"for a separable piece of work. Available agents: [{agent_list}]"
            )
        return "Delegate a bounded subtask to a named agent. No agents are currently available."

    @property
    def parameters(self) -> dict[str, Any]:
        """符合 JSON Schema 规范的参数描述字典。

        Returns:
            包含 agent_name、task_goal（必填）和 input_data（可选）的 schema
        """
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the target agent.",
                },
                "task_goal": {
                    "type": "string",
                    "description": "Clear, self-contained goal for the delegated subtask.",
                },
                "input_data": {
                    "type": "object",
                    "description": "Optional structured input data for the subtask.",
                },
            },
            "required": ["agent_name", "task_goal"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行委派逻辑。

        流程：
        1. 校验 delegation_depth + 1 是否超过 max_delegation_depth
        2. 调用 DelegationPort.delegate() 执行委派
        3. 根据 DelegationResult.success 返回结果内容或错误信息

        Args:
            **kwargs: 工具参数，包含 agent_name、task_goal、input_data（可选）

        Returns:
            :class:`ToolExecutionResult`，``content`` 为子任务执行结果字符串
            （失败时为格式化错误提示）；``metadata`` 含以下键：

            - ``target_agent`` (str): 目标 Agent 名称。
            - ``success`` (bool): 委派是否成功。

        Raises:
            DelegationDepthExceededError: 委派深度超限时抛出
        """
        agent_name: str = kwargs["agent_name"]
        task_goal: str = kwargs["task_goal"]
        input_data: dict[str, Any] = kwargs.get("input_data", {})

        # 1. 校验委派深度
        next_depth = self._current_delegation_depth + 1
        workflow_context = get_workflow_collaboration_context()
        effective_max_depth = self._max_delegation_depth
        if workflow_context is not None:
            effective_max_depth = min(
                effective_max_depth,
                workflow_context.limit.max_recursion_depth,
            )
        if DelegationDepthPolicy.exceeds_for_next_depth(
            self._current_delegation_depth, effective_max_depth
        ):
            logger.warning(
                "委派深度超限: 当前深度 %d，最大深度 %d，目标 Agent '%s'",
                self._current_delegation_depth,
                effective_max_depth,
                agent_name,
            )
            self._collaboration_summary = await record_collaboration_limit_hit(
                event_store=self._event_store,
                reason="delegation_depth_exceeded",
                action=CollaborationAction.DELEGATION,
                target_agent=agent_name,
                depth=next_depth,
                collaboration_summary=self._collaboration_summary,
            )
            raise DelegationDepthExceededError(
                current_depth=self._current_delegation_depth,
                max_depth=effective_max_depth,
                target_agent=agent_name,
            )

        # 2. 调用 DelegationPort.delegate() 执行委派
        result = await self._delegation.delegate(
            agent_name,
            task_goal,
            input_data,
            next_depth,
            effective_max_depth,
        )
        self._collaboration_summary = await record_collaboration_step(
            event_store=self._event_store,
            action=CollaborationAction.DELEGATION,
            target_agent=agent_name,
            task_summary=task_goal,
            result_summary=result.content,
            depth=next_depth,
            collaboration_summary=self._collaboration_summary,
            recent_limit=self._recent_collaboration_summary_limit,
        )

        # 3. 根据结果返回
        metadata: dict[str, Any] = {"target_agent": agent_name, "success": result.success}
        if result.success:
            return ToolExecutionResult(content=result.content, metadata=metadata)

        return ToolExecutionResult(
            content=f"子 Agent '{agent_name}' 执行失败: {result.content}",
            metadata=metadata,
        )
