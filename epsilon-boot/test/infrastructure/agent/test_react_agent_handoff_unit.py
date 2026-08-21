"""ReActAgentAdapter handoff 短路单元测试。

验证 Spec A R1.3：``HandoffToAgentTool`` 抛 ``HandoffPerformed`` 后，
``_execute_tool_call`` 把 ``signal.content`` 写入 ``ToolMessage`` 并打标
``metadata["handoff_target"]``，``_iter_rounds`` 在下一轮入口检测到 handoff
标记并产出 ``RoundOutcome(kind="handoff", ...)``，4 个执行入口
（``run`` / ``run_streaming`` / ``run_events``）正确终止并把目标 Agent 最终
回复透出。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from typing import Any

import pytest

from domain.agent.exceptions import HandoffPerformed
from domain.agent.tools import Tool, ToolExecutionResult
from domain.agent.value_objects import AgentConfig, AgentResult, AgentStreamEvent
from domain.chat.context import (
    ConversationContext,
    BaseMessage,
    ToolMessage,
)
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from domain.model_access.ports import ModelAccessPort
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


class _FakeHandoffTool(Tool):
    """测试桩：``execute`` 抛 ``HandoffPerformed`` 模拟 handoff 成功。"""

    def __init__(self, target_agent: str = "specialist", content: str = "目标 Agent 回复") -> None:
        self._target = target_agent
        self._content = content

    @property
    def name(self) -> str:
        return "handoff_to_agent"

    @property
    def description(self) -> str:
        return "test handoff tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        raise HandoffPerformed(
            target_agent=self._target,
            content=self._content,
            usage={"total_tokens": 7},
            model="gpt-4o",
        )


def _make_config() -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "handoff_to_agent"}},
        ],
        model="gpt-4",
        max_rounds=5,
        prompt_id="chat-default@v1",
    )


def _make_adapter() -> tuple[ReActAgentAdapter, _FakeHandoffTool]:
    """构造适配器：tool_registry 委托给 fake handoff tool。"""
    handoff_tool = _FakeHandoffTool()

    async def _execute_proxy(req: ToolCallRequest) -> ToolExecutionResult:
        # 模拟 ToolRegistry.execute 走完整 run() 流水线：直接 await 工具.execute
        return await handoff_tool.execute()

    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=_execute_proxy)
    tool_registry.get = MagicMock(return_value=handoff_tool)

    class _ContextBuilder:
        async def build(
            self,
            messages: list[BaseMessage],
            *,
            model_access: ModelAccessPort | None = None,
            model: str | None = None,
        ) -> ContextBuilderResult:
            del model_access, model
            return ContextBuilderResult(messages=messages, usage={})

    context_builder = _ContextBuilder()
    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )
    return adapter, handoff_tool


@pytest.mark.asyncio
async def test_run_terminates_via_handoff_and_returns_target_content() -> None:
    """``run`` 路径：handoff 后立即终止，content 取自目标 Agent 回复。"""
    adapter, _ = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("帮忙处理")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            # 第 1 轮：模型返回 tool_calls=[handoff_to_agent]
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="handoff_to_agent", arguments="{}"),
                ],
                model="gpt-4",
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                latency_ms=10.0,
            ),
            # 第 2 轮: 不应该被调用 - handoff 短路应终止
            LLMResponse(
                content="不该出现",
                tool_calls=[],
                model="gpt-4",
                usage={},
                latency_ms=0,
            ),
        ],
    )

    result = await adapter.run(context, config, model_access)

    assert isinstance(result, AgentResult)
    assert result.content == "目标 Agent 回复"
    assert result.terminated_reason == "completed"
    assert result.status == "completed"
    # 第 2 轮模型调用不应发生 — 仅第 1 轮 stream 调用即可
    assert model_access._v3_stream_counter.call_count == 1

    # 上下文末尾应有 ToolMessage 带 handoff_target
    last = context.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.content == "目标 Agent 回复"
    assert last.metadata == {"handoff_target": "specialist"}


@pytest.mark.asyncio
async def test_run_streaming_emits_handoff_chunk_and_stops() -> None:
    """``run_streaming`` 路径：handoff 后产出 finished StreamingChunk 并停止。"""
    adapter, _ = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("帮忙处理")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="handoff_to_agent", arguments="{}"),
                ],
                model="gpt-4",
                usage={"total_tokens": 8},
                latency_ms=10.0,
            ),
            # 不应被消费
            LLMResponse(content="未到达", tool_calls=[], model="gpt-4", usage={}),
        ],
    )

    chunks: list[StreamingChunk] = []
    async for ch in adapter.run_streaming(context, config, model_access):
        chunks.append(ch)

    # 最后一片为 finished + handoff_target metadata
    finished = [c for c in chunks if c.finished]
    assert len(finished) == 1
    final = finished[0]
    assert final.delta_content == "目标 Agent 回复"
    assert final.metadata == {"handoff_target": "specialist"}
    # 仅 1 次 stream 调用
    assert model_access._v3_stream_counter.call_count == 1


@pytest.mark.asyncio
async def test_run_events_emits_assistant_done_with_handoff_target() -> None:
    """``run_events`` 路径：handoff 产出 assistant_delta + assistant_done。"""
    adapter, _ = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("帮忙处理")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="handoff_to_agent", arguments="{}"),
                ],
                model="gpt-4",
                usage={"total_tokens": 8},
                latency_ms=10.0,
            ),
            LLMResponse(content="未到达", tool_calls=[], model="gpt-4", usage={}),
        ],
    )

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, config, model_access):
        events.append(ev)

    # 应包含 assistant_delta(handoff_content) + assistant_done(handoff_target)
    deltas = [e for e in events if e.kind == "assistant_delta"]
    dones = [e for e in events if e.kind == "assistant_done"]
    assert any(d.content == "目标 Agent 回复" for d in deltas)
    assert len(dones) == 1
    assert dones[0].metadata.get("handoff_target") == "specialist"
    assert set(dones[0].metadata) == {"round", "handoff_target"}
    # 第 2 轮 stream 不应被调用
    assert model_access._v3_stream_counter.call_count == 1


@pytest.mark.asyncio
async def test_handoff_does_not_set_error_metadata() -> None:
    """HandoffPerformed 是成功信号，不应写 ``metadata["error"]``。"""
    adapter, _ = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("处理")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="handoff_to_agent", arguments="{}"),
                ],
                model="gpt-4",
                usage={"total_tokens": 1},
            ),
            LLMResponse(content="x", tool_calls=[], model="gpt-4", usage={}),
        ],
    )

    await adapter.run(context, config, model_access)
    last = context.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.metadata.get("handoff_target") == "specialist"
    assert "error" not in last.metadata
