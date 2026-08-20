"""Agent Handoff（控制转移）工具模块。

本模块提供 ``HandoffToAgentTool``，继承 ``Tool`` ABC，对应 OpenAI Agents SDK
的 Handoff 概念：当前 Agent 通过该工具把对话控制权完全转移给指定命名 Agent，
目标 Agent 的最终回复将直接成为父 Agent 的最终回复，**不再回灌为 ToolMessage**
让父 Agent 继续推理。

控制流刻画：

1. LLM 调用 ``handoff_to_agent``。
2. ``execute(...)`` 从 :func:`infrastructure.agent.handoff_context.get_parent_context`
   读取父 ``ConversationContext`` 的消息快照。
3. 调用 ``DelegationPort.handoff(agent_name, snapshot, depth, max_depth)``，
   目标 Agent 独立执行 ReAct Loop 至完成。
4. **抛出** ``HandoffPerformed(target_agent, content, usage, model)`` 信号异常。
5. ``ReActAgentAdapter._execute_tool_call`` 捕获该信号，把 ``signal.content``
   作为 ``ToolMessage`` 内容、``metadata["handoff_target"] = signal.target_agent``，
   ``_iter_rounds`` 在下一轮入口检测到 handoff 标记后立即终止 Agent Loop。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from domain.agent.exceptions import (
    AgentNotFoundError,
    DelegationDepthExceededError,
    HandoffPerformed,
)
from domain.agent.handoff_policy import decide_handoff
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.workflow import CollaborationAction
from domain.run.workflow_context import get_workflow_collaboration_context
from infrastructure.agent.handoff_context import get_parent_context
from infrastructure.agent.workflow_collaboration_recorder import (
    record_collaboration_limit_hit,
    record_collaboration_step,
    record_workflow_handoff,
)

if TYPE_CHECKING:
    from domain.agent.ports import AgentRegistryPort, DelegationPort
    from domain.run.ports import RunEventStorePort

logger = logging.getLogger(__name__)


class HandoffToAgentTool(Tool):
    """Agent Handoff 工具。

    LLM 通过该工具触发"完全控制转移"语义：与 ``DelegateToAgentTool``（任务委派、
    结果回灌）形成正交对照。本工具不暴露 ``input_data`` 字段——handoff 的语义是
    "原样转交父侧对话上下文"，无需追加结构化输入。

    Attributes:
        _agent_registry: Agent 注册表，用于生成动态工具描述（不直接查询 Agent）。
        _delegation: 委派端口，调用 ``handoff(...)`` 完成控制转移。
        _current_delegation_depth: 当前 Agent 执行所处的委派深度。
        _max_delegation_depth: 最大允许委派深度。
    """

    def __init__(
        self,
        agent_registry: AgentRegistryPort,
        delegation: DelegationPort,
        current_delegation_depth: int = 0,
        max_delegation_depth: int = 3,
        event_store: RunEventStorePort | None = None,
        recent_collaboration_summary_limit: int = 5,
    ) -> None:
        """初始化 Handoff 工具。

        Args:
            agent_registry: Agent 注册表端口实例，用于生成动态工具描述。
            delegation: 委派端口实例，用于执行 ``handoff(...)``。
            current_delegation_depth: 当前委派深度，默认 0（根 Agent）。
            max_delegation_depth: 最大允许委派深度，默认 3。
        """
        self._agent_registry = agent_registry
        self._delegation = delegation
        self._current_delegation_depth = current_delegation_depth
        self._max_delegation_depth = max_delegation_depth
        self._event_store = event_store
        self._recent_collaboration_summary_limit = recent_collaboration_summary_limit
        self._collaboration_summary: dict[str, Any] = {}

    @property
    def name(self) -> str:
        """工具唯一名称。"""
        return "handoff_to_agent"

    @property
    def description(self) -> str:
        """工具功能描述，动态包含已注册 Agent 列表。"""
        registered = self._agent_registry.list_names()
        if registered:
            agent_list = ", ".join(registered)
            return (
                "Transfer control of the conversation to a named agent. Use this "
                "only when the target agent should own the next response and the "
                "current agent should stop reasoning after the handoff. Available "
                f"agents: [{agent_list}]"
            )
        return (
            "Transfer control of the conversation to a named agent. "
            "No agents are currently available."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """符合 JSON Schema 规范的参数描述字典。"""
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": (
                        "Name of the target agent that should take over the conversation."
                    ),
                },
            },
            "required": ["agent_name"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """触发 Handoff 控制转移。

        - 成功（目标 Agent 自然终止）→ 抛出 ``HandoffPerformed`` 信号异常。
        - 目标 Agent 未注册 → 返回错误字符串（让 LLM 自我纠正，不抛异常）。
        - 委派深度超限 → 返回错误字符串。
        - 父上下文 ContextVar 未设置 → 返回错误字符串（Tool 在非 Agent Loop
          场景下被直接调用时的防御）。

        Args:
            **kwargs: 工具参数，包含 ``agent_name``。

        Returns:
            :class:`ToolExecutionResult`（仅错误情形返回）：``content`` 为错误描述
            字符串，``metadata`` 含 ``target_agent`` (str) 与 ``success`` (bool，
            错误路径恒为 ``False``)。成功情形不返回，通过抛 ``HandoffPerformed`` 终止。

        Raises:
            HandoffPerformed: 控制转移成功完成的信号，携带目标 Agent 最终回复。
        """
        agent_name: str = kwargs["agent_name"]

        # 错误返回路径统一使用该 metadata 构造器：handoff 未真正发生，success 恒为 False。
        def _failure(content: str) -> ToolExecutionResult:
            return ToolExecutionResult(
                content=content,
                metadata={"target_agent": agent_name, "success": False},
            )

        # 1. 校验委派深度（与 DelegateToAgentTool 一致语义，但提前在工具层
        #    返回错误字符串而不抛 DelegationDepthExceededError，避免 LLM 拿到
        #    BizException 文本，更利于自我纠正）。
        workflow_context = get_workflow_collaboration_context()
        decision = decide_handoff(
            current_depth=self._current_delegation_depth,
            max_delegation_depth=self._max_delegation_depth,
            workflow_context=workflow_context,
        )
        next_depth = decision.next_depth
        effective_max_depth = decision.effective_max_depth
        if decision.reason == "handoff_depth_exceeded":
            logger.warning(
                "Handoff 深度超限: 当前深度 %d，最大深度 %d，目标 Agent '%s'",
                self._current_delegation_depth,
                effective_max_depth,
                agent_name,
            )
            self._collaboration_summary = await record_collaboration_limit_hit(
                event_store=self._event_store,
                reason="handoff_depth_exceeded",
                action=CollaborationAction.HANDOFF,
                target_agent=agent_name,
                depth=next_depth,
                collaboration_summary=self._collaboration_summary,
            )
            return _failure(
                f"无法 handoff 给 '{agent_name}': 委派深度超限 "
                f"({self._current_delegation_depth} → {next_depth} > {effective_max_depth})"
            )
        if decision.reason is not None and decision.reason.startswith("handoff_count_exceeded:"):
            reason = decision.reason
            self._collaboration_summary = await record_collaboration_limit_hit(
                event_store=self._event_store,
                reason=reason,
                action=CollaborationAction.HANDOFF,
                target_agent=agent_name,
                depth=next_depth,
                collaboration_summary=self._collaboration_summary,
            )
            return _failure(f"Cannot hand off to '{agent_name}': {reason}")

        # 2. 读取父 ConversationContext 消息快照
        parent_ctx = get_parent_context()
        if parent_ctx is None:
            logger.warning("Handoff 工具在非 Agent Loop 场景被调用，父上下文未设置")
            return _failure(
                "Handoff is unavailable: the current execution is not inside "
                "an Agent Loop context."
            )

        snapshot = list(parent_ctx.get_messages())  # 浅拷贝消息列表

        # 3. 调用 DelegationPort.handoff
        try:
            handoff_result = await self._delegation.handoff(
                agent_name,
                snapshot,
                delegation_depth=next_depth,
                max_delegation_depth=effective_max_depth,
            )
        except AgentNotFoundError as exc:
            return _failure(str(exc))
        except DelegationDepthExceededError as exc:
            return _failure(str(exc))
        except Exception as exc:
            logger.exception("Handoff 执行失败 agent=%s", agent_name)
            return _failure(f"Handoff 执行失败: {exc}")

        if not handoff_result.success:
            self._collaboration_summary = await record_collaboration_step(
                event_store=self._event_store,
                action=CollaborationAction.HANDOFF,
                target_agent=agent_name,
                task_summary="handoff",
                result_summary=handoff_result.content,
                depth=next_depth,
                collaboration_summary=self._collaboration_summary,
                recent_limit=self._recent_collaboration_summary_limit,
            )
            # 目标 Agent 内部失败（例如 HITL 中断 / 工具失败）→ 作为错误字符串
            # 回灌给父 LLM，与 DelegateToAgentTool 失败语义一致。
            return _failure(f"Handoff 给 '{agent_name}' 失败: {handoff_result.content}")

        # 4. 成功 → 抛出信号异常，由 ReActAgentAdapter 捕获并终止父 Agent Loop
        self._collaboration_summary = await record_collaboration_step(
            event_store=self._event_store,
            action=CollaborationAction.HANDOFF,
            target_agent=agent_name,
            task_summary="handoff",
            result_summary=handoff_result.content,
            depth=next_depth,
            collaboration_summary=self._collaboration_summary,
            recent_limit=self._recent_collaboration_summary_limit,
        )
        _workflow_state, self._collaboration_summary = await record_workflow_handoff(
            event_store=self._event_store,
            target_agent=handoff_result.target_agent,
            reason="handoff_to_agent",
            collaboration_summary=self._collaboration_summary,
            recent_limit=self._recent_collaboration_summary_limit,
        )
        raise HandoffPerformed(
            target_agent=handoff_result.target_agent,
            content=handoff_result.content,
            usage=handoff_result.usage,
            model=handoff_result.model,
        )
