"""长任务运行时收敛 P2 轻量集成回归。

本文件不启动 FastAPI、Redis 或真实模型；只用应用层 orchestrator 与领域
workflow 定义验证 P2 workflow handoff / review / revise 约束可作为运行时事实
写入事件和 workflow_run_state。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from domain.run.outcome import RunExecutionOutcome
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from domain.run.workflow import (
    AgentRoleCapability,
    CollaborationLimit,
    WorkflowApplicableCondition,
    WorkflowDefinition,
    WorkflowExecutionPolicy,
    WorkflowPhase,
    WorkflowPhaseDefinition,
)
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 13, tzinfo=UTC)


class _EventStore:
    """记录 Run 事件的轻量 fake。"""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
    ) -> RunEvent:
        """追加事件并返回带 cursor 的 RunEvent。"""

        event = RunEvent(run_id, len(self.events) + 1, event_type, payload, _NOW)
        self.events.append(event)
        return event


class _Registry:
    """固定 workflow registry。"""

    def __init__(self, workflow: WorkflowDefinition) -> None:
        self.workflow = workflow

    def require_definition(self, name: str) -> WorkflowDefinition:
        """返回测试 workflow。"""

        if name != self.workflow.name:
            raise KeyError(name)
        return self.workflow


class _Clock:
    """递增测试时钟。"""

    def __init__(self) -> None:
        self.current = _NOW

    def __call__(self) -> datetime:
        """返回当前时间并前进一秒。"""

        value = self.current
        self.current += timedelta(seconds=1)
        return value


async def test_p2_child_run_link_waiting_and_reconciliation_events_are_conservative() -> None:
    """child run 开启后父 Run 先链接并等待，不假定子流程已成功。"""

    workflow = _workflow(WorkflowExecutionPolicy(child_run_enabled=True))
    events = _EventStore()
    orchestrator = WorkflowRunOrchestrator(
        event_store=events,
        workflow_registry=_Registry(workflow),
        workflow_serializer=WorkflowSerializerAdapter(),
        now=_Clock(),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        raise AssertionError("waiting child run should not execute parent phase")

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot("execute", active_role="executor", child_run_id="child-p2"),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.PAUSED
    assert outcome.terminal_reason == "child_run_waiting"
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["child_run_state"]["reconciliation_status"] == "waiting"
    assert [event.event_type for event in events.events] == [
        RunEventType.CHILD_RUN_LINKED,
        RunEventType.CHILD_RUN_WAITING,
    ]
    assert all(event.payload["child_run_id"] == "child-p2" for event in events.events)


async def test_p2_handoff_event_and_review_revise_policy_state() -> None:
    """handoff 事件、review 路由和 revise 路由应来自 orchestrator 事实源。"""

    workflow = _workflow(
        WorkflowExecutionPolicy(
            phase_handoff_required={"execute": "reviewer"},
            review_required_phases=frozenset({"execute"}),
            revise_target_phase={"evaluate": "revise"},
        )
    )
    events = _EventStore()
    orchestrator = WorkflowRunOrchestrator(
        event_store=events,
        workflow_registry=_Registry(workflow),
        workflow_serializer=WorkflowSerializerAdapter(),
        now=_Clock(),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return RunExecutionOutcome(
            status=RunStatus.SUCCEEDED,
            result={"kind": "chat"},
            terminal_reason="completed",
            segment_metadata={"segment_count": 1},
        )

    execute_outcome = await orchestrator.execute_phase(
        snapshot=_snapshot("execute", active_role="executor"),
        execute_existing=execute,
    )
    evaluate_outcome = await orchestrator.execute_phase(
        snapshot=_snapshot("evaluate", active_role="reviewer"),
        execute_existing=execute,
    )

    handoff_events = [
        event
        for event in events.events
        if event.event_type is RunEventType.WORKFLOW_HANDOFF_RECORDED
    ]
    assert execute_outcome.workflow_run_state is not None
    assert execute_outcome.workflow_run_state["current_phase"] == "evaluate"
    assert execute_outcome.workflow_run_state["handoff_state"]["target_role"] == "reviewer"
    assert handoff_events[0].payload["reason"] == "phase_handoff_required"
    assert evaluate_outcome.workflow_run_state is not None
    assert evaluate_outcome.workflow_run_state["current_phase"] == "revise"


def _workflow(policy: WorkflowExecutionPolicy) -> WorkflowDefinition:
    """构造带 review/revise 策略的 workflow。"""

    workflow = WorkflowDefinition(
        name="code_change",
        description="code change workflow",
        applicable=WorkflowApplicableCondition(),
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.REVISE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
        ),
        roles=(
            AgentRoleCapability("planner"),
            AgentRoleCapability("executor"),
            AgentRoleCapability("reviewer"),
        ),
        collaboration_limit=CollaborationLimit(),
        execution_policy=policy,
        default_strategy_summary="default strategy",
    )
    workflow.validate()
    return workflow


def _snapshot(
    phase: str,
    *,
    active_role: str,
    child_run_id: str | None = None,
) -> RunSnapshot:
    """构造 workflow Run 快照。"""

    workflow_state = {
        "workflow_name": "code_change",
        "current_phase": phase,
        "phase_started_at": None,
        "phase_history": [],
        "phase_result_summary": None,
        "phase_error_summary": None,
        "revise_counts": {},
        "active_role": active_role,
    }
    if child_run_id is not None:
        workflow_state["request_child_run"] = True
        workflow_state["child_run_id"] = child_run_id

    return RunSnapshot(
        run_id="run-p2",
        kind=RunKind.CHAT,
        status=RunStatus.RUNNING,
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-p2",
            chat={"message": "run workflow"},
            model="model-a",
        ),
        client_request_id=None,
        payload_hash=None,
        result=None,
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_name="code_change",
        workflow_run_state=workflow_state,
    )
