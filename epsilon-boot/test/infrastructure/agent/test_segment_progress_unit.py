"""分段执行进展分析单元测试。"""

from __future__ import annotations

from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from domain.task.value_objects import TraceEntry
from infrastructure.agent.segmented_progress import (
    analyze_segment_progress,
    normalized_tool_call_digest,
    total_tokens_from_usage,
)


def test_total_tokens_from_usage_prefers_total_tokens() -> None:
    """usage 中存在 total_tokens 时优先使用该字段。"""
    assert total_tokens_from_usage({"total_tokens": 7, "prompt_tokens": 100}) == 7


def test_total_tokens_from_usage_falls_back_to_prompt_and_completion() -> None:
    """缺少 total_tokens 时回退为 prompt + completion。"""
    assert total_tokens_from_usage({"prompt_tokens": 3, "completion_tokens": 4}) == 7


def test_tool_call_digest_is_stable_for_json_argument_order() -> None:
    """JSON 参数顺序不同但语义相同时 digest 应一致。"""
    left = normalized_tool_call_digest("search", '{"b":2,"a":1}')
    right = normalized_tool_call_digest("search", '{"a":1,"b":2}')

    assert left == right


def test_tool_call_digest_accepts_non_json_arguments() -> None:
    """非 JSON 参数应按原始字符串生成稳定 digest。"""
    assert normalized_tool_call_digest("shell", "not-json") == normalized_tool_call_digest(
        "shell",
        "not-json",
    )


def test_analyze_segment_progress_counts_new_messages_and_last_tool_call() -> None:
    """进展分析应统计新增工具消息、trace、usage 和工具调用摘要。"""
    context = ConversationContext()
    context.add_user_message("hi")
    pre_count = context.message_count
    context.append_message(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest(id="call-1", name="search", arguments='{"q":"x"}')],
        )
    )
    context.append_message(ToolMessage(content="result", tool_name="search", tool_call_id="call-1"))

    snapshot, digest = analyze_segment_progress(
        context=context,
        pre_message_count=pre_count,
        previous_tool_call_digest=None,
        usage={"prompt_tokens": 2, "completion_tokens": 3},
        trace=[TraceEntry(step=1, action="tool_result", detail="ok", timestamp_ms=1)],
    )

    assert snapshot.pre_message_count == pre_count
    assert snapshot.post_message_count == context.message_count
    assert snapshot.new_tool_message_count == 1
    assert snapshot.new_trace_count == 1
    assert snapshot.token_delta == 5
    assert snapshot.has_progress is True
    assert digest == normalized_tool_call_digest("search", '{"q":"x"}')


def test_analyze_segment_progress_detects_repeated_tool_call() -> None:
    """本段最后工具调用摘要等于上一段时应标记 repeated_tool_call。"""
    previous = normalized_tool_call_digest("search", '{"q":"x"}')
    context = ConversationContext()
    pre_count = context.message_count
    context.append_message(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest(id="call-1", name="search", arguments='{"q":"x"}')],
        )
    )

    snapshot, digest = analyze_segment_progress(
        context=context,
        pre_message_count=pre_count,
        previous_tool_call_digest=previous,
        usage={},
    )

    assert digest == previous
    assert snapshot.repeated_tool_call is True
