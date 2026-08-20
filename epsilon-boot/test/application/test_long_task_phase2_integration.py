"""Long Task Continuation Phase 2 集成验收测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatRequestVO
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskContinueRequest, TaskStatus
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.task.task_agent_adapter import TaskAgentAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _loaded_prompt(name: str) -> LoadedPrompt:
    return LoadedPrompt(prompt_id=f"{name}@v1", name=name, version="v1", content="system")


def _tool_schema(name: str = "search") -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _append_tool_tail(context: ConversationContext, index: int = 1, arguments: str = "{}") -> None:
    call_id = f"call-{index}"
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=call_id, name="search", arguments=arguments)],
    )
    context.add_tool_result("search", f"result-{index}", call_id)


def _chat_adapter(
    agent: MagicMock, context: ConversationContext, policy: SegmentExecutionPolicy
) -> ChatServiceAdapter:
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context)
    session_store.save = AsyncMock()
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    loaded_prompt = _loaded_prompt("chat-default")
    tool_schemas = [_tool_schema()]
    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=MagicMock(get=MagicMock(return_value=loaded_prompt)),
        context_builder=MagicMock(),
        agent=agent,
        tool_calling_enabled=True,
        max_tool_rounds=3,
        tool_schemas=tool_schemas,
        segment_policy=policy,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=3,
            segment_policy=policy,
        ),
    )


def _task_adapter(
    agent: MagicMock, context: ConversationContext, policy: SegmentExecutionPolicy
) -> TaskAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.get_schemas.side_effect = lambda tool_names=None: [_tool_schema()]
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context)
    session_store.save = AsyncMock()
    return TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=MagicMock(),
        session_store=session_store,
        prompt_registry=MagicMock(get=MagicMock(return_value=_loaded_prompt("task-template"))),
        max_rounds=4,
        segment_policy=policy,
    )


@pytest.mark.asyncio
async def test_chat_sync_two_paused_segments_then_completed_preserves_user_and_round_limit() -> (
    None
):
    """Chat 同步 max_rounds -> max_rounds -> completed。"""
    context = ConversationContext()
    user_counts: list[int] = []
    round_limits: list[int] = []

    async def run(ctx, config, _model_access):
        user_counts.append(sum(isinstance(message, UserMessage) for message in ctx.get_messages()))
        round_limits.append(config.max_rounds)
        if len(user_counts) <= 2:
            _append_tool_tail(ctx, len(user_counts))
            return AgentResult(
                content="",
                model="test-model",
                usage={"total_tokens": 1},
                terminated_reason="max_rounds",
            )
        return AgentResult(content="done", model="test-model", usage={"total_tokens": 2})

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _chat_adapter(
        agent,
        context,
        SegmentExecutionPolicy(
            auto_continue_enabled=True, max_continuations=3, max_consecutive_paused=5
        ),
    )

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.status == "completed"
    assert response.segment_metadata.segment_count == 3
    assert response.segment_metadata.auto_continue_attempted is True
    assert response.segment_metadata.budget_usage.total_tokens == 4
    assert user_counts == [1, 1, 1]
    assert round_limits == [3, 3, 3]


@pytest.mark.asyncio
async def test_task_sync_paused_then_completed_merges_usage_and_trace() -> None:
    """Task 同步 max_rounds -> completed。"""
    context = ConversationContext()
    user_counts: list[int] = []

    async def run(ctx, config, _model_access):
        user_counts.append(sum(isinstance(message, UserMessage) for message in ctx.get_messages()))
        assert config.max_rounds == 4
        if len(user_counts) == 1:
            _append_tool_tail(ctx, 1)
            return AgentResult(
                content="",
                model="test-model",
                usage={"total_tokens": 2},
                terminated_reason="max_rounds",
            )
        return AgentResult(content="done", model="test-model", usage={"total_tokens": 3})

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _task_adapter(
        agent,
        context,
        SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.SUCCESS
    assert result.segment_metadata.segment_count == 2
    assert result.usage["total_tokens"] == 5
    assert len(result.trace) == 2
    assert user_counts == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected_reason"),
    [
        (SegmentExecutionPolicy(), "auto_disabled"),
        (
            SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=0),
            "max_continuations_reached",
        ),
        (
            SegmentExecutionPolicy(auto_continue_enabled=True, max_total_tokens=1),
            "total_token_budget_reached",
        ),
    ],
)
async def test_chat_sync_stop_reasons(policy: SegmentExecutionPolicy, expected_reason: str) -> None:
    """覆盖自动续跑关闭、最大续跑、token budget 和无进展停止。"""
    context = ConversationContext()

    async def run(ctx, _config, _model_access):
        if expected_reason != "no_progress":
            _append_tool_tail(ctx)
            usage = {"total_tokens": 1}
        else:
            usage = {}
        return AgentResult(
            content="", model="test-model", usage=usage, terminated_reason="max_rounds"
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _chat_adapter(agent, context, policy)

    response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

    assert response.segment_metadata.segment_stop_reason == expected_reason


@pytest.mark.asyncio
async def test_task_continue_no_progress_stops_without_extra_agent_runs() -> None:
    """Task 继续段可继续但无新增进展时按 no_progress 停止。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.get_messages()[0].metadata["task_allowed_tool_names"] = ["search"]
    context.add_user_message("goal")
    _append_tool_tail(context, 1)
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            usage={},
            terminated_reason="max_rounds",
        )
    )
    adapter = _task_adapter(
        agent,
        context,
        SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_continuations=2,
            max_no_progress_segments=1,
        ),
    )

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.segment_metadata.segment_stop_reason == "no_progress"
    assert agent.run.await_count == 1
