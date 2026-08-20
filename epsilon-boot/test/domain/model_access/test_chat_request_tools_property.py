"""ChatRequest tools 字段属性测试。

使用 Hypothesis 对 ChatRequest 的 tools 字段进行属性测试，
验证字段保留、_build_params 传递逻辑、frozen 不可变性和 ToolRegistry 兼容性。
"""

import dataclasses
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.tools import Tool, ToolRegistry
from domain.chat.context import BaseMessage, UserMessage
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from infrastructure.model_access.provider_config import ProviderConfig

# ---------------------------------------------------------------------------
# Hypothesis 策略
# ---------------------------------------------------------------------------

_param_name_st = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)

_tool_schema_st = st.fixed_dictionaries(
    {
        "type": st.just("function"),
        "function": st.fixed_dictionaries(
            {
                "name": _param_name_st,
                "description": st.text(min_size=1, max_size=50),
                "parameters": st.fixed_dictionaries(
                    {
                        "type": st.just("object"),
                        "properties": st.dictionaries(
                            keys=_param_name_st,
                            values=st.fixed_dictionaries(
                                {"type": st.sampled_from(["string", "integer", "boolean"])}
                            ),
                            min_size=0,
                            max_size=3,
                        ),
                    }
                ),
            }
        ),
    }
)

_tools_st = st.one_of(
    st.none(),
    st.just([]),
    st.lists(_tool_schema_st, min_size=1, max_size=5),
)

_messages_st = st.builds(
    lambda: [UserMessage(content="hello")],
)
"""每次调用生成独立的 ``BaseMessage`` 实例列表。

Hypothesis 的 ``st.just`` 会复用同一对象，导致跨用例共享同一可变列表，
因此改用 ``st.builds(lambda: [...])`` 保证每个示例都拿到全新实例。
"""


def _make_adapter() -> OpenAICompatibleAdapter:
    """构造一个用于测试的 OpenAICompatibleAdapter 实例（mock ProviderConfig）。"""
    config = ProviderConfig(
        provider_name="test",
        api_base="https://test.example.com/v1",
        api_key="test-key",
        default_model="test-model",
    )
    return OpenAICompatibleAdapter(config)


# ---------------------------------------------------------------------------
# Property 1: Tools field preservation
# Feature: chat-request-tools-field, Property 1: Tools field preservation
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=2000)
@given(tools=_tools_st, messages=_messages_st)
def test_tools_field_preservation(
    tools: list[dict[str, Any]] | None,
    messages: list[BaseMessage],
) -> None:
    """对任意合法 tools 值，ChatRequest 构造后 tools 属性应等于输入值。"""
    request = ChatRequest(messages=messages, tools=tools)
    assert request.tools == tools


# ---------------------------------------------------------------------------
# Property 2: _build_params includes tools if and only if truthy
# Feature: chat-request-tools-field, Property 2: _build_params includes tools if and only if truthy
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=2000)
@given(tools=_tools_st, messages=_messages_st)
def test_build_params_tools_inclusion(
    tools: list[dict[str, Any]] | None,
    messages: list[BaseMessage],
) -> None:
    """truthy tools 时 params 含 "tools" 键且值一致，falsy 时不含。"""
    adapter = _make_adapter()
    request = ChatRequest(messages=messages, tools=tools)
    params = adapter._build_params(request, stream=False)

    if tools:
        assert "tools" in params
        assert params["tools"] == tools
    else:
        assert "tools" not in params


# ---------------------------------------------------------------------------
# Property 3: Frozen immutability of tools field
# Feature: chat-request-tools-field, Property 3: Frozen immutability of tools field
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=2000)
@given(tools=_tools_st, messages=_messages_st)
def test_frozen_immutability(
    tools: list[dict[str, Any]] | None,
    messages: list[BaseMessage],
) -> None:
    """构造后尝试赋值 tools 应抛出 FrozenInstanceError。"""
    request = ChatRequest(messages=messages, tools=tools)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.tools = [
            {"type": "function", "function": {"name": "x", "description": "x", "parameters": {}}}
        ]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Property 4: ToolRegistry.get_schemas() to ChatRequest.tools compatibility
# Feature: chat-request-tools-field, Property 4:
# ToolRegistry.get_schemas() to ChatRequest.tools compatibility
# ---------------------------------------------------------------------------


class _MockTool(Tool):
    """用于属性测试的 mock Tool 实现。"""

    def __init__(self, tool_name: str, tool_desc: str) -> None:
        self._name = tool_name
        self._desc = tool_desc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return ""


_tool_name_st = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)
_tool_desc_st = st.text(min_size=1, max_size=30)
_mock_tool_st = st.tuples(_tool_name_st, _tool_desc_st)


@settings(max_examples=100, deadline=2000)
@given(
    tool_specs=st.lists(_mock_tool_st, min_size=0, max_size=5, unique_by=lambda t: t[0]),
    messages=_messages_st,
)
def test_tool_registry_compatibility(
    tool_specs: list[tuple[str, str]],
    messages: list[BaseMessage],
) -> None:
    """ToolRegistry.get_schemas() 的返回值可直接传入 ChatRequest.tools。"""
    registry = ToolRegistry()
    for name, desc in tool_specs:
        registry.register(_MockTool(name, desc))

    schemas = registry.get_schemas()
    request = ChatRequest(messages=messages, tools=schemas)
    assert request.tools == schemas
