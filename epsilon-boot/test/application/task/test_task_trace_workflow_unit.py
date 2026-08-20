from __future__ import annotations

from application.task.task_trace_workflow import TaskTraceWorkflow
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage, UserMessage
from domain.model_access.value_objects import ToolCallRequest


def _context_with_messages(*messages) -> ConversationContext:
    context = ConversationContext()
    for message in messages:
        context.append_message(message)
    return context


def _tool_call(
    id_: str = "call-1", name: str = "search", arguments: str = '{"q":"x"}'
) -> ToolCallRequest:
    return ToolCallRequest(id=id_, name=name, arguments=arguments)


def test_extract_trace_maps_tool_call_and_tool_result() -> None:
    context = _context_with_messages(
        AssistantMessage(content="", tool_calls=[_tool_call()]),
        ToolMessage(content="result text", tool_name="search", tool_call_id="call-1"),
    )

    trace = TaskTraceWorkflow().extract_trace(context, event_timestamps={0: 1000, 1: 2000})

    assert [(entry.step, entry.action, entry.detail, entry.timestamp_ms) for entry in trace] == [
        (1, "tool_call", 'search({"q":"x"})', 1000),
        (2, "tool_result", "result text", 2000),
    ]


def test_extract_trace_prefers_event_timestamp_for_message_global_index() -> None:
    context = _context_with_messages(
        UserMessage(content="before"),
        AssistantMessage(content="", tool_calls=[_tool_call(name="calc", arguments="1+1")]),
    )

    trace = TaskTraceWorkflow().extract_trace(context, event_timestamps={1: 123456})

    assert trace[0].timestamp_ms == 123456


def test_extract_trace_falls_back_to_current_time_when_timestamp_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "application.task.task_trace_workflow.time.time",
        lambda: 42.125,
    )
    context = _context_with_messages(
        ToolMessage(content="fallback result", tool_name="search", tool_call_id="call-1")
    )

    trace = TaskTraceWorkflow().extract_trace(context)

    assert trace[0].timestamp_ms == 42125


def test_extract_trace_start_index_offsets_messages_and_steps_from_one() -> None:
    context = _context_with_messages(
        AssistantMessage(content="", tool_calls=[_tool_call(name="old", arguments="{}")]),
        UserMessage(content="skip me"),
        AssistantMessage(
            content="",
            tool_calls=[
                _tool_call(id_="call-2", name="search", arguments='{"q":"new"}'),
                _tool_call(id_="call-3", name="lookup", arguments='{"id":1}'),
            ],
        ),
        ToolMessage(content="new result", tool_name="lookup", tool_call_id="call-3"),
    )

    trace = TaskTraceWorkflow().extract_trace(
        context,
        start_index=2,
        event_timestamps={0: 100, 2: 300, 3: 400},
    )

    assert [(entry.step, entry.action, entry.detail, entry.timestamp_ms) for entry in trace] == [
        (1, "tool_call", 'search({"q":"new"})', 300),
        (2, "tool_call", 'lookup({"id":1})', 300),
        (3, "tool_result", "new result", 400),
    ]
