"""DelegationAdapter.delegate_parallel 属性测试。

通过 hypothesis 生成 1~6 条混合成功 / 失败的 ``DelegationRequest`` 组合，
验证不变量：

- 输出长度等于输入；
- 输出顺序与输入一一对应；
- 失败条目不影响其余条目执行结果（错误隔离）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.value_objects import (
    DelegationRequest,
    NamedAgentConfig,
)
from domain.task.value_objects import Task, TaskResult, TaskStatus
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegation_adapter import DelegationAdapter

# 非空白字符串
_NON_BLANK = st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != "")

# 单条 request 状态：success / fail / not_registered
_REQUEST_STATUS = st.sampled_from(["success", "fail", "not_registered"])


_REGISTERED_AGENT_NAMES = ["alpha", "bravo", "charlie", "delta"]


@settings(max_examples=50, deadline=2000)
@given(
    statuses=st.lists(_REQUEST_STATUS, min_size=1, max_size=6),
    base_goal=_NON_BLANK,
)
@pytest.mark.asyncio
async def test_delegate_parallel_preserves_order_and_isolates_failures(
    statuses: list[str], base_goal: str
) -> None:
    """validate: 顺序保持 + 错误隔离不变量。"""
    registry = AgentRegistryAdapter()
    for n in _REGISTERED_AGENT_NAMES:
        registry.register(
            NamedAgentConfig(
                name=n,
                description=f"agent {n}",
                system_prompt=f"你是 {n}",
                prompt_id="chat-default@v1",
            )
        )

    # 构造 requests 与对应 expected_success 列表
    expected_success: list[bool] = []
    requests: list[DelegationRequest] = []
    for i, st_ in enumerate(statuses):
        if st_ == "not_registered":
            requests.append(
                DelegationRequest(
                    agent_name=f"ghost_{i}",
                    task_goal=f"{base_goal}-{i}",
                )
            )
            expected_success.append(False)
        else:
            requests.append(
                DelegationRequest(
                    agent_name=_REGISTERED_AGENT_NAMES[i % len(_REGISTERED_AGENT_NAMES)],
                    task_goal=f"{base_goal}-{i}-{st_}",
                )
            )
            expected_success.append(st_ == "success")

    task_agent = AsyncMock()

    async def _execute(task: Task) -> TaskResult:
        if "fail" in task.goal:
            raise RuntimeError("simulated runtime failure")
        return TaskResult(
            content=f"reply-{task.goal}",
            status=TaskStatus.SUCCESS,
            model="m",
            prompt_id="task-template@v1",
        )

    task_agent.execute = AsyncMock(side_effect=_execute)

    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent)
    results = await adapter.delegate_parallel(requests)

    # 不变量 1：长度相等
    assert len(results) == len(requests)

    # 不变量 2：顺序一一对应（成功条目内容包含 task_goal）
    for req, expected, res in zip(requests, expected_success, results, strict=True):
        assert res.success is expected
        if expected:
            assert req.task_goal in res.content
