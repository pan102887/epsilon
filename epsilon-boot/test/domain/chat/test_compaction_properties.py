"""上下文压缩策略属性测试模块。

使用 Hypothesis 对上下文压缩策略相关组件进行属性测试，验证在任意有效输入下，
核心不变量始终成立。包括 ConversationContext 的消息存储完整性、序列化往返一致性，
以及压缩适配器的行为正确性等属性。

测试文件对应设计文档中定义的正确性属性（Correctness Properties），
每个属性测试通过注释标注对应的设计属性编号和验证的需求编号。

策略已扩展以支持 Agent Loop 消息字段：AssistantMessage 可携带随机 tool_calls，
ToolMessage 携带随机 tool_call_id，确保 Property 2（消息完整性）和
Property 3（序列化往返一致性）自动覆盖这些新字段。
"""

import json
from typing import Any

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
from infrastructure.chat.sliding_window_compaction_adapter import SlidingWindowCompactionAdapter
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter

# ── Hypothesis 生成策略 ──

# 消息角色策略：覆盖所有合法角色
role_st = st.sampled_from(["system", "user", "assistant", "tool"])

# 消息内容策略：生成非空文本，长度适中以保证测试效率
content_st = st.text(min_size=1, max_size=200)

# 工具名称策略：生成非空文本作为工具名
tool_name_st = st.text(min_size=1, max_size=30)

