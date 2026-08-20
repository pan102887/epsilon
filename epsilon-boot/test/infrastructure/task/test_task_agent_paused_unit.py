"""TaskAgentAdapter 暂停与继续单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import ApprovalDecisionCountMismatchError
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, SystemMessage, ToolMessage, UserMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import (
    Task,
    TaskApprovalResumeRequest,
    TaskContinueRequest,
    TaskStatus,
)
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _schema(name: str) -> dict:
    """构造测试工具 schema。"""
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": {}},
    }


def _valid_context(boundary: list[str] | None = None) -> ConversationContext:
    """构造满足任务继续前置条件的上下文。"""
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="system",
            metadata={"task_allowed_tool_names": boundary},
        )
    )
    context.add_user_message("goal")
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")
    return context


def _adapter(
    *,
    agent: MagicMock,
    context: ConversationContext | None = None,
    schemas: list[dict] | None = None,
    max_rounds: int = 5,
    approval_store: MagicMock | None = None,
) -> tuple[TaskAgentAdapter, MagicMock, MagicMock]:
    """构造测试用任务适配器。"""
    all_schemas = schemas or [_schema("search"), _schema("write")]
    tool_registry = MagicMock()

    def get_schemas(tool_names=None):
        if tool_names is None:
            return list(all_schemas)
        return [schema for schema in all_schemas if schema["function"]["name"] in set(tool_names)]

    tool_registry.get_schemas.side_effect = get_schemas
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context or ConversationContext())
    session_store.save = AsyncMock()
    adapter = TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=MagicMock(),
        session_store=session_store,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=LoadedPrompt(
                    prompt_id="task-template@v1",
                    name="task-template",
                    version="v1",
                    content="template",
                )
            )
        ),
        approval_store=approval_store,
        max_rounds=max_rounds,
    )
    return adapter, session_store, tool_registry


@pytest.mark.asyncio
@pytest.mark.parametrize("terminated_reason", ["max_rounds", "token_budget_exceeded"])
async def test_execute_returns_paused_and_saves_context(terminated_reason: str) -> None:
    """验证 execute 对阶段边界返回 PAUSED 并保存上下文。"""
    context = ConversationContext()

    async def run(ctx, _config, _model_access):
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        ctx.add_tool_result("search", "result", "call-1")
        return AgentResult(
            content="",
            model="test-model",
            terminated_reason=terminated_reason,
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store, _ = _adapter(agent=agent, context=context)

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.content == ""
    assert result.terminated_reason == terminated_reason
    assert result.can_continue is True
    saved_context = session_store.save.call_args.args[1]
    assert isinstance(saved_context.get_messages()[-1], ToolMessage)


@pytest.mark.asyncio
async def test_execute_persists_tool_boundary_metadata() -> None:
    """验证 execute 首次注入 system message 时持久化工具访问边界。"""
    agent = MagicMock()
    agent.run = AsyncMock(return_value=AgentResult(content="done", model="test-model"))
    adapter, session_store, _ = _adapter(agent=agent)

    await adapter.execute(Task(goal="goal", session_id="s1", tool_names=frozenset({"search"})))

    saved_context = session_store.save.call_args.args[1]
    system_message = saved_context.get_messages()[0]
    assert system_message.metadata["task_allowed_tool_names"] == ["search"]


@pytest.mark.asyncio
async def test_execute_backfills_missing_tool_boundary_metadata_for_legacy_system() -> None:
    """验证当前任务执行会为旧 system 消息补写本次已知工具边界。"""
    context = ConversationContext()
    context.add_system_message("legacy system")

    async def run(ctx, _config, _model_access):
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        ctx.add_tool_result("search", "result", "call-1")
        return AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store, _ = _adapter(agent=agent, context=context)

    result = await adapter.execute(
        Task(goal="goal", session_id="s1", tool_names=frozenset({"search"}))
    )

    assert result.status == TaskStatus.PAUSED
    assert result.can_continue is True
    saved_context = session_store.save.call_args.args[1]
    system_message = saved_context.get_messages()[0]
    assert system_message.metadata["task_allowed_tool_names"] == ["search"]


@pytest.mark.asyncio
async def test_execute_paused_can_continue_false_when_tool_boundary_cannot_rebuild() -> None:
    """验证 paused can_continue 与 continue_task 的工具边界前置条件一致。"""
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="system",
            metadata={"task_allowed_tool_names": ["missing_tool"]},
        )
    )

    async def run(ctx, _config, _model_access):
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-1", name="search", arguments="{}")],
        )
        ctx.add_tool_result("search", "result", "call-1")
        return AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _, _ = _adapter(agent=agent, context=context)

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.can_continue is False


@pytest.mark.asyncio
async def test_continue_task_does_not_append_user_and_preserves_config() -> None:
    """验证 continue_task 不追加 user，且沿用 max_rounds 与工具子集。"""
    context = _valid_context(boundary=["search"])
    captured_configs: list[AgentConfig] = []

    async def run(ctx, config, _model_access):
        captured_configs.append(config)
        assert sum(1 for message in ctx.get_messages() if isinstance(message, UserMessage)) == 1
        return AgentResult(content="done", model="test-model")

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store, _ = _adapter(agent=agent, context=context, max_rounds=7)

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.status == TaskStatus.SUCCESS
    assert captured_configs[0].max_rounds == 7
    assert captured_configs[0].allowed_tool_names == frozenset({"search"})
    session_store.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_continue_task_pauses_again() -> None:
    """验证 continue_task 再次命中阶段边界时返回 PAUSED。"""
    context = _valid_context(boundary=["search"])
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
        )
    )
    adapter, _, _ = _adapter(agent=agent, context=context)

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.can_continue is True


@pytest.mark.asyncio
async def test_continue_task_rejects_missing_tool_boundary() -> None:
    """验证缺失 task_allowed_tool_names metadata 时拒绝继续。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("goal")
    context.add_tool_result("search", "result", "call-1")
    agent = MagicMock()
    adapter, _, _ = _adapter(agent=agent, context=context)

    with pytest.raises(ContinuationUnavailableError, match="工具访问边界"):
        await adapter.continue_task(TaskContinueRequest(session_id="s1"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        ConversationContext(),
        ConversationContext(),
        ConversationContext(),
    ],
)
async def test_continue_task_rejects_invalid_context(context: ConversationContext) -> None:
    """验证空会话、缺少 system、尾部非 ToolMessage 时拒绝继续。"""
    if context is not None and context.message_count == 0:
        pass
    agent = MagicMock()
    adapter, _, _ = _adapter(agent=agent, context=context)

    with pytest.raises(ContinuationUnavailableError):
        await adapter.continue_task(TaskContinueRequest(session_id="s1"))


