"""TaskApplicationService 单元测试。"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from application.task import TaskApplicationService, TaskRunPlan, TaskTraceWorkflow
from domain.agent.exceptions import ApprovalDecisionCountMismatchError, ApprovalExpiredError
from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.ports import ApprovalStateStorePort
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    ApprovalDecision,
    ApprovalInterrupt,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, ToolMessage, UserMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.ports import SessionContextStorePort
from domain.model_access.value_objects import ToolCallRequest
from domain.task.value_objects import (
    Task,
    TaskApprovalResumeRequest,
    TaskContinueRequest,
    TaskStatus,
)


class _SessionStore:
    """测试用 session store。"""

    def __init__(self, context: ConversationContext | None = None) -> None:
        self.context = context or ConversationContext()
        self.saved: list[tuple[str, ConversationContext]] = []

    async def load(self, session_id: str) -> ConversationContext:
        return self.context

    async def save(self, session_id: str, context: ConversationContext) -> None:
        self.saved.append((session_id, context))


class _ApprovalStore:
    """测试用审批 store。"""

    def __init__(self, interrupt: ApprovalInterrupt | None) -> None:
        self.interrupt = interrupt
        self.consume_result = interrupt
        self.calls: list[str] = []

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        self.calls.append("load")
        return self.interrupt

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        self.calls.append("consume")
        return self.consume_result


def _plan(model: str = "test-model") -> TaskRunPlan:
    return TaskRunPlan(
        config=AgentConfig(
            system_prompt="system",
            tool_schemas=[],
            model=model,
            max_rounds=3,
            prompt_id="task-template@v1",
        ),
        system_prompt="system",
        allowed_tool_names=None,
    )


def _resume_plan(model: str = "test-model") -> TaskRunPlan:
    return TaskRunPlan(
        config=AgentConfig(
            system_prompt="resume-system",
            tool_schemas=[],
            model=model,
            max_rounds=3,
            prompt_id="task-template@v1",
        ),
        system_prompt="resume-system",
        allowed_tool_names=["search"],
    )


def _service(
    *,
    context: ConversationContext | None = None,
    approval_store: _ApprovalStore | None = None,
    segment_policy: SegmentExecutionPolicy | None = None,
) -> tuple[TaskApplicationService, _SessionStore]:
    store = _SessionStore(context)
    return (
        TaskApplicationService(
            session_store=cast(SessionContextStorePort, store),
            approval_store=cast(ApprovalStateStorePort | None, approval_store),
            trace_workflow=TaskTraceWorkflow(),
            segment_policy=segment_policy or SegmentExecutionPolicy(),
            prompt_id="task-template@v1",
        ),
        store,
    )


def _append_tool_tail(context: ConversationContext) -> None:
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")


def _interrupt(*, expires_at_epoch: float = 0.0) -> ApprovalInterrupt:
    context = ConversationContext()
    context.add_system_message("system")
    _append_tool_tail(context)
    return ApprovalInterrupt(
        session_id="s1",
        approval_id="a1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="stored-model",
        expires_at_epoch=expires_at_epoch,
    )


@pytest.mark.asyncio
async def test_execute_without_session_does_not_save_and_adds_user_once() -> None:
    service, store = _service()
    user_counts: list[int] = []

    async def run_agent(context: ConversationContext, _config: AgentConfig) -> AgentResult:
        user_counts.append(sum(isinstance(msg, UserMessage) for msg in context.get_messages()))
        return AgentResult(content="done", model="test-model")

    result = await service.execute_task(
        Task(goal="goal"),
        prepare=lambda task, context: _plan(task.model or "test-model"),
        prepare_resume=lambda _session_id, _context, model: _resume_plan(model or "test-model"),
        run_agent=run_agent,
        can_continue=lambda _context: False,
    )

    assert result.status is TaskStatus.SUCCESS
    assert result.content == "done"
    assert user_counts == [1]
    assert store.saved == []


@pytest.mark.asyncio
async def test_execute_auto_continue_uses_resume_prepare_for_tool_boundary() -> None:
    context = ConversationContext()
    service, _ = _service(
        context=context,
        segment_policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )
    prepare_calls: list[str] = []
    observed_prompts: list[str] = []

    async def run_agent(ctx: ConversationContext, config: AgentConfig) -> AgentResult:
        observed_prompts.append(config.system_prompt)
        if len(observed_prompts) == 1:
            _append_tool_tail(ctx)
            return AgentResult(
                content="",
                model="test-model",
                terminated_reason="max_rounds",
                usage={"total_tokens": 1},
            )
        return AgentResult(content="done", model="test-model", usage={"total_tokens": 1})

    result = await service.execute_task(
        Task(goal="goal", session_id="s1"),
        prepare=lambda _task, _context: _plan(),
        prepare_resume=lambda _session_id, _context, _model: (
            prepare_calls.append("resume") or _resume_plan()
        ),
        run_agent=run_agent,
        can_continue=lambda current_context: isinstance(
            current_context.get_messages()[-1],
            ToolMessage,
        ),
    )

    assert result.status is TaskStatus.SUCCESS
    assert prepare_calls == ["resume"]
    assert observed_prompts == ["system", "resume-system"]


@pytest.mark.asyncio
async def test_continue_rejects_non_tool_tail_and_does_not_append_user() -> None:
    context = ConversationContext()
    context.add_user_message("goal")
    service, _ = _service(context=context)

    with pytest.raises(ContinuationUnavailableError):
        await service.continue_task(
            TaskContinueRequest(session_id="s1"),
            prepare=lambda _session_id, _context, _model: _plan(),
            run_agent=AsyncMock(),
            can_continue=lambda _context: False,
        )

    assert sum(isinstance(msg, UserMessage) for msg in context.get_messages()) == 1


@pytest.mark.asyncio
async def test_resume_approval_consumes_before_resume_and_saves_success() -> None:
    approval_store = _ApprovalStore(_interrupt())
    service, store = _service(approval_store=approval_store)
    calls: list[str] = []

    async def resume_agent(
        context: ConversationContext,
        config: AgentConfig,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        calls.append("resume")
        assert config.model == "override-model"
        assert interrupt.approval_id == "a1"
        assert decisions[0].tool_call_id == "call-1"
        context.add_tool_result("search", "ok", "call-1")
        return AgentResult(content="done", model="override-model")

    result = await service.resume_approval(
        TaskApprovalResumeRequest(
            session_id="s1",
            approval_id="a1",
            decisions=(ApprovalDecision("approve", "call-1"),),
            model="override-model",
        ),
        prepare=lambda _session_id, _context, model: _plan(model or "missing"),
        resume_agent=resume_agent,
        can_continue=lambda _context: False,
    )

    assert approval_store.calls == ["load", "consume"]
    assert calls == ["resume"]
    assert result.status is TaskStatus.SUCCESS
    assert store.saved[0][0] == "s1"


@pytest.mark.asyncio
async def test_resume_approval_invalid_decision_does_not_consume() -> None:
    approval_store = _ApprovalStore(_interrupt())
    service, _ = _service(approval_store=approval_store)

    with pytest.raises(ApprovalDecisionCountMismatchError):
        await service.resume_approval(
            TaskApprovalResumeRequest(session_id="s1", approval_id="a1", decisions=()),
            prepare=lambda _session_id, _context, _model: _plan(),
            resume_agent=AsyncMock(),
            can_continue=lambda _context: False,
        )

    assert approval_store.calls == ["load"]


@pytest.mark.asyncio
async def test_resume_approval_expired_does_not_consume() -> None:
    approval_store = _ApprovalStore(_interrupt(expires_at_epoch=1.0))
    service, _ = _service(approval_store=approval_store)

    with pytest.raises(ApprovalExpiredError):
        await service.resume_approval(
            TaskApprovalResumeRequest(
                session_id="s1",
                approval_id="a1",
                decisions=(ApprovalDecision("approve", "call-1"),),
            ),
            prepare=lambda _session_id, _context, _model: _plan(),
            resume_agent=AsyncMock(),
            can_continue=lambda _context: False,
        )

    assert approval_store.calls == ["load"]
