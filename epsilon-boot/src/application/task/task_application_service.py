"""任务应用服务。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from application.task.task_trace_workflow import TaskTraceWorkflow
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.ports import ApprovalStateStorePort
from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentProgressSnapshot,
    SegmentRunMetadata,
)
from domain.agent.segmented_orchestration import decide_next_segment
from domain.agent.segmented_progress import (
    normalized_tool_call_digest,
    total_tokens_from_usage,
)
from domain.agent.value_objects import AgentConfig, AgentResult, ApprovalInterrupt
from domain.chat.context import ConversationContext, SystemMessage, ToolMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.ports import SessionContextStorePort
from domain.task.policy import ApprovalResumePrecondition
from domain.task.result_mapping import TaskResultMapper
from domain.task.value_objects import (
    Task,
    TaskApprovalResumeRequest,
    TaskContinueRequest,
    TaskResult,
    TaskStatus,
    TraceEntry,
)


@dataclass(frozen=True)
class TaskRunPlan:
    """由 adapter 提供的任务单段执行计划。"""

    config: AgentConfig
    system_prompt: str
    allowed_tool_names: list[str] | None = None


PrepareExecuteTaskCallable = Callable[[Task, ConversationContext], TaskRunPlan]
PrepareResumeTaskCallable = Callable[[str, ConversationContext, str | None], TaskRunPlan]
RunTaskAgentCallable = Callable[[ConversationContext, AgentConfig], Awaitable[AgentResult]]
ResumeTaskAgentCallable = Callable[
    [ConversationContext, AgentConfig, ApprovalInterrupt, tuple],
    Awaitable[AgentResult],
]
CanContinueTaskCallable = Callable[[ConversationContext], bool]


def _tool_call_digest_from_trace_detail(detail: str) -> str | None:
    """从任务轨迹详情中提取工具调用摘要。"""
    open_paren_index = detail.find("(")
    if open_paren_index <= 0 or not detail.endswith(")"):
        return None
    tool_name = detail[:open_paren_index]
    arguments = detail[open_paren_index + 1 : -1]
    return normalized_tool_call_digest(tool_name, arguments)


class TaskApplicationService:
    """任务执行、继续与审批恢复用例服务。"""

    def __init__(
        self,
        *,
        session_store: SessionContextStorePort,
        approval_store: ApprovalStateStorePort | None,
        trace_workflow: TaskTraceWorkflow,
        segment_policy: SegmentExecutionPolicy,
        prompt_id: str,
    ) -> None:
        self._session_store = session_store
        self._approval_store = approval_store
        self._trace_workflow = trace_workflow
        self._segment_policy = segment_policy
        self._prompt_id = prompt_id

    async def execute_task(
        self,
        task: Task,
        *,
        prepare: PrepareExecuteTaskCallable,
        prepare_resume: PrepareResumeTaskCallable,
        run_agent: RunTaskAgentCallable,
        can_continue: CanContinueTaskCallable,
    ) -> TaskResult:
        """执行任务，支持请求内分段自动续跑。"""
        first_result = await self._execute_single_task_segment(
            task,
            prepare=prepare,
            run_agent=run_agent,
            can_continue=can_continue,
        )
        if task.session_id is None:
            return first_result

        async def continue_factory() -> TaskResult:
            return await self._continue_single_task_segment(
                TaskContinueRequest(session_id=task.session_id or "", model=task.model),
                prepare=prepare_resume,
                run_agent=run_agent,
                can_continue=can_continue,
            )

        return await self._run_segmented_task_result(
            first_result,
            continue_factory=continue_factory,
            allow_auto_continue=True,
        )

    async def continue_task(
        self,
        request: TaskContinueRequest,
        *,
        prepare: PrepareResumeTaskCallable,
        run_agent: RunTaskAgentCallable,
        can_continue: CanContinueTaskCallable,
    ) -> TaskResult:
        """基于已有任务上下文继续执行。"""
        first_result = await self._continue_single_task_segment(
            request,
            prepare=prepare,
            run_agent=run_agent,
            can_continue=can_continue,
        )

        async def continue_factory() -> TaskResult:
            return await self._continue_single_task_segment(
                request,
                prepare=prepare,
                run_agent=run_agent,
                can_continue=can_continue,
            )

        return await self._run_segmented_task_result(
            first_result,
            continue_factory=continue_factory,
            allow_auto_continue=True,
        )

    async def resume_approval(
        self,
        request: TaskApprovalResumeRequest,
        *,
        prepare: PrepareResumeTaskCallable,
        resume_agent: ResumeTaskAgentCallable,
        can_continue: CanContinueTaskCallable,
    ) -> TaskResult:
        """提交审批决策并恢复任务 Agent 执行。"""
        consumed = await self._load_consumed_interrupt(request)
        context = ConversationContext.from_dict(consumed.context_snapshot)
        context.session_id = request.session_id
        model_name = request.model or consumed.model
        plan = prepare(request.session_id, context, model_name)
        pre_message_count = context.message_count
        agent_result = await resume_agent(
            context,
            plan.config,
            consumed,
            tuple(request.decisions),
        )
        result = self._result_for_agent_outcome(
            context=context,
            pre_message_count=pre_message_count,
            agent_result=agent_result,
            can_continue=can_continue,
        )
        if agent_result.status != "approval_required":
            await self._session_store.save(request.session_id, context)
        return result

    async def _execute_single_task_segment(
        self,
        task: Task,
        *,
        prepare: PrepareExecuteTaskCallable,
        run_agent: RunTaskAgentCallable,
        can_continue: CanContinueTaskCallable,
    ) -> TaskResult:
        try:
            if task.session_id is not None:
                context = await self._session_store.load(task.session_id)
                context.session_id = task.session_id
            else:
                context = ConversationContext()

            plan = prepare(task, context)
            self._ensure_system_message(context, plan)
            context.add_user_message(task.goal)

            pre_message_count = context.message_count
            agent_result = await run_agent(context, plan.config)
            result = self._result_for_agent_outcome(
                context=context,
                pre_message_count=pre_message_count,
                agent_result=agent_result,
                can_continue=can_continue,
            )
            if task.session_id is not None:
                await self._session_store.save(task.session_id, context)
            return result
        except Exception as exc:
            return TaskResult(
                content=str(exc),
                status=TaskStatus.FAILED,
                model=task.model or "unknown",
                prompt_id=self._prompt_id,
            )

    async def _continue_single_task_segment(
        self,
        request: TaskContinueRequest,
        *,
        prepare: PrepareResumeTaskCallable,
        run_agent: RunTaskAgentCallable,
        can_continue: CanContinueTaskCallable,
    ) -> TaskResult:
        context = await self._session_store.load(request.session_id)
        context.session_id = request.session_id
        messages = context.get_messages()
        if not messages:
            raise ContinuationUnavailableError(
                request.session_id,
                "缺少可继续的任务上下文",
            )
        if not isinstance(messages[-1], ToolMessage):
            raise ContinuationUnavailableError(
                request.session_id,
                "最新消息不是工具结果",
            )

        plan = prepare(request.session_id, context, request.model)
        pre_message_count = context.message_count
        agent_result = await run_agent(context, plan.config)
        result = self._result_for_agent_outcome(
            context=context,
            pre_message_count=pre_message_count,
            agent_result=agent_result,
            can_continue=can_continue,
        )
        await self._session_store.save(request.session_id, context)
        return result

    def _result_for_agent_outcome(
        self,
        *,
        context: ConversationContext,
        pre_message_count: int,
        agent_result: AgentResult,
        can_continue: CanContinueTaskCallable,
    ) -> TaskResult:
        trace = self._trace_workflow.extract_trace(
            context,
            start_index=pre_message_count,
            event_timestamps=context.event_timestamps,
        )
        result = TaskResultMapper.to_task_result(
            agent_result=agent_result,
            trace=trace,
            context_can_continue=can_continue(context),
            prompt_id=self._prompt_id,
        )
        risk_gate_required, guardrail_reason = self._segment_risk_gate_required(
            context=context,
            pre_message_count=pre_message_count,
            approval_required=agent_result.status == "approval_required",
            approval_metadata=(
                agent_result.approval.metadata if agent_result.approval is not None else None
            ),
        )
        return replace(
            result,
            segment_metadata=replace(
                result.segment_metadata,
                risk_gate_required=risk_gate_required,
                guardrail_reason=guardrail_reason,
            ),
        )

    async def _load_consumed_interrupt(
        self,
        request: TaskApprovalResumeRequest,
    ) -> ApprovalInterrupt:
        if self._approval_store is None:
            raise ApprovalNotFoundError(request.session_id, request.approval_id)
        interrupt = await self._approval_store.load(request.session_id, request.approval_id)
        if interrupt is None:
            raise ApprovalNotFoundError(request.session_id, request.approval_id)
        if interrupt.is_expired(time.time()):
            raise ApprovalExpiredError(request.session_id, request.approval_id)
        ApprovalResumePrecondition.check(interrupt.actions, request.decisions)
        consumed = await self._approval_store.consume(
            request.session_id,
            request.approval_id,
        )
        if consumed is None:
            raise ApprovalConsumedError(request.session_id, request.approval_id)
        return consumed

    async def _run_segmented_task_result(
        self,
        first_result: TaskResult,
        *,
        continue_factory: Callable[[], Awaitable[TaskResult]],
        allow_auto_continue: bool,
    ) -> TaskResult:
        budget_usage = SegmentBudgetUsage()
        cumulative_usage: dict[str, int] = {}
        all_trace: list[TraceEntry] = []
        total_latency_ms = 0.0
        auto_continue_attempted = False
        result = first_result
        previous_tool_call_digest: str | None = None

        while True:
            token_delta = total_tokens_from_usage(result.usage)
            last_tool_call_detail = next(
                (entry.detail for entry in reversed(result.trace) if entry.action == "tool_call"),
                None,
            )
            last_tool_call_digest = (
                _tool_call_digest_from_trace_detail(last_tool_call_detail)
                if last_tool_call_detail is not None
                else None
            )
            repeated_tool_call = (
                last_tool_call_digest is not None
                and last_tool_call_digest == previous_tool_call_digest
            )
            if last_tool_call_digest is not None:
                previous_tool_call_digest = last_tool_call_digest
            progress = SegmentProgressSnapshot(
                pre_message_count=0,
                post_message_count=len(result.trace),
                new_trace_count=len(result.trace),
                token_delta=token_delta,
                final_content_present=bool(result.content),
                repeated_tool_call=repeated_tool_call,
            )
            cumulative_usage = self._merge_usage(cumulative_usage, result.usage)
            all_trace.extend(result.trace)
            total_latency_ms += result.latency_ms
            budget_usage = budget_usage.plus_segment(
                total_tokens_delta=token_delta,
                elapsed_ms_delta=result.latency_ms,
                paused=result.status == TaskStatus.PAUSED,
                has_progress=progress.has_progress,
                repeated_tool_call=progress.repeated_tool_call,
            )
            status = "completed"
            if result.status == TaskStatus.PAUSED:
                status = "paused"
            elif result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED:
                status = "approval_required"
            approval_required = result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED
            tool_boundary_available = result.can_continue or result.status != TaskStatus.PAUSED
            decision = decide_next_segment(
                policy=self._segment_policy,
                usage=budget_usage,
                status=status,
                can_continue=allow_auto_continue and result.status == TaskStatus.PAUSED,
                progress=progress,
                approval_required=approval_required,
                tool_boundary_available=tool_boundary_available,
                risk_gate_required=result.segment_metadata.risk_gate_required,
            )
            if decision.should_continue:
                auto_continue_attempted = True
                result = await continue_factory()
                continue

            metadata = SegmentRunMetadata(
                segment_index=budget_usage.segment_count,
                segment_count=budget_usage.segment_count,
                auto_continue_attempted=auto_continue_attempted,
                segment_stop_reason=decision.stop_reason,
                budget_usage=budget_usage,
                risk_gate_required=result.segment_metadata.risk_gate_required,
                guardrail_reason=result.segment_metadata.guardrail_reason,
            )
            return replace(
                result,
                usage=cumulative_usage,
                trace=all_trace,
                latency_ms=total_latency_ms,
                segment_metadata=metadata,
            )

    @staticmethod
    def _ensure_system_message(context: ConversationContext, plan: TaskRunPlan) -> None:
        existing_system_messages = [m for m in context.get_messages() if m.role == "system"]
        if not existing_system_messages:
            context.append_message(
                SystemMessage(
                    content=plan.system_prompt,
                    metadata={"task_allowed_tool_names": plan.allowed_tool_names},
                )
            )
            return
        system_message = existing_system_messages[0]
        if (
            isinstance(system_message.metadata, dict)
            and "task_allowed_tool_names" not in system_message.metadata
        ):
            system_message.metadata["task_allowed_tool_names"] = plan.allowed_tool_names

    @staticmethod
    def _segment_risk_gate_required(
        *,
        context: ConversationContext,
        pre_message_count: int,
        approval_required: bool = False,
        approval_metadata: dict | None = None,
    ) -> tuple[bool, str | None]:
        guardrail_reason: str | None = None
        for message in context.get_messages()[pre_message_count:]:
            if not isinstance(message, ToolMessage):
                continue
            metadata = message.metadata or {}
            if metadata.get("risk_gate_required") is True:
                return True, metadata.get("guardrail_reason")
            if guardrail_reason is None:
                reason = metadata.get("guardrail_reason")
                if isinstance(reason, str) and reason:
                    guardrail_reason = reason

        approval_data = approval_metadata or {}
        if approval_required and approval_data.get("source") == "guardrail":
            return True, approval_data.get("guardrail_reason") or guardrail_reason
        return False, guardrail_reason

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        return {key: left.get(key, 0) + right.get(key, 0) for key in left.keys() | right.keys()}
