"""Run checkpoint 恢复 guardrail 摘要测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_checkpoint_recovery_service import RunRecoveryService
from domain.agent.guardrails import GuardrailAction, GuardrailMode
from domain.chat.context import ConversationContext
from domain.run.ports import RunCheckpointStorePort, RunEventStorePort, RunStorePort
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
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
    """记录恢复入队参数的 RunStore fake。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        self.snapshot = snapshot
        self.enqueued: list[dict[str, Any]] = []

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        """返回单个过期快照。"""

        return [self.snapshot]

    async def enqueue_recovery(
        self,
        *,
        run_id: str,
        latest_checkpoint_id: str,
        recovery_attempt_count: int,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """记录 guardrail/workflow/collaboration 恢复写入。"""

        self.enqueued.append(
            {
                "run_id": run_id,
                "latest_checkpoint_id": latest_checkpoint_id,
                "recovery_attempt_count": recovery_attempt_count,
                "guardrail_summary": guardrail_summary,
                "workflow_run_state": workflow_run_state,
                "collaboration_summary": collaboration_summary,
            }
        )
        self.snapshot = replace(
            self.snapshot,
            status=RunStatus.QUEUED,
            latest_checkpoint_id=latest_checkpoint_id,
            recovery_attempt_count=recovery_attempt_count,
            guardrail_summary=(
                guardrail_summary
                if guardrail_summary is not None
                else self.snapshot.guardrail_summary
            ),
            workflow_run_state=(
                workflow_run_state
                if workflow_run_state is not None
                else self.snapshot.workflow_run_state
            ),
            collaboration_summary=(
                collaboration_summary
                if collaboration_summary is not None
                else self.snapshot.collaboration_summary
            ),
            recoverable=True,
            updated_at=_NOW,
        )
        return self.snapshot

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """本测试不走 lost 分支。"""

        raise AssertionError("unexpected lost recovery path")


class _CheckpointStore:
    """返回固定 checkpoint 的 fake。"""

    def __init__(self, checkpoint: DurableCheckpoint) -> None:
        self.checkpoint = checkpoint

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        """返回最新 checkpoint。"""

        return self.checkpoint

    async def list_tool_ledger(self, run_id: str) -> list[Any]:
        """本用例无工具账本阻塞。"""

        return []


class _EventStore:
    """记录恢复事件的 fake。"""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
    ) -> RunEvent:
        """追加恢复事件。"""

        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


def _policy() -> CheckpointRetentionPolicy:
    """构造测试保留策略。"""

    return CheckpointRetentionPolicy(10, 3600, 4096, 100)


def _checkpoint(
    *,
    usage: dict[str, int] | None = None,
    segment_metadata: dict[str, Any] | None = None,
) -> DurableCheckpoint:
    """构造可恢复 checkpoint。"""

    context = ConversationContext()
    context.add_user_message("hello")
    return DurableCheckpoint(
        run_id="run-1",
        checkpoint_id="chk-1",
        sequence=1,
        phase=CheckpointPhase.MODEL_COMPLETED,
        context_snapshot=context.to_dict(),
        round_num=1,
        usage=usage or {},
        trace_summary={},
        segment_metadata=segment_metadata or {},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=_NOW,
    )


def _snapshot(
    *,
    latest_checkpoint_id: str | None = None,
    guardrail_summary: dict[str, Any] | None = None,
    latest_event_cursor: int = 12,
) -> RunSnapshot:
    """构造恢复测试快照。"""

    payload = RunPayload(RunKind.CHAT, "s1", chat={"message": "hi"})
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
        segment_metadata={},
        latest_event_cursor=latest_event_cursor,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        recovery_attempt_count=0,
        latest_checkpoint_id=latest_checkpoint_id,
        guardrail_summary=guardrail_summary,
    )


