"""AgentRegistryAdapter 属性测试模块。

验证 AgentRegistryAdapter 的 register/get/has 一致性（Property 2）
和 list_names 完整性（Property 3）。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.value_objects import NamedAgentConfig
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter

# ── Hypothesis 策略 ──

# 非空白字符串策略
non_blank_str_st = st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != "")

# NamedAgentConfig 策略
named_agent_config_st = st.builds(
    NamedAgentConfig,
    name=non_blank_str_st,
    description=non_blank_str_st,
    system_prompt=st.text(max_size=50),
    prompt_id=st.just("chat-default@v1"),
    tool_names=st.none() | st.frozensets(st.text(min_size=1, max_size=10), min_size=1, max_size=5),
    model=st.none() | st.text(min_size=1, max_size=10),
)


# ---------------------------------------------------------------------------
# Property 2: AgentRegistry register/get/has 一致性
# Feature: agent-inter-communication, Property 2: AgentRegistry register/get/has 一致性
# **验证: 需求 3.2, 3.3, 3.4, 3.5**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    configs=st.lists(named_agent_config_st, min_size=0, max_size=20),
    query_name=non_blank_str_st,
)
def test_registry_get_has_consistent_after_register(
    configs: list[NamedAgentConfig],
    query_name: str,
) -> None:
    """验证注册序列后 get/has 的一致性。

    对于任意 NamedAgentConfig 序列注册到 AgentRegistryAdapter 后：
    - 若 query_name 曾被注册，get 返回最后一次注册的 config，has 返回 True
    - 若 query_name 未被注册，get 返回 None，has 返回 False
    """
    registry = AgentRegistryAdapter()
    for config in configs:
        registry.register(config)

    # 构建期望：同名覆盖，保留最后一次注册的 config
    expected: dict[str, NamedAgentConfig] = {}
    for config in configs:
        expected[config.name] = config

    if query_name in expected:
        assert registry.has(query_name) is True
        assert registry.get(query_name) == expected[query_name]
    else:
        assert registry.has(query_name) is False
        assert registry.get(query_name) is None


# ---------------------------------------------------------------------------
# Property 3: AgentRegistry list_names 完整性
# Feature: agent-inter-communication, Property 3: AgentRegistry list_names 完整性
# **验证: 需求 3.6**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(configs=st.lists(named_agent_config_st, min_size=0, max_size=20))
def test_registry_list_names_completeness(
    configs: list[NamedAgentConfig],
) -> None:
    """验证 list_names 返回的名称集合等于所有已注册 config 的 name 去重集合。

    同名覆盖后，list_names 应返回所有唯一名称。
    """
    registry = AgentRegistryAdapter()
    for config in configs:
        registry.register(config)

    expected_names = {config.name for config in configs}
    assert set(registry.list_names()) == expected_names
