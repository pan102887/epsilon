"""WorkflowRunOrchestrator 单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import RunEventStorePort, WorkflowRegistryPort
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
    WorkflowPhase,
    WorkflowPhaseDefinition,
)
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _EventStore:
    """记录 workflow event 的 fake。"""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        """追加事件。"""

        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


class _Registry:
    """单 workflow registry fake。"""

    def __init__(self, workflow: WorkflowDefinition) -> None:
        self.workflow = workflow

    def require_definition(self, name: str) -> WorkflowDefinition:
        """返回测试 workflow。"""

        if name != self.workflow.name:
            raise KeyError(name)
        return self.workflow


class _Clock:
    """每次调用递增一秒的时钟。"""

    def __init__(self) -> None:
        self.current = _NOW

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + timedelta(seconds=1)
        return value


def _orchestrator(
    event_store: _EventStore,
    *,
    workflow: WorkflowDefinition | None = None,
) -> WorkflowRunOrchestrator:
    """构造测试编排器。"""

    return WorkflowRunOrchestrator(
        event_store=cast(RunEventStorePort, event_store),
        workflow_registry=cast(WorkflowRegistryPort, _Registry(workflow or _workflow())),
        workflow_serializer=WorkflowSerializerAdapter(),
        now=_Clock(),
    )


def _snapshot(
    *,
    phase: str | None = "plan",
    workflow_state: dict[str, Any] | None | object = ...,
) -> RunSnapshot:
    """构造 RunSnapshot。"""

    if workflow_state is ...:
        workflow_state = {
            "workflow_name": "code_change",
            "current_phase": phase,
            "phase_started_at": None,
            "phase_history": [],
            "phase_result_summary": None,
            "phase_error_summary": None,
            "revise_counts": {},
        }
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.RUNNING,
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": "hello"},
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
        workflow_name="code_change" if workflow_state is not None else None,
        workflow_run_state=(
            cast(dict[str, Any], workflow_state)
            if isinstance(workflow_state, dict)
            else None
        ),
    )


def _workflow(*, max_revise_per_phase: int = 1) -> WorkflowDefinition:
    """构造 code_change workflow。"""

    workflow = WorkflowDefinition(
        name="code_change",
        description="code change workflow",
        applicable=WorkflowApplicableCondition(),
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.REVISE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="planner"),
        ),
        roles=(
            AgentRoleCapability(role="planner"),
            AgentRoleCapability(role="executor"),
            AgentRoleCapability(role="reviewer"),
        ),
        collaboration_limit=CollaborationLimit(max_revise_per_phase=max_revise_per_phase),
        default_strategy_summary="default strategy",
    )
    workflow.validate()
    return workflow


def _outcome(
    status: RunStatus,
    *,
    terminal_reason: str | None = "completed",
    can_continue: bool = False,
    approval_id: str | None = None,
) -> RunExecutionOutcome:
    """构造执行 outcome。"""

    return RunExecutionOutcome(
        status=status,
        result={"kind": "chat"},
        error={"type": "Error", "message": "boom"} if status is RunStatus.FAILED else None,
        terminal_reason=terminal_reason,
        can_continue=can_continue,
        approval_id=approval_id,
        segment_metadata={"segment_count": 1},
    )


async def test_without_workflow_state_directly_executes_existing_path() -> None:
    """非 workflow Run 直通，不写 phase 事件。"""

    events = _EventStore()
    orchestrator = _orchestrator(events)
    calls = 0

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        nonlocal calls
        calls += 1
        assert snapshot.workflow_run_state is None
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(workflow_state=None),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.SUCCEEDED
    assert calls == 1
    assert events.events == []


async def test_successful_non_final_phase_pauses_and_advances_to_next_phase() -> None:
    """非最终 phase 成功后转 paused，等待下一次 continue 推进。"""

    events = _EventStore()
    orchestrator = _orchestrator(events)

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        assert snapshot.workflow_run_state is not None
        assert snapshot.workflow_run_state["phase_started_at"] == _NOW.isoformat()
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(phase="plan"),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.PAUSED
    assert outcome.can_continue is True
    assert outcome.terminal_reason == "workflow_phase_completed"
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["current_phase"] == "execute"
    assert outcome.workflow_run_state["phase_started_at"] is None
    assert outcome.workflow_run_state["phase_history"][0]["phase"] == "plan"
    assert [event.event_type for event in events.events] == [
        RunEventType.WORKFLOW_PHASE_STARTED,
        RunEventType.WORKFLOW_PHASE_COMPLETED,
        RunEventType.WORKFLOW_HANDOFF_RECORDED,
    ]
    assert events.events[0].payload["role"] == "planner"
    assert events.events[1].payload["status"] == "succeeded"
    assert events.events[2].payload["source_role"] == "planner"
    assert events.events[2].payload["target_role"] == "executor"


async def test_finalize_success_keeps_succeeded_status() -> None:
    """最终 phase 成功后 Run 保持 succeeded。"""

    events = _EventStore()
    orchestrator = _orchestrator(events)

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return _outcome(RunStatus.SUCCEEDED, terminal_reason="done")

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(phase="finalize"),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["current_phase"] == "finalize"
    assert outcome.workflow_run_state["phase_history"][0]["phase"] == "finalize"
    assert [event.event_type for event in events.events] == [
        RunEventType.WORKFLOW_PHASE_STARTED,
        RunEventType.WORKFLOW_PHASE_COMPLETED,
    ]


@pytest.mark.parametrize(
    ("status", "approval_id", "expected_event"),
    [
        (RunStatus.FAILED, None, RunEventType.WORKFLOW_PHASE_FAILED),
        (RunStatus.PAUSED, None, RunEventType.WORKFLOW_PHASE_COMPLETED),
        (
            RunStatus.AWAITING_APPROVAL,
            "approval-1",
            RunEventType.WORKFLOW_PHASE_COMPLETED,
        ),
    ],
)
async def test_failed_paused_and_awaiting_approval_preserve_status(
    status: RunStatus,
    approval_id: str | None,
    expected_event: RunEventType,
) -> None:
    """失败、暂停和审批等待保持既有 outcome 语义并补充 state。"""

    events = _EventStore()
    orchestrator = _orchestrator(events)

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return _outcome(status, can_continue=True, approval_id=approval_id)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(phase="execute"),
        execute_existing=execute,
    )

    assert outcome.status is status
    assert outcome.can_continue is True
    assert outcome.approval_id == approval_id
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["current_phase"] == "execute"
    assert outcome.workflow_run_state["phase_started_at"] == _NOW.isoformat()
    assert [event.event_type for event in events.events] == [
        RunEventType.WORKFLOW_PHASE_STARTED,
        expected_event,
    ]


async def test_revise_limit_hit_fails_without_calling_existing_path() -> None:
    """revise 次数达到上限时直接失败并记录 limit hit。"""

    events = _EventStore()
    orchestrator = _orchestrator(events, workflow=_workflow(max_revise_per_phase=1))

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        raise AssertionError("execute_existing must not be called")

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            phase="revise",
            workflow_state={
                "workflow_name": "code_change",
                "current_phase": "revise",
                "phase_started_at": None,
                "phase_history": [],
                "phase_result_summary": None,
                "phase_error_summary": None,
                "revise_counts": {"revise": 1},
            },
        ),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.FAILED
    assert outcome.terminal_reason == "workflow_collaboration_limit_hit"
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["phase_error_summary"]["status"] == "failed"
    assert [event.event_type for event in events.events] == [
        RunEventType.COLLABORATION_LIMIT_HIT,
        RunEventType.WORKFLOW_PHASE_FAILED,
    ]
    assert events.events[0].payload["phase"] == "revise"
