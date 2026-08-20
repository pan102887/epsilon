"""委派适配器模块。

本模块提供 DelegationPort 协议的具体实现，桥接 AgentRegistryPort 和 TaskAgentPort，
将委派请求转换为 Task 执行并返回 DelegationResult。

核心流程：
1. 通过 AgentRegistryPort 查找目标 Agent 配置
2. 构造 Task 值对象（携带 tool_names、model、delegation_depth）
3. 调用 TaskAgentPort.execute(task) 执行子任务
4. 将 TaskResult 转换为 DelegationResult 返回

Spec A 扩展：

- :meth:`DelegationAdapter.delegate_parallel` — 多 Agent 并行扇出，错误隔离。
- :meth:`DelegationAdapter.handoff` — 完全控制转移；绕过 ``TaskAgentPort``
  直接调用 ``AgentPort.run``，避免 ``TaskAgentAdapter`` 在子上下文里追加
  ``add_user_message(task.goal)`` 污染父侧消息序列。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from domain.agent.exceptions import AgentNotFoundError, DelegationDepthExceededError
from domain.agent.value_objects import (
    AgentConfig,
    DelegationRequest,
    DelegationResult,
    HandoffResult,
)
from domain.chat.context import BaseMessage, ConversationContext
from domain.run.ports import RunEventStorePort
from domain.run.workflow import WorkflowCapabilityAction
from domain.task.policy import DelegationDepthPolicy
from domain.task.value_objects import Task, TaskStatus
from infrastructure.agent.workflow_capability_runtime import (
    enforce_workflow_capability_before_action,
)

if TYPE_CHECKING:
    from domain.agent.ports import AgentPort, AgentRegistryPort
    from domain.agent.tools import ToolRegistry
    from domain.model_access.ports import ModelRegistryPort
    from domain.task.ports import TaskAgentPort

logger = logging.getLogger(__name__)


_DEFAULT_HANDOFF_MAX_ROUNDS = 1_000_000
"""目标 Agent 在 handoff 路径下的默认 ``max_rounds``。

