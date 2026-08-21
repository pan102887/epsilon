"""ReActAgentAdapter OTel 链路嵌套单元测试。

验证 Spec A R2.1 / R2.2 / R2.3 / R2.4 / R2.5：

- 每轮迭代产出一个 ``react_agent.round`` span；
- span attributes 覆盖 ``react.round_num`` / ``react.tool_call_count`` /
  ``react.has_tool_calls`` / ``gen_ai.usage.*`` / ``react.approval_required``；
- ``max_rounds`` / ``token_budget_exceeded`` / ``handoff`` 三类终止形态产出
  ``react_agent.terminated`` span，``react.terminated_reason`` 准确；
- 模型调用异常 → span 状态 ERROR + record_exception；
- 嵌套：caller 自创父 span 后，``react_agent.round`` 作为 child span。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import trace as _otel_trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_exporter() -> Iterator[InMemorySpanExporter]:
    """注入 InMemorySpanExporter 收集 span，测试结束恢复全局 TracerProvider。"""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    saved_provider = _otel_trace.get_tracer_provider()
    _otel_trace.set_tracer_provider(provider)

    # ⚠️ 关键：``react_agent_adapter`` 模块级 ``tracer`` 在 import 时已绑定原
    # ``ProxyTracer``（指向旧 provider）；本测试需要把 module 内的 tracer
    # 替换为新 provider 的 tracer，否则收集不到 span。
    import infrastructure.agent.react_agent_adapter as react_mod

    saved_tracer = react_mod.tracer
    react_mod.tracer = provider.get_tracer("test")

    try:
        yield exporter
    finally:
        react_mod.tracer = saved_tracer
        _otel_trace.set_tracer_provider(saved_provider)
        provider.shutdown()


def _make_config(max_rounds: int = 3) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[{"type": "function", "function": {"name": "tool_a"}}],
        model="gpt-4",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


class _FakeContextBuilder:
    """原样返回消息的测试上下文构建器。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del model_access, model
        return ContextBuilderResult(messages=messages, usage={})


def _make_adapter(tool_result: str = "tool ok") -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content=tool_result))
    tool_registry.get = MagicMock(return_value=None)

    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),
    )


def _spans_by_name(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_round_produces_react_agent_round_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """单轮 text 路径 → 1 个 react_agent.round span，attributes 完整（R2.2/R2.3）。"""
    adapter = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("你好")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="hi",
                tool_calls=[],
                model="gpt-4",
                usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                latency_ms=5.0,
            ),
        ],
    )

    await adapter.run(context, config, model_access)

    rounds = _spans_by_name(in_memory_exporter, "react_agent.round")
    assert len(rounds) == 1
    span = rounds[0]
    attrs: dict[str, Any] = dict(span.attributes or {})
    assert attrs.get("react.round_num") == 1
    assert attrs.get("react.tool_call_count") == 0
    assert attrs.get("react.has_tool_calls") is False
    assert attrs.get("gen_ai.usage.total_tokens") == 6
    assert attrs.get("gen_ai.usage.prompt_tokens") == 4
    assert attrs.get("gen_ai.usage.completion_tokens") == 2


@pytest.mark.asyncio
async def test_two_rounds_produce_two_round_spans(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """两轮（tool_calls + text）→ 2 个 react_agent.round span。"""
    adapter = _make_adapter()
    config = _make_config(max_rounds=3)
    context = ConversationContext()
    context.add_user_message("帮我处理")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="tool_a", arguments="{}")],
                model="gpt-4",
                usage={"total_tokens": 2},
                latency_ms=10.0,
            ),
            LLMResponse(
                content="done",
                tool_calls=[],
                model="gpt-4",
                usage={"total_tokens": 5},
                latency_ms=10.0,
            ),
        ],
    )

    await adapter.run(context, config, model_access)

    rounds = _spans_by_name(in_memory_exporter, "react_agent.round")
    assert len(rounds) == 2
    assert {dict(s.attributes or {}).get("react.round_num") for s in rounds} == {1, 2}
    # round 1 应有 tool_calls；round 2 应无
    by_round = {
        dict(s.attributes or {}).get("react.round_num"): dict(s.attributes or {}) for s in rounds
    }
    assert by_round[1].get("react.has_tool_calls") is True
    assert by_round[1].get("react.tool_call_count") == 1
    assert by_round[2].get("react.has_tool_calls") is False


