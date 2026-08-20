"""DelegateToAgentTool 委派解耦属性测试模块。

验证 DelegateToAgentTool 在依赖 DelegationPort 重构后的核心行为属性：
- Property 4: DelegateToAgentTool execute 行为（深度校验 + 成功/失败路由）
- Property 5: DelegateToAgentTool description 包含所有已注册 Agent 名称

使用 hypothesis 生成任意 depth 组合和 DelegationResult，验证：
- 委派深度超限时抛出 DelegationDepthExceededError
- 未超限时根据 DelegationResult.success 正确路由返回值
- description 动态包含所有已注册 Agent 名称
- 空注册表时 description 指示无可用 Agent
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.exceptions import DelegationDepthExceededError
from domain.agent.value_objects import DelegationResult, NamedAgentConfig
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool

# ── Hypothesis 策略 ──

# 非空白字符串策略
non_blank_str_st = st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != "")


# ---------------------------------------------------------------------------
# Property 4: DelegateToAgentTool execute 行为（深度校验 + 成功/失败路由）
# Feature: agent-delegation-decoupling, Property 4:
# DelegateToAgentTool execute depth gating and result routing
# **Validates: Requirements 4.3, 7.1, 7.2, 7.4**
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

    **Validates: Requirements 4.3, 7.2**

    生成随机 current_delegation_depth 和 max_delegation_depth，
    使 current_depth + 1 > max_depth，验证 execute() 抛出
    DelegationDepthExceededError 且异常包含正确的 current_depth 和 max_depth。
    """
    # 确保 current_depth + 1 > max_depth
    current_depth = max_depth + extra

    agent_registry = MagicMock()
    delegation = AsyncMock()

    tool = DelegateToAgentTool(
        agent_registry=agent_registry,
        delegation=delegation,
        current_delegation_depth=current_depth,
        max_delegation_depth=max_depth,
    )

    with pytest.raises(DelegationDepthExceededError) as exc_info:
        await tool.execute(agent_name=agent_name, task_goal=task_goal)

    assert exc_info.value.current_depth == current_depth
    assert exc_info.value.max_depth == max_depth

    # 确保 delegation.delegate 未被调用（深度校验在委派之前）
    delegation.delegate.assert_not_called()


@settings(max_examples=100, deadline=5000)
@given(
    current_depth=st.integers(min_value=0, max_value=10),
    extra_headroom=st.integers(min_value=1, max_value=10),
    agent_name=non_blank_str_st,
    task_goal=non_blank_str_st,
    content=st.text(min_size=0, max_size=100),
    success=st.booleans(),
)
@pytest.mark.asyncio
async def test_execute_routes_result_based_on_delegation_success(
    current_depth: int,
    extra_headroom: int,
    agent_name: str,
    task_goal: str,
    content: str,
    success: bool,
) -> None:
    """验证深度合法时根据 DelegationResult.success 正确路由返回值。

    **Validates: Requirements 4.3, 7.1, 7.4**

    生成随机合法深度（current_depth + 1 <= max_depth）和任意 DelegationResult，
    验证：
    - success=True 时返回 result.content
    - success=False 时返回包含 agent_name 和 result.content 的错误字符串
    """
    # 确保 current_depth + 1 <= max_depth（合法深度）
    max_depth = current_depth + extra_headroom

    delegation_result = DelegationResult(content=content, success=success)

    agent_registry = MagicMock()
    delegation = AsyncMock()
    delegation.delegate = AsyncMock(return_value=delegation_result)

    tool = DelegateToAgentTool(
        agent_registry=agent_registry,
        delegation=delegation,
        current_delegation_depth=current_depth,
        max_delegation_depth=max_depth,
    )

    result = await tool.execute(agent_name=agent_name, task_goal=task_goal)

    assert result.metadata["target_agent"] == agent_name
    assert result.metadata["success"] is success
    if success:
        assert result.content == content
    else:
        assert agent_name in result.content
        assert content in result.content


# ── Property 5 策略 ──

# 生成合法的 NamedAgentConfig 实例
named_agent_config_st = st.builds(
    NamedAgentConfig,
    name=non_blank_str_st,
    description=non_blank_str_st,
    system_prompt=st.text(min_size=0, max_size=50),
    prompt_id=st.just("chat-default@v1"),
    tool_names=st.none(),
    model=st.none(),
)


# ---------------------------------------------------------------------------
# Property 5: DelegateToAgentTool description 包含所有已注册 Agent 名称
# Feature: agent-delegation-decoupling, Property 5:
# DelegateToAgentTool description includes registered agent names
# **Validates: Requirements 4.4, 7.5**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    configs=st.lists(named_agent_config_st, min_size=1, max_size=10, unique_by=lambda c: c.name),
)
def test_description_contains_all_registered_agent_names(
    configs: list[NamedAgentConfig],
) -> None:
    """验证 description 包含所有已注册 Agent 名称。

    **Validates: Requirements 4.4, 7.5**

    使用 hypothesis 生成任意非空 NamedAgentConfig 集合，注册到真实的
    AgentRegistryAdapter 中，创建 DelegateToAgentTool 后验证其 description
    属性包含每个已注册 Agent 的名称。
    """
    registry = AgentRegistryAdapter()
    for config in configs:
        registry.register(config)

    delegation = AsyncMock()

    tool = DelegateToAgentTool(
        agent_registry=registry,
        delegation=delegation,
    )

    description = tool.description
    for config in configs:
        assert config.name in description, (
            f"Agent 名称 '{config.name}' 未出现在 description 中: {description}"
        )


def test_description_indicates_no_agents_when_empty() -> None:
    """验证空注册表时 description 指示无可用 Agent。

    **Validates: Requirements 4.4, 7.5**

    创建空的 AgentRegistryAdapter，验证 DelegateToAgentTool 的 description
    包含"无可用 Agent"或等效的空状态提示。
    """
    registry = AgentRegistryAdapter()
    delegation = AsyncMock()

    tool = DelegateToAgentTool(
        agent_registry=registry,
        delegation=delegation,
    )

    description = tool.description
    assert "无可用" in description or "no agents are currently available" in description.lower(), (
        f"空注册表时 description 应指示无可用 Agent，实际: {description}"
    )
