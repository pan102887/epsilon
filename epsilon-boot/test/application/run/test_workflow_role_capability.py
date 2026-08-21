"""Workflow role capability 应用层治理测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from domain.agent.ports import ApprovalStateStorePort
from domain.agent.value_objects import ApprovalInterrupt
from domain.chat.context import ConversationContext
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import (
    RunCheckpointSinkPort,
    RunEventStorePort,
    RunStorePort,
    WorkflowRegistryPort,
)
from domain.run.value_objects import (
    CheckpointPhase,
    DurableCheckpoint,
    RunCreateRequest,
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

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _EventStore:
    """记录 Run 事件的 fake store。"""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
    ) -> RunEvent:
        """追加事件并分配测试 cursor。"""

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
        """按名称返回测试 workflow。"""

        if name != self.workflow.name:
            raise KeyError(name)
        return self.workflow


class _RunStore:
    """记录 child Run 创建与查询的 fake RunStore。"""

    def __init__(self, *, child: RunSnapshot | None = None) -> None:
        self.child = child
        self.created: list[RunCreateRequest] = []

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        """按 run_id 返回 child 快照。"""

        if self.child is not None and self.child.run_id == run_id:
            return self.child
        return None

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """记录 child Run 创建请求并返回真实 child 快照。"""

        self.created.append(request)
        self.child = replace(
            _snapshot(workflow_state=request.workflow_run_state or _state()),
            run_id="created-child-run",
            payload=request.payload,
            workflow_run_state=request.workflow_run_state,
            workflow_name=request.workflow_name,
            status=RunStatus.QUEUED,
        )
        return self.child


class _CheckpointSink:
    """记录 checkpoint 保存调用的 fake sink。"""

    def __init__(self) -> None:
        self.segment_done_calls: list[dict[str, Any]] = []

    async def segment_done(self, **kwargs: Any) -> DurableCheckpoint:
        """记录 segment_done checkpoint。"""

        self.segment_done_calls.append(kwargs)
        return DurableCheckpoint(
            run_id="run-1",
            checkpoint_id="chk-child-waiting",
            sequence=1,
            phase=CheckpointPhase.SEGMENT_DONE,
            context_snapshot=ConversationContext().to_dict(),
            round_num=None,
            usage=kwargs.get("usage", {}),
            trace_summary={},
            segment_metadata=kwargs.get("segment_metadata", {}),
            tool_execution_key=None,
            tool_result_ref=None,
            schema_version=1,
            sanitized=False,
            truncated_fields=(),
            created_at=_NOW,
        )


class _ApprovalStore:
    """记录审批中断的 fake store。"""

    def __init__(self) -> None:
        self.saved: list[ApprovalInterrupt] = []

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """保存审批中断。"""

        self.saved.append(interrupt)


class _Clock:
    """每次调用递增一秒的时钟。"""

    def __init__(self) -> None:
        self.current = _NOW

    def __call__(self) -> datetime:
        """返回当前测试时间并递增。"""

        value = self.current
        self.current = self.current + timedelta(seconds=1)
        return value


async def test_capability_disabled_keeps_compatible_execution_path() -> None:
    """关闭 role capability 开关时不得额外拒绝或生成审批。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(role_capability_enabled=False),
    )
    called = False

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        nonlocal called
        called = True
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                pending_capability_action="tool",
                pending_tool_name="shell_exec",
            )
        ),
        execute_existing=execute,
    )

    assert called is True
    assert outcome.status in {RunStatus.SUCCEEDED, RunStatus.PAUSED}
    assert approvals.saved == []
    assert RunEventType.ROLE_CAPABILITY_REJECTED not in [
        event.event_type for event in events.events
    ]


