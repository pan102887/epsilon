"""消息类型层次结构属性测试模块。

使用 Hypothesis 对 BaseMessage 及其子类（SystemMessage、UserMessage、AssistantMessage、ToolMessage）
进行属性测试，验证序列化往返一致性、角色一致性和未知角色错误条件等核心不变量。

测试文件对应设计文档中定义的正确性属性 1-3，每个属性测试通过注释标注对应的属性编号和验证的需求编号。
"""

import hypothesis.strategies as st
import pytest
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

# 消息内容策略：覆盖空字符串和各种长度文本
content_st = st.text(min_size=0, max_size=200)

# 元数据策略：键为非空文本，值为整数，最多 3 个键值对
metadata_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.integers(min_value=0, max_value=10000),
    max_size=3,
)

# 工具名称策略：非空文本
tool_name_st = st.text(min_size=1, max_size=30)


@st.composite
def base_message_st(draw: st.DrawFn) -> BaseMessage:
    """生成随机 BaseMessage 子类实例的组合策略。

    根据随机选择的角色，构造对应的消息子类实例。
    覆盖所有四种消息类型：SystemMessage、UserMessage、AssistantMessage、ToolMessage。

    Returns:
        随机生成的 BaseMessage 子类实例
    """
    role = draw(st.sampled_from(["system", "user", "assistant", "tool"]))
    content = draw(content_st)
    metadata = draw(metadata_st)
    if role == "system":
        return SystemMessage(content=content, metadata=metadata)
    elif role == "user":
        return UserMessage(content=content, metadata=metadata)
    elif role == "assistant":
        return AssistantMessage(content=content, metadata=metadata)
    else:
        tool_name = draw(tool_name_st)
        return ToolMessage(content=content, tool_name=tool_name, metadata=metadata)


# ── Property 1: 序列化往返一致性（Round-Trip） ──
# Feature: message-type-hierarchy, Property 1: 序列化往返一致性


@settings(max_examples=100)
@given(msg=base_message_st())
def test_serialization_roundtrip(msg: BaseMessage) -> None:
    """验证序列化往返一致性：from_dict(msg.to_dict()) 产生与原始消息等价的对象。

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 8.1, 8.2**

    对于任意有效的 BaseMessage 子类实例，执行 BaseMessage.from_dict(msg.to_dict()) 后：
    1. 反序列化后的对象与原始消息在所有字段上等价（content、metadata、role 相同）
    2. ToolMessage 额外检查 tool_name 一致
    3. 反序列化后的对象为与原始对象相同的子类类型（通过 isinstance 验证）
    """
    serialized = msg.to_dict()
    restored = BaseMessage.from_dict(serialized)

    # 验证子类类型一致
    assert type(restored) is type(msg), (
        f"反序列化后类型不一致: 原始={type(msg).__name__}, 恢复={type(restored).__name__}"
    )

    # 验证 role 一致
    assert restored.role == msg.role, (
        f"反序列化后 role 不一致: 原始={msg.role!r}, 恢复={restored.role!r}"
    )

    # 验证 content 一致
    assert restored.content == msg.content, (
        f"反序列化后 content 不一致: 原始={msg.content!r}, 恢复={restored.content!r}"
    )

    # 验证 metadata 一致
    assert restored.metadata == msg.metadata, (
        f"反序列化后 metadata 不一致: 原始={msg.metadata!r}, 恢复={restored.metadata!r}"
    )

    # ToolMessage 额外验证 tool_name
    if isinstance(msg, ToolMessage):
        assert isinstance(restored, ToolMessage)
        assert restored.tool_name == msg.tool_name, (
            f"反序列化后 tool_name 不一致: 原始={msg.tool_name!r}, 恢复={restored.tool_name!r}"
        )


# ── Property 2: 角色一致性（Role Consistency） ──
# Feature: message-type-hierarchy, Property 2: 角色一致性


# 角色与子类的映射关系
ROLE_CLASS_MAP: dict[str, type[BaseMessage]] = {
    "system": SystemMessage,
    "user": UserMessage,
    "assistant": AssistantMessage,
    "tool": ToolMessage,
}


