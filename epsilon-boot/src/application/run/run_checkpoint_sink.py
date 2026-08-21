"""Run checkpoint sink 应用服务模块。"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from domain.chat.context import ConversationContext
from domain.model_access.value_objects import ToolCallRequest
from domain.run.checkpoint_context import get_run_checkpoint_context
from domain.run.exceptions import RunCheckpointWriteError
from domain.run.ports import (
    RunCheckpointSinkPort,
    RunCheckpointStorePort,
    RunEventAppenderPort,
)
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    RunEventType,
    ToolExecutionKey,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from domain.run.workflow_context import get_workflow_collaboration_context


class RunCheckpointSink(RunCheckpointSinkPort):
    """把 Agent 执行边界转换为持久化 checkpoint 与工具账本。"""

    def __init__(
        self,
        *,
        checkpoint_store: RunCheckpointStorePort,
        event_store: RunEventAppenderPort,
        retention_policy: CheckpointRetentionPolicy,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._checkpoint_store = checkpoint_store
        self._event_store = event_store
        self._retention_policy = retention_policy
        self._now = now or (lambda: datetime.now(UTC))

    async def model_completed(
        self,
        *,
        context: ConversationContext,
        round_num: int,
        usage: dict[str, int],
        trace_summary: dict[str, Any],
        segment_metadata: dict[str, Any],
    ) -> DurableCheckpoint:
        """模型调用完成后保存上下文检查点。"""

        return await self._save_checkpoint(
            phase=CheckpointPhase.MODEL_COMPLETED,
            context=context,
            round_num=round_num,
            usage=usage,
            trace_summary=trace_summary,
            segment_metadata=segment_metadata,
            tool_execution_key=None,
            tool_result_ref=None,
        )

    async def before_tool_call(
        self,
        *,
        tool_call: ToolCallRequest,
        round_num: int,
        segment_index: int,
        replay_policy: ToolReplayPolicy,
        side_effect_level: ToolSideEffectLevel,
        idempotency_key: str | None,
    ) -> ToolResultLedgerEntry | None:
        """工具执行前写 pending；若已有 completed 结果则返回 replay entry。"""

        run_id = self._run_id()
        execution_key = self._tool_execution_key(
            run_id=run_id,
            segment_index=segment_index,
            round_num=round_num,
            tool_call=tool_call,
        )
        stable_key = execution_key.stable_key()
        existing = await self._checkpoint_store.get_tool_result(run_id, stable_key)
        if existing is not None and existing.status is ToolLedgerStatus.COMPLETED:
            await self._event_store.append_event(
                run_id,
                RunEventType.TOOL_RESULT_REPLAYED,
                {
                    "tool_execution_key": stable_key,
                    "tool_name": existing.tool_name,
                    "tool_call_id": existing.tool_call_id,
                },
            )
            return existing

        now = self._now()
        entry = ToolResultLedgerEntry(
            run_id=run_id,
            tool_execution_key=stable_key,
            status=ToolLedgerStatus.PENDING,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments_digest=execution_key.arguments_digest,
            replay_policy=replay_policy,
            side_effect_level=side_effect_level,
            idempotency_key=idempotency_key,
            result=None,
            is_error=False,
            metadata={"arguments_json_valid": self._arguments_json_valid(tool_call.arguments)},
            created_at=now,
            updated_at=now,
        )
        try:
            await self._checkpoint_store.put_tool_pending(entry)
        except Exception as exc:
            raise RunCheckpointWriteError(run_id, stable_key, str(exc)) from exc
        return None

    async def after_tool_call(
        self,
        *,
        context: ConversationContext,
        tool_execution_key: str,
        result: str,
        is_error: bool,
        metadata: dict[str, Any],
        round_num: int,
        usage: dict[str, int],
    ) -> DurableCheckpoint:
        """工具完成后写 completed/error 账本并保存检查点。"""

        run_id = self._run_id()
        stored_result, result_truncated = self._truncate_text(result, "tool_result")
        result_metadata = dict(metadata)
        if result_truncated:
            result_metadata["truncated_fields"] = [
                *result_metadata.get("truncated_fields", []),
                "tool_result",
            ]
        completed = await self._checkpoint_store.complete_tool_result(
            run_id=run_id,
            tool_execution_key=tool_execution_key,
            result=stored_result,
            is_error=is_error,
            metadata=result_metadata,
        )
        return await self._save_checkpoint(
            phase=CheckpointPhase.TOOL_COMPLETED,
            context=context,
            round_num=round_num,
            usage=usage,
            trace_summary={
                "tool_name": completed.tool_name,
                "is_error": is_error,
                "tool_result_truncated": result_truncated,
            },
            segment_metadata=result_metadata,
            tool_execution_key=tool_execution_key,
            tool_result_ref=tool_execution_key,
            extra_truncated_fields=("tool_result",) if result_truncated else (),
        )

    async def approval_interrupt(
        self,
        *,
        context: ConversationContext,
        round_num: int,
        usage: dict[str, int],
        approval_id: str,
    ) -> DurableCheckpoint:
        """进入人工审批前保存审批中断检查点。"""

        return await self._save_checkpoint(
            phase=CheckpointPhase.APPROVAL_INTERRUPT,
            context=context,
            round_num=round_num,
            usage=usage,
            trace_summary={"approval_id": approval_id},
            segment_metadata={"approval_id": approval_id},
            tool_execution_key=None,
            tool_result_ref=None,
        )

    async def segment_done(
        self,
        *,
        context: ConversationContext,
        segment_metadata: dict[str, Any],
        usage: dict[str, int],
    ) -> DurableCheckpoint:
        """执行段结束后保存分段 checkpoint。"""

        return await self._save_checkpoint(
            phase=CheckpointPhase.SEGMENT_DONE,
            context=context,
            round_num=None,
            usage=usage,
            trace_summary={},
            segment_metadata=segment_metadata,
            tool_execution_key=None,
            tool_result_ref=None,
        )

    async def _save_checkpoint(
        self,
        *,
        phase: CheckpointPhase,
        context: ConversationContext,
        round_num: int | None,
        usage: dict[str, int],
        trace_summary: dict[str, Any],
        segment_metadata: dict[str, Any],
        tool_execution_key: str | None,
        tool_result_ref: str | None,
        extra_truncated_fields: tuple[str, ...] = (),
    ) -> DurableCheckpoint:
        run_id = self._run_id()
        context_snapshot, trace, truncated_fields = self._sanitize_payload(
            context.to_dict(),
            trace_summary,
        )
        checkpoint = DurableCheckpoint(
            run_id=run_id,
            checkpoint_id="pending",
            sequence=0,
            phase=phase,
            context_snapshot=context_snapshot,
            round_num=round_num,
            usage=dict(usage),
            trace_summary=trace,
            segment_metadata=_with_workflow_segment_metadata(segment_metadata),
            tool_execution_key=tool_execution_key,
            tool_result_ref=tool_result_ref,
            schema_version=1,
            sanitized=bool(truncated_fields) or bool(extra_truncated_fields),
            truncated_fields=(*truncated_fields, *extra_truncated_fields),
            created_at=self._now(),
        )
        try:
            saved = await self._checkpoint_store.save_checkpoint(checkpoint)
        except Exception as exc:
            raise RunCheckpointWriteError(run_id, checkpoint.checkpoint_id, str(exc)) from exc

        await self._event_store.append_event(
            run_id,
            RunEventType.CHECKPOINT_SAVED,
            {
                "checkpoint_id": saved.checkpoint_id,
                "sequence": saved.sequence,
                "phase": saved.phase.value,
            },
        )
        with contextlib.suppress(Exception):
            await self._checkpoint_store.trim_checkpoints(run_id, self._retention_policy)
        return saved

    def _run_id(self) -> str:
        current = get_run_checkpoint_context()
        if current is None:
            raise RunCheckpointWriteError("unknown", "unknown", "checkpoint context missing")
        return current.run_id

    def _sanitize_payload(
        self,
        context_snapshot: dict[str, Any],
        trace_summary: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        payload = {
            "context_snapshot": context_snapshot,
            "trace_summary": trace_summary,
        }
        if self._encoded_size(payload) <= self._retention_policy.max_payload_bytes:
            return context_snapshot, dict(trace_summary), []

        sanitized_trace: dict[str, Any] = {}
        truncated: list[str] = []
        for key, value in trace_summary.items():
            if isinstance(value, str):
                sanitized_trace[key], was_truncated = self._truncate_text(
                    value, f"trace_summary.{key}"
                )
                if was_truncated:
                    truncated.append(f"trace_summary.{key}")
            else:
                sanitized_trace[key] = value
        return context_snapshot, sanitized_trace, truncated

    def _truncate_text(self, value: str, field_name: str) -> tuple[str, bool]:
        max_chars = max(16, self._retention_policy.max_payload_bytes // 4)
        if len(value.encode("utf-8")) <= max_chars:
            return value, False
        return f"{value[:max_chars]}...[truncated:{field_name}]", True

    @staticmethod
    def _tool_execution_key(
        *,
        run_id: str,
        segment_index: int,
        round_num: int,
        tool_call: ToolCallRequest,
    ) -> ToolExecutionKey:
        return ToolExecutionKey(
            run_id=run_id,
            segment_index=segment_index,
            round_num=round_num,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments_digest=RunCheckpointSink._arguments_digest(tool_call.arguments),
        )

    @staticmethod
    def _arguments_digest(arguments: str) -> str:
        return ToolExecutionKey.digest_arguments(arguments)

    @staticmethod
    def _arguments_json_valid(arguments: str) -> bool:
        try:
            json.loads(arguments)
        except json.JSONDecodeError:
            return False
        return True

    @staticmethod
    def _encoded_size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _with_workflow_segment_metadata(
    segment_metadata: dict[str, Any],
) -> dict[str, Any]:
    """把当前 workflow context 摘要合并进 checkpoint segment metadata。"""

    metadata = dict(segment_metadata)
    workflow_context = get_workflow_collaboration_context()
    if workflow_context is None:
        return metadata
    metadata.setdefault(
        "workflow_run_state",
        {
            "workflow_name": workflow_context.workflow_name,
            "current_phase": workflow_context.phase.value
            if workflow_context.phase is not None
            else None,
        },
    )
    metadata.setdefault(
        "collaboration_summary",
        {
            "delegation_count": workflow_context.delegation_count,
            "handoff_count": workflow_context.handoff_count,
            "max_depth_seen": workflow_context.depth,
        },
    )
    return metadata
