"""ConversationContext.add_assistant_message_with_tool_calls 公开 API 单元测试模块。

验证 ``ConversationContext`` 新增的 ``add_assistant_message_with_tool_calls`` 公开方法
的行为：

- 追加携带 ``tool_calls`` 的 ``AssistantMessage`` 后，消息列表末尾结构正确；
- 多次调用是追加而非覆盖，消息列表按调用顺序累积；
- 当 ``tool_calls=[]`` 时，退化为"纯文本助手消息"语义，等价于
  ``add_assistant_message(content)`` 的消息形态；
- 内部对 ``tool_calls`` 进行拷贝，外部修改入参列表不影响已追加的消息。

覆盖需求 2.1 / 2.2 / 2.3。
"""

from domain.chat.context import (
    AssistantMessage,
    ConversationContext,
)
from domain.model_access.value_objects import ToolCallRequest


def _make_tool_call(call_id: str, name: str = "echo", arguments: str = "{}") -> ToolCallRequest:
    """构造一个用于测试的 ``ToolCallRequest`` 工具调用请求。"""
    return ToolCallRequest(id=call_id, name=name, arguments=arguments)


class TestAddAssistantMessageWithToolCalls:
    """验证 ``add_assistant_message_with_tool_calls`` 的核心行为。"""

    def test_appends_assistant_message_with_tool_calls_to_tail(self) -> None:
        """追加后的最后一条消息应为携带相同 ``tool_calls`` 的 ``AssistantMessage``。"""
        context = ConversationContext()
        tc1 = _make_tool_call("call-1", name="search")
        tc2 = _make_tool_call("call-2", name="calc", arguments='{"x": 1}')

        context.add_assistant_message_with_tool_calls(
            content="我需要先查询再计算",
            tool_calls=[tc1, tc2],
        )

        messages = context.get_messages()
        assert context.message_count == 1
        assert len(messages) == 1
        last = messages[-1]
        assert isinstance(last, AssistantMessage)
        assert last.role == "assistant"
        assert last.content == "我需要先查询再计算"
        assert last.tool_calls == [tc1, tc2]

    def test_multiple_calls_accumulate_without_overwrite(self) -> None:
        """连续多次调用应按顺序追加，互不覆盖。"""
        context = ConversationContext()
        tc1 = _make_tool_call("call-1")
        tc2 = _make_tool_call("call-2")

        context.add_assistant_message_with_tool_calls(content="第一轮", tool_calls=[tc1])
        context.add_assistant_message_with_tool_calls(content="第二轮", tool_calls=[tc2])

        messages = context.get_messages()
        assert context.message_count == 2
        assert isinstance(messages[0], AssistantMessage)
        assert messages[0].content == "第一轮"
        assert messages[0].tool_calls == [tc1]
        assert isinstance(messages[1], AssistantMessage)
        assert messages[1].content == "第二轮"
        assert messages[1].tool_calls == [tc2]

    def test_empty_tool_calls_degrades_to_plain_assistant_message(self) -> None:
        """传入空列表时退化为纯文本助手消息语义。

        消息内容与 ``add_assistant_message(content)`` 的产物在 role / content /
        tool_calls 三个维度上保持一致：role=assistant，content 不变，
        tool_calls 为空列表（dataclass 默认值）。
        """
        context = ConversationContext()

        context.add_assistant_message_with_tool_calls(content="纯文本回复", tool_calls=[])

        messages = context.get_messages()
        assert context.message_count == 1
        last = messages[-1]
        assert isinstance(last, AssistantMessage)
        assert last.role == "assistant"
        assert last.content == "纯文本回复"
        assert last.tool_calls == []

    def test_external_tool_calls_mutation_does_not_affect_appended_message(self) -> None:
        """外部修改入参列表不应影响已追加消息中的 ``tool_calls``。

        方法内部应通过 ``list(tool_calls)`` 拷贝一次入参，避免调用方后续对
        原列表的 mutation 反作用到已记录的 ``AssistantMessage``。
        """
        context = ConversationContext()
        tc1 = _make_tool_call("call-1")
        tc2 = _make_tool_call("call-2")
        external_list = [tc1]

        context.add_assistant_message_with_tool_calls(content="一次调用", tool_calls=external_list)
        external_list.append(tc2)

        messages = context.get_messages()
        last = messages[-1]
        assert isinstance(last, AssistantMessage)
        assert last.tool_calls == [tc1]

    def test_serialization_includes_tool_calls(self) -> None:
        """追加消息后再 ``to_dict`` 序列化应保留 ``tool_calls`` 字段。

        与 ``AssistantMessage.to_dict`` 既有约定一致：``tool_calls`` 非空时
        以 ``[{id, name, arguments}, ...]`` 形态出现。
        """
        context = ConversationContext()
        tc1 = _make_tool_call("call-1", name="search", arguments='{"q": "hi"}')

        context.add_assistant_message_with_tool_calls(content="", tool_calls=[tc1])

        serialized = context.to_dict()
        messages = serialized["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == ""
        assert messages[0]["tool_calls"] == [
            {"id": "call-1", "name": "search", "arguments": '{"q": "hi"}'}
        ]
