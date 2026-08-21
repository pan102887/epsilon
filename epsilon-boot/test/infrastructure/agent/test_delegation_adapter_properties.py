"""DelegationAdapter 属性测试模块。

验证 DelegationAdapter 的核心行为属性：
- Property 2: DelegationAdapter 正确转换 TaskResult 为 DelegationResult
- Property 3: DelegationAdapter 对未注册 Agent 抛出 AgentNotFoundError
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.exceptions import AgentNotFoundError
from domain.agent.value_objects import NamedAgentConfig
from domain.task.value_objects import TaskResult, TaskStatus
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegation_adapter import DelegationAdapter

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

# TaskResult 策略（仅 SUCCESS 和 FAILED 状态）
task_result_st = st.builds(
    TaskResult,
    content=st.text(min_size=0, max_size=100),
    status=st.sampled_from([TaskStatus.SUCCESS, TaskStatus.FAILED]),
    model=st.text(min_size=1, max_size=20),
    prompt_id=st.just("task-template@v1"),
)


# ---------------------------------------------------------------------------
# Property 2: DelegationAdapter 正确转换 TaskResult 为 DelegationResult
# Feature: agent-delegation-decoupling, Property 2:
# DelegationAdapter TaskResult to DelegationResult transformation
# **Validates: Requirements 3.3, 7.4**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    config=named_agent_config_st,
    task_goal=non_blank_str_st,
    task_result=task_result_st,
    input_data=st.none()
    | st.dictionaries(
        keys=st.text(min_size=1, max_size=10),
        values=st.text(max_size=20),
        max_size=3,
    ),
)
@pytest.mark.asyncio
async def test_delegation_adapter_converts_task_result_to_delegation_result(
    config: NamedAgentConfig,
    task_goal: str,
    task_result: TaskResult,
    input_data: dict[str, Any] | None,
) -> None:
    """验证 DelegationAdapter 正确将 TaskResult 转换为 DelegationResult。

    生成随机已注册 NamedAgentConfig 和任意 TaskResult（SUCCESS/FAILED），
    Mock TaskAgentPort.execute 返回该 TaskResult，调用 delegate() 后验证：
    - result.content == task_result.content
    - result.success == (task_result.status == TaskStatus.SUCCESS)
    """
    # 1. 创建 AgentRegistryAdapter 并注册生成的配置
    registry = AgentRegistryAdapter()
    registry.register(config)

    # 2. 创建 AsyncMock 的 TaskAgentPort，返回生成的 TaskResult
    task_agent = AsyncMock()
    task_agent.execute = AsyncMock(return_value=task_result)

    # 3. 创建 DelegationAdapter
    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=task_agent,
    )

    # 4. 调用 delegate()
    result = await adapter.delegate(
        agent_name=config.name,
        task_goal=task_goal,
        input_data=input_data,
    )

    # 5. 验证 DelegationResult 字段与 TaskResult 的映射关系
    assert result.content == task_result.content
    assert result.success == (task_result.status == TaskStatus.SUCCESS)


# ---------------------------------------------------------------------------
# Property 3: DelegationAdapter 对未注册 Agent 抛出 AgentNotFoundError
# Feature: agent-delegation-decoupling, Property 3:
# DelegationAdapter raises AgentNotFoundError for unregistered agents
# **Validates: Requirements 3.4**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    registered_configs=st.lists(named_agent_config_st, min_size=0, max_size=5),
    unregistered_name=non_blank_str_st,
    task_goal=non_blank_str_st,
)
@pytest.mark.asyncio
async def test_delegation_adapter_raises_agent_not_found_for_unregistered_name(
    registered_configs: list[NamedAgentConfig],
    unregistered_name: str,
    task_goal: str,
) -> None:
    """验证 DelegationAdapter 对未注册 Agent 抛出 AgentNotFoundError。

    生成任意已注册 Agent 配置集合和一个不在其中的 agent_name，
    调用 delegate() 后验证：
    - 抛出 AgentNotFoundError
    - 异常的 agent_name 属性等于传入的未注册名称
    """
    # 1. 创建 AgentRegistryAdapter 并注册生成的配置
    registry = AgentRegistryAdapter()
    registered_names: set[str] = set()
    for config in registered_configs:
        registry.register(config)
        registered_names.add(config.name)

    # 2. 确保 unregistered_name 不在已注册名称中（过滤掉碰撞情况）
    if unregistered_name in registered_names:
        return  # 跳过此用例，名称碰巧已注册

    # 3. 创建 DelegationAdapter（task_agent 不会被调用，使用 AsyncMock）
    task_agent = AsyncMock()
    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=task_agent,
    )

    # 4. 调用 delegate() 并验证抛出 AgentNotFoundError
    with pytest.raises(AgentNotFoundError) as exc_info:
        await adapter.delegate(
            agent_name=unregistered_name,
            task_goal=task_goal,
        )

    # 5. 验证异常的 agent_name 属性
    assert exc_info.value.agent_name == unregistered_name
