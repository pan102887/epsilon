"""Run checkpoint 值对象单元测试模块。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from domain.run import value_objects as run_values
from domain.run.value_objects import RunEventType, RunKind, RunPayload, RunSnapshot, RunStatus


def test_run_event_type_includes_checkpoint_recovery_events() -> None:
    """RunEventType 必须包含 checkpoint 与恢复事件。"""
    assert RunEventType.CHECKPOINT_SAVED.value == "checkpoint_saved"
    assert RunEventType.RUN_RECOVERY_QUEUED.value == "run_recovery_queued"
    assert RunEventType.RUN_RECOVERY_FAILED.value == "run_recovery_failed"
    assert RunEventType.TOOL_RESULT_REPLAYED.value == "tool_result_replayed"


def test_run_snapshot_checkpoint_fields_default_for_legacy_construction() -> None:
    """旧调用方不传 checkpoint 字段时应得到安全默认值。"""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    payload = RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"})

    snapshot = RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.RUNNING,
        payload=payload,
        client_request_id="client-1",
        payload_hash=payload.stable_hash(),
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

    assert snapshot.latest_checkpoint_id is None
    assert snapshot.recoverable is False
    assert snapshot.recovery_attempt_count == 0
    assert snapshot.last_recovery_error is None
    assert data["recoverable"] is False


def test_tool_execution_key_is_stable_and_changes_with_identity_fields() -> None:
    """相同逻辑工具调用 key 稳定，不同参数摘要产生不同 key。"""
    assert hasattr(run_values, "ToolExecutionKey")
    ToolExecutionKey = run_values.ToolExecutionKey

    first = ToolExecutionKey(
        run_id="run-1",
        segment_index=2,
        round_num=3,
        tool_call_id="call-1",
        tool_name="write_file",
        arguments_digest="digest-a",
    )
    same = ToolExecutionKey(
        run_id="run-1",
        segment_index=2,
        round_num=3,
        tool_call_id="call-1",
        tool_name="write_file",
        arguments_digest="digest-a",
    )
    different = ToolExecutionKey(
        run_id="run-1",
        segment_index=2,
        round_num=3,
        tool_call_id="call-1",
        tool_name="write_file",
        arguments_digest="digest-b",
    )

    assert first.stable_key() == same.stable_key()
    assert first.stable_key() != different.stable_key()
    assert len(first.stable_key()) == 64


def test_checkpoint_value_objects_keep_recovery_metadata() -> None:
    """checkpoint 与 ledger 值对象应保留恢复需要的元数据。"""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert hasattr(run_values, "CheckpointPhase")
    assert hasattr(run_values, "CheckpointRetentionPolicy")
    assert hasattr(run_values, "DurableCheckpoint")
    assert hasattr(run_values, "RecoveryDecision")
    assert hasattr(run_values, "ToolLedgerStatus")
    assert hasattr(run_values, "ToolReplayPolicy")
    assert hasattr(run_values, "ToolResultLedgerEntry")
    assert hasattr(run_values, "ToolSideEffectLevel")
    CheckpointPhase = run_values.CheckpointPhase
    CheckpointRetentionPolicy = run_values.CheckpointRetentionPolicy
    DurableCheckpoint = run_values.DurableCheckpoint
    RecoveryDecision = run_values.RecoveryDecision
    ToolLedgerStatus = run_values.ToolLedgerStatus
    ToolReplayPolicy = run_values.ToolReplayPolicy
    ToolResultLedgerEntry = run_values.ToolResultLedgerEntry
    ToolSideEffectLevel = run_values.ToolSideEffectLevel

    checkpoint = DurableCheckpoint(
        run_id="run-1",
        checkpoint_id="chk-1",
        sequence=1,
        phase=CheckpointPhase.TOOL_COMPLETED,
        context_snapshot={"messages": []},
        round_num=2,
        usage={"total_tokens": 3},
        trace_summary={"model": "gpt-x"},
        segment_metadata={"segment_index": 1},
        tool_execution_key="tool-key",
        tool_result_ref="tool-key",
        schema_version=1,
        sanitized=True,
        truncated_fields=("trace_summary.output",),
        created_at=now,
    )
    ledger = ToolResultLedgerEntry(
        run_id="run-1",
        tool_execution_key="tool-key",
        status=ToolLedgerStatus.COMPLETED,
        tool_name="write_file",
        tool_call_id="call-1",
        arguments_digest="digest-a",
        replay_policy=ToolReplayPolicy.MANUAL_REVIEW,
        side_effect_level=ToolSideEffectLevel.EXTERNAL_WRITE,
        idempotency_key=None,
        result="ok",
        is_error=False,
        metadata={"duration_ms": 12},
        created_at=now,
        updated_at=now,
    )
    policy = CheckpointRetentionPolicy(
        max_checkpoint_count=200,
        ttl_seconds=604800,
        max_payload_bytes=262144,
        max_tool_ledger_count=1000,
    )
    decision = RecoveryDecision(
        recoverable=True,
        reason="compatible_checkpoint",
        checkpoint_id="chk-1",
    )

    assert checkpoint.phase is CheckpointPhase.TOOL_COMPLETED
    assert checkpoint.truncated_fields == ("trace_summary.output",)
    assert ledger.status is ToolLedgerStatus.COMPLETED
    assert ledger.side_effect_level is ToolSideEffectLevel.EXTERNAL_WRITE
    assert policy.max_payload_bytes == 262144
    assert decision.checkpoint_id == "chk-1"
