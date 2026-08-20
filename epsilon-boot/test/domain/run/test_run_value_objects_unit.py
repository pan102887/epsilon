"""Run 值对象单元测试模块。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunCreateRequest,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)


def test_run_status_values_are_complete() -> None:
    """验证 RunStatus 枚举值完整且严格匹配设计。"""
    assert {status.value for status in RunStatus} == {
        "queued",
        "running",
        "paused",
        "awaiting_approval",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "failed",
        "lost",
    }


def test_run_event_type_values_are_complete() -> None:
    """验证 RunEventType 枚举值完整且严格匹配设计。"""
    assert {event_type.value for event_type in RunEventType} == {
        "run_created",
        "run_queued",
        "run_claimed",
        "run_heartbeat",
        "segment_started",
        "segment_done",
        "run_paused",
        "approval_required",
        "cancel_requested",
        "run_cancelled",
        "run_succeeded",
        "run_failed",
        "run_lost",
        "replay_expired",
        "checkpoint_saved",
        "run_recovery_queued",
        "run_recovery_failed",
        "tool_result_replayed",
        "task_classified",
        "guardrail_evaluated",
        "guardrail_blocked",
        "role_capability_rejected",
        "workflow_selected",
        "workflow_selection_skipped",
        "workflow_phase_started",
        "workflow_phase_completed",
        "workflow_phase_failed",
        "workflow_handoff_recorded",
        "collaboration_step_recorded",
        "collaboration_limit_hit",
        "child_run_linked",
        "child_run_waiting",
        "child_run_reconciled",
    }


def test_payload_stable_hash_ignores_json_key_order() -> None:
    """相同语义 payload 即使 JSON key 顺序不同也产生相同摘要。"""
    left = RunPayload(
        kind=RunKind.CHAT,
        session_id="s1",
        chat={"message": "hi", "metadata": {"b": 2, "a": 1}},
        model="gpt-x",
    )
    right = RunPayload(
        kind=RunKind.CHAT,
        session_id="s1",
        chat={"metadata": {"a": 1, "b": 2}, "message": "hi"},
        model="gpt-x",
    )

    assert left.stable_hash() == right.stable_hash()


def test_payload_stable_hash_changes_when_payload_changes() -> None:
    """不同 payload 必须产生不同摘要以支撑幂等冲突检测。"""
    first = RunPayload(kind=RunKind.TASK, session_id="s1", task={"goal": "A"})
    second = RunPayload(kind=RunKind.TASK, session_id="s1", task={"goal": "B"})

    assert first.stable_hash() != second.stable_hash()


def test_create_request_uses_effective_payload_hash() -> None:
    """RunCreateRequest 应优先使用显式 hash，否则计算 payload hash。"""
    payload = RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"})

    assert (
        RunCreateRequest(payload=payload, client_request_id="c1").effective_payload_hash()
        == payload.stable_hash()
    )
    assert (
        RunCreateRequest(
            payload=payload,
            client_request_id="c1",
            payload_hash="explicit",
        ).effective_payload_hash()
        == "explicit"
    )


def test_run_snapshot_contains_required_query_fields() -> None:
    """RunSnapshot 必须包含分段元数据和最新事件游标等查询字段。"""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    payload = RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"})
    snapshot = RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        payload=payload,
        client_request_id="client-1",
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={"segment_count": 0},
        latest_event_cursor=3,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
    )

    data = asdict(snapshot)

    assert data["segment_metadata"] == {"segment_count": 0}
    assert data["latest_event_cursor"] == 3
    assert data["run_id"] == "run-1"


def test_policy_value_objects_keep_capacity_and_retention_values() -> None:
    """容量策略和事件保留策略应保留配置化数值。"""
    assert RunCapacityPolicy(max_queued_runs=10, max_running_runs=2).max_queued_runs == 10
    assert EventRetentionPolicy(max_event_count=100, ttl_seconds=3600).ttl_seconds == 3600
