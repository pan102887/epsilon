"""阶段六 workflow recovery、approval、collaboration 与 guardrail 集成测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.run.run_application_service import (
    ApprovalResumeResult,
    RunApplicationService,
)
from domain.agent.exceptions import HandoffPerformed
from domain.agent.guardrails import GuardrailMode, GuardrailPolicy
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import ApprovalDecision
from domain.chat.context import ConversationContext, ToolMessage
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
    ToolReplayPolicy,
)
from domain.run.workflow_context import reset_workflow_collaboration_context
from infrastructure.agent.delegate_parallel_tool import DelegateParallelTool
from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool
from infrastructure.agent.handoff_context import reset_parent_context
from infrastructure.agent.handoff_to_agent_tool import HandoffToAgentTool
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter
from test.application.run.test_workflow_checkpoint_recovery_unit import (
    _checkpoint,
    _CheckpointStore,
    _pending_ledger,
    _RunStore,
    _service,
    _snapshot,
)
from test.application.run.test_workflow_checkpoint_recovery_unit import (
    _EventStore as _RecoveryEventStore,
)
from test.infrastructure.agent.test_react_agent_guardrail_unit import (
    _config as _guardrail_config,
)
from test.infrastructure.agent.test_react_agent_guardrail_unit import (
    _CriticalTool,
    _tool_call,
)
from test.infrastructure.agent.test_workflow_collaboration_governance_unit import (
    _Delegation,
    _parent_context_token,
    _Registry,
    _workflow_token,
)
from test.infrastructure.agent.test_workflow_collaboration_governance_unit import (
    _EventStore as _CollaborationEventStore,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _ApprovalRunStore:
    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        return self.snapshot if run_id == self.snapshot.run_id else None

    async def acquire_approval_resume_lease(
        self, *, run_id: str, owner_id: str, lease_seconds: int
    ) -> RunSnapshot:
        """为 workflow 审批恢复测试写入短生命周期租约。"""

        assert run_id == self.snapshot.run_id
        self.snapshot = replace(
            self.snapshot,
            lease=RunLease(
                owner_id=owner_id,
                lease_until=_NOW + timedelta(seconds=lease_seconds),
                heartbeat_at=_NOW,
            ),
        )
        return self.snapshot

    async def release_approval_resume_lease(self, *, run_id: str, owner_id: str) -> RunSnapshot:
        """释放测试 fake 中的审批恢复短租约。"""

        assert run_id == self.snapshot.run_id
        if self.snapshot.lease is None or self.snapshot.lease.owner_id != owner_id:
            return self.snapshot
        self.snapshot = replace(self.snapshot, lease=None)
        return self.snapshot

    async def resolve_approval_resume(
        self, *, run_id: str, owner_id: str, result: ApprovalResumeResult
    ) -> RunSnapshot:
        assert run_id == self.snapshot.run_id
        self.snapshot = replace(
            self.snapshot,
            status=RunStatus.QUEUED if result.status == "queued" else self.snapshot.status,
            result=result.result or self.snapshot.result,
            error=result.error,
            terminal_reason=None,
            approval_id=None,
            can_continue=False,
            lease=None,
        )
        return self.snapshot


class _ApprovalEventStore:
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
            created_at=_NOW,
        )
        self.events.append(event)
        return event

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        return None


def _approval_snapshot() -> RunSnapshot:
    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="session-1",
        task={"goal": "approve current phase"},
        model="model-a",
    )
    return RunSnapshot(
        run_id="run-approval",
        kind=RunKind.TASK,
        status=RunStatus.AWAITING_APPROVAL,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
        result={"approval": "pending"},
        error=None,
        approval_id="approval-1",
        segment_metadata={},
        latest_event_cursor=None,
        can_continue=True,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_name="code_change",
        workflow_run_state={"workflow_name": "code_change", "current_phase": "execute"},
        collaboration_summary={"delegation_count": 1},
    )


async def test_checkpoint_recovery_keeps_workflow_phase_and_tool_replay_boundary() -> None:
    run_store = _RunStore(
        _snapshot(
            workflow_run_state={"workflow_name": "code_change", "current_phase": "execute"},
            collaboration_summary={"delegation_count": 2},
        )
    )
    events = _RecoveryEventStore()
    recovery = _service(run_store, _CheckpointStore(_checkpoint()), events)

    recovered = await recovery.sweep_expired_leases(now=_NOW)

    assert recovered[0].status is RunStatus.QUEUED
    assert recovered[0].workflow_run_state == {
        "workflow_name": "code_change",
        "current_phase": "execute",
    }
    assert recovered[0].collaboration_summary == {"delegation_count": 2}
    assert events.events[-1].event_type is RunEventType.RUN_RECOVERY_QUEUED

    blocked = _service(
        _RunStore(_snapshot()),
        _CheckpointStore(_checkpoint(), [_pending_ledger(ToolReplayPolicy.NEVER_REPLAY)]),
        _RecoveryEventStore(),
    )
    decision = await blocked.evaluate_recovery(_snapshot())

    assert decision.recoverable is False
    assert decision.reason == "pending_tool_replay_blocked"


async def test_awaiting_approval_resume_preserves_current_workflow_phase() -> None:
    store = _ApprovalRunStore(_approval_snapshot())
    events = _ApprovalEventStore()

    async def approval_resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        assert snapshot.workflow_run_state is not None
        assert snapshot.workflow_run_state["current_phase"] == "execute"
        assert decisions[0].tool_call_id == "call-1"
        return ApprovalResumeResult(status="queued", result={"approval": "approved"})

    service = RunApplicationService(
        run_store=store,  # type: ignore[arg-type]
        event_store=events,  # type: ignore[arg-type]
        capacity_policy=RunCapacityPolicy(max_queued_runs=10, max_running_runs=10),
        event_retention_policy=EventRetentionPolicy(max_event_count=100, ttl_seconds=3600),
        workflow_serializer=WorkflowSerializerAdapter(),
        approval_resumer=approval_resumer,
    )

    resumed = await service.resume_approval_run(
        "run-approval",
        [ApprovalDecision(type="approve", tool_call_id="call-1")],
    )

    assert resumed.status is RunStatus.QUEUED
    assert resumed.workflow_run_state == {
        "workflow_name": "code_change",
        "current_phase": "execute",
    }
    assert resumed.collaboration_summary == {"delegation_count": 1}
    assert events.events[-1].event_type is RunEventType.RUN_QUEUED


async def test_delegate_handoff_and_limit_hit_are_observable_in_event_stream() -> None:
    events = _CollaborationEventStore()
    delegation = _Delegation()
    delegate_tool = DelegateToAgentTool(_Registry(), delegation, event_store=events)
    handoff_tool = HandoffToAgentTool(_Registry(), delegation, event_store=events)
    parallel_tool = DelegateParallelTool(_Registry(), delegation, event_store=events)

    workflow_token = _workflow_token(max_parallel_delegations=1)
    try:
        delegate_result = await delegate_tool.execute(agent_name="agent-a", task_goal="single")
        assert delegate_result.content == "ok"
        parent_token = _parent_context_token()
        try:
            with pytest.raises(HandoffPerformed):
                await handoff_tool.execute(agent_name="agent-a")
        finally:
            reset_parent_context(parent_token)
        limit_result = await parallel_tool.execute(
            requests=[
                {"agent_name": "agent-a", "task_goal": "a"},
                {"agent_name": "agent-b", "task_goal": "b"},
            ]
        )
    finally:
        reset_workflow_collaboration_context(workflow_token)

    assert "并行委派数量超限" in limit_result.content
    assert [event.event_type for event in events.events] == [
        RunEventType.COLLABORATION_STEP_RECORDED,
        RunEventType.COLLABORATION_STEP_RECORDED,
        RunEventType.COLLABORATION_LIMIT_HIT,
    ]
    assert events.events[-1].payload["reason"].startswith("parallel_delegation_limit_exceeded")


async def test_guardrail_critical_enforce_still_blocks_tool_with_workflow_context() -> None:
    registry = MagicMock()
    registry.get.return_value = _CriticalTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE)),
    )
    context = ConversationContext()
    workflow_token = _workflow_token()
    try:
        result, is_error = await adapter._execute_tool_call(
            context,
            _tool_call(),
            _guardrail_config(),
        )
    finally:
        reset_workflow_collaboration_context(workflow_token)

    assert "critical" in result.content
    assert is_error is True
    registry.execute.assert_not_awaited()
    last = context.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.metadata["guardrail_blocked"] is True
    assert last.metadata["guardrail_reason"] == "tool_risk_gate_required"