# tool_call_id 策略：可为空字符串或非空标识符，覆盖默认值和正常值两种场景
tool_call_id_st = st.text(
    min_size=0, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P"))
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
def message_action_st(draw: st.DrawFn) -> tuple[str, str, str | None, str | None]:
    """生成随机消息动作的组合策略。

    返回一个元组 (role, content, tool_name, tool_call_id)，用于驱动 ConversationContext
    的消息添加方法。根据角色决定是否生成 tool_name 和 tool_call_id：
    仅 tool 角色携带工具名称和 tool_call_id。

    Returns:
        (role, content, tool_name, tool_call_id) 元组，
        其中 tool_name 和 tool_call_id 仅在 role 为 "tool" 时非 None
    """
    role = draw(role_st)
    content = draw(content_st)
    if role == "tool":
        tool_name = draw(tool_name_st)
        tool_call_id = draw(tool_call_id_st)
        return (role, content, tool_name, tool_call_id)
    return (role, content, None, None)


# ── Property 2: get_messages 返回完整的 Message 列表 ──
# Feature: context-compaction-strategy, Property 2: get_messages 返回完整的 Message 列表


def _add_message(
    ctx: ConversationContext,
    role: str,
    content: str,
    tool_name: str | None,
    tool_call_id: str | None = None,
) -> None:
    """根据角色调用 ConversationContext 对应的消息添加方法。

    Args:
        ctx: 对话上下文实例
        role: 消息角色，取值为 "system"、"user"、"assistant"、"tool"
        content: 消息内容
        tool_name: 工具名称，仅当 role 为 "tool" 时使用
        tool_call_id: 工具调用标识符，仅当 role 为 "tool" 时使用，默认为 None
    """
    if role == "system":
        ctx.add_system_message(content)
    elif role == "user":
        ctx.add_user_message(content)
    elif role == "assistant":
        ctx.add_assistant_message(content)
    elif role == "tool":
        assert tool_name is not None
        ctx.add_tool_result(tool_name, content, tool_call_id=tool_call_id or "")


@settings(max_examples=100)
@given(actions=st.lists(message_action_st(), min_size=0, max_size=50))
def test_property2_get_messages_returns_complete_message_list(
    actions: list[tuple[str, str, str | None, str | None]],
) -> None:
    """验证 get_messages() 返回完整的 Message 列表。

    **Validates: Requirements 2.1, 2.3**

    对于任意添加到 ConversationContext 的消息序列（包含任意数量的 system、user、
    assistant、tool 消息），get_messages() 应返回包含所有已添加消息的 list[Message]，
    长度等于添加的消息总数，且每个元素都是 Message 实例。
    ToolMessage 的 tool_call_id 字段也应与添加时一致。

    验证要点：
    1. 返回类型为 list
    2. 列表长度等于添加的消息总数
    3. 每个元素都是 Message 实例
    4. 每条消息的 role 和 content 与添加时一致
    5. ToolMessage 的 tool_name 和 tool_call_id 与添加时一致
    """
    ctx = ConversationContext()

    # 依次添加所有消息
    for role, content, tool_name, tool_call_id in actions:
        _add_message(ctx, role, content, tool_name, tool_call_id)

    # 获取消息列表
    result = ctx.get_messages()

    # 1. 返回类型为 list
    assert isinstance(result, list), f"get_messages() 应返回 list，实际返回 {type(result)}"

    # 2. 列表长度等于添加的消息总数
    assert len(result) == len(actions), (
        f"消息数量不一致: 添加了 {len(actions)} 条，get_messages() 返回 {len(result)} 条"
    )

    # 3 & 4 & 5. 每个元素都是 BaseMessage 实例，且 role/content 与添加时一致
    for i, ((role, content, tool_name, tool_call_id), msg) in enumerate(
        zip(actions, result, strict=True)
    ):
        assert isinstance(msg, BaseMessage), (
            f"第 {i} 条消息应为 BaseMessage 实例，实际为 {type(msg)}"
        )
        assert msg.role == role, f"第 {i} 条消息 role 不一致: 期望={role!r}, 实际={msg.role!r}"
        assert msg.content == content, (
            f"第 {i} 条消息 content 不一致: 期望={content!r}, 实际={msg.content!r}"
        )
        if role == "tool":
            assert isinstance(msg, ToolMessage)
            assert msg.tool_name == tool_name, (
                f"第 {i} 条消息 tool_name 不一致: 期望={tool_name!r}, 实际={msg.tool_name!r}"
            )
            expected_tool_call_id = tool_call_id or ""
            assert msg.tool_call_id == expected_tool_call_id, (
                f"第 {i} 条消息 tool_call_id 不一致: "
                f"期望={expected_tool_call_id!r}, 实际={msg.tool_call_id!r}"
            )


# ── Property 3: ConversationContext 序列化往返一致性 ──
# Feature: context-compaction-strategy, Property 3: ConversationContext 序列化往返一致性


@settings(max_examples=100)
@given(actions=st.lists(message_action_st(), min_size=0, max_size=50))
def test_property3_conversation_context_serialization_round_trip(
    actions: list[tuple[str, str, str | None, str | None]],
) -> None:
    """验证 ConversationContext 序列化往返一致性。

    **Validates: Requirements 2.4, 2.5, 2.6**

    对于任意有效的 ConversationContext 对象：
    1. 执行 to_dict() 后再 from_dict() 应产生与原始对象消息列表等价的 ConversationContext
    2. to_dict() 输出不包含 max_messages 键
    3. 对于包含 max_messages 字段的旧格式字典，from_dict() 也应正常工作并忽略该字段
    4. ToolMessage 的 tool_call_id 字段在往返后保持一致

    验证要点：
    - 往返后消息数量一致
    - 往返后每条消息的 role、content、tool_name、tool_call_id、metadata 一致
    - to_dict() 输出中不存在 max_messages 键
    - 旧格式字典（含 max_messages）也能正常反序列化
    """
    # 构建原始 ConversationContext
    ctx = ConversationContext()
    for role, content, tool_name, tool_call_id in actions:
        _add_message(ctx, role, content, tool_name, tool_call_id)

    original_messages = ctx.get_messages()

    # ── 验证 to_dict() → from_dict() 往返一致性 ──
    serialized = ctx.to_dict()

    # to_dict() 输出不包含 max_messages 键
    assert "max_messages" not in serialized, "to_dict() 输出不应包含 max_messages 键"

    restored_ctx = ConversationContext.from_dict(serialized)
    restored_messages = restored_ctx.get_messages()

    # 往返后消息数量一致
    assert len(restored_messages) == len(original_messages), (
        f"往返后消息数量不一致: 原始={len(original_messages)}, 恢复={len(restored_messages)}"
    )

    # 往返后每条消息的字段一致
    for i, (orig, restored) in enumerate(
        zip(original_messages, restored_messages, strict=True)
    ):
        assert orig.role == restored.role, (
            f"第 {i} 条消息 role 不一致: 原始={orig.role!r}, 恢复={restored.role!r}"
        )
        assert orig.content == restored.content, (
            f"第 {i} 条消息 content 不一致: 原始={orig.content!r}, 恢复={restored.content!r}"
        )
        if isinstance(orig, ToolMessage):
            assert isinstance(restored, ToolMessage)
            assert orig.tool_name == restored.tool_name, (
                f"第 {i} 条消息 tool_name 不一致: "
                f"原始={orig.tool_name!r}, 恢复={restored.tool_name!r}"
            )
            assert orig.tool_call_id == restored.tool_call_id, (
                f"第 {i} 条消息 tool_call_id 不一致: "
                f"原始={orig.tool_call_id!r}, 恢复={restored.tool_call_id!r}"
            )
        assert orig.metadata == restored.metadata, (
            f"第 {i} 条消息 metadata 不一致: 原始={orig.metadata!r}, 恢复={restored.metadata!r}"
        )

    # ── 验证旧格式字典（含 max_messages）也能正常 from_dict() ──
    legacy_dict = dict(serialized)
    legacy_dict["max_messages"] = 50

    legacy_ctx = ConversationContext.from_dict(legacy_dict)
    legacy_messages = legacy_ctx.get_messages()

    # 旧格式反序列化后消息数量一致
    assert len(legacy_messages) == len(original_messages), (
        "旧格式反序列化后消息数量不一致: "
        f"原始={len(original_messages)}, 旧格式={len(legacy_messages)}"
    )

    # 旧格式反序列化后每条消息的字段一致
    for i, (orig, legacy) in enumerate(zip(original_messages, legacy_messages, strict=True)):
        assert orig.role == legacy.role, (
            f"旧格式第 {i} 条消息 role 不一致: 原始={orig.role!r}, 旧格式={legacy.role!r}"
        )
        assert orig.content == legacy.content, (
            f"旧格式第 {i} 条消息 content 不一致: 原始={orig.content!r}, 旧格式={legacy.content!r}"
        )
        if isinstance(orig, ToolMessage):
            assert isinstance(legacy, ToolMessage)
            assert orig.tool_name == legacy.tool_name, (
                f"旧格式第 {i} 条消息 tool_name 不一致: "
                f"原始={orig.tool_name!r}, 旧格式={legacy.tool_name!r}"
            )
            assert orig.tool_call_id == legacy.tool_call_id, (
                f"旧格式第 {i} 条消息 tool_call_id 不一致: "
                f"原始={orig.tool_call_id!r}, 旧格式={legacy.tool_call_id!r}"
            )
        assert orig.metadata == legacy.metadata, (
            f"旧格式第 {i} 条消息 metadata 不一致: "
            f"原始={orig.metadata!r}, 旧格式={legacy.metadata!r}"
        )


# ── 消息对象直接生成策略（用于 Property 1） ──

# 消息元数据策略：生成简单的字符串键值对字典
metadata_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=10),
    values=st.text(min_size=0, max_size=50),
    max_size=3,
)