async def test_capability_enabled_rejects_undeclared_tool_before_execution() -> None:
    """开启 role capability 后，未声明工具应先写拒绝事件并进入既有审批等待。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(role_capability_enabled=True),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        raise AssertionError("越权工具不应进入真实执行")

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                pending_capability_action="tool",
                pending_tool_name="shell_exec",
            )
        ),
        execute_existing=execute,
    )

    rejected = [
        event
        for event in events.events
        if event.event_type is RunEventType.ROLE_CAPABILITY_REJECTED
    ]
    assert outcome.status is RunStatus.AWAITING_APPROVAL
    assert outcome.approval_id is not None
    assert approvals.saved[0].approval_id == outcome.approval_id
    assert approvals.saved[0].metadata["source"] == "workflow_role_capability"
    assert len(rejected) == 1
    assert rejected[0].payload["active_role"] == "executor"
    assert rejected[0].payload["action"] == "tool"
    assert rejected[0].payload["target"] == "shell_exec"
    assert (
        rejected[0].payload["workflow_run_state"]["phase_error_summary"]["terminal_reason"]
        == "role_capability_rejected"
    )


async def test_capability_enabled_allows_declared_tool() -> None:
    """已声明工具能力时应继续走原 workflow 执行路径。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(
            role_capability_enabled=True,
            executor=AgentRoleCapability(
                "executor",
                allowed_tool_names=frozenset({"shell_exec"}),
            ),
        ),
    )
    called = False

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        nonlocal called
        called = True
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                pending_capability_action="tool",
                pending_tool_name="shell_exec",
            )
        ),
        execute_existing=execute,
    )

    assert called is True
    assert outcome.status in {RunStatus.SUCCEEDED, RunStatus.PAUSED}
    assert approvals.saved == []
    assert RunEventType.ROLE_CAPABILITY_REJECTED not in [
        event.event_type for event in events.events
    ]


async def test_workflow_handoff_event_and_state_recorded_on_role_transition() -> None:
    """phase 角色切换时应写 workflow 级 handoff 事件与结果状态。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(role_capability_enabled=False),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state={
                **_state(active_role="planner"),
                "current_phase": "plan",
            }
        ),
        execute_existing=execute,
    )

    handoffs = [
        event
        for event in events.events
        if event.event_type is RunEventType.WORKFLOW_HANDOFF_RECORDED
    ]
    assert len(handoffs) == 1
    assert handoffs[0].payload["source_role"] == "planner"
    assert handoffs[0].payload["target_role"] == "executor"
    assert handoffs[0].payload["reason"] == "phase_role_changed"
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["active_role"] == "executor"
    assert outcome.workflow_run_state["handoff_state"]["status"] == "completed"


async def test_child_run_disabled_keeps_existing_in_run_execution_path() -> None:
    """child run 策略未开启时应保持既有 in-run 执行路径。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(role_capability_enabled=False),
    )
    called = False

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        nonlocal called
        called = True
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                request_child_run=True,
                child_run_id="child-1",
            )
        ),
        execute_existing=execute,
    )

    assert called is True
    assert outcome.status in {RunStatus.SUCCEEDED, RunStatus.PAUSED}
    assert RunEventType.CHILD_RUN_LINKED not in [event.event_type for event in events.events]


async def test_child_run_enabled_links_and_waits_before_existing_execution() -> None:
    """child run 策略开启时应先写 parent-child link 并让父 Run 等待。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(child_run_enabled=True),
        ),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        raise AssertionError("父 Run 等待 child run 前不应继续真实执行")

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                request_child_run=True,
                child_run_id="child-1",
            )
        ),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.PAUSED
    assert outcome.terminal_reason == "child_run_waiting"
    assert outcome.workflow_run_state is not None
    assert (
        outcome.workflow_run_state["child_run_state"]["ownership_status"] == "parent_waiting_child"
    )
    assert [event.event_type for event in events.events] == [
        RunEventType.CHILD_RUN_LINKED,
        RunEventType.CHILD_RUN_WAITING,
    ]
    assert events.events[0].payload["child_run_id"] == "child-1"


async def test_child_run_enabled_creates_real_child_and_checkpoints_before_waiting() -> None:
    """child run 启用时应创建真实 child Run，并在等待事件前保存 checkpoint。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    run_store = _RunStore()
    checkpoint_sink = _CheckpointSink()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        run_store=run_store,
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(child_run_enabled=True),
        ),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        raise AssertionError("父 Run 等待 child run 前不应继续真实执行")

    checkpoint_token = set_run_checkpoint_context(
        RunCheckpointExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=7,
            recovery_mode=False,
            sink=cast(RunCheckpointSinkPort, checkpoint_sink),
            usage={"total_tokens": 3},
        )
    )
    try:
        outcome = await orchestrator.execute_phase(
            snapshot=_snapshot(
                workflow_state=_state(
                    active_role="executor",
                    request_child_run=True,
                )
            ),
            execute_existing=execute,
        )
    finally:
        reset_run_checkpoint_context(checkpoint_token)

    assert outcome.status is RunStatus.PAUSED
    assert run_store.created
    assert events.events[0].event_type is RunEventType.CHILD_RUN_LINKED
    assert events.events[1].event_type is RunEventType.CHILD_RUN_WAITING
    assert events.events[0].payload["child_run_id"] == "created-child-run"
    assert checkpoint_sink.segment_done_calls
    checkpoint_metadata = checkpoint_sink.segment_done_calls[0]["segment_metadata"]
    assert checkpoint_metadata["segment_stop_reason"] == "child_run_waiting"
    assert (
        checkpoint_metadata["workflow_run_state"]["child_run_state"]["child_run_id"]
        == "created-child-run"
    )


