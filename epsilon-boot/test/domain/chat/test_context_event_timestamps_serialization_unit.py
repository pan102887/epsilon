"""ConversationContext.event_timestamps 序列化与双向兼容单元测试模块。

验证 v2 把 ``event_timestamps`` / ``session_id`` 升级为 ``ConversationContext``
正式字段后，``to_dict`` / ``from_dict`` 的紧凑序列化与向后兼容行为：

- 默认实例 ``to_dict()`` 仅含 ``messages`` 键；
- 写入 ``event_timestamps[k]=t`` 后 ``to_dict()`` 含 ``event_timestamps``；
- ``from_dict({"messages": [...]})`` 旧格式还原后 ``event_timestamps == {}`` 且
  ``session_id is None``；
- ``from_dict(to_dict(ctx)) == ctx``（按 messages / event_timestamps /
  session_id 三字段比对）；
- JSON 字符串键还原为 int 键，避免下游用 int 索引查表 miss；
- 仅含 ``event_timestamps`` / 仅含 ``session_id`` 的混合旧格式同样能反序列化。

覆盖需求 5.1 / 5.2 / 5.3 / 5.4 / 5.5 / 5.10 与 Property 7。
"""

import json

from domain.chat.context import ConversationContext


class TestToDictCompactStrategy:
    """验证 ``to_dict`` 的紧凑序列化策略。"""

    def test_default_instance_outputs_messages_only(self) -> None:
        """默认实例 ``event_timestamps`` 与 ``session_id`` 均为默认值,输出仅含 messages。"""
        ctx = ConversationContext()
        data = ctx.to_dict()
        assert set(data.keys()) == {"messages"}
        assert data["messages"] == []

    def test_event_timestamps_appended_when_non_empty(self) -> None:
        """写入非空 ``event_timestamps`` 后 ``to_dict`` 输出应包含该键。"""
        ctx = ConversationContext()
        ctx.event_timestamps[2] = 1_717_000_000_123
        ctx.event_timestamps[3] = 1_717_000_000_456
        data = ctx.to_dict()
        assert "event_timestamps" in data
        assert data["event_timestamps"] == {2: 1_717_000_000_123, 3: 1_717_000_000_456}

    def test_session_id_appended_when_set(self) -> None:
        """``session_id`` 设置后应出现在 to_dict 输出中。"""
        ctx = ConversationContext()
        ctx.session_id = "sess-1"
        data = ctx.to_dict()
        assert data["session_id"] == "sess-1"

    def test_to_dict_event_timestamps_is_a_copy(self) -> None:
        """to_dict 输出的 event_timestamps 应为副本,外部修改不影响实例。"""
        ctx = ConversationContext()
        ctx.event_timestamps[1] = 1000
        data = ctx.to_dict()
        data["event_timestamps"][9] = 9999
        assert 9 not in ctx.event_timestamps


class TestFromDictBackwardCompat:
    """验证 ``from_dict`` 对旧格式的双向兼容。"""

    def test_legacy_messages_only(self) -> None:
        """v1 旧格式仅含 messages,反序列化后两个新字段取默认值。"""
        ctx = ConversationContext.from_dict({"messages": []})
        assert ctx.event_timestamps == {}
        assert ctx.session_id is None

    def test_legacy_with_max_messages_field_ignored(self) -> None:
        """v1 旧格式的 max_messages 字段应被忽略,event_timestamps 默认空 dict。"""
        ctx = ConversationContext.from_dict({"messages": [], "max_messages": 100})
        assert ctx.event_timestamps == {}
        assert ctx.session_id is None

    def test_only_event_timestamps_present(self) -> None:
        """仅含 event_timestamps 的混合旧格式应正确还原,session_id 默认 None。"""
        data = {"messages": [], "event_timestamps": {0: 1000}}
        ctx = ConversationContext.from_dict(data)
        assert ctx.event_timestamps == {0: 1000}
        assert ctx.session_id is None

    def test_only_session_id_present(self) -> None:
        """仅含 session_id 的混合旧格式应正确还原,event_timestamps 默认空 dict。"""
        data = {"messages": [], "session_id": "sess-x"}
        ctx = ConversationContext.from_dict(data)
        assert ctx.event_timestamps == {}
        assert ctx.session_id == "sess-x"

    def test_session_id_null_treated_as_none(self) -> None:
        """session_id 显式为 None 应等价于缺失。"""
        data = {"messages": [], "session_id": None}
        ctx = ConversationContext.from_dict(data)
        assert ctx.session_id is None


