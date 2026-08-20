"""Run guardrail 扩展值对象单元测试。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from domain.run import RunEventType, RunKind, RunPayload, RunSnapshot, RunStatus


def test_run_event_type_includes_guardrail_events() -> None:
    assert RunEventType.TASK_CLASSIFIED.value == "task_classified"
    assert RunEventType.GUARDRAIL_EVALUATED.value == "guardrail_evaluated"
    assert RunEventType.GUARDRAIL_BLOCKED.value == "guardrail_blocked"


def test_run_snapshot_defaults_guardrail_fields() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        payload=RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"}),
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
        created_at=now,
        updated_at=now,
        version=1,
    )

    data = asdict(snapshot)

    assert data["task_classification"] is None
    assert data["guardrail_summary"] is None
