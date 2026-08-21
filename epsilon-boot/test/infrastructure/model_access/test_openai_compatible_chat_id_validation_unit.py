"""同步 chat 链路 id 校验单元测试（Task 3.2）。

对应 design 测试矩阵 T1 / T2 / T3：

- T1：mock SDK 返回 ``tool_calls[0].id=None``，``adapter.chat()`` 抛
  ``InvalidToolCallIdError(source="chat_sync", raw_id_value=None, ...)``
- T2：mock ``tool_calls[0].id=""``，同上 ``raw_id_value == ""``
- T3：mock ``tool_calls[0].id="call_xxx"``（合法），返回
  ``LLMResponse.tool_calls`` 长度为 1，无 WARN 日志（回归保护）

用 pytest ``caplog`` 断言 WARN 日志 ``record.__dict__`` 字段集包含
``source / provider / model / tool_name / tool_call_index / raw_id_value``。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import UserMessage
from domain.model_access.exceptions import InvalidToolCallIdError
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


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


def _make_completion(
    *,
    tool_call_id: str | None,
    tool_name: str = "web_search",
    arguments: str = '{"q":"hi"}',
    index: int = 0,
    model: str = "deepseek-chat",
) -> SimpleNamespace:
    """构造模拟 SDK ``ChatCompletion`` 对象，仅一条 tool_call。"""
    function = SimpleNamespace(name=tool_name, arguments=arguments)
    tc = SimpleNamespace(id=tool_call_id, function=function, index=index)
    message = SimpleNamespace(content=None, tool_calls=[tc])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        model=model,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.mark.asyncio
async def test_t1_chat_sync_id_none_raises(caplog: pytest.LogCaptureFixture) -> None:
    """T1: id=None 抛 InvalidToolCallIdError(source='chat_sync', raw_id_value=None)。"""
    adapter = _make_adapter("deepseek")
    completion = _make_completion(tool_call_id=None)
    adapter.set_chat_completion_handler(AsyncMock(return_value=completion))

    request = ChatRequest(messages=[UserMessage(content="hi")])
    caplog.set_level(
        logging.WARNING, logger="infrastructure.model_access.openai_compatible_adapter"
    )

    with pytest.raises(InvalidToolCallIdError) as exc_info:
        await adapter.chat(request)

    exc = exc_info.value
    assert exc.details["source"] == "chat_sync"
    assert exc.details["raw_id_value"] is None
    assert exc.details["provider"] == "deepseek"
    assert exc.details["model"] == "deepseek-chat"
    assert exc.details["tool_name"] == "web_search"
    assert exc.details["tool_call_index"] == 0

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("tool_call.id 不合法" in r.getMessage() for r in warns)
    record = next(r for r in warns if "tool_call.id 不合法" in r.getMessage())
    expected_keys = {
        "source",
        "provider",
        "model",
        "tool_name",
        "tool_call_index",
        "raw_id_value",
    }
    record_data: dict[str, object] = record.__dict__
    record_keys = set(record_data)
    assert expected_keys <= record_keys
    assert record_data["source"] == "chat_sync"
    assert record_data["provider"] == "deepseek"
    assert record_data["model"] == "deepseek-chat"
    assert record_data["tool_name"] == "web_search"
    assert record_data["tool_call_index"] == 0
    assert record_data["raw_id_value"] is None


@pytest.mark.asyncio
async def test_t2_chat_sync_id_empty_string_raises(caplog: pytest.LogCaptureFixture) -> None:
    """T2: id='' 抛 InvalidToolCallIdError(raw_id_value='')。"""
    adapter = _make_adapter("zhipu")
    completion = _make_completion(tool_call_id="", model="glm-4-plus")
    adapter.set_chat_completion_handler(AsyncMock(return_value=completion))

    request = ChatRequest(messages=[UserMessage(content="hi")])
    caplog.set_level(
        logging.WARNING, logger="infrastructure.model_access.openai_compatible_adapter"
    )

    with pytest.raises(InvalidToolCallIdError) as exc_info:
        await adapter.chat(request)

    exc = exc_info.value
    assert exc.details["raw_id_value"] == ""
    assert exc.details["provider"] == "zhipu"
    assert exc.details["model"] == "glm-4-plus"


@pytest.mark.asyncio
async def test_t3_chat_sync_valid_id_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """T3: id='call_xxx' 合法时返回 tool_calls 长度 1，无 WARN 日志。"""
    adapter = _make_adapter("deepseek")
    completion = _make_completion(tool_call_id="call_xxx")
    adapter.set_chat_completion_handler(AsyncMock(return_value=completion))

    request = ChatRequest(messages=[UserMessage(content="hi")])
    caplog.set_level(
        logging.WARNING, logger="infrastructure.model_access.openai_compatible_adapter"
    )

    response = await adapter.chat(request)
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_xxx"
    assert response.tool_calls[0].name == "web_search"

    id_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "tool_call.id 不合法" in r.getMessage()
    ]
    assert id_warnings == []
