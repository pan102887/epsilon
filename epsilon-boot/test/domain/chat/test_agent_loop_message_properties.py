"""Agent Loop 消息模型属性测试模块。

使用 Hypothesis 对 AssistantMessage 的 tool_calls 扩展字段和
ToolMessage 的 tool_call_id 扩展字段进行属性测试，
验证序列化往返一致性、字段的条件性包含等核心不变量。

测试覆盖需求 1.6（AssistantMessage tool_calls 往返一致性）、
需求 2.4、2.5（ToolMessage tool_call_id 往返一致性与向后兼容）和
需求 3.3、3.4、3.5（OpenAICompatibleAdapter._to_openai_messages() 输出格式符合 OpenAI API 规范），
确保在任意有效输入下，to_dict() → from_dict() 往返产生等价对象，
且序列化输出格式满足 OpenAI Chat Completions API 的结构要求。
"""

import json

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
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter

# ── Hypothesis 生成策略 ──

# 消息内容策略：覆盖空字符串和各种长度文本
content_st = st.text(min_size=0, max_size=200)

# 元数据策略：键为非空文本，值为整数，最多 3 个键值对
metadata_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.integers(min_value=0, max_value=10000),
    max_size=3,
)


@st.composite
def json_string_st(draw: st.DrawFn) -> str:
    """生成合法的 JSON 字符串策略。

    构造一个包含 1-3 个键值对的 JSON 对象字符串，
    确保生成的字符串是有效的 JSON 格式且非空。

    Returns:
        合法的 JSON 对象字符串，如 '{"key": "value"}'
    """
    obj = draw(
        st.dictionaries(
            keys=st.text(
                min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L", "N"))
            ),
            values=st.text(min_size=0, max_size=30),
            min_size=1,
            max_size=3,
        )
    )
    return json.dumps(obj, ensure_ascii=False)


@st.composite
def tool_call_request_st(draw: st.DrawFn) -> ToolCallRequest:
    """生成随机 ToolCallRequest 实例的组合策略。

    为 id、name 生成非空字符串，为 arguments 生成合法的 JSON 字符串，
    满足 ToolCallRequest 的 __post_init__ 校验要求（三个字段均不能为空）。

    Returns:
        随机生成的 ToolCallRequest 实例
    """
    tc_id = draw(
        st.text(
            min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
        )
    )
    tc_name = draw(
        st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N")))
    )
    tc_arguments = draw(json_string_st())
    return ToolCallRequest(id=tc_id, name=tc_name, arguments=tc_arguments)


@st.composite
def assistant_message_with_tool_calls_st(draw: st.DrawFn) -> AssistantMessage:
    """生成携带非空 tool_calls 的 AssistantMessage 实例。

    确保 tool_calls 列表至少包含一个 ToolCallRequest，
    用于测试 tool_calls 非空时的序列化行为。

    Returns:
        携带至少一个 tool_call 的 AssistantMessage 实例
    """
    content = draw(content_st)
    metadata = draw(metadata_st)
    tool_calls = draw(st.lists(tool_call_request_st(), min_size=1, max_size=5))
    return AssistantMessage(content=content, metadata=metadata, tool_calls=tool_calls)


@st.composite
def assistant_message_st(draw: st.DrawFn) -> AssistantMessage:
    """生成随机 AssistantMessage 实例的组合策略。

    tool_calls 列表可能为空也可能非空，覆盖两种场景。

    Returns:
        随机生成的 AssistantMessage 实例
    """
    content = draw(content_st)
    metadata = draw(metadata_st)
    tool_calls = draw(st.lists(tool_call_request_st(), min_size=0, max_size=5))
    return AssistantMessage(content=content, metadata=metadata, tool_calls=tool_calls)


# ── Property: AssistantMessage tool_calls 往返一致性 ──
# Feature: agent-loop-function-calling, Property: AssistantMessage tool_calls 往返一致性


