"""OpenAICompatibleAdapter 流式工具调用 id 恢复属性测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

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


_tool_name = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu"), max_codepoint=0x7F),
    min_size=1,
    max_size=12,
)
_arguments = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Po"), max_codepoint=0x7F),
    min_size=1,
    max_size=40,
)


@settings(max_examples=50, deadline=None)
@given(
    names=st.lists(_tool_name, min_size=1, max_size=8),
    args=st.lists(_arguments, min_size=1, max_size=8),
)
def test_recovered_ids_are_non_empty_unique_and_ascii(
    names: list[str],
    args: list[str],
) -> None:
    """recover 下多个完整缺失 id 槽位生成非空、唯一、ASCII 安全的合成 id。"""
    count = min(len(names), len(args))
    acc = {
        index: {"id": None, "name": names[index], "arguments": args[index]}
        for index in range(count)
    }
    adapter = _make_adapter("recover")

    result, recovery = adapter._materialize_full_tool_calls(
        acc,
        {"model": "m"},
        request_nonce="abc123",
    )

    assert result is not None
    ids = [delta.id for delta in result]
    assert all(ids)
    assert len(set(ids)) == len(ids)
    assert all(all(ord(ch) < 128 for ch in tc_id or "") for tc_id in ids)
    assert recovery.recovered_count == count


@settings(max_examples=50, deadline=None)
@given(
    original_ids=st.lists(
        st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), max_codepoint=0x7F),
            min_size=1,
            max_size=12,
        ).map(lambda value: f"call_{value}"),
        min_size=1,
        max_size=8,
        unique=True,
    ),
    names=st.lists(_tool_name, min_size=1, max_size=8),
    args=st.lists(_arguments, min_size=1, max_size=8),
)
def test_existing_provider_ids_are_not_overwritten(
    original_ids: list[str],
    names: list[str],
    args: list[str],
) -> None:
    """已有 Provider 原始 id 时 recover 不覆盖。"""
    count = min(len(original_ids), len(names), len(args))
    acc = {
        index: {
            "id": original_ids[index],
            "name": names[index],
            "arguments": args[index],
        }
        for index in range(count)
    }
    adapter = _make_adapter("recover")

    result, recovery = adapter._materialize_full_tool_calls(
        acc,
        {"model": "m"},
        request_nonce="abc123",
    )

    assert result is not None
    assert [delta.id for delta in result] == original_ids[:count]
    assert recovery.recovered_count == 0
