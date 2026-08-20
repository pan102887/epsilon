"""分段执行进展分析领域模块。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from domain.agent.segmented_execution import SegmentProgressSnapshot
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.task.value_objects import TraceEntry


def total_tokens_from_usage(usage: dict[str, int]) -> int:
    """从 usage 字典计算 total_tokens。"""
    if "total_tokens" in usage:
        return max(0, int(usage["total_tokens"]))
    return max(0, int(usage.get("prompt_tokens", 0))) + max(
        0,
        int(usage.get("completion_tokens", 0)),
    )


def _normalize_arguments(arguments: str | None) -> str:
    """将工具参数规范化为稳定字符串。"""
    raw = "" if arguments is None else str(arguments)
    try:
        parsed: Any = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw
    return json.dumps(
        parsed,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def normalized_tool_call_digest(tool_name: str, arguments: str) -> str:
    """对工具名和参数生成稳定摘要。"""
    normalized_arguments = _normalize_arguments(arguments)
    material = f"{tool_name}:{normalized_arguments}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def analyze_segment_progress(
    *,
    context: ConversationContext,
    pre_message_count: int,
    previous_tool_call_digest: str | None,
    usage: dict[str, int],
    trace: list[TraceEntry] | None = None,
    final_content: str = "",
) -> tuple[SegmentProgressSnapshot, str | None]:
    """分析单段执行是否有进展，并返回本段最后一个工具调用摘要。"""
    messages = context.get_messages()
    new_messages = messages[pre_message_count:]
    new_tool_message_count = sum(1 for msg in new_messages if isinstance(msg, ToolMessage))

    latest_digest: str | None = None
    for msg in new_messages:
        if not isinstance(msg, AssistantMessage):
            continue
        for tool_call in msg.tool_calls:
            latest_digest = normalized_tool_call_digest(
                tool_call.name,
                tool_call.arguments,
            )

    token_delta = total_tokens_from_usage(usage)
    repeated = bool(latest_digest and latest_digest == previous_tool_call_digest)
    snapshot = SegmentProgressSnapshot(
        pre_message_count=pre_message_count,
        post_message_count=len(messages),
        new_tool_message_count=new_tool_message_count,
        new_trace_count=len(trace or []),
        token_delta=token_delta,
        final_content_present=bool(final_content),
        repeated_tool_call=repeated,
    )
    return snapshot, latest_digest or previous_tool_call_digest
