"""``run_events`` 工具失败事件 kind 单元测试模块。

验证 v2 ``run_events`` 在工具执行流水线统一收口后的行为：

- (a) 工具失败时 ``run_events`` 产出 ``kind="tool_error"`` 且
  ``ToolMessage.metadata == {"error": True}``。
- (b) 工具成功时产出 ``kind="tool_result"`` 且
  ``ToolMessage.metadata == {}``。
- (c) ``run_events`` 内不再保留独立的 authorize/execute/except 三段实现
  （通过 mock ``_execute_tool_call`` 验证调用 1 次）。

**Validates: Requirements 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, Property 5**
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage, UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

# ── Fakes ──


class _FakeContextBuilder:
    """按顺序返回 ContextBuilderResult 的测试 fake。"""

    def __init__(self, results: list[ContextBuilderResult] | None = None) -> None:
        self._results = results or [
            ContextBuilderResult(
                messages=[UserMessage(content="go")],
                usage={},
            )
        ]

    async def build(self, *args, **kwargs) -> ContextBuilderResult:
        return self._results.pop(0)


class _FakeModelAccess:
    """按顺序返回 LLMResponse 的模型 fake。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.chat_calls = 0
        self.stream_calls = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:
        self.chat_calls += 1
        return self._responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        self.stream_calls += 1
        if self._responses:
            response = self._responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        yield StreamingChunk(delta_content="done", finished=True, usage={"total_tokens": 1})


def _config() -> AgentConfig:
    """标准测试配置，允许 echo 工具，max_rounds=3。"""
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[
            {"type": "function", "function": {"name": "echo", "parameters": {}}},
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _tool_call_response() -> LLMResponse:
    """模型返回一次 tool_call。"""
    return LLMResponse(
        content="",
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=[
            ToolCallRequest(id="call-1", name="echo", arguments='{"text": "hi"}'),
        ],
    )


def _text_response() -> LLMResponse:
    """模型返回纯文本终止。"""
    return LLMResponse(
        content="final answer",
        model="test-model",
        usage={"total_tokens": 7},
        tool_calls=[],
    )


# ── Tests ──


@pytest.mark.asyncio
async def test_tool_failure_produces_tool_error_event() -> None:
    """工具失败时 run_events 产出 kind="tool_error" 且 ToolMessage.metadata 标记 error。"""
    # 构造 tool_registry mock：execute 抛出异常
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=RuntimeError("execute failed"))

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="go")],
                    usage={"prompt_tokens": 2},
                ),
                ContextBuilderResult(
                    messages=[UserMessage(content="go2")],
                    usage={"prompt_tokens": 3},
                ),
            ]
        ),  # type: ignore[arg-type]
    )

    model = _FakeModelAccess([_tool_call_response(), _text_response()])
    context = ConversationContext()
    context.add_user_message("go")

    events = [
        event
        async for event in adapter.run_events(
            context,
            _config(),
            model,  # type: ignore[arg-type]
        )
    ]

    # 寻找 tool_error 事件
    tool_events = [e for e in events if e.kind in ("tool_result", "tool_error")]
    assert len(tool_events) == 1
    assert tool_events[0].kind == "tool_error"
    assert "execute failed" in tool_events[0].content

    # 验证 ToolMessage.metadata == {"error": True}
    tool_msgs = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].metadata == {"error": True}


@pytest.mark.asyncio
async def test_tool_success_produces_tool_result_event() -> None:
    """工具成功时 run_events 产出 kind="tool_result" 且 ToolMessage.metadata == {}。"""
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="go")],
                    usage={"prompt_tokens": 2},
                ),
                ContextBuilderResult(
                    messages=[UserMessage(content="go2")],
                    usage={"prompt_tokens": 3},
                ),
            ]
        ),  # type: ignore[arg-type]
    )

    model = _FakeModelAccess([_tool_call_response(), _text_response()])
    context = ConversationContext()
    context.add_user_message("go")

    events = [
        event
        async for event in adapter.run_events(
            context,
            _config(),
            model,  # type: ignore[arg-type]
        )
    ]

    # 寻找 tool_result 事件
    tool_events = [e for e in events if e.kind in ("tool_result", "tool_error")]
    assert len(tool_events) == 1
    assert tool_events[0].kind == "tool_result"
    assert tool_events[0].content == "tool ok"

    # 验证 ToolMessage.metadata == {}
    tool_msgs = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].metadata == {}


@pytest.mark.asyncio
async def test_run_events_calls_execute_tool_call_exactly_once_per_tool() -> None:
    """验证 run_events 通过 _execute_tool_call 执行工具（不保留独立内联实现）。

    通过 mock _execute_tool_call 验证：
    - 对每个 tool_call 恰好调用 1 次 _execute_tool_call
    - 不存在绕过 _execute_tool_call 的额外 tool_registry.execute 调用
    """
    tool_registry = MagicMock()
    # 若 run_events 仍有内联实现，会直接调用 tool_registry.execute
    tool_registry.execute = AsyncMock(
        side_effect=AssertionError("不应直接调用 tool_registry.execute")
    )

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="go")],
                    usage={"prompt_tokens": 2},
                ),
                ContextBuilderResult(
                    messages=[UserMessage(content="go2")],
                    usage={"prompt_tokens": 3},
                ),
            ]
        ),  # type: ignore[arg-type]
    )

    model = _FakeModelAccess([_tool_call_response(), _text_response()])
    context = ConversationContext()
    context.add_user_message("go")

    # mock _execute_tool_call 使之不走真实逻辑
    with patch.object(
        adapter,
        "_execute_tool_call",
        new_callable=AsyncMock,
        return_value=(ToolExecutionResult(content="mocked result"), False),
    ) as mock_exec:
        events = [
            event
            async for event in adapter.run_events(
                context,
                _config(),
                model,  # type: ignore[arg-type]
            )
        ]

    # _execute_tool_call 恰好被调用 1 次（对应 1 个 tool_call）
    assert mock_exec.call_count == 1
    # tool_registry.execute 不应被直接调用
    tool_registry.execute.assert_not_called()

    # 仍然产出 tool_start 和 tool_result 事件
    kinds = [e.kind for e in events]
    assert "tool_start" in kinds
    assert "tool_result" in kinds


@pytest.mark.asyncio
async def test_tool_error_event_carries_tool_metadata() -> None:
    """tool_error 事件携带正确的 tool_name / tool_call_id / arguments 元数据。"""
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=RuntimeError("oops"))

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="go")],
                    usage={},
                ),
                ContextBuilderResult(
                    messages=[UserMessage(content="go2")],
                    usage={},
                ),
            ]
        ),  # type: ignore[arg-type]
    )

    model = _FakeModelAccess([_tool_call_response(), _text_response()])
    context = ConversationContext()
    context.add_user_message("go")

    events = [
        event
        async for event in adapter.run_events(
            context,
            _config(),
            model,  # type: ignore[arg-type]
        )
    ]

    error_events = [e for e in events if e.kind == "tool_error"]
    assert len(error_events) == 1
    ev = error_events[0]
    assert ev.tool_name == "echo"
    assert ev.tool_call_id == "call-1"
    assert ev.arguments == '{"text": "hi"}'
    assert ev.metadata.get("round") == 1