@st.composite
def message_st(draw: st.DrawFn) -> BaseMessage:
    """生成随机 BaseMessage 子类实例的组合策略。

    根据角色创建对应的具体子类实例，覆盖所有合法角色和可选字段。
    AssistantMessage 可选携带随机 tool_calls（覆盖空列表和非空列表两种场景），
    ToolMessage 携带随机 tool_call_id（覆盖空字符串和非空标识符两种场景）。

    Returns:
        随机生成的 BaseMessage 子类实例
    """
    role = draw(role_st)
    content = draw(content_st)
    metadata = draw(metadata_st)
    if role == "system":
        return SystemMessage(content=content, metadata=metadata)
    elif role == "user":
        return UserMessage(content=content, metadata=metadata)
    elif role == "assistant":
        tool_calls = draw(st.lists(tool_call_request_st(), min_size=0, max_size=3))
        return AssistantMessage(content=content, metadata=metadata, tool_calls=tool_calls)
    else:
        tool_name = draw(tool_name_st)
        tool_call_id = draw(tool_call_id_st)
        return ToolMessage(
            content=content, tool_name=tool_name, tool_call_id=tool_call_id, metadata=metadata
        )


# max_messages 策略：正整数，范围 1~200
max_messages_st = st.integers(min_value=1, max_value=200)


