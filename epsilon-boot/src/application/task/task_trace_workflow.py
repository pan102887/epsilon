"""任务 trace 提取 workflow。"""

from __future__ import annotations

import time
from collections.abc import Mapping

from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.task.value_objects import TraceEntry


class TaskTraceWorkflow:
    """从对话上下文消息中提取任务执行轨迹。"""

    def extract_trace(
        self,
        context: ConversationContext,
        *,
        start_index: int = 0,
        event_timestamps: Mapping[int, int] | None = None,
    ) -> list[TraceEntry]:
        """按既有 TaskAgentAdapter 语义提取新增消息 trace。"""

        stamps = event_timestamps or {}
        messages = context.get_messages()
        trace: list[TraceEntry] = []
        step = 1

        for offset, message in enumerate(messages[start_index:]):
            absolute_index = start_index + offset
            event_ts = stamps.get(absolute_index)
            timestamp_ms = event_ts if event_ts is not None else int(time.time() * 1000)

            if isinstance(message, AssistantMessage) and message.tool_calls:
                for tool_call in message.tool_calls:
                    trace.append(
                        TraceEntry(
                            step=step,
                            action="tool_call",
                            detail=f"{tool_call.name}({tool_call.arguments})",
                            timestamp_ms=timestamp_ms,
                        )
                    )
                    step += 1
            elif isinstance(message, ToolMessage):
                trace.append(
                    TraceEntry(
                        step=step,
                        action="tool_result",
                        detail=message.content,
                        timestamp_ms=timestamp_ms,
                    )
                )
                step += 1

        return trace