@settings(max_examples=100)
@given(msg=assistant_message_st())
def test_assistant_message_tool_calls_roundtrip(msg: AssistantMessage) -> None:
    """验证 AssistantMessage 携带 tool_calls 时的序列化往返一致性。

    **Validates: Requirements 1.6**

    对于任意有效的 AssistantMessage 实例（tool_calls 可为空或非空），
    执行 to_dict() → from_dict() 后应产生与原始对象等价的 AssistantMessage：
    1. 反序列化后的对象为 AssistantMessage 实例
    2. role、content、metadata 字段与原始一致
    3. tool_calls 列表长度与原始一致
    4. 每个 ToolCallRequest 的 id、name、arguments 与原始一致
    """
    serialized = msg.to_dict()
    restored = BaseMessage.from_dict(serialized)

    # 验证类型
    assert isinstance(restored, AssistantMessage), (
        f"反序列化后应为 AssistantMessage，实际为 {type(restored).__name__}"
    )

    # 验证基础字段
    assert restored.role == msg.role
    assert restored.content == msg.content
    assert restored.metadata == msg.metadata

    # 验证 tool_calls 列表长度
    assert len(restored.tool_calls) == len(msg.tool_calls), (
        f"tool_calls 长度不一致: 原始={len(msg.tool_calls)}, 恢复={len(restored.tool_calls)}"
    )

    # 逐个验证 ToolCallRequest 字段
    for i, (orig_tc, rest_tc) in enumerate(
        zip(msg.tool_calls, restored.tool_calls, strict=True)
    ):
        assert rest_tc.id == orig_tc.id, (
            f"tool_calls[{i}].id 不一致: 原始={orig_tc.id!r}, 恢复={rest_tc.id!r}"
        )
        assert rest_tc.name == orig_tc.name, (
            f"tool_calls[{i}].name 不一致: 原始={orig_tc.name!r}, 恢复={rest_tc.name!r}"
        )
        assert rest_tc.arguments == orig_tc.arguments, (
            "tool_calls[{i}].arguments 不一致: "
            f"原始={orig_tc.arguments!r}, 恢复={rest_tc.arguments!r}"
        )


# ── Property: tool_calls 为空时 to_dict() 不包含 tool_calls 键 ──


@settings(max_examples=100)
@given(content=content_st, metadata=metadata_st)
def test_empty_tool_calls_excluded_from_dict(content: str, metadata: dict) -> None:
    """验证 tool_calls 为空列表时，to_dict() 输出不包含 tool_calls 键。

    **Validates: Requirements 1.3**

    当 AssistantMessage 的 tool_calls 为空列表时，to_dict() 的输出字典
    不应包含 tool_calls 键，确保与现有序列化格式向后兼容。
    """
    msg = AssistantMessage(content=content, metadata=metadata, tool_calls=[])
    serialized = msg.to_dict()

    assert "tool_calls" not in serialized, (
        f"tool_calls 为空时 to_dict() 不应包含 tool_calls 键，实际输出: {serialized}"
    )


# ── Property: tool_calls 非空时 to_dict() 包含 tool_calls 键 ──


@settings(max_examples=100)
@given(msg=assistant_message_with_tool_calls_st())
def test_nonempty_tool_calls_included_in_dict(msg: AssistantMessage) -> None:
    """验证 tool_calls 非空时，to_dict() 输出包含 tool_calls 键。

    **Validates: Requirements 1.2**

    当 AssistantMessage 的 tool_calls 列表非空时，to_dict() 的输出字典
    应包含 tool_calls 键，且值为 ToolCallRequest 列表的序列化形式，
    每个元素包含 id、name、arguments 三个键。
    """
    serialized = msg.to_dict()

    assert "tool_calls" in serialized, "tool_calls 非空时 to_dict() 应包含 tool_calls 键"

    # 验证序列化后的 tool_calls 结构
    assert len(serialized["tool_calls"]) == len(msg.tool_calls)
    for i, tc_dict in enumerate(serialized["tool_calls"]):
        assert set(tc_dict.keys()) == {"id", "name", "arguments"}, (
            f"tool_calls[{i}] 应恰好包含 id、name、arguments 三个键，实际: {set(tc_dict.keys())}"
        )


# ── Hypothesis 生成策略：ToolMessage ──

# 工具名称策略：非空字母数字字符串
tool_name_st = st.text(
    min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"))
)

