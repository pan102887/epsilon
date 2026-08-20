"""任务继续工具边界属性测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import ConversationContext, SystemMessage
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import TaskContinueRequest
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


@settings(max_examples=30, deadline=3000)
@given(
    names=st.lists(
        st.text(alphabet="abcde", min_size=1, max_size=4),
        min_size=1,
        max_size=5,
        unique=True,
    )
)
@pytest.mark.asyncio
async def test_continue_task_tool_boundary_never_broadens(names: list[str]) -> None:
    """Property 7：继续执行的工具集合不宽于持久化边界。"""
    boundary = sorted(names)
    all_schemas = [_schema(name) for name in boundary] + [_schema("outside")]
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
        [ToolCallRequest(id="call-1", name=boundary[0], arguments="{}")],
    )
    context.add_tool_result(boundary[0], "result", "call-1")
    captured: list[AgentConfig] = []

    async def run(_ctx, config, _model_access):
        captured.append(config)
        return AgentResult(content="done", model="test-model")

    registry = MagicMock()

    def get_schemas(tool_names=None):
        if tool_names is None:
            return list(all_schemas)
        return [schema for schema in all_schemas if schema["function"]["name"] in set(tool_names)]

    registry.get_schemas.side_effect = get_schemas
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context)
    session_store.save = AsyncMock()
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter = TaskAgentAdapter(
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

    await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert captured[0].allowed_tool_names == frozenset(boundary)
    assert "outside" not in captured[0].allowed_tool_names
