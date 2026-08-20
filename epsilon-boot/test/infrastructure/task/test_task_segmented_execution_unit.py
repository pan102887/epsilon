"""TaskAgentAdapter 分段执行测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import AgentResult, ApprovalRequiredPayload
from domain.chat.context import ConversationContext, SystemMessage, ToolMessage, UserMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskContinueRequest, TaskStatus
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _schema(name: str) -> dict:
    """构造测试工具 schema。"""
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _adapter(
    *,
    agent: MagicMock,
    context: ConversationContext | None = None,
    policy: SegmentExecutionPolicy,
    max_rounds: int = 5,
    schemas: list[dict] | None = None,
) -> tuple[TaskAgentAdapter, MagicMock, MagicMock]:
    """构造测试用 TaskAgentAdapter。"""
    all_schemas = schemas or [_schema("search"), _schema("write")]
    tool_registry = MagicMock()

    def get_schemas(tool_names=None):
        if tool_names is None:
            return list(all_schemas)
        requested = set(tool_names)
        return [schema for schema in all_schemas if schema["function"]["name"] in requested]

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
        max_rounds=max_rounds,
        segment_policy=policy,
    )
    return adapter, session_store, tool_registry


def _append_tool_tail(context: ConversationContext, index: int = 1) -> None:
    """追加可继续的工具尾部。"""
    call_id = f"call-{index}"
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=call_id, name="search", arguments=f'{{"i":{index}}}')],
    )
    context.add_tool_result("search", f"result-{index}", call_id)


@pytest.mark.asyncio
async def test_execute_without_session_id_runs_single_segment_only() -> None:
    """无 session_id 时只执行首段，不自动续跑。"""

    async def run(ctx, _config, _model_access):
        _append_tool_tail(ctx)
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 2},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store, _ = _adapter(
        agent=agent,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=3),
    )

    result = await adapter.execute(Task(goal="goal"))

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.segment_count == 1
    assert result.segment_metadata.auto_continue_attempted is False
    assert agent.run.await_count == 1
    session_store.save.assert_not_called()


@pytest.mark.asyncio
async def test_execute_auto_disabled_returns_segment_metadata() -> None:
    """自动续跑关闭时返回 auto_disabled 停止原因。"""
    context = ConversationContext()

    async def run(ctx, _config, _model_access):
        _append_tool_tail(ctx)
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 3},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _, _ = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(),
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.segment_count == 1
    assert result.segment_metadata.segment_stop_reason == "auto_disabled"
    assert result.segment_metadata.budget_usage.total_tokens == 3


@pytest.mark.asyncio
async def test_execute_auto_continue_completes_and_merges_trace_usage_without_new_user() -> None:
    """自动续跑完成时合并 trace/usage，且不追加额外 user。"""
    context = ConversationContext()
    user_counts: list[int] = []
    max_rounds: list[int] = []

    async def run(ctx, config, _model_access):
        user_counts.append(sum(isinstance(message, UserMessage) for message in ctx.get_messages()))
        max_rounds.append(config.max_rounds)
        if len(user_counts) == 1:
            _append_tool_tail(ctx, 1)
            return AgentResult(
                content="",
                model="test-model",
                usage={"total_tokens": 2},
                terminated_reason="max_rounds",
            )
        return AgentResult(content="done", model="test-model", usage={"total_tokens": 4})

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store, _ = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
        max_rounds=7,
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.SUCCESS
    assert result.content == "done"
    assert result.usage["total_tokens"] == 6
    assert result.segment_metadata.segment_count == 2
    assert result.segment_metadata.auto_continue_attempted is True
    assert result.segment_metadata.segment_stop_reason == "completed"
    assert len(result.trace) == 2
    assert user_counts == [1, 1]
    assert max_rounds == [7, 7]
    saved_context = session_store.save.call_args.args[1]
    assert sum(isinstance(message, UserMessage) for message in saved_context.get_messages()) == 1


@pytest.mark.asyncio
async def test_execute_stops_on_total_token_budget() -> None:
    """累计 token 达预算时不自动续跑。"""
    context = ConversationContext()

    async def run(ctx, _config, _model_access):
        _append_tool_tail(ctx)
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 5},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _, _ = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_continuations=3,
            max_total_tokens=5,
        ),
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.segment_stop_reason == "total_token_budget_reached"
    assert agent.run.await_count == 1


@pytest.mark.asyncio
async def test_continue_stops_when_tool_boundary_unavailable_before_agent_run() -> None:
    """工具边界不可重建时拒绝继续且不调用 AgentPort.run。"""
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="system",
            metadata={"task_allowed_tool_names": ["missing"]},
        )
    )
    context.add_user_message("goal")
    _append_tool_tail(context)
    agent = MagicMock()
    agent.run = AsyncMock()
    adapter, _, _ = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
        schemas=[_schema("search")],
    )

    with pytest.raises(ContinuationUnavailableError):
        await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    agent.run.assert_not_called()


@pytest.mark.asyncio
async def test_execute_stops_on_risk_gate_required_from_tool_metadata() -> None:
    """首段命中稳定 guardrail metadata 时按 risk_gate_required 停止。"""
    context = ConversationContext()

    async def run(ctx, _config, _model_access):
        _append_tool_tail(ctx)
        tool_message = ctx.get_messages()[-1]
        assert isinstance(tool_message, ToolMessage)
        tool_message.metadata.update(
            {
                "guardrail_action": "stop",
                "guardrail_reason": "tool_risk_gate_required",
                "risk_gate_required": True,
            }
        )
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 2},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, _, _ = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=3),
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.segment_stop_reason == "risk_gate_required"
    assert result.segment_metadata.risk_gate_required is True
    assert result.segment_metadata.guardrail_reason == "tool_risk_gate_required"
    assert agent.run.await_count == 1


@pytest.mark.asyncio
async def test_continue_task_stops_on_guardrail_approval_metadata() -> None:
    """continue_task 在 guardrail 审批态时应透传风险门禁信号。"""
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="system",
            metadata={"task_allowed_tool_names": ["search"]},
        )
    )
    context.add_user_message("goal")
    _append_tool_tail(context)

    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            status="approval_required",
            approval=ApprovalRequiredPayload(
                session_id="s1",
                approval_id="approval-2",
                actions=(),
                prompt_id="task-template@v1",
                metadata={
                    "source": "guardrail",
                    "guardrail_reason": "tool_risk_gate_required",
                    "risk_gate_required": True,
                },
            ),
        )
    )
    adapter, _, _ = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED
    assert result.approval_id == "approval-2"
    assert result.segment_metadata.segment_stop_reason == "approval_required"
    assert result.segment_metadata.risk_gate_required is True
    assert result.segment_metadata.guardrail_reason == "tool_risk_gate_required"