与长任务入口的“不限制轮次”哨兵对齐，避免 handoff 子 Agent 在第 10 轮暂停。"""


class DelegationAdapter:
    """DelegationPort 适配器，桥接 AgentRegistryPort 和 TaskAgentPort。

    该适配器承担 Agent 查找和 Task 构造职责，使 DelegateToAgentTool
    无需感知 TaskAgentPort 等底层细节。Spec A 扩展后还承担 handoff 路径下
    "组 ConversationContext + 调 AgentPort.run + 翻译 HandoffResult" 三步。

    Attributes:
        _agent_registry: Agent 注册表端口，用于查找目标 Agent 配置。
        _task_agent: 任务型 Agent 端口，用于 ``delegate(...)`` 子任务执行。
        _model_registry: 模型注册中心，用于 ``handoff(...)`` 解析子 Agent 模型。
        _agent_provider: 异步工厂，懒解析 :class:`AgentPort`，规避
            ``DelegationPort → AgentPort → ToolRegistry → DelegateToAgentTool
            → DelegationPort`` 循环依赖。
        _tool_registry_provider: 异步工厂，懒解析 :class:`ToolRegistry`。
        _handoff_max_rounds: handoff 路径子 Agent ``AgentConfig.max_rounds``，
            默认使用长任务不限制轮次哨兵。
    """

    def __init__(
        self,
        agent_registry: AgentRegistryPort,
        task_agent: TaskAgentPort,
        model_registry: ModelRegistryPort | None = None,
        agent_provider: Callable[[], Awaitable[AgentPort]] | None = None,
        tool_registry_provider: Callable[[], Awaitable[ToolRegistry]] | None = None,
        handoff_max_rounds: int = _DEFAULT_HANDOFF_MAX_ROUNDS,
        event_store: RunEventStorePort | None = None,
    ) -> None:
        """初始化委派适配器。

        Args:
            agent_registry: Agent 注册表端口实例。
            task_agent: 任务型 Agent 端口实例。
            model_registry: 模型注册中心；仅 ``handoff(...)`` 需要。
                ``None`` 时调用 ``handoff(...)`` 将抛 ``RuntimeError``。
            agent_provider: 异步工厂返回 ``AgentPort``；仅 ``handoff(...)`` 需要。
            tool_registry_provider: 异步工厂返回 ``ToolRegistry``；仅 ``handoff(...)`` 需要。
            handoff_max_rounds: handoff 路径子 Agent 默认 ``max_rounds``。
            event_store: Run 事件端口，用于 role capability 拒绝事件。
        """
        self._agent_registry = agent_registry
        self._task_agent = task_agent
        self._model_registry = model_registry
        self._agent_provider = agent_provider
        self._tool_registry_provider = tool_registry_provider
        self._handoff_max_rounds = handoff_max_rounds
        self._event_store = event_store

    async def delegate(
        self,
        agent_name: str,
        task_goal: str,
        input_data: dict[str, Any] | None = None,
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> DelegationResult:
        """将子任务委派给指定命名 Agent 执行。

        通过 agent_name 定位目标 Agent，将 task_goal 和可选的 input_data
        封装为 Task 交由 TaskAgentPort 执行，并将 TaskResult 转换为
        DelegationResult 返回。

        Args:
            agent_name: 目标 Agent 唯一标识名称。
            task_goal: 子任务目标描述。
            input_data: 可选的附加输入数据字典，默认 None（内部转空 dict）。
            delegation_depth: 当前委派深度。
            max_delegation_depth: 最大允许委派深度。

        Returns:
            ``DelegationResult``。

        Raises:
            AgentNotFoundError: 当 ``agent_name`` 未注册。
        """
        capability_decision = await enforce_workflow_capability_before_action(
            event_store=self._event_store,
            action=WorkflowCapabilityAction.DELEGATION,
            target=agent_name,
        )
        if capability_decision is not None:
            return DelegationResult(
                content=f"role capability rejected: {capability_decision.reason}",
                success=False,
            )

        # 1. 查找目标 Agent 配置
        config = self._agent_registry.get(agent_name)
        if config is None:
            raise AgentNotFoundError(
                agent_name=agent_name,
                registered_names=self._agent_registry.list_names(),
            )

        # 2. 构造 Task
        task = Task(
            goal=task_goal,
            input_data=input_data or {},
            tool_names=config.tool_names,
            model=config.model,
            delegation_depth=delegation_depth,
            session_id=None,
        )

        # 3. 执行子任务
        result = await self._task_agent.execute(task)

        # 4. 转换为 DelegationResult
        return DelegationResult(
            content=result.content,
            success=result.status == TaskStatus.SUCCESS,
        )

    async def delegate_parallel(
        self,
        requests: list[DelegationRequest],
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> list[DelegationResult]:
        """并行将多个子任务委派给指定命名 Agent 执行（错误隔离）。

        每条子委派独立 try/except 包裹后通过 ``asyncio.gather`` 并发执行；
        单条失败（``AgentNotFoundError`` / 子任务异常 / 深度超限）只产生
        对应位置的 ``DelegationResult(success=False, content=<错误描述>)``，
        不抛异常，不中断其余条目。

        Args:
            requests: 委派请求列表。
            delegation_depth: 当前委派深度（统一应用于所有子条目）。
            max_delegation_depth: 最大允许委派深度。

        Returns:
            ``list[DelegationResult]``，长度与 ``requests`` 一致，顺序对应。
        """
        if not requests:
            return []

        async def _one(req: DelegationRequest) -> DelegationResult:
            # 调用方（``DelegateParallelTool.execute``）已将 ``next_depth = current+1``
            # 作为 ``delegation_depth`` 传入，本入参语义为"子 Agent 实际执行深度"。
            # 因此本处直接以 ``delegation_depth > max_delegation_depth`` 判定超限，
            # 与单条 ``DelegateToAgentTool`` 链行为对齐——子 Agent 在 max_depth 时仍可执行，
            # 仅当下一层（max_depth+1）才阻断。
            if DelegationDepthPolicy.exceeds_for_current_depth(
                delegation_depth, max_delegation_depth
            ):
                return DelegationResult(
                    content=(
                        f"委派深度超限: 当前深度 {delegation_depth}, "
                        f"最大深度 {max_delegation_depth}, 目标 Agent '{req.agent_name}'"
                    ),
                    success=False,
                )
            try:
                return await self.delegate(
                    req.agent_name,
                    req.task_goal,
                    req.input_data,
                    delegation_depth=delegation_depth,
                    max_delegation_depth=max_delegation_depth,
                )
            except AgentNotFoundError as exc:
                return DelegationResult(content=str(exc), success=False)
            except DelegationDepthExceededError as exc:
                return DelegationResult(content=str(exc), success=False)
            except Exception as exc:
                logger.warning(
                    "并行委派单条失败 agent=%s err=%s",
                    req.agent_name,
                    exc,
                )
                return DelegationResult(content=str(exc), success=False)

        # _one 自身吞异常返回 result 对象，gather 不会因单条失败短路
        results = await asyncio.gather(*[_one(r) for r in requests])
        return list(results)

    async def handoff(
        self,
        agent_name: str,
        context_messages: list[BaseMessage],
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> HandoffResult:
        """把当前 Agent 控制权完全转移给指定命名 Agent。

        实现要点：

        - **不**走 ``TaskAgentPort.execute(task)`` 路径，因为 ``TaskAgentAdapter``
          会强制 ``add_user_message(task.goal)``，与 handoff "原样转交父侧消息
          序列"语义冲突；
        - 直接组装独立 ``ConversationContext``（克隆消息引用）+ ``AgentConfig``
          后调用 ``AgentPort.run``；
        - 子 Agent 自身 system_prompt 由 ``ReActAgentAdapter._iter_rounds``
          的幂等注入路径完成（``_ensure_agent_system_prompt``），目标
          ``ConversationContext`` 中已有 SystemMessage 时跳过，否则追加目标
          Agent 的 ``config.system_prompt``。

        Args:
            agent_name: 目标 Agent 唯一标识名称。
            context_messages: 父 ``ConversationContext`` 消息快照。
            delegation_depth: 当前委派深度。
            max_delegation_depth: 最大允许委派深度。

        Returns:
            ``HandoffResult``。

        Raises:
            AgentNotFoundError: 当 ``agent_name`` 未注册。
            DelegationDepthExceededError: 当 ``delegation_depth + 1 > max_delegation_depth``。
            RuntimeError: 当适配器构造期未注入 ``model_registry`` /
                ``agent_provider`` / ``tool_registry_provider`` 时。
        """
        capability_decision = await enforce_workflow_capability_before_action(
            event_store=self._event_store,
            action=WorkflowCapabilityAction.HANDOFF,
            target=agent_name,
        )
        if capability_decision is not None:
            return HandoffResult(
                target_agent=agent_name,
                content=f"role capability rejected: {capability_decision.reason}",
                success=False,
                usage={},
                model="",
            )

        if DelegationDepthPolicy.exceeds_for_next_depth(
            delegation_depth, max_delegation_depth
        ):
            raise DelegationDepthExceededError(
                current_depth=delegation_depth,
                max_depth=max_delegation_depth,
                target_agent=agent_name,
            )

        config = self._agent_registry.get(agent_name)
        if config is None:
            raise AgentNotFoundError(
                agent_name=agent_name,
                registered_names=self._agent_registry.list_names(),
            )

        if (
            self._model_registry is None
            or self._agent_provider is None
            or self._tool_registry_provider is None
        ):
            raise RuntimeError(
                "DelegationAdapter.handoff 不可用："
                "构造期需注入 model_registry / agent_provider / tool_registry_provider"
            )

        # 1. 克隆消息列表到子 ConversationContext。
        sub_context = ConversationContext()
        for msg in context_messages:
            sub_context.append_message(msg)

        # 2. 解析子 Agent 模型与工具 schema
        model_name = config.model or self._model_registry.get_default_model()
        model_access = self._model_registry.get_adapter_for_model(model_name)
        tool_registry = await self._tool_registry_provider()
        tool_schemas = tool_registry.get_schemas(tool_names=config.tool_names)

        # 3. 组装 AgentConfig
        agent_config_obj = AgentConfig(
            system_prompt=config.system_prompt,
            tool_schemas=tool_schemas,
            model=model_name,
            max_rounds=self._handoff_max_rounds,
            prompt_id=config.prompt_id,
        )

        # 4. 调用子 Agent
        agent = await self._agent_provider()
        try:
            result = await agent.run(sub_context, agent_config_obj, model_access)
        except Exception as exc:
            logger.exception("Handoff 执行失败 agent=%s", agent_name)
            return HandoffResult(
                target_agent=agent_name,
                content=f"目标 Agent 执行失败: {exc}",
                success=False,
                usage={},
                model=model_name,
            )

        # 5. 翻译为 HandoffResult
        return HandoffResult(
            target_agent=agent_name,
            content=result.content,
            success=result.status == "completed" and result.terminated_reason == "completed",
            usage=dict(result.usage),
            model=result.model or model_name,
        )
