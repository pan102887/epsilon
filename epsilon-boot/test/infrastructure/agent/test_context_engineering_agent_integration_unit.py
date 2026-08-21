"""Agent 上下文工程集成测试。

本模块验证 ``ReActAgentAdapter`` 与上下文构建端口的同步 Agent Loop
协作边界：每轮模型请求只使用 builder 输出的序列化消息，环境上下文不写入
``ConversationContext``，同时累计 builder usage 与主模型 usage。
"""

from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import AgentConfig
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class FakeToolRegistry:
    """执行固定工具结果的工具注册表 fake。"""

    def __init__(self) -> None:
        """初始化工具调用记录。"""
        self.executed: list[ToolCallRequest] = []

    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        """记录工具请求并返回固定结果。"""
        self.executed.append(request)
        return ToolExecutionResult(content="tool result from fake registry")


class FakeModelAccess:
    """v3：捕获模型请求并按顺序返回预置响应（``stream`` 等价分片）。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        """初始化响应队列与请求记录。"""
        self._responses = list(responses)
        self.chat_requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """v3 ReAct 不再调用 chat，仅保留以兼容 ChatServiceAdapter 等非 ReAct 场景。"""
        self.chat_requests.append(request)
        return self._responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        # ``chat_requests`` 仍记录每轮请求，以便保留既有断言语义（NFR-3）。
        self.chat_requests.append(request)
        if not self._responses:
            from domain.model_access.value_objects import StreamingChunk

            yield StreamingChunk(delta_content="", finished=True, usage={})
            return
        response = self._responses.pop(0)
        for chunk in response_to_chunks(response):
            yield chunk

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return sum(len(message.content) for message in messages)


@pytest.mark.asyncio
async def test_agent_context_engineering_uses_builder_messages_without_persisting_environment() -> (
    None
):
    """Agent 两轮调用应使用 builder messages、累计 usage，并隔离环境上下文。"""
    first_builder_messages: list[BaseMessage] = [
        SystemMessage(content="system prompt"),
        SystemMessage(
            content="<environment_context>\nworkspace: workspace:/\n</environment_context>"
        ),
        UserMessage(content="builder round 1 user"),
    ]
    second_builder_messages: list[BaseMessage] = [
        SystemMessage(content="system prompt"),
        SystemMessage(
            content="<environment_context>\nworkspace: workspace:/\n</environment_context>"
        ),
        AssistantMessage(content="builder round 2 assistant"),
        ToolMessage(
            content="builder round 2 tool", tool_call_id="call_1", tool_name="lookup_context"
        ),
    ]
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        side_effect=[
            ContextBuilderResult(
                messages=first_builder_messages,
                usage={"prompt_tokens": 2, "summary_tokens": 3},
                environment_injected=True,
            ),
            ContextBuilderResult(
                messages=second_builder_messages,
                usage={"prompt_tokens": 11, "summary_tokens": 13},
                environment_injected=True,
            ),
        ]
    )

    tool_call = ToolCallRequest(
        id="call_1",
        name="lookup_context",
        arguments='{"query": "status"}',
    )
    model_access = FakeModelAccess(
        responses=[
            LLMResponse(
                content="I need a tool.",
                model="test-model",
                usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
                tool_calls=[tool_call],
            ),
            LLMResponse(
                content="Final answer.",
                model="test-model",
                usage={"prompt_tokens": 17, "completion_tokens": 19, "total_tokens": 36},
            ),
        ]
    )
    tool_registry = FakeToolRegistry()
    adapter = ReActAgentAdapter(
        tool_registry=cast(ToolRegistry, tool_registry),
        context_builder=context_builder,
    )
    config = AgentConfig(
        system_prompt="你是一个有用的 AI 助手。",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "lookup_context",
                    "description": "lookup context",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )
    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("请查询上下文工程状态")

    result = await adapter.run(context, config, model_access)

    assert len(model_access.chat_requests) == 2
    assert model_access.chat_requests[0].messages == first_builder_messages
    assert model_access.chat_requests[1].messages == second_builder_messages
    assert model_access.chat_requests[0].tools == config.tool_schemas
    assert model_access.chat_requests[1].tools == config.tool_schemas

    assert result.content == "Final answer."
    assert result.usage == {
        "prompt_tokens": 35,
        "summary_tokens": 16,
        "completion_tokens": 26,
        "total_tokens": 48,
    }

    assert tool_registry.executed == [tool_call]
    context_builder.build.assert_awaited()
    assert context_builder.build.await_count == 2

    payload = context.to_dict()
    serialized_payload = str(payload)
    assert "<environment_context>" not in serialized_payload
    assert "workspace:/" not in serialized_payload
    assert "context_kind=environment" not in serialized_payload

    messages = payload["messages"]
    assistant_messages = [message for message in messages if message["role"] == "assistant"]
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert assistant_messages == [
        {
            "role": "assistant",
            "content": "I need a tool.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "lookup_context",
                    "arguments": '{"query": "status"}',
                }
            ],
        }
    ]
    assert tool_messages == [
        {
            "role": "tool",
            "content": "tool result from fake registry",
            "tool_name": "lookup_context",
            "tool_call_id": "call_1",
        }
    ]
