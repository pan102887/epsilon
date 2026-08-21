"""滑动窗口上下文压缩适配器模块。

实现 ContextCompactionPort 协议的滑动窗口压缩策略。
该适配器保留所有 system 消息和最近 max_messages 条非 system 消息，
并在裁剪时识别 AssistantMessage(tool_calls) 与 ToolMessage 的配对关系，
整组保留或整组丢弃，避免 OpenAI/Anthropic 400 错误。
"""

import logging
from collections.abc import Sequence

from domain.chat.context import AssistantMessage, BaseMessage, ToolMessage
from domain.chat.value_objects import ContextCompactionResult
from domain.model_access.ports import ModelAccessPort

logger = logging.getLogger(__name__)


class SlidingWindowCompactionAdapter:
    """滑动窗口压缩适配器，实现 ContextCompactionPort。

    保留所有 system 消息和最近 max_messages 条非 system 消息，
    在裁剪时通过配对保护确保 tool_calls/ToolMessage 完整性。

    Attributes:
        _max_messages: 非 system 消息的最大保留数量
    """

    def __init__(self, max_messages: int = 50) -> None:
        """初始化滑动窗口压缩适配器。

        Args:
            max_messages: 非 system 消息的最大保留数量，必须为正整数，默认值为 50

        Raises:
            ValueError: 当 max_messages ≤ 0 时抛出
        """
        if max_messages <= 0:
            raise ValueError(f"max_messages 必须为正整数，当前值为 {max_messages}")
        self._max_messages = max_messages

    @property
    def max_messages(self) -> int:
        """Maximum number of non-system messages retained."""
        return self._max_messages

    def _trim_with_pairing(
        self,
        non_system_messages: list[BaseMessage],
    ) -> list[BaseMessage]:
        """对非 system 消息列表执行配对保护裁剪。

        单次反向扫描算法（O(N)）：从尾部向头部遍历，维护待配对 ToolMessage
        缓冲；遇到 AssistantMessage(tool_calls) 时检查全集匹配，整组保留或丢弃。

        Args:
            non_system_messages: 已剔除 SystemMessage 的消息列表，按原始顺序。

        Returns:
            裁剪后的非 system 消息列表，按原始正向顺序。
        """
        max_messages = self._max_messages
        kept_reverse: list[BaseMessage] = []
        used = 0
        pending_tools_by_id: dict[str, ToolMessage] = {}
        dropped_groups = 0
        dropped_messages = 0

        for msg in reversed(non_system_messages):
            if isinstance(msg, ToolMessage):
                pending_tools_by_id[msg.tool_call_id] = msg
                continue

            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                tool_call_ids = [tc.id for tc in msg.tool_calls]
                matched_tools = [
                    pending_tools_by_id[tc_id]
                    for tc_id in tool_call_ids
                    if tc_id in pending_tools_by_id
                ]

                if len(matched_tools) != len(tool_call_ids):
                    dropped_groups += 1
                    dropped_messages += 1 + len(matched_tools)
                    for tc_id in tool_call_ids:
                        pending_tools_by_id.pop(tc_id, None)
                    continue

                group_size = 1 + len(matched_tools)
                if used + group_size > max_messages:
                    dropped_groups += 1
                    dropped_messages += group_size
                    for tc_id in tool_call_ids:
                        pending_tools_by_id.pop(tc_id, None)
                    continue

                group = [msg, *matched_tools]
                kept_reverse.extend(reversed(group))
                used += group_size
                for tc_id in tool_call_ids:
                    pending_tools_by_id.pop(tc_id, None)
                continue

            if used < max_messages:
                kept_reverse.append(msg)
                used += 1
            else:
                break

        if pending_tools_by_id:
            orphan_count = len(pending_tools_by_id)
            dropped_groups += 1
            dropped_messages += orphan_count
            pending_tools_by_id.clear()

        if dropped_groups > 0:
            logger.debug(
                "配对保护裁剪丢弃 groups=%d messages=%d",
                dropped_groups,
                dropped_messages,
            )

        kept_reverse.reverse()
        return kept_reverse

    def compact_messages(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """同步压缩消息列表。

        执行配对保护的滑动窗口压缩策略：保留所有 system 消息，对非 system
        消息在有 ToolMessage 时走配对保护路径，否则退化到 v3 原有逻辑。

        Args:
            messages: 完整的消息列表

        Returns:
            压缩后的消息列表。
        """
        if not messages:
            return []

        system_messages = [m for m in messages if m.role == "system"]
        non_system_messages = [m for m in messages if m.role != "system"]

        has_tool_messages = any(isinstance(m, ToolMessage) for m in non_system_messages)

        if not has_tool_messages:
            if len(non_system_messages) > self._max_messages:
                non_system_messages = non_system_messages[-self._max_messages :]
            return system_messages + non_system_messages

        trimmed = self._trim_with_pairing(non_system_messages)
        return system_messages + trimmed

    async def compact(
        self,
        messages: Sequence[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextCompactionResult:
        """异步端口入口，返回滑动窗口压缩结果。

        ``model_access`` 与 ``model`` 仅为兼容 LLM 摘要策略签名，滑动窗口策略忽略。
        """
        return ContextCompactionResult(
            messages=self.compact_messages(messages),
            usage={},
            summary_created=False,
        )
