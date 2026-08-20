"""聊天应用服务。

该模块承载聊天 continue 与审批恢复的应用层用例编排。模型访问解析、
AgentConfig 构造、流式事件包装和 HTTP/SSE 协议适配仍由基础设施层提供。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from application.chat.session_context_workflow import ChatSessionContextWorkflow
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.ports import AgentPort, ApprovalStateStorePort
from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentRunMetadata,
)
from domain.agent.segmented_orchestration import decide_next_segment
from domain.agent.segmented_progress import (
    analyze_segment_progress,
    total_tokens_from_usage,
)
from domain.agent.value_objects import AgentConfig, AgentResult, AgentStreamEvent
from domain.chat.context import ConversationContext, ToolMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.value_objects import (
    ApprovalResumeRequestVO,
    ChatContinueRequestVO,
    ChatResponseVO,
)
from domain.model_access.ports import ModelAccessPort

RunAgentCallable = Callable[[ConversationContext, str | None], Awaitable[AgentResult]]
RunChatCallable = Callable[[ConversationContext, str | None], Awaitable[ChatResponseVO]]
RunAgentEventsCallable = Callable[
    [ConversationContext, str | None],
    AsyncIterator[AgentStreamEvent],
]
ModelAccessResolver = Callable[[str | None], tuple[ModelAccessPort, str]]
AgentConfigFactory = Callable[[str | None], AgentConfig]


@dataclass(frozen=True)
class SegmentStreamFrame:
    """分段流应用层业务帧。"""

    kind: Literal["forward", "segment_done", "final_done"]
    event: AgentStreamEvent | None = None
    usage: dict[str, int] | None = None
    segment_metadata: SegmentRunMetadata | None = None


class ChatApplicationService:
    """聊天 continue 与审批恢复用例服务。

    服务只依赖领域 Port、领域值对象和由适配器提供的模型技术适配回调，不接收
    任何具体基础设施 adapter。同步/流式出口的协议包装仍留在
    ``ChatServiceAdapter``。
    """

    def __init__(
        self,
        session_workflow: ChatSessionContextWorkflow,
        agent: AgentPort,
        approval_store: ApprovalStateStorePort | None,
        segment_policy: SegmentExecutionPolicy,
        *,
        resolve_model_access: ModelAccessResolver,
        make_agent_config: AgentConfigFactory,
    ) -> None:
        """初始化聊天应用服务。

        Args:
            session_workflow: 会话上下文 workflow。
            agent: Agent 执行端口。
            approval_store: 可选审批状态存储端口。
            segment_policy: 分段执行策略；当前切片保留字段以对齐用例边界。
            resolve_model_access: 由 adapter 提供的模型访问解析回调。
            make_agent_config: 由 adapter 提供的 AgentConfig 构造回调。
        """

        self._session_workflow = session_workflow
        self._agent = agent
        self._approval_store = approval_store
        self._segment_policy = segment_policy
        self._resolve_model_access = resolve_model_access
        self._make_agent_config = make_agent_config

    async def continue_chat(
        self,
        request: ChatContinueRequestVO,
        *,
        run_agent: RunAgentCallable | None = None,
        run_chat: RunChatCallable | None = None,
    ) -> ChatResponseVO:
        """基于已有会话上下文继续执行一段聊天 Agent。

        继续请求不追加新的用户消息；仅在上下文尾部是 ``ToolMessage`` 时允许
        进入下一段 Agent 执行。

        Args:
            request: 聊天继续请求值对象。
            run_agent: 由 adapter 提供的单段 Agent 执行回调，负责模型技术适配。
            run_chat: 由 adapter 提供的完整聊天续跑回调，用于保持分段自动续跑行为。

        Returns:
            聊天响应值对象，保持 completed / paused / approval_required 字段语义。
        """

        context = await self._session_workflow.load_for_continue(request)
        if not self._can_continue_from_context(context):
            raise ContinuationUnavailableError(
                request.session_id,
                self._continuation_unavailable_reason(context),
            )

        if run_chat is not None:
            return await run_chat(context, request.model)
        if run_agent is None:
            raise ValueError("run_agent 或 run_chat 至少需要提供一个")
        agent_result = await run_agent(context, request.model)
        await self._save_context_for_agent_result(
            session_id=request.session_id,
            context=context,
            agent_result=agent_result,
        )
        return self._to_chat_response(
            session_id=request.session_id,
            context=context,
            agent_result=agent_result,
        )

    async def run_segmented_chat_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        run_agent: RunAgentCallable,
    ) -> ChatResponseVO:
        """在既有上下文上执行同步分段 Agent 聊天。

        应用层负责分段风险门、保存时机、自动续跑决策与 ChatResponseVO 状态组合；
        模型访问解析、AgentConfig 构造和具体 Agent 调用由 adapter 提供的回调完成。
        """

        budget_usage = SegmentBudgetUsage()
        cumulative_usage: dict[str, int] = {}
        previous_tool_call_digest: str | None = None
        auto_continue_attempted = False

        while True:
            pre_message_count = context.message_count
            started_at = time.monotonic()
            agent_result = await run_agent(context, model)
            elapsed_ms = (time.monotonic() - started_at) * 1000
            await self._save_context_for_agent_result(
                session_id=session_id,
                context=context,
                agent_result=agent_result,
            )
            response = self._to_chat_response(
                session_id=session_id,
                context=context,
                agent_result=agent_result,
            )
            progress, previous_tool_call_digest = analyze_segment_progress(
                context=context,
                pre_message_count=pre_message_count,
                previous_tool_call_digest=previous_tool_call_digest,
                usage=agent_result.usage,
                final_content=agent_result.content,
            )
            cumulative_usage = self._merge_usage(cumulative_usage, agent_result.usage)
            budget_usage = budget_usage.plus_segment(
                total_tokens_delta=total_tokens_from_usage(agent_result.usage),
                elapsed_ms_delta=elapsed_ms,
                paused=response.status == "paused",
                has_progress=progress.has_progress,
                repeated_tool_call=progress.repeated_tool_call,
            )
            risk_gate_required, guardrail_reason = self._segment_risk_gate_required(
                context=context,
                pre_message_count=pre_message_count,
                approval_required=response.status == "approval_required",
                approval_metadata=(
                    agent_result.approval.metadata if agent_result.approval is not None else None
                ),
            )
            decision = decide_next_segment(
                policy=self._segment_policy,
                usage=budget_usage,
                status=response.status,
                can_continue=response.can_continue,
                progress=progress,
                approval_required=response.status == "approval_required",
                risk_gate_required=risk_gate_required,
            )
            if decision.should_continue:
                auto_continue_attempted = True
                continue

            metadata = SegmentRunMetadata(
                segment_index=budget_usage.segment_count,
                segment_count=budget_usage.segment_count,
                auto_continue_attempted=auto_continue_attempted,
                segment_stop_reason=decision.stop_reason,
                budget_usage=budget_usage,
                risk_gate_required=risk_gate_required,
                guardrail_reason=guardrail_reason,
            )
            return replace(
                response,
                usage=cumulative_usage,
                segment_metadata=metadata,
            )

    async def stream_segmented_chat_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        run_events: RunAgentEventsCallable,
    ) -> AsyncIterator[SegmentStreamFrame]:
        """在既有上下文上执行分段 Agent 事件流业务编排。"""

        budget_usage = SegmentBudgetUsage()
        previous_tool_call_digest: str | None = None
        auto_continue_attempted = False

        while True:
            pre_message_count = context.message_count
            segment_usage: dict[str, int] = {}
            full_reply_parts: list[str] = []
            status = "completed"
            can_continue = False
            approval_required = False
            approval_metadata: dict[str, object] | None = None
            final_done_event: AgentStreamEvent | None = None
            started_at = time.monotonic()

            async for event in run_events(context, model):
                if event.kind == "assistant_delta":
                    full_reply_parts.append(event.content)
                    yield SegmentStreamFrame(kind="forward", event=event)
                    continue
                if event.kind == "approval_required":
                    approval_required = True
                    status = "approval_required"
                    approval_metadata = dict(event.metadata)
                    yield SegmentStreamFrame(kind="forward", event=event)
                    break
                if event.kind == "assistant_done":
                    terminated_reason = event.metadata.get("terminated_reason", "completed")
                    segment_usage = event.usage or {}
                    if terminated_reason in ("max_rounds", "token_budget_exceeded"):
                        status = "paused"
                        can_continue = self._can_continue_from_context(context)
                        final_done_event = AgentStreamEvent(
                            kind=event.kind,
                            content=event.content,
                            tool_name=event.tool_name,
                            tool_call_id=event.tool_call_id,
                            arguments=event.arguments,
                            usage=event.usage,
                            metadata={
                                **event.metadata,
                                "status": "paused",
                                "terminated_reason": terminated_reason,
                                "can_continue": can_continue,
                            },
                        )
                        await self._session_workflow.save_context_and_index(
                            session_id,
                            context,
                            model=model,
                        )
                    else:
                        status = "completed"
                        can_continue = False
                        context.add_assistant_message("".join(full_reply_parts))
                        await self._session_workflow.save_context_and_index(
                            session_id,
                            context,
                            model=model,
                        )
                        final_done_event = event
                    break
                yield SegmentStreamFrame(kind="forward", event=event)

            elapsed_ms = (time.monotonic() - started_at) * 1000
            progress, previous_tool_call_digest = analyze_segment_progress(
                context=context,
                pre_message_count=pre_message_count,
                previous_tool_call_digest=previous_tool_call_digest,
                usage=segment_usage,
                final_content="".join(full_reply_parts),
            )
            budget_usage = budget_usage.plus_segment(
                total_tokens_delta=total_tokens_from_usage(segment_usage),
                elapsed_ms_delta=elapsed_ms,
                paused=status == "paused",
                has_progress=progress.has_progress,
                repeated_tool_call=progress.repeated_tool_call,
            )
            risk_gate_required, guardrail_reason = self._segment_risk_gate_required(
                context=context,
                pre_message_count=pre_message_count,
                approval_required=approval_required,
                approval_metadata=approval_metadata,
            )
            decision = decide_next_segment(
                policy=self._segment_policy,
                usage=budget_usage,
                status=status,
                can_continue=can_continue,
                progress=progress,
                approval_required=approval_required,
                risk_gate_required=risk_gate_required,
            )
            segment_metadata = SegmentRunMetadata(
                segment_index=budget_usage.segment_count,
                segment_count=budget_usage.segment_count,
                auto_continue_attempted=auto_continue_attempted,
                segment_stop_reason=decision.stop_reason,
                budget_usage=budget_usage,
                risk_gate_required=risk_gate_required,
                guardrail_reason=guardrail_reason,
            )

            yield SegmentStreamFrame(
                kind="segment_done",
                usage=segment_usage,
                segment_metadata=segment_metadata,
            )

            if decision.should_continue:
                auto_continue_attempted = True
                continue

            if final_done_event is not None:
                yield SegmentStreamFrame(
                    kind="final_done",
                    event=final_done_event,
                    segment_metadata=segment_metadata,
                )
            return

    async def resume_approval_to_agent_result(
        self,
        request: ApprovalResumeRequestVO,
    ) -> tuple[ConversationContext, AgentResult]:
        """校验并消费审批中断后恢复 Agent 执行。

        Args:
            request: 审批恢复请求值对象。

        Returns:
            恢复后的会话上下文与 Agent 执行结果。

        Raises:
            ApprovalNotFoundError: 审批状态存储未配置或批次不存在。
            ApprovalExpiredError: 审批批次已过期。
            ApprovalDecisionCountMismatchError: 决策数量与动作数不一致。
            ApprovalDecisionOrderMismatchError: 决策顺序与动作不一致。
            ApprovalDecisionNotAllowedError: 决策类型不在动作允许集合内。
            ApprovalConsumedError: 审批批次已被消费。
        """

        if self._approval_store is None:
            raise ApprovalNotFoundError(request.session_id, request.approval_id)

        interrupt = await self._approval_store.load(request.session_id, request.approval_id)
        if interrupt is None:
            raise ApprovalNotFoundError(request.session_id, request.approval_id)

        if interrupt.is_expired(time.time()):
            raise ApprovalExpiredError(request.session_id, request.approval_id)
        if len(request.decisions) != len(interrupt.actions):
            raise ApprovalDecisionCountMismatchError(
                len(interrupt.actions),
                len(request.decisions),
            )
        for action, decision in zip(interrupt.actions, request.decisions, strict=True):
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

        consumed = await self._approval_store.consume(request.session_id, request.approval_id)
        if consumed is None:
            raise ApprovalConsumedError(request.session_id, request.approval_id)

        context = ConversationContext.from_dict(consumed.context_snapshot)
        context.session_id = request.session_id
        model = request.model or consumed.model
        model_access, _ = self._resolve_model_access(model)
        agent_result = await self._agent.resume(
            context,
            self._make_agent_config(model),
            model_access,
            consumed,
            request.decisions,
        )
        return context, agent_result

    @staticmethod
    def _can_continue_from_context(context: ConversationContext) -> bool:
        """判断上下文尾部是否满足继续执行前置条件。"""

        messages = context.get_messages()
        return bool(messages) and isinstance(messages[-1], ToolMessage)

    @staticmethod
    def _continuation_unavailable_reason(context: ConversationContext) -> str:
        """返回与既有 adapter 一致的不可继续原因。"""

        if context.get_messages():
            return "最新消息不是工具结果"
        return "缺少可继续的上下文"

    def _to_chat_response(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        agent_result: AgentResult,
    ) -> ChatResponseVO:
        """把 AgentResult 转换为 continue 响应值对象。"""

        if agent_result.status == "approval_required":
            approval = agent_result.approval
            assert approval is not None
            return ChatResponseVO(
                session_id=session_id,
                reply="",
                model=agent_result.model,
                usage=agent_result.usage,
                prompt_id=self._session_workflow.prompt_id,
                status="approval_required",
                approval_id=approval.approval_id,
                action_requests=approval.actions,
                terminated_reason="completed",
                can_continue=False,
            )

        terminated_reason = getattr(agent_result, "terminated_reason", "completed")
        if terminated_reason not in ("max_rounds", "token_budget_exceeded"):
            return ChatResponseVO(
                session_id=session_id,
                reply=agent_result.content,
                model=agent_result.model,
                usage=agent_result.usage,
                prompt_id=self._session_workflow.prompt_id,
                status="completed",
                terminated_reason="completed",
                can_continue=False,
            )

        return ChatResponseVO(
            session_id=session_id,
            reply="",
            model=agent_result.model,
            usage=agent_result.usage,
            prompt_id=self._session_workflow.prompt_id,
            status="paused",
            terminated_reason=agent_result.terminated_reason,
            can_continue=self._can_continue_from_context(context),
        )

    async def _save_context_for_agent_result(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        agent_result: AgentResult,
    ) -> None:
        """按 Agent 终止语义保存聊天上下文。"""

        if agent_result.status == "approval_required":
            return
        terminated_reason = getattr(agent_result, "terminated_reason", "completed")
        if terminated_reason not in ("max_rounds", "token_budget_exceeded"):
            context.add_assistant_message(agent_result.content)
        await self._session_workflow.save_context_and_index(
            session_id,
            context,
            model=agent_result.model,
        )

    @staticmethod
    def _segment_risk_gate_required(
        *,
        context: ConversationContext,
        pre_message_count: int,
        approval_required: bool = False,
        approval_metadata: dict[str, object] | None = None,
    ) -> tuple[bool, str | None]:
        """根据本段新增稳定 metadata 判断是否需要风险门禁。"""

        guardrail_reason: str | None = None
        messages = context.get_messages()
        for message in messages[pre_message_count:]:
            if not isinstance(message, ToolMessage):
                continue
            metadata = message.metadata or {}
            if metadata.get("risk_gate_required") is True:
                return True, metadata.get("guardrail_reason")
            if guardrail_reason is None:
                guardrail_reason = metadata.get("guardrail_reason")

        approval_data = approval_metadata or {}
        if approval_required and approval_data.get("source") == "guardrail":
            reason = approval_data.get("guardrail_reason")
            return True, str(reason or guardrail_reason) if reason or guardrail_reason else None
        return False, guardrail_reason

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        """合并 token usage，保持与 infrastructure.chat.usage.merge_usage 等价。"""

        return {key: left.get(key, 0) + right.get(key, 0) for key in left.keys() | right.keys()}

    @property
    def segment_policy(self) -> SegmentExecutionPolicy:
        """返回当前服务持有的分段执行策略。"""

        return self._segment_policy