# ── Property 1: compact 输出是输入的子集 ──
# Feature: context-compaction-strategy, Property 1: compact 输出是输入的子集


@settings(max_examples=100)
@given(
    messages=st.lists(message_st(), min_size=0, max_size=50),
    max_messages=max_messages_st,
)
def test_property1_compact_output_is_subset_of_input(
    messages: list[BaseMessage],
    max_messages: int,
) -> None:
    """验证 compact 输出是输入的子集。

    **Validates: Requirements 1.3**

    对于任意消息列表和任意 SlidingWindowCompactionAdapter 实例，
    compact(messages) 返回的每个 Message 对象都应在输入列表中存在
    引用相同的对象（通过 is 判断），且返回列表长度不超过输入列表长度。

    验证要点：
    1. 输出列表长度 ≤ 输入列表长度
    2. 输出中的每个 Message 对象在输入中存在引用相同的对象（identity check）
    """
    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    result = adapter.compact_messages(messages)

    # 1. 输出长度 ≤ 输入长度
    assert len(result) <= len(messages), (
        f"compact 输出长度 ({len(result)}) 超过输入长度 ({len(messages)})"
    )

    # 2. 输出中每个 Message 在输入中存在引用相同的对象
    for i, msg in enumerate(result):
        found = any(msg is input_msg for input_msg in messages)
        assert found, (
            f"compact 输出第 {i} 条消息 (role={msg.role!r}, content={msg.content!r}) "
            f"在输入列表中不存在引用相同的对象"
        )


# ── Property 4: 滑动窗口压缩保留所有 system 消息并裁剪非 system 消息 ──
# Feature: context-compaction-strategy, Property 4:
# 滑动窗口压缩保留所有 system 消息并裁剪非 system 消息


@settings(max_examples=100)
@given(
    messages=st.lists(message_st(), min_size=0, max_size=50),
    max_messages=max_messages_st,
)
def test_property4_sliding_window_preserves_system_and_trims_non_system(
    messages: list[BaseMessage],
    max_messages: int,
) -> None:
    """验证滑动窗口压缩保留所有 system 消息并裁剪非 system 消息。

    **Validates: Requirements 3.3, 3.4, 3.5**

    配对保护改造后，ToolMessage 无对应 assistant 配对时会被丢弃，
    因此断言改为：
    (a) 所有 system 消息保留
    (b) 非 system 消息数 ≤ max_messages
    (c) 输出中每条 ToolMessage 都有对应 assistant 配对（Property 8）
    (d) system 在前、非 system 在后
    """
    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    result = adapter.compact_messages(messages)

    input_system = [m for m in messages if m.role == "system"]
    output_system = [m for m in result if m.role == "system"]
    output_non_system = [m for m in result if m.role != "system"]

    # (a) 所有 system 消息保留
    assert len(output_system) == len(input_system)
    for inp, out in zip(input_system, output_system, strict=True):
        assert inp is out

    # (b) 非 system 消息数 ≤ max_messages
    assert len(output_non_system) <= max_messages

    # (c) 配对保护：输出中每条 ToolMessage 都有对应 assistant 配对
    assistant_tc_ids: set[str] = set()
    for m in result:
        if isinstance(m, AssistantMessage) and m.tool_calls:
            for tc in m.tool_calls:
                assistant_tc_ids.add(tc.id)
    for m in result:
        if isinstance(m, ToolMessage) and m.tool_call_id:
            assert m.tool_call_id in assistant_tc_ids, (
                f"ToolMessage {m.tool_call_id} has no matching assistant"
            )

    # (d) system 在前、非 system 在后
    if output_system and output_non_system:
        last_system_idx = -1
        first_non_system_idx = len(result)
        for idx, m in enumerate(result):
            if m.role == "system":
                last_system_idx = idx
            elif first_non_system_idx == len(result):
                first_non_system_idx = idx
        assert last_system_idx < first_non_system_idx, (
            f"system 消息未全部在非 system 消息之前: "
            f"最后一条 system 索引={last_system_idx}, "
            f"第一条非 system 索引={first_non_system_idx}"
        )


