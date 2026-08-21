"""Agent 并行委派工具模块。

本模块提供 ``DelegateParallelTool``，继承 ``Tool`` ABC，对应 LangGraph
``Send()`` / AutoGen GroupChat 等业内主流框架的"扇出—合并"模式：当前 Agent
可一次性派发多个独立子任务给不同命名 Agent 并发执行，取得聚合结果后继续推理。

与 ``DelegateToAgentTool`` 的关系：

- ``DelegateToAgentTool`` 单次只委派一个 Agent；
- ``DelegateParallelTool`` 一次性派发多个委派请求并通过
  :meth:`DelegationPort.delegate_parallel` 并发执行，错误隔离，结果按输入顺序聚合。

注册条件复用 ``AGENT_DELEGATE_TOOL_ENABLED``，与 ``DelegateToAgentTool``
一同启用或禁用。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from domain.agent.exceptions import DelegationDepthExceededError
from domain.agent.tools import Tool, ToolExecutionResult
from domain.agent.value_objects import DelegationRequest
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


class DelegateParallelTool(Tool):
    """Agent 并行委派工具。

    Attributes:
        _agent_registry: Agent 注册表端口，用于生成动态工具描述。
        _delegation: 委派端口，调用 ``delegate_parallel(...)``。
        _current_delegation_depth: 当前 Agent 执行所处的委派深度。
        _max_delegation_depth: 最大允许委派深度。
    """

    _MIN_REQUESTS = 1
    _MAX_REQUESTS = 8
    """单次并行委派的请求数量上限。8 是兼顾"足够并发以见效"与"防止滥用 /
    资源失控"的工程经验值，与 LangGraph 默认 fan-out 上限相近。"""

    def __init__(
        self,
        agent_registry: AgentRegistryPort,
        delegation: DelegationPort,
        current_delegation_depth: int = 0,
        max_delegation_depth: int = 3,
        event_store: RunEventAppenderPort | None = None,
        recent_collaboration_summary_limit: int = 5,
    ) -> None:
        """初始化并行委派工具。

        Args:
            agent_registry: Agent 注册表端口实例。
            delegation: 委派端口实例。
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
        return "delegate_parallel"

    @property
    def description(self) -> str:
        """工具功能描述，动态包含已注册 Agent 列表。"""
        registered = self._agent_registry.list_names()
        if registered:
            agent_list = ", ".join(registered)
            return (
                "Delegate multiple independent subtasks to named agents in parallel "
                "and wait for the aggregated result. Use only when the subtasks are "
                f"independent and can be evaluated separately. Maximum {self._MAX_REQUESTS} "
                f"subtasks per call. Available agents: [{agent_list}]"
            )
        return (
            "Delegate multiple independent subtasks to named agents in parallel. "
            "No agents are currently available."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """符合 JSON Schema 规范的参数描述字典。"""
        return {
            "type": "object",
            "properties": {
                "requests": {
                    "type": "array",
                    "description": (
                        "Parallel delegation requests. Each item runs independently "
                        "with isolated errors. Results are returned in the same order."
                    ),
                    "minItems": self._MIN_REQUESTS,
                    "maxItems": self._MAX_REQUESTS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_name": {
                                "type": "string",
                                "description": "Name of the target agent.",
                            },
                            "task_goal": {
                                "type": "string",
                                "description": "Clear, self-contained goal for this subtask.",
                            },
                            "input_data": {
                                "type": "object",
                                "description": "Optional structured input data for this subtask.",
                            },
                        },
                        "required": ["agent_name", "task_goal"],
                    },
                },
            },
            "required": ["requests"],
        }

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """扩展默认校验：补充 minItems / maxItems 与子项 required 校验。

        ``Tool.validate_params`` 仅做"required + 顶层类型"校验，不深入 array
        items 的 minItems / maxItems / required 字段。本方法在父类基础上对
        ``requests`` 数组做最小必要的 schema 扩展校验，避免 LLM 调用时 Agent Loop
        默默接受 0 条或超长的请求列表。
        """
        errors = super().validate_params(params)
        requests = params.get("requests")
        if not isinstance(requests, list):
            return errors  # 已由父类 type 校验产生错误
        request_items = cast(list[object], requests)
        if len(request_items) < self._MIN_REQUESTS:
            errors.append(f"requests 至少包含 {self._MIN_REQUESTS} 条，当前 {len(request_items)}")
        if len(request_items) > self._MAX_REQUESTS:
            errors.append(f"requests 最多包含 {self._MAX_REQUESTS} 条，当前 {len(request_items)}")
        for idx, item in enumerate(request_items):
            if not isinstance(item, dict):
                errors.append(f"requests[{idx}] 必须为对象")
                continue
            request_item = cast(dict[object, object], item)
            if not request_item.get("agent_name") or not isinstance(
                request_item.get("agent_name"), str
            ):
                errors.append(f"requests[{idx}].agent_name 必填且必须为字符串")
            if not request_item.get("task_goal") or not isinstance(
                request_item.get("task_goal"), str
            ):
                errors.append(f"requests[{idx}].task_goal 必填且必须为字符串")
        return errors

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行并行委派。

        Args:
            **kwargs: 工具参数，必含 ``requests`` 列表。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为按 ``requests`` 顺序聚合
            的可读文本，单条形态 ``[✓/✗] <agent_name>\\n<content>``，多条以两个
            换行分隔；``metadata`` 含以下键：

            - ``targets`` (list[str]): 目标 Agent 名称列表。
            - ``results_count`` (int): 返回的结果条数。
            - ``success_count`` (int): 成功的结果条数。

        Raises:
            DelegationDepthExceededError: 当 ``current_depth + 1`` 已经超限，
                与 ``DelegateToAgentTool`` 行为一致（不进入并行委派步骤）。
        """
        raw_requests: list[dict[str, Any]] = kwargs["requests"]
        workflow_context = get_workflow_collaboration_context()
        if (
            workflow_context is not None
            and len(raw_requests) > workflow_context.limit.max_parallel_delegations
        ):
            reason = (
                "parallel_delegation_limit_exceeded:"
                f"{len(raw_requests)}>{workflow_context.limit.max_parallel_delegations}"
            )
            self._collaboration_summary = await record_collaboration_limit_hit(
                event_store=self._event_store,
                reason=reason,
                action=CollaborationAction.DELEGATION,
                target_agent=",".join(r.get("agent_name", "?") for r in raw_requests),
                depth=self._current_delegation_depth + 1,
                collaboration_summary=self._collaboration_summary,
            )
            return ToolExecutionResult(
                content=f"并行委派数量超限: {reason}",
                metadata={
                    "targets": [r.get("agent_name", "?") for r in raw_requests],
                    "results_count": 0,
                    "success_count": 0,
                },
            )

        # 整体深度校验：与 DelegateToAgentTool 对齐，统一在工具层抛出，
        # 而非交给 _one() 内部。延后到这里确保 schema 校验先行。
        next_depth = self._current_delegation_depth + 1
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
                "并行委派深度超限: 当前深度 %d，最大深度 %d",
                self._current_delegation_depth,
                effective_max_depth,
            )
            self._collaboration_summary = await record_collaboration_limit_hit(
                event_store=self._event_store,
                reason="delegation_depth_exceeded",
                action=CollaborationAction.DELEGATION,
                target_agent=",".join(r.get("agent_name", "?") for r in raw_requests),
                depth=next_depth,
                collaboration_summary=self._collaboration_summary,
            )
            raise DelegationDepthExceededError(
                current_depth=self._current_delegation_depth,
                max_depth=effective_max_depth,
                target_agent=",".join(r.get("agent_name", "?") for r in raw_requests),
            )

        delegation_requests = [
            DelegationRequest(
                agent_name=r["agent_name"],
                task_goal=r["task_goal"],
                input_data=r.get("input_data") or {},
            )
            for r in raw_requests
        ]

        results = await self._delegation.delegate_parallel(
            delegation_requests,
            delegation_depth=next_depth,
            max_delegation_depth=effective_max_depth,
        )

        sections: list[str] = []
        success_count = 0
        for req, res in zip(delegation_requests, results, strict=True):
            self._collaboration_summary = await record_collaboration_step(
                event_store=self._event_store,
                action=CollaborationAction.DELEGATION,
                target_agent=req.agent_name,
                task_summary=req.task_goal,
                result_summary=res.content,
                depth=next_depth,
                collaboration_summary=self._collaboration_summary,
                recent_limit=self._recent_collaboration_summary_limit,
            )
            if res.success:
                success_count += 1
            tag = "✓" if res.success else "✗"
            sections.append(f"[{tag}] {req.agent_name}\n{res.content}")
        return ToolExecutionResult(
            content="\n\n".join(sections),
            metadata={
                "targets": [req.agent_name for req in delegation_requests],
                "results_count": len(results),
                "success_count": success_count,
            },
        )