@settings(max_examples=100)
@given(msg=base_message_st())
def test_role_consistency(msg: BaseMessage) -> None:
    """验证角色一致性：role 属性与 isinstance 检查始终一致。

    **Validates: Requirements 2.1, 3.1, 4.1, 5.1, 9.5**

    对于任意 BaseMessage 子类实例：
    1. message.role 返回的字符串与该子类对应的固定角色标识一致
    2. isinstance(message, ExpectedClass) 检查与 message.role == expected_role 检查始终一致
    """
    # 验证 role 属性值属于已知角色集合
    assert msg.role in ROLE_CLASS_MAP, f"role 属性值 {msg.role!r} 不在已知角色集合中"

    # 验证 isinstance 检查与 role 字符串检查一致
    expected_class = ROLE_CLASS_MAP[msg.role]
    assert isinstance(msg, expected_class), (
        "isinstance 检查失败: "
        f"role={msg.role!r} 但 isinstance(msg, {expected_class.__name__}) 为 False"
    )

    # 反向验证：对于所有角色类，isinstance 为 True 当且仅当 role 匹配
    for role_str, cls in ROLE_CLASS_MAP.items():
        is_instance = isinstance(msg, cls)
        role_matches = msg.role == role_str
        assert is_instance == role_matches, (
            f"isinstance 与 role 检查不一致: "
            f"isinstance(msg, {cls.__name__})={is_instance}, "
            f"msg.role == {role_str!r} = {role_matches}"
        )


# ── Property 3: 未知角色错误条件 ──
# Feature: message-type-hierarchy, Property 3: 未知角色错误条件

# 已知角色集合
KNOWN_ROLES = {"system", "user", "assistant", "tool"}

# 生成不属于已知角色的字符串策略
unknown_role_st = st.text(min_size=1, max_size=50).filter(lambda s: s not in KNOWN_ROLES)


@settings(max_examples=100)
@given(unknown_role=unknown_role_st, content=content_st)
def test_unknown_role_raises_value_error(unknown_role: str, content: str) -> None:
    """验证未知角色错误条件：from_dict 对未知 role 抛出 ValueError。

    **Validates: Requirements 6.6**

    对于任意不属于 {"system", "user", "assistant", "tool"} 的字符串作为 role 值，
    调用 BaseMessage.from_dict({"role": unknown_role, "content": content}) 应抛出 ValueError。
    """
    with pytest.raises(ValueError):
        BaseMessage.from_dict({"role": unknown_role, "content": content})


# ── Property 4: ConversationContext 添加方法类型正确性 ──
# Feature: message-type-hierarchy, Property 4: ConversationContext 添加方法类型正确性


@settings(max_examples=100)
@given(content=content_st, tool_name=tool_name_st)
def test_add_methods_create_correct_types(content: str, tool_name: str) -> None:
    """验证 ConversationContext 各 add 方法创建正确的消息子类实例。

    **Validates: Requirements 7.1, 7.2, 7.3, 7.4**

    对于任意内容字符串和工具名称，调用各 add 方法后：
    1. add_system_message 创建的消息为 SystemMessage 实例，content 与传入参数一致
    2. add_user_message 创建的消息为 UserMessage 实例，content 与传入参数一致
    3. add_assistant_message 创建的消息为 AssistantMessage 实例，content 与传入参数一致
    4. add_tool_result 创建的消息为 ToolMessage 实例，content 和 tool_name 与传入参数一致
    """
    # 验证 add_system_message
    ctx = ConversationContext()
    ctx.add_system_message(content)
    msgs = ctx.get_messages()
    assert len(msgs) == 1
    assert isinstance(msgs[0], SystemMessage), (
        f"add_system_message 应创建 SystemMessage，实际为 {type(msgs[0]).__name__}"
    )
    assert msgs[0].content == content
    assert msgs[0].role == "system"

    # 验证 add_user_message
    ctx = ConversationContext()
    ctx.add_user_message(content)
    msgs = ctx.get_messages()
    assert len(msgs) == 1
    assert isinstance(msgs[0], UserMessage), (
        f"add_user_message 应创建 UserMessage，实际为 {type(msgs[0]).__name__}"
    )
    assert msgs[0].content == content
    assert msgs[0].role == "user"

    # 验证 add_assistant_message
    ctx = ConversationContext()
    ctx.add_assistant_message(content)
    msgs = ctx.get_messages()
    assert len(msgs) == 1
    assert isinstance(msgs[0], AssistantMessage), (
        f"add_assistant_message 应创建 AssistantMessage，实际为 {type(msgs[0]).__name__}"
    )
    assert msgs[0].content == content
    assert msgs[0].role == "assistant"

    # 验证 add_tool_result
    ctx = ConversationContext()
    ctx.add_tool_result(tool_name, content)
    msgs = ctx.get_messages()
    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage), (
        f"add_tool_result 应创建 ToolMessage，实际为 {type(msgs[0]).__name__}"
    )
    assert msgs[0].content == content
    assert msgs[0].role == "tool"
    assert msgs[0].tool_name == tool_name, (
        f"tool_name 不一致: 期望={tool_name!r}, 实际={msgs[0].tool_name!r}"
    )