@pytest.mark.asyncio
async def test_resume_approval_restores_context_boundary_and_returns_new_approval_id() -> None:
    """验证任务审批恢复复用中断快照、保留工具边界并透传新 approval_id。"""
    context = _valid_context(boundary=["search"])
    approval_store = MagicMock()
    approval_store.load = AsyncMock(
        return_value=ApprovalInterrupt(
            session_id="s1",
            approval_id="approval-1",
            actions=(
                PendingActionRequest(
                    tool_call_id="call-1",
                    tool_name="search",
                    arguments="{}",
                    allowed_decisions=frozenset({"approve"}),
                ),
            ),
            context_snapshot=context.to_dict(),
            round_num=1,
            model="stored-model",
        )
    )
    approval_store.consume = AsyncMock(return_value=approval_store.load.return_value)
    captured_configs: list[AgentConfig] = []

    async def resume(ctx, config, _model_access, interrupt, decisions):
        captured_configs.append(config)
        assert ctx.session_id == "s1"
        assert interrupt.approval_id == "approval-1"
        assert decisions == (ApprovalDecision(type="approve", tool_call_id="call-1"),)
        return AgentResult(
            content="",
            model="resumed-model",
            status="approval_required",
            approval=ApprovalRequiredPayload(
                session_id="s1",
                approval_id="approval-2",
                actions=(
                    PendingActionRequest(
                        tool_call_id="call-2",
                        tool_name="search",
                        arguments='{"q":"next"}',
                        allowed_decisions=frozenset({"approve", "reject"}),
                        reason="need approval again",
                    ),
                ),
                prompt_id="task-template@v1",
            ),
        )

    agent = MagicMock()
    agent.resume = AsyncMock(side_effect=resume)
    adapter, session_store, _ = _adapter(
        agent=agent,
        context=context,
        approval_store=approval_store,
    )

    result = await adapter.resume_approval(
        TaskApprovalResumeRequest(
            session_id="s1",
            approval_id="approval-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
            model="override-model",
        )
    )

    assert result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED
    assert result.approval_id == "approval-2"
    assert result.model == "resumed-model"
    assert result.segment_metadata.risk_gate_required is False
    assert result.segment_metadata.guardrail_reason is None
    assert captured_configs[0].model == "override-model"
    assert captured_configs[0].allowed_tool_names == frozenset({"search"})
    session_store.save.assert_not_called()