# tool_call_id 策略：可为空字符串或非空标识符，覆盖默认值和正常值两种场景
tool_call_id_st = st.text(
    min_size=0, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
)


@st.composite
def tool_message_st(draw: st.DrawFn) -> ToolMessage:
    """生成随机 ToolMessage 实例的组合策略。

    为 content、tool_name、tool_call_id、metadata 生成随机值，
    tool_call_id 可为空字符串或非空标识符，覆盖向后兼容和正常使用两种场景。

    Returns:
        随机生成的 ToolMessage 实例
    """
    content = draw(content_st)
    tool_name = draw(tool_name_st)
    tool_call_id = draw(tool_call_id_st)
    metadata = draw(metadata_st)
    return ToolMessage(
        content=content, tool_name=tool_name, tool_call_id=tool_call_id, metadata=metadata
    )


# ── Property: ToolMessage tool_call_id 往返一致性 ──
# Feature: agent-loop-function-calling, Property: ToolMessage tool_call_id 往返一致性


@settings(max_examples=100)
@given(msg=tool_message_st())
def test_tool_message_tool_call_id_roundtrip(msg: ToolMessage) -> None:
    """验证 ToolMessage 携带 tool_call_id 时的序列化往返一致性。

    **Validates: Requirements 2.5**

    对于任意有效的 ToolMessage 实例（tool_call_id 可为空或非空），
    执行 to_dict() → from_dict() 后应产生与原始对象等价的 ToolMessage：
    1. 反序列化后的对象为 ToolMessage 实例
    2. role、content、metadata 字段与原始一致
    3. tool_name 字段与原始一致
    4. tool_call_id 字段与原始一致
    """
    serialized = msg.to_dict()
    restored = BaseMessage.from_dict(serialized)

    # 验证类型
    assert isinstance(restored, ToolMessage), (
        f"反序列化后应为 ToolMessage，实际为 {type(restored).__name__}"
    )

    # 验证基础字段
    assert restored.role == msg.role
    assert restored.content == msg.content
    assert restored.metadata == msg.metadata

    # 验证 ToolMessage 特有字段
    assert restored.tool_name == msg.tool_name, (
        f"tool_name 不一致: 原始={msg.tool_name!r}, 恢复={restored.tool_name!r}"
    )
    assert restored.tool_call_id == msg.tool_call_id, (
        f"tool_call_id 不一致: 原始={msg.tool_call_id!r}, 恢复={restored.tool_call_id!r}"
    )


# ── Property: 旧格式（无 tool_call_id）反序列化向后兼容 ──


@settings(max_examples=100)
@given(content=content_st, tool_name=tool_name_st, metadata=metadata_st)
def test_tool_message_legacy_format_backward_compat(
    content: str, tool_name: str, metadata: dict
) -> None:
    """验证旧格式字典（不含 tool_call_id 键）反序列化时 tool_call_id 默认为空字符串。

    **Validates: Requirements 2.4**

    当反序列化的字典数据来自旧版本（不包含 tool_call_id 键）时，
    from_dict() 应将 tool_call_id 设为空字符串，确保向后兼容已持久化的旧格式数据。
    """
    legacy_dict: dict = {"role": "tool", "content": content, "tool_name": tool_name}
    if metadata:
        legacy_dict["metadata"] = metadata

    restored = BaseMessage.from_dict(legacy_dict)

    # 验证类型
    assert isinstance(restored, ToolMessage), (
        f"反序列化后应为 ToolMessage，实际为 {type(restored).__name__}"
    )

    # 验证基础字段
    assert restored.content == content
    assert restored.tool_name == tool_name

    # 核心断言：旧格式缺失 tool_call_id 时应默认为空字符串
    assert restored.tool_call_id == "", (
        f"旧格式缺失 tool_call_id 时应默认为空字符串，实际为 {restored.tool_call_id!r}"
    )


# ── Hypothesis 生成策略：SystemMessage / UserMessage ──


@st.composite
def system_message_st(draw: st.DrawFn) -> SystemMessage:
    """生成随机 SystemMessage 实例的组合策略。

    Returns:
        随机生成的 SystemMessage 实例
    """
    content = draw(content_st)
    return SystemMessage(content=content)


