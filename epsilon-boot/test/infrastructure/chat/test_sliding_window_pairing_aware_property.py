"""SlidingWindowCompactionAdapter 配对保护 property-based 测试。"""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from domain.chat.context import AssistantMessage, BaseMessage, ToolMessage, UserMessage
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)


def _tool_pair_group_strategy() -> st.SearchStrategy[list[BaseMessage]]:
    """生成 Tool_Pair_Group：一条 assistant(tool_calls) + 对应 ToolMessages。"""

    @st.composite
    def strategy(draw: st.DrawFn) -> list[BaseMessage]:
        n_tools = draw(st.integers(min_value=1, max_value=4))
        tool_call_ids = [f"tc-{draw(st.uuids())}" for _ in range(n_tools)]
        tool_calls = [
            ToolCallRequest(id=tc_id, name=f"tool_{i}", arguments="{}")
            for i, tc_id in enumerate(tool_call_ids)
        ]
        assistant = AssistantMessage(content="", tool_calls=tool_calls)
        tool_msgs = [
            ToolMessage(content="ok", tool_name=f"tool_{i}", tool_call_id=tc_id)
            for i, tc_id in enumerate(tool_call_ids)
        ]
        return [assistant, *tool_msgs]

    return strategy()


@st.composite
def messages_with_tool_groups(draw: st.DrawFn) -> list[BaseMessage]:
    """生成包含若干 tool_pair_group 和 plain 消息的混合序列。"""
    n_groups = draw(st.integers(min_value=0, max_value=4))
    n_plain = draw(st.integers(min_value=0, max_value=5))

    messages: list[BaseMessage] = []
    for _ in range(n_groups):
        group = draw(_tool_pair_group_strategy())
        messages.extend(group)
        if draw(st.booleans()):
            messages.append(UserMessage(content="q"))

    for _ in range(n_plain):
        if draw(st.booleans()):
            messages.append(UserMessage(content="plain"))
        else:
            messages.append(AssistantMessage(content="plain-reply"))

    return messages


@given(
    messages=messages_with_tool_groups(),
    max_messages=st.integers(min_value=1, max_value=30),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_each_tool_message_has_assistant(
    messages: list[BaseMessage], max_messages: int
) -> None:
    """Property 8: 输出中每条 ToolMessage 都有对应的 assistant。"""
    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    result = adapter.compact_messages(messages)

    assistant_tc_ids: set[str] = set()
    for m in result:
        if isinstance(m, AssistantMessage) and m.tool_calls:
            for tc in m.tool_calls:
                assistant_tc_ids.add(tc.id)

    for m in result:
        if isinstance(m, ToolMessage):
            assert m.tool_call_id in assistant_tc_ids, (
                f"ToolMessage {m.tool_call_id} has no matching assistant"
            )


@given(
    messages=messages_with_tool_groups(),
    max_messages=st.integers(min_value=1, max_value=30),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_each_assistant_tool_calls_fully_covered(
    messages: list[BaseMessage], max_messages: int
) -> None:
    """Property 9: 输出中每条 assistant 的 tool_calls 全集都能找到 ToolMessage。"""
    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    result = adapter.compact_messages(messages)

    tool_msg_ids: set[str] = set()
    for m in result:
        if isinstance(m, ToolMessage):
            tool_msg_ids.add(m.tool_call_id)

    for m in result:
        if isinstance(m, AssistantMessage) and m.tool_calls:
            for tc in m.tool_calls:
                assert tc.id in tool_msg_ids, (
                    f"AssistantMessage tool_call {tc.id} has no matching ToolMessage"
                )


@given(
    messages=messages_with_tool_groups(),
    max_messages=st.integers(min_value=1, max_value=30),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_system_messages_fully_preserved(
    messages: list[BaseMessage], max_messages: int
) -> None:
    """Property 10: system 消息全保留。"""
    from domain.chat.context import SystemMessage

    sys_msg = SystemMessage(content="system-prompt")
    all_messages: list[BaseMessage] = [sys_msg, *messages]

    adapter = SlidingWindowCompactionAdapter(max_messages=max_messages)
    result = adapter.compact_messages(all_messages)

    system_in_result = [m for m in result if m.role == "system"]
    assert len(system_in_result) == 1
    assert system_in_result[0].content == "system-prompt"
