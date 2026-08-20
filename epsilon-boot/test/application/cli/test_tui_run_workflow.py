"""TUI Run workflow 展示测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from application.cli.tui import render_run_event, render_run_snapshot
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)


def _snapshot(
    *,
    workflow: bool = True,
    latest_event_cursor: int | None = 9,
) -> RunSnapshot:
    now = datetime.now(UTC)
    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="tui-test",
        task={"goal": "ship workflow"},
        model="test-model",
    )
    snapshot = RunSnapshot(
        run_id="run-1",
        kind=RunKind.TASK,
        status=RunStatus.PAUSED,
        payload=payload,
        client_request_id="client-1",
        payload_hash=payload.stable_hash(),
        result={"content": "phase done"},
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=latest_event_cursor,
        can_continue=True,
        terminal_reason="workflow_phase_completed",
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
    )
    if not workflow:
        return snapshot
    return replace(
        snapshot,
        workflow_name="code_change",
        workflow_run_state={
            "workflow_name": "code_change",
            "current_phase": "evaluate",
            "phase_history": [
                {"phase": "plan", "status": "succeeded"},
                {"phase": "execute", "status": "succeeded"},
            ],
        },
        collaboration_summary={
            "delegation_count": 1,
            "recent_steps": [
                {
                    "action": "delegation",
                    "target_agent": "reviewer",
                    "result_summary": "review done",
                }
            ],
        },
    )


def test_render_run_snapshot_includes_workflow_and_latest_collaboration() -> None:
    rendered = render_run_snapshot(_snapshot())

    assert "workflow_name: code_change" in rendered
    assert "workflow_phase: evaluate" in rendered
    assert "workflow_phase_history:" in rendered
    assert "plan: succeeded" in rendered
    assert "execute: succeeded" in rendered
    assert "latest_collaboration_summary:" in rendered
    assert "delegation / reviewer / review done" in rendered
    assert "recent_collaboration_summary:" not in rendered


def test_render_run_snapshot_omits_empty_workflow_fields() -> None:
    rendered = render_run_snapshot(_snapshot(workflow=False))

    assert "run_id: run-1" in rendered
    assert "workflow_name:" not in rendered
    assert "workflow_phase:" not in rendered
    assert "latest_collaboration_summary:" not in rendered


def test_render_run_snapshot_after_replay_expired_uses_snapshot_workflow_fields() -> None:
    rendered = render_run_snapshot(_snapshot(latest_event_cursor=None))

    assert "latest_cursor: None" in rendered
    assert "workflow_name: code_change" in rendered
    assert "workflow_phase: evaluate" in rendered


def test_render_run_event_recognizes_workflow_and_collaboration_event_types() -> None:
    workflow_event = RunEvent(
        run_id="run-1",
        cursor=10,
        event_type=RunEventType.WORKFLOW_PHASE_COMPLETED,
        payload={"summary": "execute completed"},
        created_at=datetime.now(UTC),
    )
    collaboration_event = RunEvent(
        run_id="run-1",
        cursor=11,
        event_type=RunEventType.COLLABORATION_STEP_RECORDED,
        payload={"summary": "delegation recorded"},
        created_at=datetime.now(UTC),
    )

    assert "type: workflow_phase_completed" in render_run_event(workflow_event)
    assert "execute completed" in render_run_event(workflow_event)
    assert "type: collaboration_step_recorded" in render_run_event(collaboration_event)
    assert "delegation recorded" in render_run_event(collaboration_event)