# ── Property 5: ConversationContext 序列化往返保留子类型 ──
# Feature: message-type-hierarchy, Property 5: ConversationContext 序列化往返保留子类型


@st.composite
def conversation_context_st(draw: st.DrawFn) -> ConversationContext:
    """生成包含混合消息类型的 ConversationContext 实例。

    随机生成 1-10 条消息，每条消息随机选择类型，
    确保覆盖所有四种消息类型的混合场景。

    Returns:
        包含随机消息的 ConversationContext 实例
    """
    messages = draw(st.lists(base_message_st(), min_size=1, max_size=10))
    ctx = ConversationContext()
    for msg in messages:
        if isinstance(msg, SystemMessage):
            ctx.add_system_message(msg.content)
        elif isinstance(msg, UserMessage):
            ctx.add_user_message(msg.content)
        elif isinstance(msg, AssistantMessage):
            ctx.add_assistant_message(msg.content)
        elif isinstance(msg, ToolMessage):
            ctx.add_tool_result(msg.tool_name, msg.content)
    return ctx


@settings(max_examples=100)
@given(ctx=conversation_context_st())
def test_context_roundtrip_preserves_subtypes(ctx: ConversationContext) -> None:
    """验证 ConversationContext 序列化往返保留子类型和字段值。

    **Validates: Requirements 7.6, 8.1**

    对于任意包含混合消息类型的 ConversationContext，执行 from_dict(ctx.to_dict()) 后：
    1. 还原的上下文消息数量与原始一致
    2. 每条消息的类型（isinstance）与原始一致
    3. 每条消息的所有字段值（content、role、metadata）与原始一致
    4. ToolMessage 额外验证 tool_name 一致
    """
    serialized = ctx.to_dict()
    restored = ConversationContext.from_dict(serialized)

    original_msgs = ctx.get_messages()
    restored_msgs = restored.get_messages()

    # 验证消息数量一致
    assert len(restored_msgs) == len(original_msgs), (
        f"消息数量不一致: 原始={len(original_msgs)}, 恢复={len(restored_msgs)}"
    )

    # 逐条验证消息类型和字段值
    for i, (orig, rest) in enumerate(zip(original_msgs, restored_msgs, strict=True)):
        # 验证子类类型一致
        assert type(rest) is type(orig), (
            f"消息 {i} 类型不一致: 原始={type(orig).__name__}, 恢复={type(rest).__name__}"
        )

        # 验证 role 一致
        assert rest.role == orig.role, (
            f"消息 {i} role 不一致: 原始={orig.role!r}, 恢复={rest.role!r}"
        )

        # 验证 content 一致
        assert rest.content == orig.content, (
            f"消息 {i} content 不一致: 原始={orig.content!r}, 恢复={rest.content!r}"
        )

        # 验证 metadata 一致
        assert rest.metadata == orig.metadata, (
            f"消息 {i} metadata 不一致: 原始={orig.metadata!r}, 恢复={rest.metadata!r}"
        )

        # ToolMessage 额外验证 tool_name
        if isinstance(orig, ToolMessage):
            assert isinstance(rest, ToolMessage)
            assert rest.tool_name == orig.tool_name, (
                f"消息 {i} tool_name 不一致: 原始={orig.tool_name!r}, 恢复={rest.tool_name!r}"
            )