async def test_child_run_resume_observes_terminal_child_and_writes_reconciled_event() -> None:
    """父 Run 恢复时应观察真实 child 终态并写 CHILD_RUN_RECONCILED。"""

    child = replace(
        _snapshot(workflow_state=_state()),
        run_id="child-1",
        status=RunStatus.SUCCEEDED,
        terminal_reason="completed",
    )
    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        run_store=_RunStore(child=child),
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(child_run_enabled=True),
        ),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        raise AssertionError("reconciliation 节点应先于继续执行返回")

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                child_run_state={
                    "parent_run_id": "run-1",
                    "child_run_id": "child-1",
                    "phase": "execute",
                    "role": "executor",
                    "ownership_status": "parent_waiting_child",
                    "reconciliation_status": "waiting",
                    "reason": "child_run_policy_enabled",
                },
            )
        ),
        execute_existing=execute,
    )

    assert outcome.status is RunStatus.PAUSED
    assert outcome.terminal_reason == "child_run_reconciled"
    assert events.events[0].event_type is RunEventType.CHILD_RUN_RECONCILED
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["child_run_state"]["reconciliation_status"] == "reconciled"


async def test_child_run_resume_unreconciled_nonterminal_child_stays_waiting() -> None:
    """child 未终态时父 Run 恢复应保持 waiting，不假定成功完成。"""

    child = replace(
        _snapshot(workflow_state=_state()),
        run_id="child-1",
        status=RunStatus.RUNNING,
    )
    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        run_store=_RunStore(child=child),
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(child_run_enabled=True),
        ),
    )

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                child_run_state={
                    "parent_run_id": "run-1",
                    "child_run_id": "child-1",
                    "phase": "execute",
                    "role": "executor",
                    "ownership_status": "parent_waiting_child",
                    "reconciliation_status": "waiting",
                    "reason": "child_run_policy_enabled",
                },
            )
        ),
        execute_existing=lambda snapshot: _raise_unexpected(),
    )

    assert outcome.status is RunStatus.PAUSED
    assert outcome.terminal_reason == "child_run_waiting"
    assert [event.event_type for event in events.events] == []


async def test_phase_handoff_policy_uses_required_target_role() -> None:
    """phase_handoff_required 应作为执行顺序约束写入 handoff 结果状态。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(
                phase_handoff_required={"execute": "reviewer"},
            ),
        ),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(workflow_state=_state(active_role="executor")),
        execute_existing=execute,
    )

    handoff = next(
        event
        for event in events.events
        if event.event_type is RunEventType.WORKFLOW_HANDOFF_RECORDED
    )
    assert handoff.payload["target_role"] == "reviewer"
    assert handoff.payload["reason"] == "phase_handoff_required"
    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["handoff_state"]["target_role"] == "reviewer"


async def test_review_policy_routes_completed_phase_to_evaluate() -> None:
    """review_required_phases 应把完成后的 phase 推进到 evaluate。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(
                review_required_phases=frozenset({"execute"}),
            ),
        ),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(workflow_state=_state(active_role="executor")),
        execute_existing=execute,
    )

    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["current_phase"] == "evaluate"


