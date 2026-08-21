"""ChatApplicationService 单元测试。"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.chat.chat_application_service import ChatApplicationService
from application.chat.session_context_workflow import ChatSessionContextWorkflow
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.ports import ApprovalStateStorePort
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalRequiredPayload,
    PendingActionRequest,
    ApprovalDecisionType,
)
from domain.chat.context import ConversationContext, ToolMessage, UserMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.ports import SessionContextStorePort
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatContinueRequestVO
from domain.model_access.value_objects import ToolCallRequest


class _MemorySessionStore:
    """测试用会话存储。"""

    def __init__(self, context: ConversationContext) -> None:
        self.context = context
        self.saved: list[tuple[str, ConversationContext]] = []

    async def load(self, session_id: str) -> ConversationContext:
        """返回预置上下文。"""

        return self.context

    async def save(self, session_id: str, context: ConversationContext) -> None:
        """记录保存调用。"""

        self.saved.append((session_id, context))


class _ApprovalStore:
    """测试用审批状态存储。"""

    def __init__(self, interrupt: ApprovalInterrupt | None) -> None:
        self.interrupt = interrupt
        self.consume_result = interrupt
        self.calls: list[str] = []

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """记录并返回审批中断。"""

        self.calls.append("load")
        return self.interrupt

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """记录并返回消费结果。"""

        self.calls.append("consume")
        return self.consume_result


def _valid_context() -> ConversationContext:
    """构造可继续的聊天上下文。"""

    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")
    return context


def _action(
    tool_call_id: str = "call-1",
    *,
    allowed: frozenset[ApprovalDecisionType] = frozenset({"approve", "reject"}),
) -> PendingActionRequest:
    """构造审批动作。"""

    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name="write_file",
        arguments="{}",
        allowed_decisions=allowed,
    )


def _interrupt(
    *,
    action: PendingActionRequest | None = None,
    expires_at_epoch: float = 0.0,
) -> ApprovalInterrupt:
    """构造审批中断。"""

    context = ConversationContext()
    context.add_user_message("hello")
    return ApprovalInterrupt(
        session_id="s1",
        approval_id="a1",
        actions=(action or _action(),),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="gpt-test",
        expires_at_epoch=expires_at_epoch,
    )


def _service(
    *,
    context: ConversationContext | None = None,
    approval_store: _ApprovalStore | None = None,
    agent: MagicMock | None = None,
    segment_policy: SegmentExecutionPolicy | None = None,
) -> tuple[ChatApplicationService, _MemorySessionStore]:
    """构造测试用应用服务。"""

    store = _MemorySessionStore(context or ConversationContext())
    workflow = ChatSessionContextWorkflow(
        cast(SessionContextStorePort, store),
        None,
        "system",
        "chat-default@v1",
    )

    def _make_config(model: str | None) -> AgentConfig:
        return AgentConfig(
            system_prompt="system",
            tool_schemas=[{"type": "function", "function": {"name": "write_file"}}],
            model=model,
            max_rounds=3,
            prompt_id="chat-default@v1",
        )

    service = ChatApplicationService(
        session_workflow=workflow,
        agent=agent or MagicMock(),
        approval_store=cast(ApprovalStateStorePort | None, approval_store),
        segment_policy=segment_policy or SegmentExecutionPolicy(),
        resolve_model_access=lambda _model: (MagicMock(), "gpt-test"),
        make_agent_config=_make_config,
    )
    return service, store


def _append_tool_tail(context: ConversationContext, call_id: str = "call-1") -> None:
    """追加一段可继续的工具尾部。"""

    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=call_id, name="search", arguments='{"q":"x"}')],
    )
    context.add_tool_result("search", "result", call_id)


@pytest.mark.asyncio
async def test_continue_chat_does_not_append_user_and_saves_completed_context() -> None:
    """继续执行不追加用户消息，完成后追加助手消息并保存。"""

    context = _valid_context()
    service, store = _service(context=context)
    observed_user_counts: list[int] = []

    async def _run_agent(ctx: ConversationContext, model: str | None) -> AgentResult:
        observed_user_counts.append(
            sum(isinstance(message, UserMessage) for message in ctx.get_messages())
        )
        assert model is None
        return AgentResult(content="done", model="gpt-test", usage={"total_tokens": 1})

    response = await service.continue_chat(
        ChatContinueRequestVO(session_id="s1"),
        run_agent=_run_agent,
    )

    assert observed_user_counts == [1]
    assert response.status == "completed"
    assert response.reply == "done"
    assert response.prompt_id == "chat-default@v1"
    assert store.saved[0][1].get_messages()[-1].content == "done"


@pytest.mark.asyncio
async def test_continue_chat_rejects_empty_context_with_existing_reason() -> None:
    """空上下文保持既有不可继续原因。"""

    service, _ = _service(context=ConversationContext())

    with pytest.raises(ContinuationUnavailableError) as exc_info:
        await service.continue_chat(
            ChatContinueRequestVO(session_id="s1"),
            run_agent=AsyncMock(),
        )

    assert exc_info.value.reason == "缺少可继续的上下文"


@pytest.mark.asyncio
async def test_continue_chat_rejects_non_tool_tail_with_existing_reason() -> None:
    """非工具尾部保持既有不可继续原因。"""

    context = ConversationContext()
    context.add_user_message("goal")
    service, _ = _service(context=context)

    with pytest.raises(ContinuationUnavailableError) as exc_info:
        await service.continue_chat(
            ChatContinueRequestVO(session_id="s1"),
            run_agent=AsyncMock(),
        )

    assert exc_info.value.reason == "最新消息不是工具结果"


@pytest.mark.asyncio
async def test_run_segmented_chat_auto_continue_completes_and_accumulates_usage() -> None:
    """分段同步执行自动续跑完成，累计 usage 且不追加重复 user message。"""

    context = ConversationContext()
    context.add_user_message("goal")
    service, store = _service(
        context=context,
        segment_policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )
    observed_user_counts: list[int] = []

    async def _run_agent(ctx: ConversationContext, model: str | None) -> AgentResult:
        observed_user_counts.append(
            sum(isinstance(message, UserMessage) for message in ctx.get_messages())
        )
        assert model == "gpt-test"
        if len(observed_user_counts) == 1:
            _append_tool_tail(ctx)
            return AgentResult(
                content="",
                model="gpt-test",
                usage={"total_tokens": 5},
                terminated_reason="max_rounds",
            )
        return AgentResult(
            content="done",
            model="gpt-test",
            usage={"total_tokens": 3},
        )

    response = await service.run_segmented_chat_on_context(
        session_id="s1",
        context=context,
        model="gpt-test",
        run_agent=_run_agent,
    )

    assert response.status == "completed"
    assert response.reply == "done"
    assert response.usage == {"total_tokens": 8}
    assert response.prompt_id == "chat-default@v1"
    assert response.segment_metadata is not None
    assert response.segment_metadata.segment_count == 2
    assert response.segment_metadata.auto_continue_attempted is True
    assert response.segment_metadata.segment_stop_reason == "completed"
    assert observed_user_counts == [1, 1]
    assert len(store.saved) == 2
    assert store.saved[-1][1].get_messages()[-1].content == "done"


@pytest.mark.asyncio
async def test_run_segmented_chat_paused_auto_disabled_saves_tool_tail_only() -> None:
    """自动续跑关闭时返回 paused metadata，保存工具尾部但不追加 assistant content。"""

    context = ConversationContext()
    context.add_user_message("goal")
    service, store = _service(context=context)

    async def _run_agent(ctx: ConversationContext, _model: str | None) -> AgentResult:
        _append_tool_tail(ctx)
        return AgentResult(
            content="",
            model="gpt-test",
            usage={"prompt_tokens": 2, "completion_tokens": 3},
            terminated_reason="max_rounds",
        )

    response = await service.run_segmented_chat_on_context(
        session_id="s1",
        context=context,
        model=None,
        run_agent=_run_agent,
    )

    assert response.status == "paused"
    assert response.can_continue is True
    assert response.usage == {"prompt_tokens": 2, "completion_tokens": 3}
    assert response.segment_metadata is not None
    assert response.segment_metadata.segment_count == 1
    assert response.segment_metadata.segment_stop_reason == "auto_disabled"
    assert response.segment_metadata.budget_usage.total_tokens == 5
    assert isinstance(store.saved[-1][1].get_messages()[-1], ToolMessage)


@pytest.mark.asyncio
async def test_run_segmented_chat_approval_required_does_not_save_and_sets_risk_gate() -> None:
    """审批中断停止分段且不保存，guardrail metadata 透传风险门。"""

    context = ConversationContext()
    context.add_user_message("goal")
    service, store = _service(
        context=context,
        segment_policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )
    approval = ApprovalRequiredPayload(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        prompt_id="chat-default@v1",
        metadata={
            "source": "guardrail",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": True,
        },
    )

    async def _run_agent(_ctx: ConversationContext, _model: str | None) -> AgentResult:
        return AgentResult(
            content="",
            model="gpt-test",
            status="approval_required",
            approval=approval,
        )

    response = await service.run_segmented_chat_on_context(
        session_id="s1",
        context=context,
        model=None,
        run_agent=_run_agent,
    )

    assert response.status == "approval_required"
    assert response.approval_id == "approval-1"
    assert response.segment_metadata is not None
    assert response.segment_metadata.segment_stop_reason == "approval_required"
    assert response.segment_metadata.risk_gate_required is True
    assert response.segment_metadata.guardrail_reason == "tool_risk_gate_required"
    assert store.saved == []


@pytest.mark.asyncio
async def test_stream_segmented_chat_emits_business_frames_and_saves_completed_context() -> None:
    """分段流应用层产出业务帧，线格式包装留给 adapter。"""

    context = ConversationContext()
    context.add_user_message("goal")
    service, store = _service(
        context=context,
        segment_policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )
    calls = 0

    async def _run_events(ctx: ConversationContext, _model: str | None):
        nonlocal calls
        calls += 1
        if calls == 1:
            _append_tool_tail(ctx)
            yield AgentStreamEvent(
                kind="assistant_done",
                usage={"total_tokens": 2},
                metadata={"terminated_reason": "max_rounds"},
            )
            return
        yield AgentStreamEvent(kind="assistant_delta", content="done")
        yield AgentStreamEvent(kind="assistant_done", usage={"total_tokens": 3})

    frames = [
        frame
        async for frame in service.stream_segmented_chat_on_context(
            session_id="s1",
            context=context,
            model=None,
            run_events=_run_events,
        )
    ]

    assert [frame.kind for frame in frames] == [
        "segment_done",
        "forward",
        "segment_done",
        "final_done",
    ]
    assert frames[0].segment_metadata is not None
    assert frames[0].segment_metadata.segment_stop_reason == "completed"
    assert frames[-1].segment_metadata is not None
    assert frames[-1].segment_metadata.segment_count == 2
    assert frames[-1].segment_metadata.segment_stop_reason == "completed"
    assert len(store.saved) == 2
    assert store.saved[-1][1].get_messages()[-1].content == "done"


@pytest.mark.asyncio
async def test_resume_approval_loads_consumes_then_resumes_agent() -> None:
    """审批恢复按 load、consume、agent.resume 顺序执行。"""

    calls: list[str] = []
    store = _ApprovalStore(_interrupt())

    async def _resume(
        context: ConversationContext,
        config: AgentConfig,
        _model_access: object,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        store.calls.append("resume")
        calls.append("resume")
        assert context.session_id == "s1"
        assert config.model == "gpt-test"
        assert interrupt.approval_id == "a1"
        assert decisions[0].tool_call_id == "call-1"
        return AgentResult(content="done", model="gpt-test")

    agent = MagicMock()
    agent.resume = _resume
    service, _ = _service(approval_store=store, agent=agent)

    context, result = await service.resume_approval_to_agent_result(
        ApprovalResumeRequestVO(
            session_id="s1",
            approval_id="a1",
            decisions=(ApprovalDecision("approve", "call-1"),),
        )
    )

    assert context.session_id == "s1"
    assert result.content == "done"
    assert store.calls == ["load", "consume", "resume"]
    assert calls == ["resume"]


@pytest.mark.asyncio
async def test_resume_approval_without_store_raises_not_found() -> None:
    """未配置审批存储时按既有语义返回 not found。"""

    service, _ = _service(approval_store=None)

    with pytest.raises(ApprovalNotFoundError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
        )


@pytest.mark.asyncio
async def test_resume_approval_missing_interrupt_raises_not_found() -> None:
    """审批批次不存在时返回 not found 且不消费。"""

    store = _ApprovalStore(None)
    service, _ = _service(approval_store=store)

    with pytest.raises(ApprovalNotFoundError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
        )

    assert store.calls == ["load"]


@pytest.mark.asyncio
async def test_resume_approval_expired_raises_before_consume() -> None:
    """审批过期时抛 expired，且不执行 consume/resume。"""

    store = _ApprovalStore(_interrupt(expires_at_epoch=1.0))
    agent = MagicMock()
    agent.resume = AsyncMock()
    service, _ = _service(approval_store=store, agent=agent)

    with pytest.raises(ApprovalExpiredError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
        )

    assert store.calls == ["load"]
    agent.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_approval_consumed_raises_before_resume() -> None:
    """consume 返回 None 时抛 consumed，且不执行 agent.resume。"""

    store = _ApprovalStore(_interrupt())
    store.consume_result = None
    agent = MagicMock()
    agent.resume = AsyncMock()
    service, _ = _service(approval_store=store, agent=agent)

    with pytest.raises(ApprovalConsumedError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
        )

    assert store.calls == ["load", "consume"]
    agent.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_approval_count_mismatch_raises_before_consume() -> None:
    """决策数量与动作数量不一致时不消费。"""

    store = _ApprovalStore(_interrupt())
    service, _ = _service(approval_store=store)

    with pytest.raises(ApprovalDecisionCountMismatchError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO(
                "s1",
                "a1",
                (
                    ApprovalDecision("approve", "call-1"),
                    ApprovalDecision("approve", "call-2"),
                ),
            )
        )

    assert store.calls == ["load"]


@pytest.mark.asyncio
async def test_resume_approval_order_mismatch_raises_before_consume() -> None:
    """决策 tool_call_id 与动作顺序不一致时不消费。"""

    store = _ApprovalStore(_interrupt(action=_action("call-1")))
    service, _ = _service(approval_store=store)

    with pytest.raises(ApprovalDecisionOrderMismatchError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "wrong"),))
        )

    assert store.calls == ["load"]


@pytest.mark.asyncio
async def test_resume_approval_not_allowed_raises_before_consume() -> None:
    """决策类型不在动作允许集合内时不消费。"""

    store = _ApprovalStore(_interrupt(action=_action(allowed=frozenset({"reject"}))))
    service, _ = _service(approval_store=store)

    with pytest.raises(ApprovalDecisionNotAllowedError):
        await service.resume_approval_to_agent_result(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
        )

    assert store.calls == ["load"]