@pytest.mark.asyncio
async def test_resume_approval_pauses_without_appending_original_goal_and_saves_context() -> None:
    """验证任务审批恢复后再次暂停时仍复用原上下文且不重复追加 goal。"""
    context = _valid_context(boundary=["search"])
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments="{}",
                allowed_decisions=frozenset({"approve"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="stored-model",
    )
    approval_store = MagicMock()
    approval_store.load = AsyncMock(return_value=interrupt)
    approval_store.consume = AsyncMock(return_value=interrupt)
    captured_configs: list[AgentConfig] = []

    async def resume(ctx, config, _model_access, consumed, decisions):
        captured_configs.append(config)
        assert consumed.approval_id == "approval-1"
        assert decisions == (ApprovalDecision(type="approve", tool_call_id="call-1"),)
        assert sum(1 for message in ctx.get_messages() if isinstance(message, UserMessage)) == 1
        return AgentResult(
            content="",
            model="resumed-model",
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.resume = AsyncMock(side_effect=resume)
    adapter, session_store, _ = _adapter(
        agent=agent,
        context=context,
        approval_store=approval_store,
    )

    result = await adapter.resume_approval(
        TaskApprovalResumeRequest(
            session_id="s1",
            approval_id="approval-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
            model="override-model",
        )
    )

    assert result.status == TaskStatus.PAUSED
    assert result.can_continue is True
    assert result.approval_id is None
    assert result.model == "resumed-model"
    assert captured_configs[0].model == "override-model"
    assert captured_configs[0].allowed_tool_names == frozenset({"search"})
    session_store.save.assert_awaited_once()


async def test_resume_approval_rejects_decision_count_mismatch() -> None:
    """验证任务审批恢复沿用既有审批决策数量校验语义。"""
    context = _valid_context(boundary=["search"])
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments="{}",
                allowed_decisions=frozenset({"approve"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="stored-model",
    )
    approval_store = MagicMock()
    approval_store.load = AsyncMock(return_value=interrupt)
    approval_store.consume = AsyncMock(return_value=interrupt)
    agent = MagicMock()
    adapter, _, _ = _adapter(
        agent=agent,
        context=context,
        approval_store=approval_store,
    )

    with pytest.raises(ApprovalDecisionCountMismatchError):
        await adapter.resume_approval(
            TaskApprovalResumeRequest(
                session_id="s1",
                approval_id="approval-1",
                decisions=(),
            )
        )

    approval_store.consume.assert_not_awaited()
    agent.resume.assert_not_called()
