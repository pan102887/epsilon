"""Agent 工具调用滥用检测测试。"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace as _otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


@pytest.fixture()
def in_memory_exporter() -> Iterator[InMemorySpanExporter]:
    """注入 InMemorySpanExporter 收集工具滥用检测事件。"""

    current_provider = _otel_trace.get_tracer_provider()
    if not isinstance(current_provider, TracerProvider):
        current_provider = TracerProvider()
        _otel_trace.set_tracer_provider(current_provider)

    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    current_provider.add_span_processor(processor)

    try:
        yield exporter
    finally:
        exporter.clear()
        processor.shutdown()


def test_tool_abuse_detector_flags_repeated_tool_calls() -> None:
    """同一轮运行中同一工具调用次数超过阈值时应命中滥用检测。"""

    from infrastructure.agent.tool_abuse_detector import ToolAbuseDetector

    detector = ToolAbuseDetector(max_same_tool_calls=5)

    verdicts = [detector.record_tool_call("shell_exec", {"command": "pwd"}) for _ in range(6)]

    assert verdicts[-1].abuse_detected is True
    assert verdicts[-1].reason == "same_tool_call_limit_exceeded"


@pytest.mark.asyncio
async def test_react_adapter_blocks_repeated_same_tool_before_execution(
    in_memory_exporter: InMemorySpanExporter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ReAct 工具预执行阶段应阻断第六次同名工具自动调用并记录可观测事件。"""

    registry = MagicMock()
    registry.get.return_value = None
    adapter = ReActAgentAdapter(tool_registry=registry, context_builder=MagicMock())
    context = ConversationContext()
    config = AgentConfig(
        system_prompt="sys",
        tool_schemas=[{"type": "function", "function": {"name": "shell_exec"}}],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )
    tool_calls = tuple(
        ToolCallRequest(
            id=f"call-{index}",
            name="shell_exec",
            arguments='{"command":"pwd"}',
        )
        for index in range(6)
    )

    tracer = _otel_trace.get_tracer("test")
    with caplog.at_level("WARNING"), tracer.start_as_current_span("parent"):
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=config,
            tool_calls=tool_calls,
            round_num=1,
            model="test-model",
            usage_so_far={},
        )

    assert approval is None
    assert [tool_call.id for tool_call in executable] == [
        "call-0",
        "call-1",
        "call-2",
        "call-3",
        "call-4",
    ]
    blocked_message = context.get_messages()[-1]
    assert isinstance(blocked_message, ToolMessage)
    assert blocked_message.tool_call_id == "call-5"
    assert blocked_message.metadata["error"] is True
    assert blocked_message.metadata["tool_abuse_detected"] is True
    assert blocked_message.metadata["tool_abuse_reason"] == "same_tool_call_limit_exceeded"
    assert any(
        record.tool_name == "shell_exec" and record.reason == "same_tool_call_limit_exceeded"
        for record in caplog.records
    )
    spans = in_memory_exporter.get_finished_spans()
    assert any(
        event.name == "agent.tool_abuse_detected"
        and event.attributes["tool_name"] == "shell_exec"
        and event.attributes["reason"] == "same_tool_call_limit_exceeded"
        for span in spans
        for event in span.events
    )
