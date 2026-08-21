"""任务继续上下文不变量属性测试。"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.ports import AgentPort
from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import ConversationContext, SystemMessage, ToolMessage
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import TaskContinueRequest, TaskStatus
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _schema(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _context(user_count: int) -> ConversationContext:
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="system",
            metadata={"task_allowed_tool_names": ["search"]},
        )
    )
    for index in range(user_count):
        context.add_user_message(f"user-{index}")
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")
    return context


def _adapter(agent: AgentPort, context: ConversationContext) -> TaskAgentAdapter:
    registry = MagicMock()
    registry.get_schemas.return_value = [_schema("search")]
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context)
    session_store.save = AsyncMock()
    return TaskAgentAdapter(
        agent=agent,
        tool_registry=registry,
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
    )


@settings(max_examples=25, deadline=3000)
@given(user_count=st.integers(min_value=1, max_value=5))
@pytest.mark.asyncio
async def test_continue_task_preserves_user_count(user_count: int) -> None:
    """Property 3：继续请求不追加用户消息。"""
    context = _context(user_count)

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        assert sum(1 for message in ctx.get_messages() if message.role == "user") == user_count
        return AgentResult(content="done", model="test-model")

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = _adapter(agent, context)

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.status == TaskStatus.SUCCESS


@settings(max_examples=25, deadline=3000)
@given(user_count=st.integers(min_value=1, max_value=5))
@pytest.mark.asyncio
async def test_continue_task_paused_tail_remains_tool(user_count: int) -> None:
    """Property 2：暂停保存时尾部不追加空最终助手消息。"""
    context = _context(user_count)
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
        )
    )
    adapter = _adapter(agent, context)

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert isinstance(context.get_messages()[-1], ToolMessage)
