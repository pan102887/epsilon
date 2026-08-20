"""上下文构建 usage 合并属性测试。"""

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.value_objects import AgentResult
from domain.chat.context import UserMessage
from domain.chat.value_objects import ChatResponseVO, ContextBuilderResult
from domain.model_access.value_objects import StreamingChunk
from infrastructure.chat.usage import merge_usage

_usage_key = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=24,
)
_usage_dict = st.dictionaries(
    keys=_usage_key,
    values=st.integers(min_value=0, max_value=1_000_000),
    max_size=12,
)


def _sum_usage_by_key(*usages: dict[str, int]) -> dict[str, int]:
    """按现有 merge_usage 语义逐 key 累加 usage。"""
    expected: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            expected[key] = expected.get(key, 0) + value
    return expected


@settings(max_examples=100)
@given(builder_usage=_usage_dict, main_usage=_usage_dict)
def test_merge_usage_builder_and_main_usage_equals_keywise_sum(
    builder_usage: dict[str, int],
    main_usage: dict[str, int],
) -> None:
    """Property 5：builder usage 与主模型 usage 按缺失 key 为 0 累加。"""
    builder_result = ContextBuilderResult(
        messages=[UserMessage(content="x")],
        usage=builder_usage,
    )

    merged = merge_usage(builder_result.usage, main_usage)

    assert builder_result.usage == builder_usage
    assert merged == _sum_usage_by_key(builder_usage, main_usage)


@settings(max_examples=100)
@given(builder_usage=_usage_dict, main_usage=_usage_dict)
def test_merged_context_builder_usage_can_feed_later_entry_results(
    builder_usage: dict[str, int],
    main_usage: dict[str, int],
) -> None:
    """合并后的 usage 可被后续 Chat、流式和 Agent 入口测试复用。"""
    builder_result = ContextBuilderResult(
        messages=[UserMessage(content="x")],
        usage=builder_usage,
    )
    merged_usage = merge_usage(builder_result.usage, main_usage)
    expected_usage = _sum_usage_by_key(builder_usage, main_usage)

    chat_response = ChatResponseVO(
        session_id="s1",
        reply="x",
        model="test-model",
        usage=merged_usage,
        prompt_id="chat-default@v1",
    )
    streaming_chunk = StreamingChunk(finished=True, usage=merged_usage)
    agent_result = AgentResult(content="x", model="test-model", usage=merged_usage)

    assert chat_response.usage == expected_usage
    assert streaming_chunk.usage == expected_usage
    assert agent_result.usage == expected_usage