async def test_revise_policy_routes_completed_evaluate_to_revise() -> None:
    """revise_target_phase 应把指定 phase 推进到配置的 revise 目标。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(
            role_capability_enabled=False,
            execution_policy=WorkflowExecutionPolicy(
                revise_target_phase={"evaluate": "revise"},
            ),
            with_revise=True,
        ),
    )

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        return _outcome(RunStatus.SUCCEEDED)

    outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state={
                **_state(active_role="reviewer"),
                "current_phase": "evaluate",
            }
        ),
        execute_existing=execute,
    )

    assert outcome.workflow_run_state is not None
    assert outcome.workflow_run_state["current_phase"] == "revise"
    assert outcome.workflow_run_state["active_role"] == "executor"


async def test_role_switch_reloads_new_active_role_capability() -> None:
    """active_role 切换后应重新读取新角色能力，不能沿用旧角色拒绝缓存。"""

    events = _EventStore()
    approvals = _ApprovalStore()
    orchestrator = _orchestrator(
        events,
        approvals=approvals,
        workflow=_workflow(
            role_capability_enabled=True,
            executor=AgentRoleCapability(
                "executor",
                allowed_tool_names=frozenset({"shell_exec"}),
            ),
        ),
    )
    called = False

    async def execute(snapshot: RunSnapshot) -> RunExecutionOutcome:
        nonlocal called
        called = True
        return _outcome(RunStatus.SUCCEEDED)

    planner_outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="planner",
                pending_capability_action="tool",
                pending_tool_name="shell_exec",
            )
        ),
        execute_existing=lambda snapshot: _raise_unexpected(),
    )
    executor_outcome = await orchestrator.execute_phase(
        snapshot=_snapshot(
            workflow_state=_state(
                active_role="executor",
                pending_capability_action="tool",
                pending_tool_name="shell_exec",
            )
        ),
        execute_existing=execute,
    )

    assert planner_outcome.status is RunStatus.AWAITING_APPROVAL
    assert called is True
    assert executor_outcome.status in {RunStatus.SUCCEEDED, RunStatus.PAUSED}
    assert [event.event_type for event in events.events].count(
        RunEventType.ROLE_CAPABILITY_REJECTED
    ) == 1


async def _raise_unexpected() -> RunExecutionOutcome:
    """测试辅助：被调用即失败。"""

    raise AssertionError("越权动作不应进入真实执行")


def _orchestrator(
    event_store: _EventStore,
    *,
    approvals: _ApprovalStore,
    workflow: WorkflowDefinition,
    run_store: _RunStore | None = None,
) -> WorkflowRunOrchestrator:
    """构造测试编排器。"""

    return WorkflowRunOrchestrator(
        event_store=cast(RunEventStorePort, event_store),
        workflow_registry=cast(WorkflowRegistryPort, _Registry(workflow)),
        workflow_serializer=WorkflowSerializerAdapter(),
        approval_store=cast(ApprovalStateStorePort, approvals),
        run_store=cast(RunStorePort | None, run_store),
        now=_Clock(),
    )


def _workflow(
    *,
    role_capability_enabled: bool,
    executor: AgentRoleCapability | None = None,
    execution_policy: WorkflowExecutionPolicy | None = None,
    with_revise: bool = False,
) -> WorkflowDefinition:
    """构造 code_change workflow。"""

    phases = (
        WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
        WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
        WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
        WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
    )
    if with_revise:
        phases = (
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.REVISE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
        )
    policy = execution_policy or WorkflowExecutionPolicy(
        role_capability_enabled=role_capability_enabled,
    )
    if (
        execution_policy is not None
        and execution_policy.role_capability_enabled != role_capability_enabled
    ):
        policy = WorkflowExecutionPolicy(
            role_capability_enabled=role_capability_enabled,
            phase_handoff_required=execution_policy.phase_handoff_required,
            review_required_phases=execution_policy.review_required_phases,
            revise_target_phase=execution_policy.revise_target_phase,
            child_run_enabled=execution_policy.child_run_enabled,
        )
    workflow = WorkflowDefinition(
        name="code_change",
        description="code change workflow",
        applicable=WorkflowApplicableCondition(),
        phases=phases,
        roles=(
            AgentRoleCapability("planner"),
            executor or AgentRoleCapability("executor"),
            AgentRoleCapability("reviewer"),
        ),
        collaboration_limit=CollaborationLimit(),
        execution_policy=policy,
        default_strategy_summary="default strategy",
    )
    workflow.validate()
    return workflow


def _state(**extra: Any) -> dict[str, Any]:
    """构造 workflow_run_state。"""

    return {
        "workflow_name": "code_change",
        "current_phase": "execute",
        "phase_started_at": None,
        "phase_history": [],
        "phase_result_summary": None,
        "phase_error_summary": None,
        "revise_counts": {},
        **extra,
    }


def _snapshot(*, workflow_state: dict[str, Any]) -> RunSnapshot:
    """构造 RunSnapshot。"""

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
        workflow_name="code_change",
        workflow_run_state=workflow_state,
    )


def _outcome(status: RunStatus) -> RunExecutionOutcome:
    """构造执行 outcome。"""

    return RunExecutionOutcome(
        status=status,
        result={"kind": "chat"},
        terminal_reason="completed",
        segment_metadata={"segment_count": 1},
    )