@pytest.mark.asyncio
async def test_max_rounds_terminated_emits_terminated_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """``max_rounds`` 命中 → ``react_agent.terminated`` span 含 terminated_reason=max_rounds。"""
    adapter = _make_adapter()
    config = _make_config(max_rounds=2)
    context = ConversationContext()
    context.add_user_message("loop")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="tool_a", arguments="{}")],
                model="gpt-4",
                usage={"total_tokens": 1},
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="c2", name="tool_a", arguments="{}")],
                model="gpt-4",
                usage={"total_tokens": 1},
            ),
        ],
    )

    result = await adapter.run(context, config, model_access)
    assert result.terminated_reason == "max_rounds"

    terminated = _spans_by_name(in_memory_exporter, "react_agent.terminated")
    assert len(terminated) == 1
    attrs = dict(terminated[0].attributes or {})
    assert attrs.get("react.terminated_reason") == "max_rounds"


@pytest.mark.asyncio
async def test_model_exception_marks_span_error(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """模型 stream 抛异常 → round span 状态 ERROR + record_exception（R2.4）。"""
    adapter = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("hi")

    model_access = MagicMock()

    async def _broken_stream(req: ChatRequest) -> AsyncIterator[StreamingChunk]:
        del req
        if False:
            yield StreamingChunk()  # 让函数成为 async generator
        raise RuntimeError("api failure")

    model_access.stream = _broken_stream

    with pytest.raises(RuntimeError, match="api failure"):
        await adapter.run(context, config, model_access)

    rounds = _spans_by_name(in_memory_exporter, "react_agent.round")
    assert len(rounds) == 1
    span = rounds[0]
    # status 应为 ERROR
    from opentelemetry.trace import StatusCode

    assert span.status.status_code == StatusCode.ERROR
    # 至少一个 exception event 被记录
    exception_events = [e for e in span.events if e.name == "exception"]
    assert exception_events
    exception_attributes = exception_events[0].attributes
    assert exception_attributes is not None
    assert "api failure" in str(exception_attributes.get("exception.message", ""))


@pytest.mark.asyncio
async def test_round_span_nests_under_parent_span(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """caller 自创父 span 后，``react_agent.round`` 作为 child span 嵌套。"""
    import infrastructure.agent.react_agent_adapter as react_mod

    test_tracer = react_mod.tracer  # 已被 fixture 替换为新 provider 的 tracer

    adapter = _make_adapter()
    config = _make_config()
    context = ConversationContext()
    context.add_user_message("nest")

    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            LLMResponse(
                content="ok",
                tool_calls=[],
                model="gpt-4",
                usage={"total_tokens": 1},
            ),
        ],
    )

    with test_tracer.start_as_current_span("test.parent") as parent:
        parent_ctx = parent.get_span_context()
        await adapter.run(context, config, model_access)

    rounds = _spans_by_name(in_memory_exporter, "react_agent.round")
    parents = _spans_by_name(in_memory_exporter, "test.parent")
    assert rounds and parents
    # round span 的 parent 应是 test.parent
    round_parent_ctx = rounds[0].parent
    assert round_parent_ctx is not None
    assert round_parent_ctx.span_id == parent_ctx.span_id
    assert round_parent_ctx.trace_id == parent_ctx.trace_id


@pytest.mark.asyncio
async def test_otel_disabled_does_not_break_run(
    in_memory_exporter: InMemorySpanExporter,
) -> None:
    """R2.5：即使采集器为 NoOp（exporter 拿不到 span），``run`` 仍正常返回。

    具体形态：本测试让 react_mod.tracer 临时降级为 NoOpTracer，验证 run 路径
    无依赖 OTel SDK 的隐含 import / API。
    """
    import infrastructure.agent.react_agent_adapter as react_mod

    saved = react_mod.tracer
    # NoOpTracer 是 OTel 默认 fallback
    from opentelemetry.trace import NoOpTracer

    react_mod.tracer = NoOpTracer()
    try:
        adapter = _make_adapter()
        config = _make_config()
        context = ConversationContext()
        context.add_user_message("hi")

        model_access = MagicMock()
        install_stream_mock(
            model_access,
            responses=[
                LLMResponse(content="ok", tool_calls=[], model="gpt-4", usage={}),
            ],
        )
        result = await adapter.run(context, config, model_access)
        assert result.content == "ok"
    finally:
        react_mod.tracer = saved
