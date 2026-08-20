"""RunGuardrailRecorder 应用服务单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from application.run.run_guardrail_recorder import RunGuardrailRecorder
from domain.agent.guardrails import (
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailMode,
    GuardrailObservation,
    GuardrailReason,
    GuardrailRuntimeStats,
)
from domain.run.exceptions import RunLeaseConflictError
from domain.run.runtime_context import (
    RunExecutionContext,
    reset_run_execution_context,
    set_run_execution_context,
)
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _RunStore:
    """返回固定快照的 RunStore fake。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        """记录读取的 run_id 并返回固定快照。"""

        self.calls.append(run_id)
        return self.snapshot


class _ObservationStore:
    """记录观察写入参数的 ObservationStore fake。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def record_runtime_observation(
        self,
        *,
        run_id: str,
        owner_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> tuple[RunSnapshot, RunEvent]:
        """记录参数并返回模拟的快照与事件。"""

        if self.error is not None:
            raise self.error
        self.calls.append(
            {
                "run_id": run_id,
                "owner_id": owner_id,
                "event_type": event_type,
                "payload": payload,
                "guardrail_summary": guardrail_summary,
                "workflow_run_state": workflow_run_state,
                "collaboration_summary": collaboration_summary,
            }
        )
        next_cursor = (self.snapshot.latest_event_cursor or 0) + 1
        self.snapshot = replace(
            self.snapshot,
            latest_event_cursor=next_cursor,
            guardrail_summary=guardrail_summary,
            updated_at=_NOW,
            version=self.snapshot.version + 1,
        )
        return self.snapshot, RunEvent(
            run_id=run_id,
            cursor=next_cursor,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )


def _snapshot(*, guardrail_summary: dict[str, Any] | None = None) -> RunSnapshot:
    """构造带既有 guardrail 摘要的 Run 快照。"""

    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-1",
        chat={"message": "hello"},
        model="model-a",
    )
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.RUNNING,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={"segment_count": 1},
        latest_event_cursor=7,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        guardrail_summary=guardrail_summary,
    )


def _existing_summary() -> dict[str, Any]:
    """构造已有累计 guardrail 摘要。"""

    return {
        "mode": GuardrailMode.OBSERVE.value,
        "action": "observe",
        "reason": GuardrailReason.REPEATED_TOOL_CALL.value,
        "message": "previous",
        "estimated_cost": None,
        "metadata": {"source": "run_runtime", "tool_name": "shell_exec"},
        "evaluation_count": 3,
        "blocked_count": 2,
        "approval_request_count": 1,
        "last_event_cursor": 7,
        "updated_at": _NOW.isoformat(),
        "runtime_stats": {"total_tokens": 11},
        "stale": False,
        "stale_reason": None,
    }


def _observation(decision: GuardrailDecision) -> GuardrailObservation:
    """构造用于 recorder 的 guardrail 观测。"""

    return GuardrailObservation(
        stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
        decision=decision,
        stats=GuardrailRuntimeStats(total_tokens=42, total_tool_calls=3),
        segment_index=2,
        round_num=4,
        tool_name="shell_exec",
        tool_call_id="call-1",
        created_at=_NOW,
    )


@pytest.mark.parametrize(
    ("decision", "expected_event_type", "expected_blocked", "expected_approval"),
    [
        pytest.param(
            GuardrailDecision.allow(),
            RunEventType.GUARDRAIL_EVALUATED,
            2,
            1,
            id="allow",
        ),
        pytest.param(
            GuardrailDecision.observe(
                reason=GuardrailReason.REPEATED_TOOL_CALL,
                message="observe only",
            ),
            RunEventType.GUARDRAIL_EVALUATED,
            2,
            1,
            id="observe",
        ),
        pytest.param(
            GuardrailDecision.require_approval(
                reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
                message="need approval",
            ),
            RunEventType.GUARDRAIL_BLOCKED,
            3,
            2,
            id="require-approval",
        ),
        pytest.param(
            GuardrailDecision.stop(
                reason=GuardrailReason.UNSAFE_TOOL_INPUT,
                message="stop now",
            ),
            RunEventType.GUARDRAIL_BLOCKED,
            3,
            1,
            id="stop",
        ),
    ],
)
async def test_record_observation_maps_event_type_and_accumulates_summary_counts(
    decision: GuardrailDecision,
    expected_event_type: RunEventType,
    expected_blocked: int,
    expected_approval: int,
) -> None:
    """allow/observe/require_approval/stop 应映射正确事件并累计摘要计数。"""

    snapshot = _snapshot(guardrail_summary=_existing_summary())
    run_store = _RunStore(snapshot)
    observation_store = _ObservationStore(snapshot)
    recorder = RunGuardrailRecorder(
        run_store=run_store,
        observation_store=observation_store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    token = set_run_execution_context(
        RunExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=2,
        )
    )

    try:
        updated = await recorder.record_observation(observation=_observation(decision))
    finally:
        reset_run_execution_context(token)

    assert run_store.calls == ["run-1"]
    assert len(observation_store.calls) == 1
    call = observation_store.calls[0]
    assert call["run_id"] == "run-1"
    assert call["owner_id"] == "worker-1"
    assert call["event_type"] is expected_event_type
    assert call["payload"]["action"] == decision.action.value
    assert call["payload"]["segment_index"] == 2
    assert call["payload"]["tool_name"] == "shell_exec"

    summary = call["guardrail_summary"]
    assert summary is not None
    assert summary["evaluation_count"] == 4
    assert summary["blocked_count"] == expected_blocked
    assert summary["approval_request_count"] == expected_approval
    assert summary["last_event_cursor"] == 8
    assert summary["updated_at"] == _NOW.isoformat()
    assert summary["runtime_stats"] == call["payload"]["stats"]
    assert summary["runtime_stats"]["total_tokens"] == 42
    assert updated == observation_store.snapshot
    assert updated is not None
    assert updated.guardrail_summary == summary


async def test_record_observation_surfaces_owner_conflict_from_store() -> None:
    """错误 owner_id 或租约冲突应由 recorder 原样透传存储失败。"""

    snapshot = _snapshot(guardrail_summary=_existing_summary())
    run_store = _RunStore(snapshot)
    observation_store = _ObservationStore(snapshot)
    observation_store.error = RunLeaseConflictError("run-1", "worker-1")
    recorder = RunGuardrailRecorder(
        run_store=run_store,
        observation_store=observation_store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    token = set_run_execution_context(
        RunExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=2,
        )
    )

    try:
        with pytest.raises(RunLeaseConflictError):
            await recorder.record_observation(
                observation=_observation(
                    GuardrailDecision.require_approval(
                        reason=GuardrailReason.TOOL_RISK_GATE_REQUIRED,
                        message="need approval",
                    )
                )
            )
    finally:
        reset_run_execution_context(token)

    assert run_store.calls == ["run-1"]
    assert observation_store.calls == []


async def test_record_observation_returns_none_without_run_context() -> None:
    """非 Run 路径调用 recorder 时应直接跳过持久化。"""

    snapshot = _snapshot(guardrail_summary=_existing_summary())
    run_store = _RunStore(snapshot)
    observation_store = _ObservationStore(snapshot)
    recorder = RunGuardrailRecorder(
        run_store=run_store,
        observation_store=observation_store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )

    result = await recorder.record_observation(
        observation=_observation(
            GuardrailDecision.observe(
                reason=GuardrailReason.REPEATED_TOOL_CALL,
                message="observe only",
            )
        )
    )

    assert result is None
    assert run_store.calls == []
    assert observation_store.calls == []
