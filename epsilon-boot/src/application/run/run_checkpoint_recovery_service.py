"""Run checkpoint 恢复判定应用服务模块。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from application.run.serialization_ports import GuardrailSerializerPort
from domain.agent.guardrails import mark_guardrail_summary_stale
from domain.chat.context import ConversationContext
from domain.run.ports import RunCheckpointStorePort, RunEventStorePort, RunStorePort
from domain.run.value_objects import (
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RecoveryDecision,
    RunEventType,
    RunSnapshot,
    RunStatus,
    ToolLedgerStatus,
    ToolReplayPolicy,
)
from domain.run.workflow import WorkflowPhase


class RunRecoveryService:
    """对租约过期 Run 做 checkpoint 自动恢复判定。"""

    def __init__(
        self,
        *,
        run_store: RunStorePort,
        checkpoint_store: RunCheckpointStorePort,
        event_store: RunEventStorePort,
        retention_policy: CheckpointRetentionPolicy,
        max_recovery_attempts: int,
        auto_recovery_enabled: bool,
        guardrail_serializer: GuardrailSerializerPort,
    ) -> None:
        self._run_store = run_store
        self._checkpoint_store = checkpoint_store
        self._event_store = event_store
        self._retention_policy = retention_policy
        self._max_recovery_attempts = max_recovery_attempts
        self._auto_recovery_enabled = auto_recovery_enabled
        self._guardrail_serializer = guardrail_serializer

    async def sweep_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        """扫描过期租约，能恢复则重新入队，否则保守标记 lost。"""

        snapshots = await self._run_store.list_expired_leased_runs(now=now)
        results: list[RunSnapshot] = []
        for snapshot in snapshots:
            decision = await self.evaluate_recovery(snapshot)
            if decision.recoverable and decision.checkpoint_id is not None:
                checkpoint = await self._checkpoint_store.latest_checkpoint(snapshot.run_id)
                workflow_run_state, collaboration_summary = _recovery_workflow_fields(
                    snapshot,
                    checkpoint,
                )
                guardrail_summary = _recovery_guardrail_summary(
                    snapshot,
                    checkpoint,
                    self._guardrail_serializer,
                )
                recovered = await self._run_store.enqueue_recovery(
                    run_id=snapshot.run_id,
                    latest_checkpoint_id=decision.checkpoint_id,
                    recovery_attempt_count=snapshot.recovery_attempt_count + 1,
                    guardrail_summary=guardrail_summary,
                    workflow_run_state=workflow_run_state,
                    collaboration_summary=collaboration_summary,
                )
                await self._event_store.append_event(
                    snapshot.run_id,
                    RunEventType.RUN_RECOVERY_QUEUED,
                    {
                        "checkpoint_id": decision.checkpoint_id,
                        "recovery_attempt_count": recovered.recovery_attempt_count,
                    },
                )
                results.append(recovered)
                continue

            lost = await self._run_store.mark_lost_expired_run(
                run_id=snapshot.run_id,
                reason=decision.reason,
                recovery_error=decision.error or {"reason": decision.reason},
            )
            await self._event_store.append_event(
                snapshot.run_id,
                RunEventType.RUN_RECOVERY_FAILED,
                decision.error or {"reason": decision.reason},
            )
            results.append(lost)
        return results

    async def evaluate_recovery(self, snapshot: RunSnapshot) -> RecoveryDecision:
        """评估单个过期 Run 是否满足自动恢复前置条件。"""

        if not self._auto_recovery_enabled:
            return self._blocked("auto_recovery_disabled")
        if snapshot.status is RunStatus.CANCEL_REQUESTED:
            return self._blocked("cancel_requested")
        if snapshot.recovery_attempt_count >= self._max_recovery_attempts:
            return self._blocked("recovery_attempts_exhausted")

        checkpoint = await self._checkpoint_store.latest_checkpoint(snapshot.run_id)
        if checkpoint is None:
            return self._blocked("checkpoint_missing")
        if checkpoint.schema_version != 1:
            return self._blocked(
                "checkpoint_schema_incompatible",
                checkpoint_id=checkpoint.checkpoint_id,
                schema_version=checkpoint.schema_version,
            )

        try:
            ConversationContext.from_dict(checkpoint.context_snapshot)
        except Exception as exc:
            return self._blocked(
                "context_deserialize_failed",
                checkpoint_id=checkpoint.checkpoint_id,
                error=str(exc),
            )

        workflow_error = _workflow_state_error(snapshot, checkpoint)
        if workflow_error is not None:
            return self._blocked(
                "workflow_state_invalid",
                checkpoint_id=checkpoint.checkpoint_id,
                error=workflow_error,
            )

        ledger = await self._checkpoint_store.list_tool_ledger(snapshot.run_id)
        for entry in ledger:
            if entry.status is not ToolLedgerStatus.PENDING:
                continue
            if entry.replay_policy in {
                ToolReplayPolicy.MANUAL_REVIEW,
                ToolReplayPolicy.NEVER_REPLAY,
            }:
                return self._blocked(
                    "pending_tool_replay_blocked",
                    checkpoint_id=checkpoint.checkpoint_id,
                    tool_name=entry.tool_name,
                    tool_execution_key=entry.tool_execution_key,
                )

        return RecoveryDecision(
            recoverable=True,
            reason="compatible_checkpoint",
            checkpoint_id=checkpoint.checkpoint_id,
        )

    @staticmethod
    def _blocked(
        reason: str,
        **error: Any,
    ) -> RecoveryDecision:
        payload = {"reason": reason, **error}
        return RecoveryDecision(recoverable=False, reason=reason, error=payload)


def _recovery_guardrail_summary(
    snapshot: RunSnapshot,
    checkpoint: DurableCheckpoint | None,
    guardrail_serializer: GuardrailSerializerPort,
) -> dict[str, Any] | None:
    """返回恢复入队时应保留或保守标记的 guardrail 摘要。

    恢复路径只复用快照或 checkpoint 中已经持久化的摘要统计，严禁根据
    checkpoint.usage、上下文消息或工具账本回算 token、工具调用与失败次数，
    避免把历史已提交的运行时统计重复累计。
    """

    if snapshot.guardrail_summary is not None:
        return snapshot.guardrail_summary
    checkpoint_summary = _checkpoint_guardrail_summary(checkpoint)
    if checkpoint_summary is not None:
        return checkpoint_summary
    if snapshot.latest_checkpoint_id is None:
        return None

    base_summary: dict[str, Any] | None = None
    if snapshot.latest_event_cursor is not None:
        base_summary = {
            "mode": "observe",
            "action": "observe",
            "message": "guardrail summary recovered conservatively",
            "metadata": {"source": "checkpoint_recovery"},
            "evaluation_count": 0,
            "blocked_count": 0,
            "approval_request_count": 0,
            "last_event_cursor": snapshot.latest_event_cursor,
            "runtime_stats": {},
            "stale": False,
            "stale_reason": None,
        }
    return guardrail_serializer.guardrail_summary_to_dict(
        mark_guardrail_summary_stale(
            base_summary,
            reason="recovered_without_persisted_guardrail_summary",
            updated_at=datetime.now(UTC),
        )
    )


def _checkpoint_guardrail_summary(
    checkpoint: DurableCheckpoint | None,
) -> dict[str, Any] | None:
    """从 checkpoint segment metadata 读取已持久化 guardrail 摘要。"""

    metadata = checkpoint.segment_metadata if checkpoint is not None else {}
    candidate = metadata.get("guardrail_summary")
    return cast(dict[str, Any], candidate) if isinstance(candidate, dict) else None


def _recovery_workflow_fields(
    snapshot: RunSnapshot,
    checkpoint: DurableCheckpoint | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """返回恢复入队时应持久化的 workflow/collaboration 字段。"""

    metadata = checkpoint.segment_metadata if checkpoint is not None else {}
    workflow_run_state = snapshot.workflow_run_state
    if workflow_run_state is None:
        candidate = metadata.get("workflow_run_state")
        workflow_run_state = (
            cast(dict[str, Any], candidate) if isinstance(candidate, dict) else None
        )

    collaboration_summary = snapshot.collaboration_summary
    if collaboration_summary is None:
        candidate = metadata.get("collaboration_summary")
        collaboration_summary = (
            cast(dict[str, Any], candidate) if isinstance(candidate, dict) else None
        )
    return workflow_run_state, collaboration_summary


def _workflow_state_error(
    snapshot: RunSnapshot,
    checkpoint: DurableCheckpoint | None,
) -> str | None:
    """校验可恢复 workflow state；无 workflow state 时不阻断恢复。"""

    workflow_run_state, _ = _recovery_workflow_fields(snapshot, checkpoint)
    if workflow_run_state is None:
        return None
    raw_phase = workflow_run_state.get("current_phase")
    if raw_phase is None:
        return None
    try:
        WorkflowPhase(str(raw_phase))
    except ValueError:
        return "workflow_phase_invalid"
    child_state = workflow_run_state.get("child_run_state")
    if isinstance(child_state, dict):
        child_state = cast(dict[str, Any], child_state)
        ownership = child_state.get("ownership_status")
        reconciliation = child_state.get("reconciliation_status")
        if ownership == "parent_waiting_child" and reconciliation != "reconciled":
            return "child_run_reconciliation_missing"
    return None
