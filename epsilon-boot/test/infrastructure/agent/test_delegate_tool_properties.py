"""DelegateToAgentTool 属性测试模块。

验证 DelegateToAgentTool 的核心行为属性（重构后使用 DelegationPort）：
- Property 6: 未注册 Agent 抛出 AgentNotFoundError（通过 DelegationPort 传播）
- Property 7: 正确调用 DelegationPort.delegate 并传递参数
- Property 8: 返回值映射（基于 DelegationResult.success）
- Property 9: 深度超限抛出 DelegationDepthExceededError
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.exceptions import AgentNotFoundError, DelegationDepthExceededError
from domain.agent.value_objects import DelegationResult, NamedAgentConfig
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool

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
# Property 6: DelegateToAgentTool 未注册 Agent 抛出 AgentNotFoundError
# Feature: agent-inter-communication, Property 6:
# DelegateToAgentTool 未注册 Agent 抛出 AgentNotFoundError
# **验证: 需求 6.6**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    unregistered_name=non_blank_str_st,
    registered_configs=st.lists(named_agent_config_st, min_size=0, max_size=5),
    task_goal=non_blank_str_st,
)
@pytest.mark.asyncio
async def test_unregistered_agent_raises_agent_not_found_error(
    unregistered_name: str,
    registered_configs: list[NamedAgentConfig],
    task_goal: str,
) -> None:
    """验证对未注册的 agent_name 调用 execute 时抛出 AgentNotFoundError。

    DelegationPort.delegate 抛出 AgentNotFoundError，DelegateToAgentTool 向上传播。
    """
    registry = AgentRegistryAdapter()
    for config in registered_configs:
        registry.register(config)

    registered_names = set(registry.list_names())
    if unregistered_name in registered_names:
        return

    delegation = AsyncMock()
    delegation.delegate = AsyncMock(
        side_effect=AgentNotFoundError(
            agent_name=unregistered_name,
            registered_names=list(registered_names),
        )
    )

    tool = DelegateToAgentTool(
        agent_registry=registry,
        delegation=delegation,
        current_delegation_depth=0,
        max_delegation_depth=3,
    )

    with pytest.raises(AgentNotFoundError) as exc_info:
        await tool.execute(agent_name=unregistered_name, task_goal=task_goal)

    assert exc_info.value.agent_name == unregistered_name


# ---------------------------------------------------------------------------
# Property 7: DelegateToAgentTool 正确调用 DelegationPort.delegate
# Feature: agent-inter-communication, Property 7:
# DelegateToAgentTool 正确调用 DelegationPort.delegate
# **验证: 需求 6.7, 6.8, 8.1**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    config=named_agent_config_st,
    current_depth=st.integers(min_value=0, max_value=10),
    task_goal=non_blank_str_st,
    input_data=st.dictionaries(
        keys=st.text(min_size=1, max_size=10),
        values=st.text(max_size=20),
        max_size=3,
    ),
)
@pytest.mark.asyncio
async def test_task_construction_correctness(
    config: NamedAgentConfig,
    current_depth: int,
    task_goal: str,
    input_data: dict[str, object],
) -> None:
    """验证 DelegateToAgentTool 正确调用 DelegationPort.delegate 并传递参数。

    验证 delegate 被调用时传入的参数：
    - agent_name 等于请求的 agent_name
    - task_goal 等于请求的 task_goal
    - input_data 等于请求的 input_data
    - delegation_depth 等于 current_depth + 1
    - max_delegation_depth 等于工具配置的 max_delegation_depth
    """
    registry = AgentRegistryAdapter()
    registry.register(config)

    max_depth = current_depth + 5

    delegation = AsyncMock()
    delegation.delegate = AsyncMock(return_value=DelegationResult(content="ok", success=True))

    tool = DelegateToAgentTool(
        agent_registry=registry,
        delegation=delegation,
        current_delegation_depth=current_depth,
        max_delegation_depth=max_depth,
    )

    await tool.execute(
        agent_name=config.name,
        task_goal=task_goal,
        input_data=input_data,
    )

    delegation.delegate.assert_called_once_with(
        config.name,
        task_goal,
        input_data,
        current_depth + 1,
        max_depth,
    )


# ---------------------------------------------------------------------------
# Property 8: DelegateToAgentTool 返回值映射
# Feature: agent-inter-communication, Property 8: DelegateToAgentTool 返回值映射
# **验证: 需求 6.9, 6.10**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    config=named_agent_config_st,
    task_goal=non_blank_str_st,
    content=st.text(min_size=0, max_size=100),
    success=st.booleans(),
)
@pytest.mark.asyncio
async def test_return_value_mapping(
    config: NamedAgentConfig,
    task_goal: str,
    content: str,
    success: bool,
) -> None:
    """验证 DelegateToAgentTool 的返回值映射。

    生成随机 DelegationResult，验证：
    - success=True 时返回 content
    - success=False 时返回包含 agent_name 和 content 的错误字符串
    """
    registry = AgentRegistryAdapter()
    registry.register(config)

    delegation_result = DelegationResult(content=content, success=success)
    delegation = AsyncMock()
    delegation.delegate = AsyncMock(return_value=delegation_result)

    tool = DelegateToAgentTool(
        agent_registry=registry,
        delegation=delegation,
        current_delegation_depth=0,
        max_delegation_depth=3,
    )

    result = await tool.execute(
        agent_name=config.name,
        task_goal=task_goal,
    )

    if success:
        assert result.content == content
    else:
        assert content in result.content
        assert config.name in result.content


# ---------------------------------------------------------------------------
# Property 9: DelegateToAgentTool 深度超限抛出 DelegationDepthExceededError
# Feature: agent-inter-communication, Property 9:
# DelegateToAgentTool 深度超限抛出 DelegationDepthExceededError
# **验证: 需求 7.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    max_depth=st.integers(min_value=0, max_value=20),
    extra=st.integers(min_value=0, max_value=10),
    agent_name=non_blank_str_st,
    task_goal=non_blank_str_st,
)
@pytest.mark.asyncio
async def test_depth_exceeded_raises_delegation_depth_exceeded_error(
    max_depth: int,
    extra: int,
    agent_name: str,
    task_goal: str,
) -> None:
    """验证委派深度超限时抛出 DelegationDepthExceededError。

    生成随机 current_delegation_depth 和 max_delegation_depth，
    使 current_depth + 1 > max_depth，验证 execute() 抛出
    DelegationDepthExceededError 且异常包含正确的 current_depth 和 max_depth。
    """
    current_depth = max_depth + extra

    delegation = AsyncMock()

    tool = DelegateToAgentTool(
        agent_registry=AgentRegistryAdapter(),
        delegation=delegation,
        current_delegation_depth=current_depth,
        max_delegation_depth=max_depth,
    )

    with pytest.raises(DelegationDepthExceededError) as exc_info:
        await tool.execute(agent_name=agent_name, task_goal=task_goal)

    assert exc_info.value.current_depth == current_depth
    assert exc_info.value.max_depth == max_depth
