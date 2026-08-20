"""ConversationContext 序列化往返属性测试模块。

对随机生成的 ``(messages, event_timestamps, session_id)`` 三元组构造
``ConversationContext``，断言：

- ``ConversationContext.from_dict(ctx.to_dict())`` 的结果在 messages 列表内容
  / event_timestamps / session_id 三字段上等价于原 ctx。
- 旧格式 ``{"messages": [...]}`` 经 ``from_dict`` 还原后两个新字段取默认值。

覆盖需求 5.3 / 5.4 / 5.5 与 Property 7。
"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.chat.context import ConversationContext

# 文本策略
content_st = st.text(min_size=0, max_size=80)

# message_index → ms 时间戳的子集随机生成
event_ts_st = st.dictionaries(
    keys=st.integers(min_value=0, max_value=100),
    values=st.integers(min_value=0, max_value=2_000_000_000_000),
    min_size=0,
    max_size=8,
)

# session_id 策略: None 或随机字符串
session_id_st = st.one_of(
    st.none(),
    st.text(
        min_size=1,
        max_size=40,
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    ),
)


def _populate_context(
    ctx: ConversationContext,
    user_msgs: list[str],
    assistant_msgs: list[str],
) -> None:
    """按交错顺序追加用户 / 助手消息以生成测试用 messages 列表。"""
    for u, a in zip(user_msgs, assistant_msgs, strict=False):
        ctx.add_user_message(u)
        ctx.add_assistant_message(a)


@settings(max_examples=60, deadline=None)
@given(
    user_msgs=st.lists(content_st, min_size=0, max_size=4),
    assistant_msgs=st.lists(content_st, min_size=0, max_size=4),
    event_timestamps=event_ts_st,
    session_id=session_id_st,
)
def test_roundtrip_preserves_three_fields(
    user_msgs: list[str],
    assistant_msgs: list[str],
    event_timestamps: dict[int, int],
    session_id: str | None,
) -> None:
    """随机三元组构造 ctx,from_dict(to_dict(ctx)) 在三字段上等价。"""
    ctx = ConversationContext()
    _populate_context(ctx, user_msgs, assistant_msgs)
    # 直接赋值给正式字段
    ctx.event_timestamps = dict(event_timestamps)
    ctx.session_id = session_id

    serialized = ctx.to_dict()
    restored = ConversationContext.from_dict(serialized)

    # messages 列表内容比对(role + content 序列)
    original_msgs = ctx.get_messages()
    restored_msgs = restored.get_messages()
    assert len(original_msgs) == len(restored_msgs)
    for orig, rest in zip(original_msgs, restored_msgs, strict=True):
        assert orig.role == rest.role
        assert orig.content == rest.content

    # event_timestamps / session_id 等价
    assert restored.event_timestamps == ctx.event_timestamps
    assert restored.session_id == ctx.session_id


@settings(max_examples=40, deadline=None)
@given(
    user_msgs=st.lists(content_st, min_size=0, max_size=3),
    assistant_msgs=st.lists(content_st, min_size=0, max_size=3),
)
def test_legacy_format_recovers_to_default_extra_fields(
    user_msgs: list[str], assistant_msgs: list[str]
) -> None:
    """v1 旧格式仅含 messages,反序列化后 event_timestamps == {} 且 session_id is None。"""
    ctx = ConversationContext()
    _populate_context(ctx, user_msgs, assistant_msgs)

    legacy = {"messages": [m.to_dict() for m in ctx.get_messages()]}
    restored = ConversationContext.from_dict(legacy)

    assert restored.event_timestamps == {}
    assert restored.session_id is None
    assert restored.message_count == ctx.message_count

    # 反序列化后再 to_dict, 输出与原 legacy 在 messages 字段上等价、不引入伪写入
    out = restored.to_dict()
    assert set(out.keys()) == {"messages"}
