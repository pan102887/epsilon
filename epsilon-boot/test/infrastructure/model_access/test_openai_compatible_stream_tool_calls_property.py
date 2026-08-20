"""``OpenAICompatibleAdapter.stream`` 工具调用透传 — Hypothesis 属性测试。

覆盖 PR-1 任务 1.8 / Property 3：

* 中间分片 ``arguments_delta`` 顺序拼接 = 完整 ``arguments``；
* ``finished=True`` 分片携带的累积完整列表与原始工具调用列表按
  ``(id, name, arguments)`` 三元组逐一相等且顺序一致。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.chat.context import UserMessage
from domain.model_access.value_objects import ChatRequest, StreamingToolCallDelta
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter() -> OpenAICompatibleAdapter:
    cfg = MagicMock()
    cfg.api_key = "k"
    cfg.api_base = "https://fake/v1"
    cfg.timeout = 30
    cfg.max_retries = 0
    cfg.max_connections = 10
    cfg.max_keepalive_connections = 5
    cfg.provider_name = "test"
    cfg.default_model = "m"
    cfg.temperature = 0.7
    cfg.max_tokens = 4096
    return OpenAICompatibleAdapter(cfg)


class _MockAsyncStream:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[SimpleNamespace]:
        for c in self._chunks:
            yield c


def _split_string(s: str, parts: int) -> list[str]:
    """把字符串均匀切成 ``parts`` 段（最后一段吸收余数）。"""
    if not s:
        return [""] * parts
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
    tool_calls: list[tuple[str, str, str]],  # (id, name, arguments)
    splits: list[int],
) -> list[SimpleNamespace]:
    """构造模拟 SDK 分片序列：每个工具调用首片携带 id/name，后续按 splits 切分 arguments。"""
    sdk_chunks: list[SimpleNamespace] = []
    # 首先逐个工具调用：发首片 (id+name+空 args)
    for index, (tc_id, tc_name, _) in enumerate(tool_calls):
        delta = SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    index=index,
                    id=tc_id,
                    function=SimpleNamespace(name=tc_name, arguments=""),
                )
            ],
        )
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        sdk_chunks.append(SimpleNamespace(choices=[choice], usage=None))

    # 然后逐工具调用按 splits 输出 arguments 增量
    for index, (_, _, tc_args) in enumerate(tool_calls):
        parts = max(splits[index] if index < len(splits) else 1, 1)
        for piece in _split_string(tc_args, parts):
            delta = SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        index=index,
                        id=None,
                        function=SimpleNamespace(name=None, arguments=piece),
                    )
                ],
            )
            choice = SimpleNamespace(delta=delta, finish_reason=None)
            sdk_chunks.append(SimpleNamespace(choices=[choice], usage=None))

    # 终止分片：finish_reason="tool_calls"
    sdk_chunks.append(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, tool_calls=None), finish_reason="tool_calls"
                )
            ],
            usage=None,
        )
    )
    return sdk_chunks


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
    min_size=0,
    max_size=30,
)


@settings(max_examples=30, deadline=None)
@given(
    tool_calls=st.lists(
        st.tuples(_id_strategy, _name_strategy, _args_strategy),
        min_size=1,
        max_size=3,
        unique_by=lambda t: t[0],
    ),
    splits=st.lists(st.integers(min_value=1, max_value=5), min_size=3, max_size=3),
)
@pytest.mark.asyncio
async def test_arguments_delta_concat_equals_full_arguments(
    tool_calls: list[tuple[str, str, str]],
    splits: list[int],
) -> None:
    """中间分片 ``arguments_delta`` 顺序拼接 = 完整 arguments；最终分片携带完整列表。"""
    adapter = _make_adapter()
    sdk_chunks = _build_chunks(tool_calls, splits)
    adapter._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_MockAsyncStream(sdk_chunks)
    )

    out = [c async for c in adapter.stream(ChatRequest(messages=[UserMessage(content="x")]))]

    # 1) 中间分片增量按 index 拼接 = 完整 arguments
    accumulated: dict[int, str] = {}
    for chunk in out:
        if chunk.finished:
            continue
        if not chunk.tool_calls:
            continue
        for d in chunk.tool_calls:
            if d.arguments_delta is not None:
                accumulated[d.index] = accumulated.get(d.index, "") + d.arguments_delta

    for index, (_, _, args) in enumerate(tool_calls):
        assert accumulated.get(index, "") == args

    # 2) finished=True 分片携带按 index 升序的完整列表。
    # 注：D3 / id-validation-analysis 决策后，``_materialize_full_tool_calls``
    # 把累积态的空字符串归一化为 ``None``，让下游 ``_RoundStreamAccumulator``
    # 的 finished 违约判定可一次到位。所以当 ``tc_args == ""`` 时，
    # ``arguments_delta`` 期望为 ``None``；其他情况下保持原值。``id`` /
    # ``name`` 同理（hypothesis 策略已保证非空，故仅 args 出现该退化）。
    final = next(c for c in out if c.finished)
    assert final.tool_calls is not None
    assert len(final.tool_calls) == len(tool_calls)
    for delta_obj, (tc_id, tc_name, tc_args) in zip(
        final.tool_calls, tool_calls, strict=True
    ):
        assert isinstance(delta_obj, StreamingToolCallDelta)
        assert delta_obj.id == tc_id
        assert delta_obj.name == tc_name
        if tc_args == "":
            assert delta_obj.arguments_delta is None
        else:
            assert delta_obj.arguments_delta == tc_args
