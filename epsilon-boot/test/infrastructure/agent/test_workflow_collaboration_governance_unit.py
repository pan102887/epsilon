"""Workflow 协作治理工具单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from domain.agent.exceptions import DelegationDepthExceededError, HandoffPerformed
from domain.agent.value_objects import DelegationResult, HandoffResult
from domain.chat.context import ConversationContext
from domain.run.value_objects import RunEvent, RunEventType
from domain.run.workflow import CollaborationLimit, WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from infrastructure.agent.delegate_parallel_tool import DelegateParallelTool
from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool
from infrastructure.agent.handoff_context import reset_parent_context, set_parent_context
from infrastructure.agent.handoff_to_agent_tool import HandoffToAgentTool

pytestmark = pytest.mark.asyncio


class _Registry:
    def list_names(self) -> list[str]:
        return ["agent-a", "agent-b"]


class _Delegation:
    def __init__(self) -> None:
        self.delegate_calls: list[tuple[Any, ...]] = []
        self.parallel_calls: list[Any] = []
        self.handoff_calls: list[tuple[Any, ...]] = []
        self.delegate_result = DelegationResult("ok", True)
        self.parallel_results = [
            DelegationResult("ok-a", True),
            DelegationResult("bad-b", False),
        ]
        self.handoff_result = HandoffResult("agent-a", "handoff ok", True)

    async def delegate(
        self,
        agent_name: str,
        task_goal: str,
        input_data: dict[str, Any],
        delegation_depth: int,
        max_delegation_depth: int,
    ) -> DelegationResult:
        self.delegate_calls.append(
            (agent_name, task_goal, input_data, delegation_depth, max_delegation_depth)
        )
        return self.delegate_result

    async def delegate_parallel(
        self,
        requests,
        *,
        delegation_depth: int,
        max_delegation_depth: int,
    ):
        self.parallel_calls.append((requests, delegation_depth, max_delegation_depth))
        return self.parallel_results[: len(requests)]

    async def handoff(
        self,
        agent_name: str,
        messages,
        *,
        delegation_depth: int,
        max_delegation_depth: int,
    ) -> HandoffResult:
        self.handoff_calls.append((agent_name, messages, delegation_depth, max_delegation_depth))
        return self.handoff_result


class _EventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        self.events.append(event)
        return event


def _workflow_token(
    *,
    max_recursion_depth: int = 3,
    max_parallel_delegations: int = 3,
    max_handoff_count: int = 1,
    handoff_count: int = 0,
):
    return set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="run-1",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(
                max_recursion_depth=max_recursion_depth,
                max_parallel_delegations=max_parallel_delegations,
                max_handoff_count=max_handoff_count,
            ),
            depth=0,
            handoff_count=handoff_count,
            delegation_count=0,
        )
    )


def _parent_context_token():
    context = ConversationContext()
    context.add_user_message("hello")
    return set_parent_context(context)


async def test_no_workflow_context_keeps_delegate_parallel_and_handoff_behavior() -> None:
    events = _EventStore()
    delegation = _Delegation()
    delegate_tool = DelegateToAgentTool(_Registry(), delegation, event_store=events)
    parallel_tool = DelegateParallelTool(_Registry(), delegation, event_store=events)
    handoff_tool = HandoffToAgentTool(_Registry(), delegation, event_store=events)
    parent_token = _parent_context_token()
    try:
        delegate_result = await delegate_tool.execute(
            agent_name="agent-a",
            task_goal="do work",
        )
        parallel_result = await parallel_tool.execute(
            requests=[{"agent_name": "agent-a", "task_goal": "a"}]
        )
        with pytest.raises(HandoffPerformed):
            await handoff_tool.execute(agent_name="agent-a")
    finally:
        reset_parent_context(parent_token)

    assert delegate_result.content == "ok"
    assert "[✓] agent-a" in parallel_result.content
    assert len(delegation.delegate_calls) == 1
    assert len(delegation.parallel_calls) == 1
    assert len(delegation.handoff_calls) == 1
    assert events.events == []


async def test_delegate_depth_limit_uses_stricter_workflow_limit() -> None:
    events = _EventStore()
    delegation = _Delegation()
    tool = DelegateToAgentTool(
        _Registry(),
        delegation,
        current_delegation_depth=1,
        max_delegation_depth=3,
        event_store=events,
    )
    token = _workflow_token(max_recursion_depth=1)
    try:
        with pytest.raises(DelegationDepthExceededError):
            await tool.execute(agent_name="agent-a", task_goal="too deep")
    finally:
        reset_workflow_collaboration_context(token)

    assert delegation.delegate_calls == []
    assert events.events[-1].event_type is RunEventType.COLLABORATION_LIMIT_HIT
    assert events.events[-1].payload["reason"] == "delegation_depth_exceeded"


async def test_parallel_fanout_limit_does_not_call_delegation_port() -> None:
    events = _EventStore()
    delegation = _Delegation()
    tool = DelegateParallelTool(_Registry(), delegation, event_store=events)
    token = _workflow_token(max_parallel_delegations=1)
    try:
        result = await tool.execute(
            requests=[
                {"agent_name": "agent-a", "task_goal": "a"},
                {"agent_name": "agent-b", "task_goal": "b"},
            ]
        )
    finally:
        reset_workflow_collaboration_context(token)

    assert "并行委派数量超限" in result.content
    assert delegation.parallel_calls == []
    assert events.events[-1].event_type is RunEventType.COLLABORATION_LIMIT_HIT


async def test_handoff_depth_limit_uses_stricter_workflow_limit() -> None:
    events = _EventStore()
    delegation = _Delegation()
    tool = HandoffToAgentTool(
        _Registry(),
        delegation,
        current_delegation_depth=1,
        max_delegation_depth=3,
        event_store=events,
    )
    token = _workflow_token(max_recursion_depth=1)
    try:
        result = await tool.execute(agent_name="agent-a")
    finally:
        reset_workflow_collaboration_context(token)

    assert result.content == "无法 handoff 给 'agent-a': 委派深度超限 (1 → 2 > 1)"
    assert result.metadata == {"target_agent": "agent-a", "success": False}
    assert delegation.handoff_calls == []
    assert events.events[-1].event_type is RunEventType.COLLABORATION_LIMIT_HIT
    assert events.events[-1].payload["reason"] == "handoff_depth_exceeded"
    assert events.events[-1].payload["action"] == "handoff"
    assert events.events[-1].payload["depth"] == 2


async def test_handoff_count_limit_does_not_call_delegation_port() -> None:
    events = _EventStore()
    delegation = _Delegation()
    tool = HandoffToAgentTool(_Registry(), delegation, event_store=events)
    token = _workflow_token(max_handoff_count=1, handoff_count=1)
    try:
        result = await tool.execute(agent_name="agent-a")
    finally:
        reset_workflow_collaboration_context(token)

    assert result.content == "Cannot hand off to 'agent-a': handoff_count_exceeded:2>1"
    assert result.metadata == {"target_agent": "agent-a", "success": False}
    assert delegation.handoff_calls == []
    assert events.events[-1].event_type is RunEventType.COLLABORATION_LIMIT_HIT
    assert events.events[-1].payload["reason"] == "handoff_count_exceeded:2>1"
    assert events.events[-1].payload["action"] == "handoff"
    assert events.events[-1].payload["depth"] == 1


async def test_success_and_failure_steps_record_json_safe_payloads() -> None:
    events = _EventStore()
    delegation = _Delegation()
    delegation.delegate_result = DelegationResult("failed text", False)
    delegate_tool = DelegateToAgentTool(_Registry(), delegation, event_store=events)
    parallel_tool = DelegateParallelTool(_Registry(), delegation, event_store=events)
    token = _workflow_token()
    try:
        delegate_result = await delegate_tool.execute(
            agent_name="agent-a",
            task_goal="single",
        )
        parallel_result = await parallel_tool.execute(
            requests=[
                {"agent_name": "agent-a", "task_goal": "a"},
                {"agent_name": "agent-b", "task_goal": "b"},
            ]
        )
    finally:
        reset_workflow_collaboration_context(token)

    assert "执行失败" in delegate_result.content
    assert "[✗] agent-b" in parallel_result.content
    step_events = [
        event
        for event in events.events
        if event.event_type is RunEventType.COLLABORATION_STEP_RECORDED
    ]
    assert len(step_events) == 3
    for event in step_events:
        assert event.payload["run_id"] == "run-1"
        assert event.payload["phase"] == "execute"
        assert event.payload["action"] == "delegation"
        assert isinstance(event.payload["created_at"], str)


async def test_successful_handoff_records_step_before_signal() -> None:
    events = _EventStore()
    delegation = _Delegation()
    tool = HandoffToAgentTool(_Registry(), delegation, event_store=events)
    workflow_token = _workflow_token()
    parent_token = _parent_context_token()
    try:
        with pytest.raises(HandoffPerformed):
            await tool.execute(agent_name="agent-a")
    finally:
        reset_parent_context(parent_token)
        reset_workflow_collaboration_context(workflow_token)

    assert events.events[-1].event_type is RunEventType.COLLABORATION_STEP_RECORDED
    assert events.events[-1].payload["action"] == "handoff"
    assert events.events[-1].payload["target_agent"] == "agent-a"


async def test_successful_handoff_passes_policy_depths_to_delegation_port() -> None:
    events = _EventStore()
    delegation = _Delegation()
    tool = HandoffToAgentTool(
        _Registry(),
        delegation,
        current_delegation_depth=0,
        max_delegation_depth=5,
        event_store=events,
    )
    workflow_token = _workflow_token(max_recursion_depth=2)
    parent_token = _parent_context_token()
    try:
        with pytest.raises(HandoffPerformed):
            await tool.execute(agent_name="agent-a")
    finally:
        reset_parent_context(parent_token)
        reset_workflow_collaboration_context(workflow_token)

    assert len(delegation.handoff_calls) == 1
    _agent_name, _messages, delegation_depth, max_delegation_depth = delegation.handoff_calls[0]
    assert delegation_depth == 1
    assert max_delegation_depth == 2