@st.composite
def user_message_st(draw: st.DrawFn) -> UserMessage:
    """生成随机 UserMessage 实例的组合策略。

    Returns:
        随机生成的 UserMessage 实例
    """
    content = draw(content_st)
    return UserMessage(content=content)


# ── Property: _to_openai_messages() 输出格式符合 OpenAI API 规范 ──
# Feature: agent-loop-function-calling, Property: 消息序列化输出格式


@settings(max_examples=100)
@given(msg=assistant_message_with_tool_calls_st())
def test_serialize_assistant_with_tool_calls_openai_format(msg: AssistantMessage) -> None:
    """验证携带 tool_calls 的 AssistantMessage 序列化输出符合 OpenAI API 格式。

    **Validates: Requirements 3.3**

    对于任意携带非空 tool_calls 的 AssistantMessage，经 _to_openai_messages() 序列化后：
    1. role 字段为 "assistant"
    2. content 字段存在
    3. tool_calls 列表中每个元素包含 id、type（值为 "function"）和 function 字典
    4. function 字典包含 name 和 arguments 两个键
    """
    result = OpenAICompatibleAdapter._to_openai_messages([msg])
    assert len(result) == 1

    serialized = result[0]
    assert serialized["role"] == "assistant"
    assert "content" in serialized
    assert "tool_calls" in serialized

    for i, tc in enumerate(serialized["tool_calls"]):
        assert "id" in tc, f"tool_calls[{i}] 缺少 id 字段"
        assert tc["type"] == "function", (
            f"tool_calls[{i}].type 应为 'function'，实际为 {tc['type']!r}"
        )
        assert "function" in tc, f"tool_calls[{i}] 缺少 function 字段"
        func = tc["function"]
        assert "name" in func, f"tool_calls[{i}].function 缺少 name 字段"
        assert "arguments" in func, f"tool_calls[{i}].function 缺少 arguments 字段"


@settings(max_examples=100)
@given(msg=tool_message_st())
def test_serialize_tool_message_includes_tool_call_id(msg: ToolMessage) -> None:
    """验证 ToolMessage 序列化输出包含 role、content 和 tool_call_id 字段。

    **Validates: Requirements 3.4**

    对于任意有效的 ToolMessage，经 _to_openai_messages() 序列化后：
    1. role 字段为 "tool"
    2. content 字段存在
    3. tool_call_id 字段存在
    """
    result = OpenAICompatibleAdapter._to_openai_messages([msg])
    assert len(result) == 1

    serialized = result[0]
    assert serialized["role"] == "tool"
    assert "content" in serialized
    assert "tool_call_id" in serialized, (
        f"ToolMessage 序列化输出缺少 tool_call_id 字段，实际键: {set(serialized.keys())}"
    )


@settings(max_examples=100)
@given(msg=st.one_of(system_message_st(), user_message_st()))
def test_serialize_system_user_messages_only_role_content(msg: BaseMessage) -> None:
    """验证 SystemMessage 和 UserMessage 序列化输出仅包含 role 和 content 键。

    **Validates: Requirements 3.5**

    对于任意 SystemMessage 或 UserMessage，经 _to_openai_messages() 序列化后，
    输出字典应仅包含 role 和 content 两个键，不包含 tool_calls、tool_call_id 等扩展字段。
    """
    result = OpenAICompatibleAdapter._to_openai_messages([msg])
    assert len(result) == 1

    serialized = result[0]
    assert set(serialized.keys()) == {"role", "content"}, (
        f"SystemMessage/UserMessage 序列化应仅含 role 和 content，实际键: {set(serialized.keys())}"
    )


# ── Property 5: ConversationContext 含 Agent Loop 消息的往返一致性 ──
# Feature: agent-loop-function-calling, Property 5:
# ConversationContext 含 Agent Loop 消息的往返一致性


@st.composite
def mixed_message_list_st(draw: st.DrawFn) -> list[BaseMessage]:
    """生成包含各类消息类型的随机消息列表策略。

    生成的列表包含 SystemMessage、UserMessage、AssistantMessage（含随机 tool_calls）
    和 ToolMessage（含随机 tool_call_id）的混合消息，用于测试 ConversationContext
    的序列化往返一致性。

    Returns:
        包含 1-10 条随机消息的列表，消息类型从四种子类中随机选取
    """
    messages = draw(
        st.lists(
            st.one_of(
                system_message_st(),
                user_message_st(),
                assistant_message_st(),
                tool_message_st(),
            ),
            min_size=1,
            max_size=10,
        )
    )
    return messages