class TestRoundtripWithJSONStringifiedKeys:
    """验证 JSON 友好性: int 键经 json.dumps 后还原为 int。"""

    def test_int_keys_recovered_from_string_keys(self) -> None:
        """JSON 反序列化得到 dict[str, int] 时应被 from_dict 还原为 dict[int, int]。"""
        # JSON 不支持 int 键,json.dumps 会自动 stringify 为 str
        data = {"messages": [], "event_timestamps": {"2": 1000, "3": 2000}}
        ctx = ConversationContext.from_dict(data)
        assert ctx.event_timestamps == {2: 1000, 3: 2000}
        # 关键: 键的类型必须是 int
        for k in ctx.event_timestamps:
            assert isinstance(k, int)
        for v in ctx.event_timestamps.values():
            assert isinstance(v, int)

    def test_to_dict_then_json_roundtrip(self) -> None:
        """ctx -> to_dict -> json.dumps -> json.loads -> from_dict 完整回环还原。"""
        ctx = ConversationContext()
        ctx.add_user_message("hi")
        ctx.event_timestamps[1] = 1_717_000_000_999
        ctx.session_id = "sess-r"

        serialized = json.dumps(ctx.to_dict(), ensure_ascii=False)
        # json.dumps 会把 int 键变成 str
        assert '"1": 1717000000999' in serialized

        restored = ConversationContext.from_dict(json.loads(serialized))
        assert restored.event_timestamps == {1: 1_717_000_000_999}
        assert restored.session_id == "sess-r"


class TestRoundtripIdempotent:
    """验证 from_dict(to_dict(ctx)) 等价于 ctx。"""

    def test_default_ctx_roundtrip(self) -> None:
        """默认实例往返后 messages / event_timestamps / session_id 三字段均等价。"""
        ctx = ConversationContext()
        restored = ConversationContext.from_dict(ctx.to_dict())
        assert restored.message_count == 0
        assert restored.event_timestamps == ctx.event_timestamps == {}
        assert restored.session_id is ctx.session_id is None

    def test_full_ctx_roundtrip(self) -> None:
        """含消息 / 时间戳 / session_id 的完整实例往返等价。"""
        ctx = ConversationContext()
        ctx.add_system_message("sys")
        ctx.add_user_message("hello")
        ctx.event_timestamps[0] = 100
        ctx.event_timestamps[1] = 200
        ctx.session_id = "sess-full"

        restored = ConversationContext.from_dict(ctx.to_dict())
        # 比对消息列表内容
        assert [m.role for m in restored.get_messages()] == ["system", "user"]
        assert [m.content for m in restored.get_messages()] == ["sys", "hello"]
        # 比对新增字段
        assert restored.event_timestamps == ctx.event_timestamps
        assert restored.session_id == ctx.session_id

    def test_legacy_then_to_dict_does_not_inject_pseudo_keys(self) -> None:
        """v1 旧格式 from_dict 后立即 to_dict,不应注入空 event_timestamps / session_id 键。"""
        legacy = {"messages": []}
        ctx = ConversationContext.from_dict(legacy)
        out = ctx.to_dict()
        assert set(out.keys()) == {"messages"}
        assert "event_timestamps" not in out
        assert "session_id" not in out
