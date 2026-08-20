"""ReAct 审批状态缝合协作者（基础设施层内部）。

从 ``ReActAgentAdapter`` 门面抽出的审批中断状态职责（SRP 拆分，
``ddd-followup-refinements`` 切片 C）：把「待审批动作收集 + 审批中断创建/保存 +
最近 assistant tool_calls 索引」这组围绕 ``ApprovalPolicyPort`` /
``ApprovalStateStorePort`` 的审批状态操作收敛到单一协作类。

本协作者只承载可清晰分离、仅依赖审批策略/审批存储与领域纯函数的方法；
与门面核心执行链路深度耦合的审批决策应用（``_apply_approval_decisions`` /
``_record_rejected_tool_call`` / workflow capability 中断，需回调
``_execute_tool_call`` / ``_checkpoint_tool_metadata`` / ``_stamp_event`` /
``_tool_registry`` / ``_run_event_store``）按 design §5.4「避免过度拆分」保留在
门面。checkpoint sink 调用（``checkpoint_model_completed`` /
``checkpoint_approval_interrupt``）属 ``AgentLoopEffects`` 协议方法，也保留门面。

本模块为基础设施层内部协作者，不改分层方向、不上提领域层、不改变对外行为。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from domain.agent.agent_loop_policy import (
    collect_pending_actions as _domain_collect_pending_actions,
)
from domain.agent.value_objects import ApprovalInterrupt, ApprovalRequiredPayload
from infrastructure.agent.approval_logging import approval_log_extra

if TYPE_CHECKING:
    from domain.agent.ports import ApprovalPolicyPort, ApprovalStateStorePort
    from domain.agent.value_objects import (
        AgentConfig,
        ApprovalPolicy,
        PendingActionRequest,
    )
    from domain.chat.context import ConversationContext
    from domain.model_access.value_objects import ToolCallRequest

logger = logging.getLogger(__name__)


class ApprovalCheckpointStitcher:
    """审批中断状态缝合协作者。

    持有审批策略与审批状态存储端口，承载待审批动作收集、审批中断创建与保存，
    以及最近 assistant tool_calls 索引查询。
    """

    def __init__(
        self,
        approval_policy: ApprovalPolicyPort,
        approval_store: ApprovalStateStorePort,
    ) -> None:
        """初始化审批缝合协作者。

        Args:
            approval_policy: 审批策略端口，用于按工具名解析审批策略。
            approval_store: 审批状态存储端口，用于保存审批中断快照。
        """
        self._approval_policy = approval_policy
        self._approval_store = approval_store

    def collect_pending_actions(
        self,
        tool_calls: list[ToolCallRequest],
        config: AgentConfig,
    ) -> tuple[PendingActionRequest, ...]:
        """按模型 tool_calls 顺序收集需要审批的动作。

        保留 not-allowed warning 日志后委托领域纯函数 collect_pending_actions。
        """
        # 预输出 warning 日志（副作用留 adapter）
        for tool_call in tool_calls:
            if tool_call.name not in config.allowed_tool_names:
                logger.warning(
                    "工具调用被拒绝: %s，允许的工具: %s",
                    tool_call.name,
                    sorted(config.allowed_tool_names),
                )
        # 预解析 policies mapping
        policies: dict[str, ApprovalPolicy] = {
            tc.name: self._approval_policy.policy_for(tc.name)
            for tc in tool_calls
            if tc.name in config.allowed_tool_names
        }
        return _domain_collect_pending_actions(
            tool_calls=tool_calls,
            allowed_tool_names=config.allowed_tool_names,
            policies=policies,
        )

    async def save_interrupt(
        self,
        context: ConversationContext,
        config: AgentConfig,
        actions: tuple[PendingActionRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequiredPayload:
        """创建并保存审批中断。"""
        now = time.time()
        approval_id = uuid.uuid4().hex
        interrupt_metadata = {"tool_names": [action.tool_name for action in actions]}
        if metadata:
            interrupt_metadata.update(metadata)
        interrupt = ApprovalInterrupt(
            session_id=context.session_id or "",
            approval_id=approval_id,
            actions=actions,
            context_snapshot=context.to_dict(),
            round_num=round_num,
            model=model,
            usage_so_far=dict(usage_so_far),
            created_at_epoch=now,
            metadata=interrupt_metadata,
        )
        await self._approval_store.save(interrupt)
        logger.info(
            "Agent 工具调用等待人工审批",
            extra=approval_log_extra(
                session_id=interrupt.session_id,
                approval_id=approval_id,
                tool_names=[action.tool_name for action in actions],
                action_count=len(actions),
                round_num=round_num,
            ),
        )
        return ApprovalRequiredPayload(
            session_id=interrupt.session_id,
            approval_id=approval_id,
            actions=actions,
            prompt_id=config.prompt_id,
            metadata=interrupt.metadata,
        )

    @staticmethod
    def latest_tool_calls_by_id(
        context: ConversationContext,
    ) -> dict[str, ToolCallRequest]:
        """返回上下文中最近 assistant tool_calls 的 ID 映射。"""
        from domain.chat.context import AssistantMessage

        for message in reversed(context.get_messages()):
            if isinstance(message, AssistantMessage) and message.tool_calls:
                return {tool_call.id: tool_call for tool_call in message.tool_calls}
        return {}
