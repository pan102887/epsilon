"""Workflow collaboration events and summary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from domain.run.value_objects import RunEvent, RunEventType
from domain.run.workflow import (
    CollaborationAction,
    CollaborationLimit,
    ParentChildRunLink,
    WorkflowPhase,
)
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from infrastructure.agent.workflow_collaboration_recorder import (
    record_collaboration_limit_hit,
    record_collaboration_step,
    summarize_workflow_handoff_state,
)
from infrastructure.run.workflow_serialization import parent_child_run_link_to_dict

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _EventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            cursor=len([item for item in self.events if item.run_id == run_id]) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


def _context_token():
    return set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="parent-run",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(),
            depth=1,
            handoff_count=0,
            delegation_count=0,
        )
    )


async def test_step_trace_link_events_keep_run_cursor_order() -> None:
    events = _EventStore()
    token = _context_token()
    try:
        await record_collaboration_step(
            event_store=events,
            action=CollaborationAction.DELEGATION,
            target_agent="agent-a",
            task_summary="first",
            result_summary="ok",
        )
        await record_collaboration_step(
            event_store=events,
            action=CollaborationAction.HANDOFF,
            target_agent="agent-b",
            task_summary="second",
            result_summary="done",
        )
    finally:
        reset_workflow_collaboration_context(token)

    assert [event.cursor for event in events.events] == [1, 2]
    assert [event.payload["action"] for event in events.events] == [
        "delegation",
        "handoff",
    ]
    assert all(
        event.event_type is RunEventType.COLLABORATION_STEP_RECORDED for event in events.events
    )


async def test_latest_summary_is_trimmed_to_recent_limit() -> None:
    events = _EventStore()
    summary: dict[str, Any] = {}
    token = _context_token()
    try:
        for index in range(4):
            summary = await record_collaboration_step(
                event_store=events,
                action=CollaborationAction.DELEGATION,
                target_agent=f"agent-{index}",
                task_summary=f"task-{index}",
                result_summary="ok",
                collaboration_summary=summary,
                recent_limit=2,
            )
    finally:
        reset_workflow_collaboration_context(token)

    assert summary["delegation_count"] == 4
    assert len(summary["latest_steps"]) == 2
    assert [step["target_agent"] for step in summary["latest_steps"]] == [
        "agent-2",
        "agent-3",
    ]
    assert len(events.events) == 4


async def test_parent_run_can_observe_collaboration_steps_from_event_stream() -> None:
    events = _EventStore()
    token = _context_token()
    try:
        await record_collaboration_step(
            event_store=events,
            action=CollaborationAction.DELEGATION,
            target_agent="agent-a",
            task_summary="child task",
            result_summary="child result",
        )
    finally:
        reset_workflow_collaboration_context(token)

    parent_events = [event for event in events.events if event.run_id == "parent-run"]
    assert len(parent_events) == 1
    assert parent_events[0].payload["run_id"] == "parent-run"
    assert parent_events[0].payload["target_agent"] == "agent-a"
    assert parent_events[0].payload["task_summary"] == "child task"


async def test_collaboration_helper_reads_legacy_recent_steps_without_double_writing() -> None:
    """协作 helper 兼容历史 recent_steps 输入但只输出 latest_steps。"""

    events = _EventStore()
    legacy_summary = {
        "recent_steps": [{"link_id": "legacy-step", "target_agent": "legacy"}],
        "child_links": [{"child_run_id": "child-1"}],
        "delegation_count": 2,
        "handoff_count": 1,
        "max_depth_seen": 1,
        "limit_hit_reason": None,
    }
    token = _context_token()
    try:
        summary = await record_collaboration_step(
            event_store=events,
            action=CollaborationAction.HANDOFF,
            target_agent="agent-new",
            task_summary="handoff task",
            result_summary="ok",
            collaboration_summary=legacy_summary,
            recent_limit=5,
        )
        limit_summary = await record_collaboration_limit_hit(
            event_store=events,
            reason="max handoff",
            action=CollaborationAction.HANDOFF,
            target_agent="agent-new",
            collaboration_summary=summary,
        )
    finally:
        reset_workflow_collaboration_context(token)

    assert "recent_steps" not in summary
    assert "recent_steps" not in limit_summary
    assert [step["target_agent"] for step in summary["latest_steps"]] == [
        "legacy",
        "agent-new",
    ]
    assert summary["child_links"] == [{"child_run_id": "child-1"}]
    assert summary["delegation_count"] == 2
    assert summary["handoff_count"] == 2
    assert limit_summary["limit_hit_reason"] == "max handoff"
    assert limit_summary["child_links"] == [{"child_run_id": "child-1"}]


async def test_handoff_state_can_feed_canonical_collaboration_summary() -> None:
    """workflow handoff state 可映射为 latest_steps，但不重算策略。"""

    summary = summarize_workflow_handoff_state(
        workflow_run_state={
            "run_id": "run-1",
            "workflow_name": "code_change",
            "current_phase": "evaluate",
            "handoff_state": {
                "status": "completed",
                "source_role": "executor",
                "target_role": "reviewer",
                "target_agent": "review_agent",
                "reason": "phase_handoff_required",
            },
        },
        collaboration_summary={"recent_steps": [{"target_agent": "legacy"}]},
    )

    assert "recent_steps" not in summary
    assert summary["handoff_count"] == 1
    assert summary["latest_steps"][0]["target_agent"] == "legacy"
    assert summary["latest_steps"][1]["target_agent"] == "review_agent"
    assert summary["latest_steps"][1]["action"] == "handoff"


async def test_parent_child_run_link_model_is_json_safe_without_requiring_child_run() -> None:
    link = ParentChildRunLink(
        parent_run_id="parent-run",
        child_run_id="child-run",
        role="executor",
        phase=WorkflowPhase.EXECUTE,
        reason="future child run",
        created_at=_NOW,
    )

    assert parent_child_run_link_to_dict(link) == {
        "parent_run_id": "parent-run",
        "child_run_id": "child-run",
        "role": "executor",
        "phase": "execute",
        "reason": "future child run",
        "created_at": _NOW.isoformat(),
    }
