"""DelegationAdapter handoff / delegate_parallel 单元测试。

覆盖 Spec A 新增能力：

- ``DelegationAdapter.handoff(...)`` —— Agent 控制转移。
- ``DelegationAdapter.delegate_parallel(...)`` —— 并行扇出 + 错误隔离。

原有 ``delegate(...)`` 行为由 ``test_delegation_adapter_properties.py`` 覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import (
    AgentNotFoundError,
    DelegationDepthExceededError,
)
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    DelegationRequest,
    HandoffResult,
    NamedAgentConfig,
)
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    UserMessage,
)
from domain.model_access.ports import ModelAccessPort
from domain.run.runtime_context import (
    RunExecutionContext,
    reset_run_execution_context,
    set_run_execution_context,
)
from domain.run.value_objects import RunEvent, RunEventType
from domain.run.workflow import AgentRoleCapability, CollaborationLimit, WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from domain.task.value_objects import Task, TaskResult, TaskStatus
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegation_adapter import DelegationAdapter


class _EventStore:
    """记录 role capability 拒绝事件的 fake。"""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, object]
    ) -> RunEvent:
        """追加事件。"""

        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        self.events.append(event)
        return event


def _make_named_config(
    name: str,
    *,
    tool_names: frozenset[str] | None = None,
    model: str | None = None,
) -> NamedAgentConfig:
    return NamedAgentConfig(
        name=name,
        description=f"agent {name}",
        system_prompt=f"你是 {name}",
        prompt_id="chat-default@v1",
        tool_names=tool_names,
        model=model,
    )


# ---------------------------------------------------------------------------
# role capability runtime enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_denied_by_workflow_role_capability_before_task_execution() -> None:
    """真实 delegate adapter 路径应在调用 TaskAgentPort 前执行 capability 拒绝。"""

    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("reviewer"))
    task_agent = AsyncMock()
    task_agent.execute = AsyncMock()
    events = _EventStore()
    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent, event_store=events)
    run_token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    workflow_token = set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="run-1",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(),
            depth=0,
            handoff_count=0,
            delegation_count=0,
            role_capability_enabled=True,
            roles=(AgentRoleCapability("executor"),),
        )
    )
    try:
        result = await adapter.delegate("reviewer", "review this")
    finally:
        reset_workflow_collaboration_context(workflow_token)
        reset_run_execution_context(run_token)

    assert result.success is False
    assert "role capability rejected" in result.content
    task_agent.execute.assert_not_awaited()
    assert events.events[0].payload["action"] == "delegation"
    assert events.events[0].payload["target"] == "reviewer"


@pytest.mark.asyncio
async def test_handoff_denied_by_workflow_role_capability_before_control_transfer() -> None:
    """真实 handoff adapter 路径应在目标 Agent 接管前执行 capability 拒绝。"""

    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("reviewer"))
    task_agent = AsyncMock()
    agent_provider = AsyncMock()
    tool_registry_provider = AsyncMock()
    model_registry = MagicMock()
    events = _EventStore()
    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=task_agent,
        model_registry=model_registry,
        agent_provider=agent_provider,
        tool_registry_provider=tool_registry_provider,
        event_store=events,
    )
    run_token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    workflow_token = set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="run-1",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(),
            depth=0,
            handoff_count=0,
            delegation_count=0,
            role_capability_enabled=True,
            roles=(AgentRoleCapability("executor"),),
        )
    )
    try:
        result = await adapter.handoff("reviewer", [])
    finally:
        reset_workflow_collaboration_context(workflow_token)
        reset_run_execution_context(run_token)

    assert result.success is False
    assert "role capability rejected" in result.content
    agent_provider.assert_not_awaited()
    tool_registry_provider.assert_not_awaited()
    assert events.events[0].payload["action"] == "handoff"
    assert events.events[0].payload["target"] == "reviewer"


@pytest.mark.asyncio
async def test_handoff_uses_configured_child_agent_max_rounds() -> None:
    """handoff 子 Agent 应使用构造期配置的 max_rounds，而不是固定 10。"""

    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("reviewer", model="model-a"))
    task_agent = AsyncMock()
    model_registry = MagicMock()
    model_access = MagicMock()
    model_registry.get_adapter_for_model.return_value = model_access
    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = []
    child_agent = AsyncMock()
    child_agent.run = AsyncMock(
        return_value=AgentResult(
            content="handoff done",
            model="model-a",
            usage={},
        )
    )
    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=task_agent,
        model_registry=model_registry,
        agent_provider=AsyncMock(return_value=child_agent),
        tool_registry_provider=AsyncMock(return_value=tool_registry),
        handoff_max_rounds=1234,
    )

    result = await adapter.handoff("reviewer", [UserMessage(content="please review")])

    assert result.success is True
    child_config = child_agent.run.await_args.args[1]
    assert child_config.max_rounds == 1234
    assert child_config.max_rounds != 10


# ---------------------------------------------------------------------------
# delegate_parallel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_parallel_preserves_input_order_when_all_succeed() -> None:
    """所有子委派成功时，结果列表顺序与 ``requests`` 一致（R3.1）。"""
    registry = AgentRegistryAdapter()
    for name in ("a1", "a2", "a3"):
        registry.register(_make_named_config(name))

    task_agent = AsyncMock()

    # 让结果可与 agent_name 关联：把 agent_name 编码到 TaskResult.content
    async def _delegate_side_effect(task: Task) -> TaskResult:
        # task.goal 形如 "goal-a2"，借此识别
        return TaskResult(
            content=f"reply for {task.goal}",
            status=TaskStatus.SUCCESS,
            model="m",
            prompt_id="task-template@v1",
        )

    task_agent.execute = AsyncMock(side_effect=_delegate_side_effect)

    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent)

    requests = [
        DelegationRequest(agent_name="a1", task_goal="goal-a1"),
        DelegationRequest(agent_name="a2", task_goal="goal-a2"),
        DelegationRequest(agent_name="a3", task_goal="goal-a3"),
    ]
    results = await adapter.delegate_parallel(requests)

    assert len(results) == 3
    assert all(r.success for r in results)
    assert results[0].content == "reply for goal-a1"
    assert results[1].content == "reply for goal-a2"
    assert results[2].content == "reply for goal-a3"


@pytest.mark.asyncio
async def test_delegate_parallel_isolates_unregistered_agent_failure() -> None:
    """未注册 Agent 只影响对应位置 ``success=False``，其余继续（R3.2）。"""
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("real"))

    task_agent = AsyncMock()
    task_agent.execute = AsyncMock(
        return_value=TaskResult(
            content="ok",
            status=TaskStatus.SUCCESS,
            model="m",
            prompt_id="task-template@v1",
        )
    )

    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent)

    requests = [
        DelegationRequest(agent_name="real", task_goal="g1"),
        DelegationRequest(agent_name="ghost", task_goal="g2"),
        DelegationRequest(agent_name="real", task_goal="g3"),
    ]
    results = await adapter.delegate_parallel(requests)

    assert len(results) == 3
    assert results[0].success is True
    assert results[1].success is False
    assert "ghost" in results[1].content  # AgentNotFoundError 信息保留 agent 名
    assert results[2].success is True


@pytest.mark.asyncio
async def test_delegate_parallel_isolates_runtime_failure() -> None:
    """子任务运行期异常 → 对应位置 success=False，不抛 / 不中断其余（R3.2）。"""
    registry = AgentRegistryAdapter()
    for name in ("a1", "a2"):
        registry.register(_make_named_config(name))

    task_agent = AsyncMock()
    call_count = {"n": 0}

    async def _execute(task: Task) -> TaskResult:
        call_count["n"] += 1
        if task.goal == "boom":
            raise RuntimeError("internal failure")
        return TaskResult(
            content=f"ok-{task.goal}",
            status=TaskStatus.SUCCESS,
            model="m",
            prompt_id="task-template@v1",
        )

    task_agent.execute = AsyncMock(side_effect=_execute)

    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent)
    results = await adapter.delegate_parallel(
        [
            DelegationRequest(agent_name="a1", task_goal="ok"),
            DelegationRequest(agent_name="a2", task_goal="boom"),
        ]
    )

    assert results[0].success is True
    assert results[0].content == "ok-ok"
    assert results[1].success is False
    assert "internal failure" in results[1].content
    assert call_count["n"] == 2  # 第二条仍被执行（fail 不中断）


@pytest.mark.asyncio
async def test_delegate_parallel_returns_failure_when_depth_exceeded() -> None:
    """单条 request 深度超限 → success=False 字符串，不抛（R3.3）。

    语义：``delegation_depth`` 入参为"子 Agent 实际执行深度"（由
    ``DelegateParallelTool.execute`` 传入 ``next_depth = current+1``）；
    当该深度 > ``max_delegation_depth`` 时阻断。
    """
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("a1"))

    task_agent = AsyncMock()
    task_agent.execute = AsyncMock()

    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent)
    results = await adapter.delegate_parallel(
        [DelegationRequest(agent_name="a1", task_goal="g")],
        delegation_depth=4,
        max_delegation_depth=3,
    )

    assert results[0].success is False
    assert "委派深度超限" in results[0].content
    task_agent.execute.assert_not_called()


@pytest.mark.asyncio
async def test_delegate_parallel_empty_returns_empty_list() -> None:
    """空 requests → 空列表，不调用 task_agent。"""
    registry = AgentRegistryAdapter()
    task_agent = AsyncMock()
    task_agent.execute = AsyncMock()

    adapter = DelegationAdapter(agent_registry=registry, task_agent=task_agent)
    results = await adapter.delegate_parallel([])

    assert results == []
    task_agent.execute.assert_not_called()


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_calls_agent_run_with_cloned_context() -> None:
    """``handoff`` 把父消息克隆到子 ConversationContext 后调 AgentPort.run。"""
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("specialist", model="gpt-4o"))

    task_agent = AsyncMock()  # handoff 路径不会调 task_agent
    model_access = MagicMock()
    model_registry = MagicMock()
    model_registry.get_default_model = MagicMock(return_value="gpt-4o")
    model_registry.get_adapter_for_model = MagicMock(return_value=model_access)

    tool_registry = MagicMock()
    tool_registry.get_schemas = MagicMock(return_value=[])

    captured_ctx: list[ConversationContext] = []

    agent = MagicMock()

    async def _run(
        ctx: ConversationContext, cfg: AgentConfig, ma: ModelAccessPort
    ) -> AgentResult:
        captured_ctx.append(ctx)
        return AgentResult(
            content="specialist 的最终回答",
            model="gpt-4o",
            usage={"total_tokens": 42},
            terminated_reason="completed",
        )

    agent.run = _run

    async def _agent_provider():
        return agent

    async def _tool_registry_provider():
        return tool_registry

    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=task_agent,
        model_registry=model_registry,
        agent_provider=_agent_provider,
        tool_registry_provider=_tool_registry_provider,
    )

    parent_messages: list[BaseMessage] = [
        SystemMessage(content="父系统提示"),
        UserMessage(content="请帮我处理"),
        AssistantMessage(content="收到", tool_calls=[]),
    ]
    result = await adapter.handoff(
        agent_name="specialist",
        context_messages=parent_messages,
        delegation_depth=0,
        max_delegation_depth=3,
    )

    # 1. 子上下文消息按顺序克隆
    assert len(captured_ctx) == 1
    sub_ctx = captured_ctx[0]
    cloned = sub_ctx.get_messages()
    assert len(cloned) == 3
    # 原引用应在克隆后保留（只浅拷贝列表，不深拷贝消息）
    for src, dst in zip(parent_messages, cloned, strict=True):
        assert src is dst

    # 2. HandoffResult 字段正确翻译
    assert isinstance(result, HandoffResult)
    assert result.target_agent == "specialist"
    assert result.content == "specialist 的最终回答"
    assert result.success is True
    assert result.usage == {"total_tokens": 42}
    assert result.model == "gpt-4o"

    # 3. 不触碰 task_agent
    task_agent.execute.assert_not_called()


@pytest.mark.asyncio
async def test_handoff_raises_when_depth_exceeded() -> None:
    """``delegation_depth + 1 > max`` 抛 ``DelegationDepthExceededError``（R1.4）。"""
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("specialist"))

    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=AsyncMock(),
        model_registry=MagicMock(),
        agent_provider=AsyncMock(),
        tool_registry_provider=AsyncMock(),
    )

    with pytest.raises(DelegationDepthExceededError):
        await adapter.handoff(
            agent_name="specialist",
            context_messages=[],
            delegation_depth=3,
            max_delegation_depth=3,
        )


@pytest.mark.asyncio
async def test_handoff_raises_agent_not_found_for_unregistered() -> None:
    """未注册目标 Agent → ``AgentNotFoundError``。"""
    adapter = DelegationAdapter(
        agent_registry=AgentRegistryAdapter(),
        task_agent=AsyncMock(),
        model_registry=MagicMock(),
        agent_provider=AsyncMock(),
        tool_registry_provider=AsyncMock(),
    )
    with pytest.raises(AgentNotFoundError):
        await adapter.handoff(
            agent_name="ghost",
            context_messages=[],
        )


@pytest.mark.asyncio
async def test_handoff_returns_failure_when_sub_agent_raises() -> None:
    """子 Agent.run 抛异常 → ``HandoffResult(success=False)``。"""
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("specialist"))

    model_access = MagicMock()
    model_registry = MagicMock()
    model_registry.get_default_model = MagicMock(return_value="m")
    model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
    tool_registry = MagicMock()
    tool_registry.get_schemas = MagicMock(return_value=[])

    agent = MagicMock()

    async def _broken_run(
        ctx: ConversationContext, cfg: AgentConfig, ma: ModelAccessPort
    ) -> AgentResult:
        raise RuntimeError("LLM down")

    agent.run = _broken_run

    async def _agent_provider():
        return agent

    async def _tool_registry_provider():
        return tool_registry

    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=AsyncMock(),
        model_registry=model_registry,
        agent_provider=_agent_provider,
        tool_registry_provider=_tool_registry_provider,
    )

    result = await adapter.handoff(
        agent_name="specialist",
        context_messages=[],
    )
    assert isinstance(result, HandoffResult)
    assert result.success is False
    assert "LLM down" in result.content


@pytest.mark.asyncio
async def test_handoff_propagates_terminated_reason_max_rounds_as_unsuccess() -> None:
    """子 Agent 因 ``max_rounds`` 终止 → ``HandoffResult.success=False``。"""
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("specialist"))

    model_access = MagicMock()
    model_registry = MagicMock()
    model_registry.get_default_model = MagicMock(return_value="m")
    model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
    tool_registry = MagicMock()
    tool_registry.get_schemas = MagicMock(return_value=[])

    agent = MagicMock()

    async def _run(
        ctx: ConversationContext, cfg: AgentConfig, ma: ModelAccessPort
    ) -> AgentResult:
        return AgentResult(
            content="",
            model="m",
            usage={"total_tokens": 1},
            terminated_reason="max_rounds",
        )

    agent.run = _run

    async def _agent_provider():
        return agent

    async def _tool_registry_provider():
        return tool_registry

    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=AsyncMock(),
        model_registry=model_registry,
        agent_provider=_agent_provider,
        tool_registry_provider=_tool_registry_provider,
    )

    result = await adapter.handoff(
        agent_name="specialist",
        context_messages=[],
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_handoff_raises_runtime_error_when_required_dependencies_missing() -> None:
    """构造期未注入 model_registry / providers → ``handoff`` 抛 RuntimeError。"""
    registry = AgentRegistryAdapter()
    registry.register(_make_named_config("specialist"))

    adapter = DelegationAdapter(
        agent_registry=registry,
        task_agent=AsyncMock(),
        # 不注入 model_registry / agent_provider / tool_registry_provider
    )
    with pytest.raises(RuntimeError, match=r"DelegationAdapter\.handoff 不可用"):
        await adapter.handoff(
            agent_name="specialist",
            context_messages=[],
        )
