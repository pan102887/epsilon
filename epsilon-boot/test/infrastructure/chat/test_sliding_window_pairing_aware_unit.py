"""SlidingWindowCompactionAdapter 配对保护裁剪单元测试。"""

import logging

import pytest

from domain.chat.context import AssistantMessage, ToolMessage, UserMessage
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)


def _system_msg(content="sys"):
    """快捷创建 system 消息。"""
    from domain.chat.context import SystemMessage

    return SystemMessage(content=content)


def _user_msg(content="u"):
    return UserMessage(content=content)


def _assistant_msg(content="a"):
    return AssistantMessage(content=content)


def _assistant_with_tools(tool_call_ids: list[str], content=""):
    """创建带 tool_calls 的 AssistantMessage。"""
    tool_calls = [
        ToolCallRequest(id=tc_id, name=f"tool_{tc_id}", arguments="{}") for tc_id in tool_call_ids
    ]
    return AssistantMessage(content=content, tool_calls=tool_calls)


def _tool_msg(tool_call_id: str, content="result"):
    return ToolMessage(content=content, tool_name=f"tool_{tool_call_id}", tool_call_id=tool_call_id)


class TestPairingAwareTrimming:
    """配对保护裁剪核心逻辑。"""

    def test_window_boundary_splits_pair_drops_whole_group(self):
        """窗口边界恰好切在 assistant 与 ToolMessage 之间 → 整组丢弃。"""
        messages = [
            _user_msg("old"),
            _assistant_with_tools(["tc1"]),
            _tool_msg("tc1"),
            _user_msg("recent"),
            _assistant_msg("reply"),
        ]
        adapter = SlidingWindowCompactionAdapter(max_messages=2)
        result = adapter.compact_messages(messages)
        non_system = [m for m in result if m.role != "system"]
        assert len(non_system) == 2
        assert non_system[0].content == "recent"
        assert non_system[1].content == "reply"
        assert not any(isinstance(m, ToolMessage) for m in result)

    def test_three_tool_calls_one_outside_window_drops_group(self):
        """assistant 含 3 id，1 条 ToolMessage 被配额挤出 → 整组丢弃。"""
        messages = [
            _assistant_with_tools(["a", "b", "c"]),
            _tool_msg("a"),
            _tool_msg("b"),
            _tool_msg("c"),
            _user_msg("u1"),
            _assistant_msg("final"),
        ]
        # max_messages=3: 只能保留最近 3 条非 system
        # 从尾部反向：final(1), u1(2), tool_c... 需要整组 assistant+3tools=4，超配额
        adapter = SlidingWindowCompactionAdapter(max_messages=3)
        result = adapter.compact_messages(messages)
        non_system = [m for m in result if m.role != "system"]
        assert len(non_system) <= 3
        # 不含孤儿 ToolMessage
        for m in non_system:
            if isinstance(m, ToolMessage):
                # 如果有 ToolMessage，其对应 assistant 必须也在
                assistant_found = any(
                    isinstance(am, AssistantMessage)
                    and any(tc.id == m.tool_call_id for tc in am.tool_calls)
                    for am in non_system
                )
                assert assistant_found

    def test_chained_groups_recent_kept_older_dropped(self):
        """多组串联：最近完整组保留，上一组半组丢弃。"""
        messages = [
            _assistant_with_tools(["old1"]),
            _tool_msg("old1"),
            _assistant_with_tools(["new1", "new2"]),
            _tool_msg("new1"),
            _tool_msg("new2"),
        ]
        # max_messages=4: 可保留 recent group (assistant + 2 tools = 3)，还剩 1 配额
        # old group 需要 2 (assistant + 1 tool)，配额不够 → 丢弃
        adapter = SlidingWindowCompactionAdapter(max_messages=4)
        result = adapter.compact_messages(messages)
        non_system = [m for m in result if m.role != "system"]
        tool_ids = {m.tool_call_id for m in non_system if isinstance(m, ToolMessage)}
        assert "new1" in tool_ids
        assert "new2" in tool_ids
        assert "old1" not in tool_ids

    def test_no_tool_messages_falls_back_to_v3_literal(self):
        """无 ToolMessage 时退化为 v3 逻辑。"""
        messages = [
            _system_msg(),
            _user_msg("u1"),
            _assistant_msg("a1"),
            _user_msg("u2"),
            _assistant_msg("a2"),
        ]
        adapter = SlidingWindowCompactionAdapter(max_messages=2)
        result = adapter.compact_messages(messages)
        assert result[0].role == "system"
        non_system = [m for m in result if m.role != "system"]
        assert len(non_system) == 2
        assert non_system[0].content == "u2"
        assert non_system[1].content == "a2"

    def test_system_messages_fully_preserved(self):
        """system 消息全保留。"""
        messages = [
            _system_msg("sys1"),
            _system_msg("sys2"),
            _user_msg("u1"),
            _assistant_msg("a1"),
            _user_msg("u2"),
        ]
        adapter = SlidingWindowCompactionAdapter(max_messages=1)
        result = adapter.compact_messages(messages)
        system = [m for m in result if m.role == "system"]
        assert len(system) == 2
        assert system[0].content == "sys1"
        assert system[1].content == "sys2"

    def test_empty_input_returns_empty(self):
        """空输入返回空。"""
        adapter = SlidingWindowCompactionAdapter(max_messages=5)
        assert adapter.compact_messages([]) == []

    def test_logger_debug_records_dropped_count(self, caplog):
        """丢弃时 logger.debug 记录信息。"""
        messages = [
            _assistant_with_tools(["tc1"]),
            _tool_msg("tc1"),
            _user_msg("recent"),
        ]
        adapter = SlidingWindowCompactionAdapter(max_messages=1)
        with caplog.at_level(logging.DEBUG):
            adapter.compact_messages(messages)
        assert any("配对保护裁剪丢弃" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_compact_async_signature_unchanged(self):
        """compact() 异步入口返回 ContextCompactionResult。"""
        from domain.chat.value_objects import ContextCompactionResult

        messages = [_user_msg("u")]
        adapter = SlidingWindowCompactionAdapter(max_messages=5)
        result = await adapter.compact(messages)
        assert isinstance(result, ContextCompactionResult)
        assert result.usage == {}
        assert result.summary_created is False
