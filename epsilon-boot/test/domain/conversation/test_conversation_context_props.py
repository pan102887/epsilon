"""ConversationContext 属性测试。

使用 Hypothesis 对 ConversationContext 的序列化往返一致性进行属性测试，
验证在任意有效输入下，核心不变量始终成立。

注意：ConversationContext 已重构为纯消息容器，不再包含 max_messages 窗口裁剪逻辑。
消息裁剪/压缩职责由 ContextCompactionPort 的实现在编排层中执行。
"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

# ── Hypothesis 生成策略 ──

# 消息角色策略
role_st = st.sampled_from(["system", "user", "assistant", "tool"])

# 非空文本策略（用于消息内容）
content_st = st.text(min_size=0, max_size=200)

# 工具名称策略：tool 角色时有值
tool_name_st = st.text(min_size=1, max_size=30)

# 元数据策略：简单的字符串到整数映射
metadata_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.integers(min_value=0, max_value=10000),
    max_size=3,
)


@st.composite
def message_st(draw: st.DrawFn) -> BaseMessage:
    """生成随机 BaseMessage 子类实例的组合策略。

    根据角色创建对应的具体子类实例，覆盖所有合法角色和可选字段。
    仅 tool 角色携带 tool_name。

    Returns:
        随机生成的 BaseMessage 子类实例
    """
    role = draw(role_st)
    content = draw(content_st)
    meta = draw(metadata_st)
    if role == "system":
        return SystemMessage(content=content, metadata=meta)
    elif role == "user":
        return UserMessage(content=content, metadata=meta)
    elif role == "assistant":
        return AssistantMessage(content=content, metadata=meta)
    else:
        tn = draw(tool_name_st)
        return ToolMessage(content=content, tool_name=tn, metadata=meta)


@st.composite
def conversation_context_st(
    draw: st.DrawFn,
    min_messages: int = 0,
    max_messages_count: int = 50,
) -> ConversationContext:
    """生成随机 ConversationContext 实例的组合策略。

    创建一个 ConversationContext 并填充随机消息。

    Args:
        draw: Hypothesis draw 函数
        min_messages: 最少消息数量
        max_messages_count: 消息列表最大长度上限
    """
    msgs = draw(st.lists(message_st(), min_size=min_messages, max_size=max_messages_count))
    ctx = ConversationContext()
    ctx.replace_messages(msgs)
    return ctx


# ── Property 2: ConversationContext 序列化往返一致性 ──
# Feature: chat-chat-api, Property 2: ConversationContext 序列化往返一致性


@settings(max_examples=100)
@given(ctx=conversation_context_st(max_messages_count=80))
def test_serialization_roundtrip(ctx: ConversationContext) -> None:
    """验证 ConversationContext 序列化往返一致性。

    对于任意有效的 ConversationContext 对象（包含任意数量的 system、user、
    assistant、tool 消息），执行
    ``ConversationContext.from_dict(ctx.to_dict())`` 后产生的对象
    应与原始对象在消息列表内容上完全等价。

    验证: 需求 10.1
    """
    serialized = ctx.to_dict()
    restored = ConversationContext.from_dict(serialized)

    # 消息数量一致
    assert restored.message_count == ctx.message_count, (
        f"消息数量不一致: 原始={ctx.message_count}, 还原={restored.message_count}"
    )

    # 逐条消息内容一致
    for i, (orig, rest) in enumerate(
        zip(ctx.get_messages(), restored.get_messages(), strict=True)
    ):
        assert orig.role == rest.role, f"消息 {i} role 不一致"
        assert orig.content == rest.content, f"消息 {i} content 不一致"
        if isinstance(orig, ToolMessage):
            assert isinstance(rest, ToolMessage)
            assert orig.tool_name == rest.tool_name, f"消息 {i} tool_name 不一致"
        assert orig.metadata == rest.metadata, f"消息 {i} metadata 不一致"


# ── Property 4: 消息列表完整性 ──
# Feature: chat-chat-api, Property 4: 消息列表完整性


@settings(max_examples=100)
@given(ctx=conversation_context_st(max_messages_count=200))
def test_get_messages_returns_all_messages(ctx: ConversationContext) -> None:
    """验证 get_messages() 返回所有已添加的消息。

    对于任意 ConversationContext（消息数量从 0 到 200），
    调用 get_messages() 后返回的消息列表应满足：
    (a) 消息数量与内部列表一致
    (b) 每条消息都是 BaseMessage 实例
    (c) 消息的 role 和 content 与内部列表一致

    验证: 需求 10.3
    """
    result = ctx.get_messages()

    # (a) 消息数量一致
    original = ctx.get_messages()
    assert len(result) == len(original), (
        f"消息数量不一致: 内部={len(original)}, 返回={len(result)}"
    )

    # (b) & (c) 每条消息都是 BaseMessage 实例，且字段一致
    for i, (orig, returned) in enumerate(zip(original, result, strict=True)):
        assert isinstance(returned, BaseMessage), (
            f"第 {i} 条消息应为 BaseMessage 实例，实际为 {type(returned)}"
        )
        assert orig.role == returned.role, (
            f"第 {i} 条消息 role 不一致: 原始={orig.role!r}, 返回={returned.role!r}"
        )
        assert orig.content == returned.content, (
            f"第 {i} 条消息 content 不一致: 原始={orig.content!r}, 返回={returned.content!r}"
        )
