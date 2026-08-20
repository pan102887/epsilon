"""Long Task Continuation Phase 1 集成测试。"""

from unittest.mock import MagicMock

import pytest

from domain.agent.value_objects import AgentResult
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskContinueRequest, TaskStatus
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.task.task_agent_adapter import TaskAgentAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _schema(name: str) -> dict:
    """构造工具 schema。"""
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": {}},
    }


class MemorySessionStore:
    """测试用内存会话存储。"""

    def __init__(self) -> None:
        self.contexts: dict[str, ConversationContext] = {}

    async def save(self, session_id: str, context: ConversationContext) -> None:
        self.contexts[session_id] = context

    async def load(self, session_id: str) -> ConversationContext:
        return self.contexts.setdefault(session_id, ConversationContext())

    async def delete(self, session_id: str) -> None:
        self.contexts.pop(session_id, None)

    async def exists(self, session_id: str) -> bool:
        return session_id in self.contexts

    async def compare_and_swap(self, session_id, mutator):
        context = await self.load(session_id)
        result = await mutator(context)
        await self.save(session_id, context)
        return result


class QueueAgent:
    """按顺序返回 AgentResult 的 fake Agent。"""

    def __init__(self, results: list[AgentResult]) -> None:
        self.results = results
        self.user_counts: list[int] = []

    async def run(self, context, _config, _model_access):
        self.user_counts.append(
            sum(1 for message in context.get_messages() if message.role == "user")
        )
        result = self.results.pop(0)
        if result.terminated_reason != "completed":
            context.add_assistant_message_with_tool_calls(
                "",
                [ToolCallRequest(id="call-1", name="search", arguments="{}")],
            )
            context.add_tool_result("search", "result", "call-1")
        return result


def _model_registry() -> MagicMock:
    """构造模型注册表 fake。"""
    registry = MagicMock()
    registry.get_default_model.return_value = "test-model"
    registry.get_adapter_for_model.return_value = MagicMock()
    return registry


def _prompt_registry(prompt_id: str, name: str) -> MagicMock:
    """构造 Prompt 注册表 fake。"""
    return MagicMock(
        get=MagicMock(
            return_value=LoadedPrompt(
                prompt_id=prompt_id,
                name=name,
                version="v1",
                content="system",
            )
        )
    )


@pytest.mark.asyncio
async def test_chat_pause_then_continue_without_empty_assistant_tail() -> None:
    """覆盖 Chat 暂停、继续和上下文尾部不追加空 final。"""
    store = MemorySessionStore()
    agent = QueueAgent(
        [
            AgentResult(
                content="",
                model="test-model",
                terminated_reason="max_rounds",
            ),
            AgentResult(content="done", model="test-model"),
        ]
    )
    model_registry = _model_registry()
    loaded_prompt = _prompt_registry("chat-default@v1", "chat-default").get.return_value
    prompt_registry = MagicMock(get=MagicMock(return_value=loaded_prompt))
    tool_schemas = [_schema("search")]
    adapter = ChatServiceAdapter(
        session_store=store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=MagicMock(),
        agent=agent,
        tool_calling_enabled=True,
        max_tool_rounds=2,
        tool_schemas=tool_schemas,
        **make_chat_adapter_dependencies(
            session_store=store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=2,
        ),
    )

    paused = await adapter.chat(ChatRequestVO(session_id="s1", message="goal"))

    assert paused.status == "paused"
    assert paused.terminated_reason == "max_rounds"
    assert paused.can_continue is True
    assert isinstance(store.contexts["s1"].get_messages()[-1], ToolMessage)

    completed = await adapter.continue_chat(ChatContinueRequestVO(session_id="s1"))

    assert completed.status == "completed"
    assert completed.can_continue is False
    assert agent.user_counts == [1, 1]
    assert store.contexts["s1"].get_messages()[-1].content == "done"


@pytest.mark.asyncio
async def test_task_pause_then_continue_without_empty_assistant_tail() -> None:
    """覆盖 Task 暂停、继续和上下文尾部不追加空 final。"""
    store = MemorySessionStore()
    agent = QueueAgent(
        [
            AgentResult(
                content="",
                model="test-model",
                terminated_reason="max_rounds",
            ),
            AgentResult(content="task done", model="test-model"),
        ]
    )
    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = [_schema("search")]
    adapter = TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=_model_registry(),
        compaction=MagicMock(),
        session_store=store,
        prompt_registry=_prompt_registry("task-template@v1", "task-template"),
        max_rounds=2,
    )

    paused = await adapter.execute(Task(goal="goal", session_id="t1"))

    assert paused.status == TaskStatus.PAUSED
    assert paused.terminated_reason == "max_rounds"
    assert paused.can_continue is True
    assert isinstance(store.contexts["t1"].get_messages()[-1], ToolMessage)
    assert not isinstance(store.contexts["t1"].get_messages()[-1], AssistantMessage)

    completed = await adapter.continue_task(TaskContinueRequest(session_id="t1"))

    assert completed.status == TaskStatus.SUCCESS
    assert completed.can_continue is False
    assert agent.user_counts == [1, 1]
