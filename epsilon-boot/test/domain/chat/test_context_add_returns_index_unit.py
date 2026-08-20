"""ConversationContext.add_* 返回索引单元测试模块。

验证 v2 重构中 ``ConversationContext.add_assistant_message_with_tool_calls`` 与
``ConversationContext.add_tool_result`` 的返回类型由 ``None`` 升级为 ``int`` 后的
行为：

- 单次 ``add_assistant_message_with_tool_calls`` 在空容器上返回 0；
- 连续两次返回值为 0、1（严格单调递增 1）；
- 单次 ``add_tool_result`` 在已有 N 条消息时返回 ``N``（即 ``prev_count``）;
- 任意一次返回值都等于该次调用后的 ``message_count - 1``，与 Property 6 一致。

覆盖需求 4.1 / 4.2 / 4.8。
"""

from domain.chat.context import ConversationContext
from domain.model_access.value_objects import ToolCallRequest


def _make_tool_call(
    call_id: str = "call-1", name: str = "echo", arguments: str = "{}"
) -> ToolCallRequest:
    """构造一个用于测试的 ``ToolCallRequest``。"""
    return ToolCallRequest(id=call_id, name=name, arguments=arguments)


class TestAddAssistantMessageWithToolCallsReturnsIndex:
    """``add_assistant_message_with_tool_calls`` 返回索引语义。"""

    def test_first_call_returns_zero(self) -> None:
        """空容器上首次调用应返回 0,即 message_count - 1。"""
        ctx = ConversationContext()
        idx = ctx.add_assistant_message_with_tool_calls(
            content="",
            tool_calls=[_make_tool_call()],
        )
        assert idx == 0
        assert idx == ctx.message_count - 1

    def test_two_consecutive_calls_return_zero_then_one(self) -> None:
        """连续两次调用应分别返回 0 与 1,严格单调递增 1。"""
        ctx = ConversationContext()
        idx_a = ctx.add_assistant_message_with_tool_calls(
            content="第一次",
            tool_calls=[_make_tool_call("call-1")],
        )
        idx_b = ctx.add_assistant_message_with_tool_calls(
            content="第二次",
            tool_calls=[_make_tool_call("call-2")],
        )
        assert idx_a == 0
        assert idx_b == 1
        assert idx_b - idx_a == 1
        assert idx_b == ctx.message_count - 1

    def test_returned_index_locates_the_last_message(self) -> None:
        """返回的索引必须能直接命中刚追加的消息。"""
        ctx = ConversationContext()
        ctx.add_user_message("hi")
        ctx.add_user_message("there")
        idx = ctx.add_assistant_message_with_tool_calls(
            content="working",
            tool_calls=[_make_tool_call("call-x")],
        )
        assert idx == 2
        messages = ctx.get_messages()
        assert messages[idx].content == "working"


class TestAddToolResultReturnsIndex:
    """``add_tool_result`` 返回索引语义。"""

    def test_returns_prev_count(self) -> None:
        """已有 N 条消息时调用应返回 N,即 prev_count。"""
        ctx = ConversationContext()
        ctx.add_system_message("sys")
        ctx.add_user_message("hello")
        prev = ctx.message_count
        idx = ctx.add_tool_result(
            tool_name="echo",
            result="ok",
            tool_call_id="call-1",
        )
        assert idx == prev
        assert idx == ctx.message_count - 1

    def test_multiple_tool_results_increment_index(self) -> None:
        """多次 add_tool_result 索引应严格单调递增 1。"""
        ctx = ConversationContext()
        idx_a = ctx.add_tool_result(tool_name="a", result="r_a", tool_call_id="ta")
        idx_b = ctx.add_tool_result(tool_name="b", result="r_b", tool_call_id="tb")
        idx_c = ctx.add_tool_result(tool_name="c", result="r_c", tool_call_id="tc")
        assert (idx_a, idx_b, idx_c) == (0, 1, 2)
        assert idx_c == ctx.message_count - 1


class TestMixedAddCallsReturnIndices:
    """混合 ``add_assistant_message_with_tool_calls`` 与 ``add_tool_result`` 索引语义。"""

    def test_indices_match_message_count_after_each_call(self) -> None:
        """每次返回值都应等于该次调用后的 ``message_count - 1``。"""
        ctx = ConversationContext()
        idx0 = ctx.add_assistant_message_with_tool_calls(
            content="",
            tool_calls=[_make_tool_call("c-0")],
        )
        assert idx0 == ctx.message_count - 1
        idx1 = ctx.add_tool_result(tool_name="t1", result="ok", tool_call_id="c-0")
        assert idx1 == ctx.message_count - 1
        idx2 = ctx.add_assistant_message_with_tool_calls(
            content="",
            tool_calls=[_make_tool_call("c-1")],
        )
        assert idx2 == ctx.message_count - 1
        idx3 = ctx.add_tool_result(tool_name="t2", result="ok", tool_call_id="c-1")
        assert idx3 == ctx.message_count - 1
        assert (idx0, idx1, idx2, idx3) == (0, 1, 2, 3)
