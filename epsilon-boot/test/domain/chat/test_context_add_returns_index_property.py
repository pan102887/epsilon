"""ConversationContext.add_* 返回索引性质属性测试模块。

对随机生成的 0~50 次混合调用 ``add_assistant_message_with_tool_calls`` 与
``add_tool_result``，验证：

- 所有返回值 ≥ 0;
- 严格单调递增 1（每次返回值比上一次大 1）;
- 每次返回值等于该次调用后的 ``message_count - 1``。

覆盖需求 4.1 / 4.2 与 Property 6。
"""

import itertools

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.chat.context import ConversationContext
from domain.model_access.value_objects import ToolCallRequest

# 操作种类：追加 assistant_with_tool_calls 或 tool_result
op_st = st.sampled_from(["assistant_with_tool_calls", "tool_result"])

# 操作序列长度: 0 ~ 50, 覆盖空序列和较长序列
ops_st = st.lists(op_st, min_size=0, max_size=50)


@settings(max_examples=80, deadline=None)
@given(ops=ops_st)
def test_returned_indices_strictly_monotonic_match_message_count(ops: list[str]) -> None:
    """任意混合操作序列下,返回值序列严格单调递增 1 且等于每次调用后的 message_count - 1。"""
    ctx = ConversationContext()
    returned: list[int] = []

    for i, op in enumerate(ops):
        if op == "assistant_with_tool_calls":
            tc = ToolCallRequest(id=f"call-{i}", name="echo", arguments="{}")
            idx = ctx.add_assistant_message_with_tool_calls(content="", tool_calls=[tc])
        else:
            idx = ctx.add_tool_result(tool_name="echo", result="ok", tool_call_id=f"call-{i}")
        returned.append(idx)
        # 单次调用不变量: 返回值 == message_count - 1
        assert idx == ctx.message_count - 1
        # 单次调用不变量: 返回值 >= 0
        assert idx >= 0

    # 整个序列不变量: 严格单调递增 1
    for prev, cur in itertools.pairwise(returned):
        assert cur - prev == 1
    # 整个序列不变量: 长度匹配
    assert len(returned) == len(ops)