def _service(
    snapshot: RunSnapshot,
    *,
    checkpoint: DurableCheckpoint | None = None,
) -> tuple[RunRecoveryService, _RunStore]:
    """构造恢复服务与可观测 RunStore fake。"""

    run_store = _RunStore(snapshot)
    service = RunRecoveryService(
        run_store=cast(RunStorePort, run_store),
        checkpoint_store=cast(
            RunCheckpointStorePort, _CheckpointStore(checkpoint or _checkpoint())
        ),
        event_store=cast(RunEventStorePort, _EventStore()),
        retention_policy=_policy(),
        max_recovery_attempts=3,
        auto_recovery_enabled=True,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    return service, run_store


async def test_recovery_keeps_existing_guardrail_summary() -> None:
    """恢复入队优先保留快照中已有的 guardrail 摘要。"""

    summary = {
        "mode": GuardrailMode.OBSERVE.value,
        "action": GuardrailAction.OBSERVE.value,
        "message": "kept",
        "metadata": {"source": "run_runtime"},
        "evaluation_count": 3,
        "blocked_count": 1,
        "approval_request_count": 1,
        "last_event_cursor": 12,
        "updated_at": _NOW.isoformat(),
        "runtime_stats": {
            "total_tokens": 20,
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tool_calls": 4,
        },
        "stale": False,
        "stale_reason": None,
    }
    service, run_store = _service(
        _snapshot(latest_checkpoint_id="chk-previous", guardrail_summary=summary)
    )

    recovered = await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued[0]["guardrail_summary"] == summary
    assert recovered[0].guardrail_summary == summary
    recovered_summary = recovered[0].guardrail_summary
    assert recovered_summary is not None
    assert recovered_summary["evaluation_count"] == 3
    assert recovered_summary["runtime_stats"] == {
        "total_tokens": 20,
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tool_calls": 4,
    }


async def test_recovery_reuses_checkpoint_guardrail_summary_without_usage_recount() -> None:
    """快照缺失摘要时可复用 checkpoint 中已保存摘要但不得按 usage 回算统计。"""

    checkpoint_summary = {
        "mode": GuardrailMode.OBSERVE.value,
        "action": GuardrailAction.OBSERVE.value,
        "message": "from checkpoint",
        "metadata": {"source": "checkpoint"},
        "evaluation_count": 5,
        "blocked_count": 1,
        "approval_request_count": 0,
        "last_event_cursor": 10,
        "updated_at": _NOW.isoformat(),
        "runtime_stats": {
            "total_tokens": 20,
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tool_calls": 2,
            "consecutive_failure_count": 1,
        },
        "stale": False,
        "stale_reason": None,
    }
    service, run_store = _service(
        _snapshot(latest_checkpoint_id="chk-previous", latest_event_cursor=12),
        checkpoint=_checkpoint(
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            segment_metadata={"guardrail_summary": checkpoint_summary},
        ),
    )

    recovered = await service.sweep_expired_leases(now=_NOW)

    assert run_store.enqueued[0]["guardrail_summary"] == checkpoint_summary
    assert recovered[0].guardrail_summary == checkpoint_summary
    recovered_summary = recovered[0].guardrail_summary
    assert recovered_summary is not None
    assert recovered_summary["runtime_stats"] == {
        "total_tokens": 20,
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tool_calls": 2,
        "consecutive_failure_count": 1,
    }


async def test_recovery_blocks_waiting_child_run_without_reconciliation() -> None:
    """父 Run 等待 child run 且未对账时不得假定子流程成功。"""

    workflow_state = {
        "workflow_name": "code_change",
        "current_phase": "execute",
        "child_run_state": {
            "parent_run_id": "run-1",
            "child_run_id": "child-1",
            "phase": "execute",
            "role": "executor",
            "ownership_status": "parent_waiting_child",
            "reconciliation_status": "waiting",
            "reason": "child_run_policy_enabled",
        },
    }
    service, _run_store = _service(
        replace(
            _snapshot(latest_checkpoint_id="chk-previous"),
            workflow_run_state=workflow_state,
        ),
        checkpoint=_checkpoint(segment_metadata={"workflow_run_state": workflow_state}),
    )

    blocked = await service.evaluate_recovery(
        replace(
            _snapshot(latest_checkpoint_id="chk-previous"),
            workflow_run_state=workflow_state,
        )
    )

    assert blocked.recoverable is False
    assert blocked.reason == "workflow_state_invalid"
    assert blocked.error is not None
    assert blocked.error["error"] == "child_run_reconciliation_missing"


async def test_recovery_allows_reconciled_child_run_state() -> None:
    """已写 reconciliation 节点的 child run 状态可继续恢复。"""

    workflow_state = {
        "workflow_name": "code_change",
        "current_phase": "execute",
        "child_run_state": {
            "parent_run_id": "run-1",
            "child_run_id": "child-1",
            "phase": "execute",
            "role": "executor",
            "ownership_status": "parent_waiting_child",
            "reconciliation_status": "reconciled",
            "reason": "child_run_policy_enabled",
        },
    }
    service, _run_store = _service(
        replace(
            _snapshot(latest_checkpoint_id="chk-previous"),
            workflow_run_state=workflow_state,
        ),
        checkpoint=_checkpoint(segment_metadata={"workflow_run_state": workflow_state}),
    )

    decision = await service.evaluate_recovery(
        replace(
            _snapshot(latest_checkpoint_id="chk-previous"),
            workflow_run_state=workflow_state,
        )
    )

    assert decision.recoverable is True


async def test_recovery_marks_conservative_stale_summary_when_missing() -> None:
    """已有 checkpoint 但缺失摘要时写入保守 stale guardrail 摘要。"""

    service, run_store = _service(
        _snapshot(latest_checkpoint_id="chk-previous", latest_event_cursor=12),
        checkpoint=_checkpoint(
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        ),
    )

    recovered = await service.sweep_expired_leases(now=_NOW)

    summary = run_store.enqueued[0]["guardrail_summary"]
    assert summary is not None
    assert summary["mode"] == GuardrailMode.OBSERVE.value
    assert summary["action"] == GuardrailAction.OBSERVE.value
    assert summary["message"] == "guardrail summary recovered conservatively"
    assert summary["metadata"] == {"source": "checkpoint_recovery"}
    assert summary["evaluation_count"] == 0
    assert summary["blocked_count"] == 0
    assert summary["approval_request_count"] == 0
    assert summary["last_event_cursor"] == 12
    assert summary["runtime_stats"] == {}
    assert summary["stale"] is True
    assert summary["stale_reason"] == "recovered_without_persisted_guardrail_summary"
    assert isinstance(summary["updated_at"], str)
    assert recovered[0].guardrail_summary == summary
