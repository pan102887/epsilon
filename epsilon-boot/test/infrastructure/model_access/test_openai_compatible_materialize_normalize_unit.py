"""``_materialize_full_tool_calls`` 恢复与归一化单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter(strategy: str = "recover") -> OpenAICompatibleAdapter:
    """构造测试用 adapter。"""
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
    cfg.stream_tool_call_id_strategy = strategy
    return OpenAICompatibleAdapter(cfg)


def test_empty_id_recovered_when_slot_complete() -> None:
    """slot['id']='' 且 name/arguments 完整 → 生成合成 id。"""
    adapter = _make_adapter("recover")
    acc = {
        0: {"id": "", "name": "web_search", "arguments": '{"q":"hi"}'},
    }
    result, recovery = adapter._materialize_full_tool_calls(
        acc,
        {"model": "m"},
        request_nonce="abc123",
    )
    assert result is not None
    assert len(result) == 1
    delta = result[0]
    assert delta.id == "call_synthetic_abc123_0"
    assert delta.name == "web_search"
    assert delta.arguments_delta == '{"q":"hi"}'
    assert recovery.recovered_count == 1
    assert recovery.synthetic_ids == ("call_synthetic_abc123_0",)
    assert acc[0]["id"] == "call_synthetic_abc123_0"


def test_empty_arguments_normalized_to_none() -> None:
    """slot['arguments']='' → 不恢复，arguments_delta is None。"""
    adapter = _make_adapter("recover")
    acc = {
        0: {"id": "", "name": "web_search", "arguments": ""},
    }
    result, recovery = adapter._materialize_full_tool_calls(
        acc,
        {"model": "m"},
        request_nonce="abc123",
    )
    assert result is not None
    assert result[0].id is None
    assert result[0].arguments_delta is None
    assert recovery.recovered_count == 0


def test_empty_name_normalized_to_none() -> None:
    """slot['name']='' → 不恢复，name is None。"""
    adapter = _make_adapter("recover")
    acc = {
        0: {"id": "", "name": "", "arguments": '{"q":"hi"}'},
    }
    result, recovery = adapter._materialize_full_tool_calls(
        acc,
        {"model": "m"},
        request_nonce="abc123",
    )
    assert result is not None
    assert result[0].id is None
    assert result[0].name is None
    assert recovery.recovered_count == 0


def test_legal_slot_passes_through() -> None:
    """合法三字段值不变（回归保护）。"""
    adapter = _make_adapter("recover")
    acc = {
        0: {"id": "call_x", "name": "web_search", "arguments": '{"q":"hi"}'},
    }
    result, recovery = adapter._materialize_full_tool_calls(
        acc,
        {"model": "m"},
        request_nonce="abc123",
    )
    assert result is not None
    delta = result[0]
    assert delta.id == "call_x"
    assert delta.name == "web_search"
    assert delta.arguments_delta == '{"q":"hi"}'
    assert recovery.recovered_count == 0


def test_empty_acc_returns_none() -> None:
    """空 acc → (None, empty recovery)。"""
    adapter = _make_adapter("recover")
    result, recovery = adapter._materialize_full_tool_calls(
        {},
        {"model": "m"},
        request_nonce="abc123",
    )
    assert result is None
    assert recovery.recovered_count == 0