@settings(max_examples=100)
@given(messages=mixed_message_list_st())
def test_conversation_context_agent_loop_roundtrip(messages: list[BaseMessage]) -> None:
    """验证包含 Agent Loop 消息的 ConversationContext 序列化往返一致性。

    **Validates: Requirements 8.2, 8.4**

    对于任意包含 SystemMessage、UserMessage、AssistantMessage（含随机 tool_calls）
    和 ToolMessage（含随机 tool_call_id）的 ConversationContext 对象，
    执行 to_dict() → ConversationContext.from_dict() 后应产生与原始对象消息列表
    等价的 ConversationContext：
    1. 消息数量一致
    2. 每条消息的 role、content 一致
    3. AssistantMessage 的 tool_calls 列表长度一致，每个 ToolCallRequest 的 id、name、arguments 一致
    4. ToolMessage 的 tool_name、tool_call_id 一致
    """
    # 构建 ConversationContext 并直接设置消息列表
    ctx = ConversationContext()
    ctx._messages = list(messages)

    # 序列化 → 反序列化
    serialized = ctx.to_dict()
    restored = ConversationContext.from_dict(serialized)

    # 验证消息数量一致
    restored_messages = restored.get_messages()
    assert len(restored_messages) == len(messages), (
        f"消息数量不一致: 原始={len(messages)}, 恢复={len(restored_messages)}"
    )

    # 逐条验证消息
    for i, (orig, rest) in enumerate(zip(messages, restored_messages, strict=True)):
        # 验证 role 和 content
        assert rest.role == orig.role, (
            f"消息[{i}] role 不一致: 原始={orig.role!r}, 恢复={rest.role!r}"
        )
        assert rest.content == orig.content, (
            f"消息[{i}] content 不一致: 原始={orig.content!r}, 恢复={rest.content!r}"
        )

        # AssistantMessage: 验证 tool_calls
        if isinstance(orig, AssistantMessage):
            assert isinstance(rest, AssistantMessage), (
                f"消息[{i}] 应为 AssistantMessage，实际为 {type(rest).__name__}"
            )
            assert len(rest.tool_calls) == len(orig.tool_calls), (
                "消息[{i}] tool_calls 长度不一致: "
                f"原始={len(orig.tool_calls)}, 恢复={len(rest.tool_calls)}"
            )
            for j, (orig_tc, rest_tc) in enumerate(
                zip(orig.tool_calls, rest.tool_calls, strict=True)
            ):
                assert rest_tc.id == orig_tc.id, (
                    f"消息[{i}] tool_calls[{j}].id 不一致: 原始={orig_tc.id!r}, 恢复={rest_tc.id!r}"
                )
                assert rest_tc.name == orig_tc.name, (
                    f"消息[{i}] tool_calls[{j}].name 不一致: "
                    f"原始={orig_tc.name!r}, 恢复={rest_tc.name!r}"
                )
                assert rest_tc.arguments == orig_tc.arguments, (
                    f"消息[{i}] tool_calls[{j}].arguments 不一致: "
                    f"原始={orig_tc.arguments!r}, 恢复={rest_tc.arguments!r}"
                )

        # ToolMessage: 验证 tool_name 和 tool_call_id
        elif isinstance(orig, ToolMessage):
            assert isinstance(rest, ToolMessage), (
                f"消息[{i}] 应为 ToolMessage，实际为 {type(rest).__name__}"
            )
            assert rest.tool_name == orig.tool_name, (
                f"消息[{i}] tool_name 不一致: 原始={orig.tool_name!r}, 恢复={rest.tool_name!r}"
            )
            assert rest.tool_call_id == orig.tool_call_id, (
                f"消息[{i}] tool_call_id 不一致: "
                f"原始={orig.tool_call_id!r}, 恢复={rest.tool_call_id!r}"
            )
