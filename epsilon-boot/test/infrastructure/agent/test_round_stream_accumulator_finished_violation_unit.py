"""流式 finished 违约回退单元测试（Task 4.3）。

对应 design 测试矩阵 T4 / T5 / T6 / T7：

- T4：finished 分片 ``delta.id=None``，``build_response().tool_calls`` 与
  "增量累积三字段缺一即跳过"语义一致；``caplog`` 断言 WARN extra 含
  ``violation_field="id"``
- T5：finished 分片 ``delta.id=""``，同 T4
- T6：先发增量 ``delta.id=""``（被累积进 slot），再发 finished 违约分片 →
  回退至增量结果，但增量 id 仍是 ``""`` → ``build_response().tool_calls``
  为空列表（与"三字段缺一跳过"对齐）
- T7：合法 finished（三字段全有）→ 优先取 finished 完整列表覆盖增量
  （回归保护）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest

from domain.model_access.value_objects import (
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.agent.round_stream_accumulator import (
    RoundStreamAccumulator as _RoundStreamAccumulator,
)


async def _stream(chunks: list[StreamingChunk]) -> AsyncIterator[StreamingChunk]:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_t4_finished_id_none_falls_back_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T4: finished 分片 id=None 触发回退，WARN extra 含 violation_field='id'。"""
    chunks = [
        StreamingChunk(
            delta_content="",
            finished=True,
            usage={"total_tokens": 10},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0,
                    id=None,
                    name="web_search",
                    arguments_delta='{"q":"hi"}',
                )
            ],
        )
    ]
    caplog.set_level(
        logging.WARNING,
        logger="infrastructure.agent.round_stream_accumulator",
    )

    acc = _RoundStreamAccumulator(model="glm-4-plus")
    await acc.consume(_stream(chunks))
    response = acc.build_response()

    # 既无增量累积也无 finished 完整列表 → tool_calls 为空，与"三字段缺一跳过"对齐
    assert response.tool_calls == []
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("finished 分片违约" in r.getMessage() for r in warns)
    record = next(r for r in warns if "finished 分片违约" in r.getMessage())
    assert getattr(record, "violation_field", None) == "id"
    assert getattr(record, "source", None) == "stream_finished"
    assert getattr(record, "model", None) == "glm-4-plus"
    assert getattr(record, "tool_call_index", None) == 0
    assert getattr(record, "raw_id_value", None) is None


@pytest.mark.asyncio
async def test_t5_finished_id_empty_string_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T5: finished 分片 id="" 同 T4 触发回退。"""
    chunks = [
        StreamingChunk(
            delta_content="",
            finished=True,
            usage={"total_tokens": 8},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0,
                    id="",
                    name="web_search",
                    arguments_delta='{"q":"hi"}',
                )
            ],
        )
    ]
    caplog.set_level(
        logging.WARNING,
        logger="infrastructure.agent.round_stream_accumulator",
    )

    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(_stream(chunks))
    response = acc.build_response()

    assert response.tool_calls == []
    record = next(
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "finished 分片违约" in r.getMessage()
    )
    assert getattr(record, "violation_field", None) == "id"
    assert getattr(record, "raw_id_value", None) == ""


@pytest.mark.asyncio
async def test_t6_incremental_empty_id_then_finished_violation_falls_back_to_incomplete(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T6: 增量 id="" 累积进 slot；finished 违约 → 回退增量 → 增量 id="" → tool_calls 为空。"""
    chunks = [
        # 中间分片：id="" 进入累积 slot（增量分支保留 is not None 判定）
        StreamingChunk(
            delta_content="",
            finished=False,
            tool_calls=[
                StreamingToolCallDelta(
                    index=0,
                    id="",
                    name="web_search",
                    arguments_delta='{"q":"hi"}',
                )
            ],
        ),
        # finished 违约：id=None
        StreamingChunk(
            delta_content="",
            finished=True,
            usage={"total_tokens": 5},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0,
                    id=None,
                    name="web_search",
                    arguments_delta='{"q":"hi"}',
                )
            ],
        ),
    ]
    caplog.set_level(
        logging.WARNING,
        logger="infrastructure.agent.round_stream_accumulator",
    )

    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(_stream(chunks))
    response = acc.build_response()

    # 回退到增量累积结果，但增量 id="" → build_response 跳过 → 空列表
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_t7_finished_with_all_fields_overrides_incremental() -> None:
    """T7: 合法 finished 完整列表 → 覆盖增量结果（回归保护）。"""
    chunks = [
        StreamingChunk(
            delta_content="",
            finished=False,
            tool_calls=[
                StreamingToolCallDelta(
                    index=0,
                    id="call_x",
                    name="web_search",
                    arguments_delta='{"q":"hi"}',
                )
            ],
        ),
        StreamingChunk(
            delta_content="",
            finished=True,
            usage={"total_tokens": 5},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0,
                    id="call_x",
                    name="web_search",
                    arguments_delta='{"q":"hi"}',
                )
            ],
        ),
    ]

    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(_stream(chunks))
    response = acc.build_response()

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_x"
    assert response.tool_calls[0].name == "web_search"
    assert response.tool_calls[0].arguments == '{"q":"hi"}'
