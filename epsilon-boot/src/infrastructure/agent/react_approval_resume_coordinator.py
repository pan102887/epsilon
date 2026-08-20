"""ReAct 审批恢复协作者。

本模块承接 ``ReActAgentAdapter`` 中可独立表达的审批恢复流程：按中断动作
顺序校验人工决策，并把 approve / edit / reject 分支委托给窄运行时回调。
协作者只依赖领域值对象、对话上下文和值对象形状，不导入 application 或具体
adapter。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from domain.agent.exceptions import (
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
    ApprovalEditInvalidArgumentsError,
    ApprovalEditToolNameMismatchError,
)
from domain.agent.value_objects import AgentConfig, ApprovalDecision, ApprovalInterrupt
from domain.chat.context import AssistantMessage, ConversationContext
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.react_runtime_protocols import ApprovalResumeRuntime


class ReactApprovalResumeCoordinator:
    """ReAct 审批恢复基础设施协作者。

    类本身不执行工具、不写 checkpoint，也不直接写 ToolMessage；这些副作用由
    ``ApprovalResumeRuntime`` 暴露的窄回调承接，便于后续让 adapter 门面降为
    委托层。
    """

    def __init__(self, runtime: ApprovalResumeRuntime) -> None:
        """初始化协作者。

        Args:
            runtime: 审批恢复所需的执行与拒绝记录回调。
        """
        self._runtime = runtime

    async def apply_decisions(
        self,
        *,
        context: ConversationContext,
        config: AgentConfig,
        interrupt: ApprovalInterrupt,
        decisions: Sequence[ApprovalDecision],
    ) -> None:
        """按中断动作顺序应用审批恢复决策。

        Args:
            context: 恢复执行使用的对话上下文。
            interrupt: 已持久化的审批中断状态。
            decisions: 调用方按 ``interrupt.actions`` 顺序传入的审批决策。

        Raises:
            ApprovalDecisionCountMismatchError: 决策数量与待审批动作数量不一致。
            ApprovalDecisionOrderMismatchError: 决策 tool_call_id 与动作顺序不一致。
            ApprovalDecisionNotAllowedError: 决策类型不在动作允许集合内。
            ApprovalEditToolNameMismatchError: edit 决策试图修改工具名。
            ApprovalEditInvalidArgumentsError: edit 决策缺少动作或参数不是合法 JSON。
        """
        if len(decisions) != len(interrupt.actions):
            raise ApprovalDecisionCountMismatchError(len(interrupt.actions), len(decisions))

        original_by_id = self.latest_tool_calls_by_id(context)
        for action, decision in zip(interrupt.actions, decisions, strict=True):
            if decision.tool_call_id != action.tool_call_id:
                raise ApprovalDecisionOrderMismatchError(
                    action.tool_call_id,
                    decision.tool_call_id,
                )
            if decision.type not in action.allowed_decisions:
                raise ApprovalDecisionNotAllowedError(
                    action.tool_name,
                    decision.type,
                    frozenset(action.allowed_decisions),
                )

            original = original_by_id.get(action.tool_call_id) or ToolCallRequest(
                id=action.tool_call_id,
                name=action.tool_name,
                arguments=action.arguments,
            )
            if decision.type == "approve":
                await self._runtime.execute_approved_tool_call(
                    context,
                    original,
                    config,
                    round_num=interrupt.round_num,
                    usage=dict(interrupt.usage_so_far),
                )
            elif decision.type == "edit":
                edited = decision.edited_action
                if edited is None:
                    raise ApprovalEditInvalidArgumentsError(action.tool_name, "缺少编辑后动作")
                if edited.name != action.tool_name:
                    raise ApprovalEditToolNameMismatchError(action.tool_name, edited.name)
                try:
                    edited_params = json.loads(edited.arguments)
                except json.JSONDecodeError as exc:
                    raise ApprovalEditInvalidArgumentsError(
                        action.tool_name,
                        "JSON 格式错误",
                    ) from exc
                self._runtime.validate_edited_tool_call(action.tool_name, edited_params)
                await self._runtime.execute_approved_tool_call(
                    context,
                    ToolCallRequest(
                        id=action.tool_call_id,
                        name=action.tool_name,
                        arguments=edited.arguments,
                    ),
                    config,
                    round_num=interrupt.round_num,
                    usage=dict(interrupt.usage_so_far),
                )
            elif decision.type == "reject":
                await self._runtime.record_rejected_tool_call(
                    context,
                    action,
                    decision,
                    round_num=interrupt.round_num,
                    usage=dict(interrupt.usage_so_far),
                )

    @staticmethod
    def latest_tool_calls_by_id(context: ConversationContext) -> Mapping[str, ToolCallRequest]:
        """按工具调用 ID 返回上下文中最近一次 assistant tool_call。

        扫描 ``ConversationContext`` 内所有 ``AssistantMessage.tool_calls``，同一
        ID 多次出现时以后出现的调用为准。返回值是普通 ``dict``，调用方只应按
        只读映射使用。

        Args:
            context: 待扫描的对话上下文。

        Returns:
            ``tool_call_id -> ToolCallRequest`` 的最近调用映射。
        """
        latest: dict[str, ToolCallRequest] = {}
        for message in context.get_messages():
            if isinstance(message, AssistantMessage):
                for tool_call in message.tool_calls:
                    latest[tool_call.id] = tool_call
        return latest
