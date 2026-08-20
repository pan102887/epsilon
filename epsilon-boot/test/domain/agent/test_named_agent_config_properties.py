"""NamedAgentConfig 属性测试模块。

使用 Hypothesis 对 NamedAgentConfig 值对象进行属性测试，验证：
- 空白字段校验：name 或 description 为空或纯空白时抛出 ValueError
- 合法构造：非空白 name 和 description 构造应成功
"""

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.value_objects import NamedAgentConfig

# ── Hypothesis 策略 ──

# 空白字符串策略：空串或仅由空白字符组成的字符串
whitespace_chars = " \t\n\r\x0b\x0c"
blank_str_st = st.from_regex(r"^[\s]*$", fullmatch=True).filter(
    lambda s: len(s) == 0 or s.strip() == ""
)

# 非空白字符串策略：至少包含一个非空白字符
non_blank_str_st = st.text(min_size=1).filter(lambda s: s.strip() != "")

# 合法的 system_prompt 策略
system_prompt_st = st.text()

# tool_names 策略：None 或非空 frozenset
tool_names_st = st.none() | st.frozensets(st.text(min_size=1, max_size=20), min_size=1)

# model 策略：None 或非空字符串
model_st = st.none() | st.text(min_size=1, max_size=20)


# ── Property 1: NamedAgentConfig 空白字段校验 ──
# Feature: agent-inter-communication, Property 1: NamedAgentConfig 空白字段校验


@settings(max_examples=100, deadline=5000)
@given(
    blank_name=blank_str_st,
    valid_description=non_blank_str_st,
    system_prompt=system_prompt_st,
)
def test_blank_name_raises_value_error(
    blank_name: str,
    valid_description: str,
    system_prompt: str,
) -> None:
    """验证 name 为空或纯空白时构造 NamedAgentConfig 抛出 ValueError。

    **Validates: Requirements 2.7**

    对于任意空白字符串作为 name，构造 NamedAgentConfig 应在 __post_init__ 中
    抛出 ValueError。
    """
    with pytest.raises(ValueError):
        NamedAgentConfig(
            name=blank_name,
            description=valid_description,
            system_prompt=system_prompt,
            prompt_id="chat-default@v1",
        )


@settings(max_examples=100, deadline=5000)
@given(
    valid_name=non_blank_str_st,
    blank_description=blank_str_st,
    system_prompt=system_prompt_st,
)
def test_blank_description_raises_value_error(
    valid_name: str,
    blank_description: str,
    system_prompt: str,
) -> None:
    """验证 description 为空或纯空白时构造 NamedAgentConfig 抛出 ValueError。

    **Validates: Requirements 2.8**

    对于任意空白字符串作为 description，构造 NamedAgentConfig 应在 __post_init__ 中
    抛出 ValueError。
    """
    with pytest.raises(ValueError):
        NamedAgentConfig(
            name=valid_name,
            description=blank_description,
            system_prompt=system_prompt,
            prompt_id="chat-default@v1",
        )


@settings(max_examples=100, deadline=5000)
@given(
    valid_name=non_blank_str_st,
    valid_description=non_blank_str_st,
    system_prompt=system_prompt_st,
    tool_names=tool_names_st,
    model=model_st,
)
def test_non_blank_fields_construct_successfully(
    valid_name: str,
    valid_description: str,
    system_prompt: str,
    tool_names: frozenset[str] | None,
    model: str | None,
) -> None:
    """验证非空白 name 和 description 构造 NamedAgentConfig 成功且字段值保留。

    **Validates: Requirements 2.7, 2.8**

    对于任意非空白的 name 和 description，构造 NamedAgentConfig 应成功，
    且所有字段值与传入值完全一致。
    """
    config = NamedAgentConfig(
        name=valid_name,
        description=valid_description,
        system_prompt=system_prompt,
        prompt_id="chat-default@v1",
        tool_names=tool_names,
        model=model,
    )

    assert config.name == valid_name
    assert config.description == valid_description
    assert config.system_prompt == system_prompt
    assert config.prompt_id == "chat-default@v1"
    assert config.tool_names == tool_names
    assert config.model == model