# ── Property 5: Message 序列化为模型调用格式 ──
# Feature: context-compaction-strategy, Property 5: Message 序列化为模型调用格式


@settings(max_examples=100)
@given(msg=message_st())
def test_property5_message_serialization_to_model_call_format(
    msg: BaseMessage,
) -> None:
    """验证 Message 序列化为模型调用格式的正确性。

    **Validates: Requirements 6.1, 6.2, 6.3**

    对于任意有效的 Message 对象，将其序列化为模型调用格式后，
    结果字典应恰好包含 role 和 content 两个键，且值分别等于
    Message.role 和 Message.content。

    模型调用格式序列化仅提取 role 和 content 字段，不包含 tool_name 和 metadata，
    确保与 ModelAccessPort 所需的 dict[str, str] 格式一致。

    验证要点：
    1. 序列化结果恰好包含 2 个键
    2. 包含 "role" 键
    3. 包含 "content" 键
    4. "role" 值等于 msg.role
    5. "content" 值等于 msg.content
    """
    # 模型调用格式序列化：仅提取 role 和 content
    serialized = {"role": msg.role, "content": msg.content}

    # 1. 恰好包含 2 个键
    assert len(serialized) == 2, (
        f"序列化结果应恰好包含 2 个键，实际包含 {len(serialized)} 个键: {list(serialized.keys())}"
    )

    # 2 & 3. 包含 "role" 和 "content" 键
    assert "role" in serialized, "序列化结果应包含 'role' 键"
    assert "content" in serialized, "序列化结果应包含 'content' 键"

    # 4 & 5. 值与原始 Message 属性一致
    assert serialized["role"] == msg.role, (
        f"序列化 role 不一致: 期望={msg.role!r}, 实际={serialized['role']!r}"
    )
    assert serialized["content"] == msg.content, (
        f"序列化 content 不一致: 期望={msg.content!r}, 实际={serialized['content']!r}"
    )


# ── Property 6: 重构后行为等价性 ──
# Feature: context-compaction-strategy, Property 6: 重构后行为等价性


def _old_flow(messages: list[BaseMessage], max_messages: int) -> list[dict[str, str]]:
    """模拟重构前 ConversationContext.get_messages() 的旧流程。

    旧流程逻辑：
    1. 将消息分为 system 消息和非 system 消息
    2. 若非 system 消息数量超过 max_messages，仅保留最后 max_messages 条
    3. 合并 system 消息（在前）和裁剪后的非 system 消息（在后）
    4. 序列化为 list[dict]，格式与 _to_openai_messages() 一致：
       - AssistantMessage 携带 tool_calls 时输出 OpenAI 嵌套格式
       - ToolMessage 输出 tool_call_id
       - 其他消息仅输出 role 和 content

    Args:
        messages: 完整的消息列表
        max_messages: 非 system 消息的最大保留数量

    Returns:
        序列化后的字典列表，模拟旧流程的输出
    """
    system_msgs = [m for m in messages if m.role == "system"]
    non_system_msgs = [m for m in messages if m.role != "system"]
    if len(non_system_msgs) > max_messages:
        non_system_msgs = non_system_msgs[-max_messages:]
    combined = system_msgs + non_system_msgs
    result: list[dict[str, Any]] = []
    for m in combined:
        if isinstance(m, AssistantMessage) and m.tool_calls:
            result.append(
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif isinstance(m, ToolMessage):
            result.append(
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                }
            )
        else:
            result.append({"role": m.role, "content": m.content})
    return result


def _new_flow(messages: list[BaseMessage], max_messages: int) -> list[dict[str, Any]]:
    """执行重构后的新流程：压缩 → 序列化。

    新流程逻辑：
    1. 使用 SlidingWindowCompactionAdapter 进行滑动窗口压缩
    2. 使用 OpenAICompatibleAdapter._to_openai_messages 将压缩后的消息序列化为字典列表

    Args:
        messages: 完整的消息列表
        max_messages: 非 system 消息的最大保留数量

    Returns:
        序列化后的字典列表，新流程的输出
    """
    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    compacted = adapter.compact_messages(messages)
    return OpenAICompatibleAdapter.to_openai_messages(compacted)


