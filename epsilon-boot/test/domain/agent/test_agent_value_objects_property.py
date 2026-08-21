"""Agent 值对象属性测试模块。

使用 Hypothesis 对 AgentConfig 和 AgentResult 值对象进行属性测试，验证：
- 构造正确性：任意合法参数构造后字段值保留
- 不可变性：frozen dataclass 赋值属性时抛出 FrozenInstanceError
"""

from dataclasses import FrozenInstanceError
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.value_objects import AgentConfig, AgentResult

# ── Hypothesis 策略 ──

# AgentConfig 策略
system_prompt_st = st.text()
tool_schemas_st: st.SearchStrategy[list[dict[str, Any]]] = st.lists(
    st.fixed_dictionaries({"name": st.text(min_size=1, max_size=10)})
)
model_st = st.none() | st.text(min_size=1)
max_rounds_st = st.integers(min_value=1, max_value=100)

# AgentResult 策略
content_st = st.text()
result_model_st = st.text(min_size=1)
usage_st = st.dictionaries(
    st.text(min_size=1, max_size=10),
    st.integers(min_value=0, max_value=10000),
)
latency_ms_st = st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False)


# ── Property 1: Value object construction and immutability ──
# Feature: agent-abstraction-layer, Property 1: Value object construction and immutability


@settings(max_examples=100, deadline=5000)
@given(
    system_prompt=system_prompt_st,
    tool_schemas=tool_schemas_st,
    model=model_st,
    max_rounds=max_rounds_st,
)
def test_agent_config_construction_preserves_fields(
    system_prompt: str,
    tool_schemas: list[dict[str, Any]],
    model: str | None,
    max_rounds: int,
) -> None:
    """验证 AgentConfig 构造成功且所有字段值保留。

    **Validates: Requirements 1.1**

    对于任意合法的 system_prompt、tool_schemas、model 和 max_rounds（>0），
    构造 AgentConfig 后各字段应与传入值完全一致。
    """
    config = AgentConfig(
        system_prompt=system_prompt,
        tool_schemas=tool_schemas,
        model=model,
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )

    assert config.system_prompt == system_prompt
    assert config.tool_schemas == tool_schemas
    assert config.model == model
    assert config.max_rounds == max_rounds
    assert config.prompt_id == "chat-default@v1"


@settings(max_examples=100, deadline=5000)
@given(
    system_prompt=system_prompt_st,
    tool_schemas=tool_schemas_st,
    model=model_st,
    max_rounds=max_rounds_st,
)
def test_agent_config_is_frozen(
    system_prompt: str,
    tool_schemas: list[dict[str, Any]],
    model: str | None,
    max_rounds: int,
) -> None:
    """验证 AgentConfig 为 frozen dataclass，赋值属性时抛出 FrozenInstanceError。

    **Validates: Requirements 1.1**

    对于任意合法参数构造的 AgentConfig，尝试修改任意属性应抛出 FrozenInstanceError。
    """
    config = AgentConfig(
        system_prompt=system_prompt,
        tool_schemas=tool_schemas,
        model=model,
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )

    with pytest.raises(FrozenInstanceError):
        config.system_prompt = "changed"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.max_rounds = 999  # type: ignore[misc]


@settings(max_examples=100, deadline=5000)
@given(
    content=content_st,
    model=result_model_st,
    usage=usage_st,
    latency_ms=latency_ms_st,
)
def test_agent_result_construction_preserves_fields(
    content: str,
    model: str,
    usage: dict[str, int],
    latency_ms: float,
) -> None:
    """验证 AgentResult 构造成功且所有字段值保留。

    **Validates: Requirements 3.1**

    对于任意合法的 content、model、usage 和 latency_ms，
    构造 AgentResult 后各字段应与传入值完全一致。
    """
    result = AgentResult(
        content=content,
        model=model,
        usage=usage,
        latency_ms=latency_ms,
    )

    assert result.content == content
    assert result.model == model
    assert result.usage == usage
    assert result.latency_ms == latency_ms


@settings(max_examples=100, deadline=5000)
@given(
    content=content_st,
    model=result_model_st,
    usage=usage_st,
    latency_ms=latency_ms_st,
)
def test_agent_result_is_frozen(
    content: str,
    model: str,
    usage: dict[str, int],
    latency_ms: float,
) -> None:
    """验证 AgentResult 为 frozen dataclass，赋值属性时抛出 FrozenInstanceError。

    **Validates: Requirements 3.1**

    对于任意合法参数构造的 AgentResult，尝试修改任意属性应抛出 FrozenInstanceError。
    """
    result = AgentResult(
        content=content,
        model=model,
        usage=usage,
        latency_ms=latency_ms,
    )

    with pytest.raises(FrozenInstanceError):
        result.content = "changed"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        result.latency_ms = 999.0  # type: ignore[misc]


# ── Property 2: AgentConfig max_rounds validation ──
# Feature: agent-abstraction-layer, Property 2: AgentConfig max_rounds validation


@settings(max_examples=100, deadline=5000)
@given(max_rounds=st.integers(max_value=0))
def test_agent_config_rejects_non_positive_max_rounds(max_rounds: int) -> None:
    """验证 max_rounds <= 0 时 AgentConfig 构造抛出 ValueError。

    **Validates: Requirements 1.2**

    对于任意 max_rounds <= 0 的整数，构造 AgentConfig 应在 __post_init__ 中
    抛出 ValueError，拒绝非法配置。
    """
    with pytest.raises(ValueError):
        AgentConfig(
            system_prompt="test",
            tool_schemas=[],
            model=None,
            max_rounds=max_rounds,
            prompt_id="chat-default@v1",
        )


@settings(max_examples=100, deadline=5000)
@given(max_rounds=st.integers(min_value=1, max_value=1000))
def test_agent_config_accepts_positive_max_rounds(max_rounds: int) -> None:
    """验证 max_rounds > 0 时 AgentConfig 构造成功。

    **Validates: Requirements 1.2**

    对于任意 max_rounds > 0 的整数，构造 AgentConfig 应成功且 max_rounds 字段值保留。
    """
    config = AgentConfig(
        system_prompt="test",
        tool_schemas=[],
        model=None,
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )
    assert config.max_rounds == max_rounds
