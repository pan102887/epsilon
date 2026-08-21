"""日志 extra 与异常 details 对齐用例（Task 7.3）。

对应 design 测试矩阵 T19 / requirement R1.3 / R2.2 / R3.4 / R5.2：

分别触发 chat_sync（T1）、stream_finished（T4）、history_restore（T8）三条
链路，使用 ``caplog`` 抓取 WARN 记录的 ``record.__dict__``（含 extra 字段），
断言 extra 中的统一字段（``source / provider / model / tool_name /
tool_call_index / raw_id_value``）与对应抛出的 / 跳过的异常 ``details``
同名键值一致：

- chat_sync：与抛出的 ``InvalidToolCallIdError.details`` 键值相等；
- stream_finished：与 design §统一诊断字段集表（不抛异常）对齐；
- history_restore（filter）：与跳过的违约项映射到统一 schema 后键值相等。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat import context as ctx_module
from domain.chat.context import BaseMessage, UserMessage
from domain.model_access.exceptions import InvalidToolCallIdError
from domain.model_access.value_objects import (
    ChatRequest,
    StreamingChunk,
    StreamingToolCallDelta,
)
from infrastructure.agent.round_stream_accumulator import (
    RoundStreamAccumulator as _RoundStreamAccumulator,
)
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter

_UNIFIED_KEYS = {
    "source",
    "provider",
    "model",
    "tool_name",
    "tool_call_index",
    "raw_id_value",
}


def _make_adapter(provider_name: str = "deepseek") -> OpenAICompatibleAdapter:
    cfg = MagicMock()
    cfg.api_key = "k"
    cfg.api_base = "https://fake/v1"
    cfg.timeout = 30
    cfg.max_retries = 0
    cfg.max_connections = 10
    cfg.max_keepalive_connections = 5
    cfg.provider_name = provider_name
    cfg.default_model = "deepseek-chat"
    cfg.temperature = 0.7
    cfg.max_tokens = 4096
    return OpenAICompatibleAdapter(cfg)


def _make_completion(*, tool_call_id: str | None) -> SimpleNamespace:
    function = SimpleNamespace(name="web_search", arguments='{"q":"hi"}')
    tc = SimpleNamespace(id=tool_call_id, function=function, index=0)
    message = SimpleNamespace(content=None, tool_calls=[tc])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        model="deepseek-chat",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


@pytest.mark.asyncio
async def test_chat_sync_log_extra_aligns_with_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T19/chat_sync：WARN extra 与异常 details 同名键值相等。"""
    adapter = _make_adapter("deepseek")
    completion = _make_completion(tool_call_id="")
    adapter.set_chat_completion_handler(AsyncMock(return_value=completion))

    request = ChatRequest(messages=[UserMessage(content="hi")])
    caplog.set_level(
        logging.WARNING, logger="infrastructure.model_access.openai_compatible_adapter"
    )
    with pytest.raises(InvalidToolCallIdError) as exc_info:
        await adapter.chat(request)
    exc = exc_info.value

    record = next(
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "tool_call.id 不合法" in r.getMessage()
    )
    for key in _UNIFIED_KEYS:
        assert getattr(record, key) == exc.details[key], f"key {key!r} mismatch"


async def _stream(chunks: list[StreamingChunk]) -> AsyncIterator[StreamingChunk]:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_stream_finished_log_extra_keys_complete(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T19/stream_finished：WARN extra 含统一字段集（不抛异常路径）。"""
    chunks = [
        StreamingChunk(
            delta_content="",
            finished=True,
            usage={"total_tokens": 10},
            tool_calls=[
                StreamingToolCallDelta(
                    index=0, id="", name="web_search", arguments_delta='{"q":"hi"}'
                )
            ],
        )
    ]
    caplog.set_level(logging.WARNING, logger="infrastructure.agent.round_stream_accumulator")

    acc = _RoundStreamAccumulator(model="m")
    await acc.consume(_stream(chunks))
    acc.build_response()

    record = next(
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "finished 分片违约" in r.getMessage()
    )
    for key in _UNIFIED_KEYS:
        assert hasattr(record, key), f"missing key {key!r}"
    assert record.__dict__["source"] == "stream_finished"
    assert record.__dict__["provider"] is None  # 不适用字段：键存在 + None
    assert record.__dict__["model"] == "m"
    assert record.__dict__["violation_field"] == "id"


def test_history_restore_filter_drops_invalid_tool_call() -> None:
    """T19/history_restore（filter）：非法 tool_call 被过滤，行为等价。

    注：``ddd-tactical-remediation`` 需求 A 移除了领域层 ``logging`` 依赖，
    ``domain/chat/context.py`` 的 filter 分支内部诊断 WARN 日志随之删除，原
    「WARN extra 与跳过项统一字段对齐」的日志断言不再适用（属经用户批准的
    受控可观测面变更，见 requirement 需求 A AC 3）。history_restore 的 raise
    分支信号完整保留于 ``InvalidToolCallIdError.details``，由
    ``test/domain/chat/test_base_message_from_dict_raise_strategy_unit.py`` 覆盖。
    本用例改为断言 filter 分支的对外可观测行为：非法项被过滤、metadata 保留。
    """
    from domain.chat.context import AssistantMessage

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ctx_module, "history_restore_strategy", "filter")
        data = {
            "role": "assistant",
            "content": "x",
            "tool_calls": [{"id": "", "name": "web_search", "arguments": "{}"}],
            "metadata": {"session_id": "sess-zzz"},
        }

        msg = BaseMessage.from_dict(data)

    assert isinstance(msg, AssistantMessage)
    assert msg.tool_calls == []
    assert msg.metadata == {"session_id": "sess-zzz"}