@settings(max_examples=100)
@given(
    messages=st.lists(message_st(), min_size=0, max_size=50),
    max_messages=max_messages_st,
)
def test_property6_refactored_behavior_equivalence(
    messages: list[BaseMessage],
    max_messages: int,
) -> None:
    """验证重构后行为等价性：无 ToolMessage 时新旧流程输出完全一致。

    **Validates: Requirements 7.1**

    配对保护改造后，含 ToolMessage 时行为可能不同（孤儿 ToolMessage 被丢弃），
    因此仅在无 ToolMessage 时断言与旧流程完全等价。
    含 ToolMessage 时验证弱不变量：system 全保留 + 非 system ≤ max_messages。
    """
    has_tool_messages = any(isinstance(m, ToolMessage) for m in messages)

    if not has_tool_messages:
        old_result = _old_flow(messages, max_messages)
        new_result = _new_flow(messages, max_messages)
        assert len(new_result) == len(old_result), (
            f"新旧流程输出长度不一致: 旧流程={len(old_result)}, 新流程={len(new_result)}"
        )
        for i, (old_dict, new_dict) in enumerate(zip(old_result, new_result, strict=True)):
            assert old_dict == new_dict, (
                f"第 {i} 条消息新旧流程输出不一致: 旧流程={old_dict!r}, 新流程={new_dict!r}"
            )
    else:
        adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
        result = adapter.compact_messages(messages)
        output_system = [m for m in result if m.role == "system"]
        output_non_system = [m for m in result if m.role != "system"]
        input_system = [m for m in messages if m.role == "system"]
        assert len(output_system) == len(input_system)
        assert len(output_non_system) <= max_messages


# ── Property 6 (agent-loop-function-calling): 滑动窗口压缩将 ToolMessage 视为非 system 消息 ──
# Feature: agent-loop-function-calling, Property 6: 滑动窗口压缩将 ToolMessage 视为非 system 消息


@settings(max_examples=100)
@given(
    messages=st.lists(message_st(), min_size=0, max_size=50),
    max_messages=max_messages_st,
)
def test_property6_tool_message_treated_as_non_system_in_sliding_window(
    messages: list[BaseMessage],
    max_messages: int,
) -> None:
    """验证滑动窗口压缩将 ToolMessage 视为非 system 消息。

    **Validates: Requirements 8.3**

    配对保护改造后，ToolMessage 仍视为非 system（不与 system 一起保留），
    但孤儿 ToolMessage 会被丢弃。验证：
    (a) ToolMessage 不在 system 组中
    (b) 非 system 消息数 ≤ max_messages
    (c) 输出中每条 ToolMessage 都有对应 assistant 配对（配对保护不变量）
    """
    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    result = adapter.compact_messages(messages)

    output_system = [m for m in result if m.role == "system"]
    output_non_system = [m for m in result if m.role != "system"]

    # (a) ToolMessage 不在 system 组中
    for m in output_system:
        assert not isinstance(m, ToolMessage)

    # (b) 非 system 消息数 ≤ max_messages
    assert len(output_non_system) <= max_messages

    # (c) 配对保护：每条 ToolMessage 都有对应 assistant 配对
    assistant_tc_ids: set[str] = set()
    for m in result:
        if isinstance(m, AssistantMessage) and m.tool_calls:
            for tc in m.tool_calls:
                assistant_tc_ids.add(tc.id)
    for m in result:
        if isinstance(m, ToolMessage) and m.tool_call_id:
            assert m.tool_call_id in assistant_tc_ids

    # 额外验证：输出中的 ToolMessage 数量不超过输入中的 ToolMessage 数量
    # （ToolMessage 可能因滑动窗口裁剪而减少，但不会增加）
    input_tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))
    output_tool_count = sum(1 for m in result if isinstance(m, ToolMessage))
    assert output_tool_count <= input_tool_count, (
        f"输出中 ToolMessage 数量 ({output_tool_count}) 不应超过输入中的数量 ({input_tool_count})"
    )
