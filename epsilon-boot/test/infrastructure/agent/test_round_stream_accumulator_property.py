"""``_RoundStreamAccumulator`` Hypothesis 属性测试模块。

覆盖 PR-2 任务 2.9 / Property 1：

* 任意分片切分方案下 ``content`` 等于所有 ``delta_content`` 顺序拼接；
* ``usage`` 等于 ``finished=True`` 分片携带的 ``usage``；
* ``tool_calls`` 与"等价 chat 一次返回"按 ``(id, name, arguments)`` 三元组逐一相等且顺序一致。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.model_access.value_objects import (
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.agent.round_stream_accumulator import _RoundStreamAccumulator


def _split_string(s: str, parts: int) -> list[str]:
    if not s:
        return [""] * max(parts, 1)
    parts = max(parts, 1)
    n = len(s)
    if parts >= n:
        return list(s) + [""] * (parts - n)
    step = n // parts
    out: list[str] = []
    for i in range(parts - 1):
        out.append(s[i * step : (i + 1) * step])
    out.append(s[(parts - 1) * step :])
    return out


def _build_chunks(
    content: str,
    tool_calls: list[tuple[str, str, str]],
    usage: dict[str, int],
    splits: list[int],
) -> list[StreamingChunk]:
    chunks: list[StreamingChunk] = []
    # text 分片
    for piece in _split_string(content, max(splits[0] if splits else 1, 1)):
        if piece:
            chunks.append(StreamingChunk(delta_content=piece))

    # tool_calls 增量分片：首片携带 id/name+空 arguments，后续按 splits 切 arguments
    for index, (tc_id, tc_name, tc_args) in enumerate(tool_calls):
        chunks.append(
            StreamingChunk(
                tool_calls=[
                    StreamingToolCallDelta(index=index, id=tc_id, name=tc_name, arguments_delta="")
                ]
            )
        )
        parts = max(splits[index + 1] if index + 1 < len(splits) else 1, 1)
        for piece in _split_string(tc_args, parts):
            chunks.append(
                StreamingChunk(
                    tool_calls=[StreamingToolCallDelta(index=index, arguments_delta=piece)]
                )
            )

    # 末尾 finished 分片携带 usage + 完整 tool_calls 列表（决策 11 优先覆盖）
    full_list: list[StreamingToolCallDelta] | None = None
    if tool_calls:
        full_list = [
            StreamingToolCallDelta(index=i, id=tc_id, name=tc_name, arguments_delta=tc_args)
            for i, (tc_id, tc_name, tc_args) in enumerate(tool_calls)
        ]
    chunks.append(
        StreamingChunk(
            finished=True,
            usage=dict(usage),
            tool_calls=full_list,
        )
    )
    return chunks


_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=8,
).map(lambda s: f"call_{s}")
_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L",)),
    min_size=1,
    max_size=8,
)
_args_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=20,
)
_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Z")),
    min_size=0,
    max_size=20,
)
_usage_strategy = st.fixed_dictionaries(
    {
        "prompt_tokens": st.integers(min_value=0, max_value=100),
        "completion_tokens": st.integers(min_value=0, max_value=100),
        "total_tokens": st.integers(min_value=0, max_value=200),
    }
)


async def _aiter(chunks: list[StreamingChunk]) -> AsyncIterator[StreamingChunk]:
    for c in chunks:
        yield c


@settings(max_examples=30, deadline=None)
@given(
    content=_content_strategy,
    tool_calls=st.lists(
        st.tuples(_id_strategy, _name_strategy, _args_strategy),
        min_size=0,
        max_size=2,
        unique_by=lambda t: t[0],
    ),
    usage=_usage_strategy,
    splits=st.lists(st.integers(min_value=1, max_value=4), min_size=4, max_size=4),
)
@pytest.mark.asyncio
async def test_accumulator_equivalence_to_chat_response(
    content: str,
    tool_calls: list[tuple[str, str, str]],
    usage: dict[str, int],
    splits: list[int],
) -> None:
    """累积器产出与等价 chat 返回值在四元组 ``(content, id, name, arguments)``
    + ``usage`` 上完全相等。"""
    acc = _RoundStreamAccumulator(model="m")
    chunks = _build_chunks(content, tool_calls, usage, splits)
    await acc.consume(_aiter(chunks))

    response = acc.build_response()

    assert response.content == content
    assert response.usage == usage
    assert len(response.tool_calls) == len(tool_calls)
    for tc, (tc_id, tc_name, tc_args) in zip(
        response.tool_calls, tool_calls, strict=True
    ):
        assert (tc.id, tc.name, tc.arguments) == (tc_id, tc_name, tc_args)
