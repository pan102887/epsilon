"""Task 分段停止原因测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import ConversationContext, SystemMessage
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskContinueRequest, TaskStatus
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _schema(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _adapter(
    *,
    agent: MagicMock,
    context: ConversationContext,
    policy: SegmentExecutionPolicy,
    schemas: list[dict[str, Any]] | None = None,
) -> TaskAgentAdapter:
    all_schemas = schemas or [_schema("search")]
    tool_registry = MagicMock()

    def get_schemas(tool_names: frozenset[str] | None = None) -> list[dict[str, Any]]:
        if tool_names is None:
            return list(all_schemas)
        requested = set(tool_names)
        return [schema for schema in all_schemas if schema["function"]["name"] in requested]

    tool_registry.get_schemas.side_effect = get_schemas
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
        segment_policy=policy,
    )


def _append_tool_tail(context: ConversationContext, arguments: str = "{}") -> None:
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id=f"call-{context.message_count}", name="search", arguments=arguments)],
    )
    context.add_tool_result("search", "result", f"call-{context.message_count - 1}")


@pytest.mark.asyncio
async def test_execute_stops_on_tool_boundary_unavailable() -> None:
    """暂停后工具边界不可重建时停止原因是 tool_boundary_unavailable。"""
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="legacy",
            metadata={"task_allowed_tool_names": ["missing"]},
        )
    )

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        _append_tool_tail(ctx)
        return AgentResult(content="", model="test-model", terminated_reason="max_rounds")

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(auto_continue_enabled=True, max_continuations=2),
        schemas=[_schema("search")],
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.segment_stop_reason == "tool_boundary_unavailable"
    assert agent.run.await_count == 1


@pytest.mark.asyncio
async def test_execute_stops_on_no_progress() -> None:
    """无 trace、无 token、无最终内容时按 no_progress 停止。"""
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
            usage={},
            terminated_reason="max_rounds",
        )
    )
    adapter = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_continuations=2,
            max_no_progress_segments=1,
        ),
    )

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.segment_metadata.segment_stop_reason == "no_progress"
    assert agent.run.await_count == 1


@pytest.mark.asyncio
async def test_execute_stops_on_repeated_tool_call() -> None:
    """连续相同工具调用达到阈值时按 repeated_tool_call 停止。"""
    context = ConversationContext()

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        _append_tool_tail(ctx, arguments='{"q":"same"}')
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 1},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_continuations=3,
            max_consecutive_paused=10,
            max_repeated_tool_calls=1,
        ),
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.segment_metadata.segment_stop_reason == "repeated_tool_call"
    assert agent.run.await_count == 2


@pytest.mark.asyncio
async def test_execute_stops_on_repeated_tool_call_with_equivalent_json_arguments() -> None:
    """JSON 参数顺序不同但语义相同时也按重复工具调用停止。"""
    context = ConversationContext()
    arguments_by_segment = [
        '{"q":"same","page":1}',
        '{"page":1,"q":"same"}',
        '{"q":"other","page":2}',
        '{"page":2,"q":"other"}',
    ]

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        _append_tool_tail(ctx, arguments=arguments_by_segment.pop(0))
        return AgentResult(
            content="",
            model="test-model",
            usage={"total_tokens": 1},
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _adapter(
        agent=agent,
        context=context,
        policy=SegmentExecutionPolicy(
            auto_continue_enabled=True,
            max_continuations=3,
            max_consecutive_paused=10,
            max_repeated_tool_calls=1,
        ),
    )

    result = await adapter.execute(Task(goal="goal", session_id="s1"))

    assert result.segment_metadata.segment_stop_reason == "repeated_tool_call"
    assert agent.run.await_count == 2
