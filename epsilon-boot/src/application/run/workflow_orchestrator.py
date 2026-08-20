"""Run workflow phase 编排器。

本模块在既有 Chat/Task 执行段外包装轻量 workflow phase 状态。它不执行
具体业务逻辑，也不改变 Chat/Task port 签名；每次调用最多推进一个 phase。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import is_dataclass, replace
from datetime import UTC, date, datetime
from enum import Enum, StrEnum
from time import time
from typing import Any
from uuid import uuid4

from application.run.serialization_ports import WorkflowSerializerPort
from domain.agent.ports import ApprovalStateStorePort
from domain.agent.value_objects import ApprovalInterrupt, PendingActionRequest
from domain.chat.context import ConversationContext
from domain.run.checkpoint_context import get_run_checkpoint_context
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import RunEventStorePort, RunStorePort, WorkflowRegistryPort
from domain.run.value_objects import RunCreateRequest, RunEventType, RunSnapshot, RunStatus
from domain.run.workflow import (
    ChildRunOrchestrationState,
    WorkflowCapabilityAction,
    WorkflowCapabilityCheck,
    WorkflowDefinition,
    WorkflowPhase,
    evaluate_role_capability,
)

ExecuteExisting = Callable[[RunSnapshot], Awaitable[RunExecutionOutcome]]


class WorkflowRunOrchestrator:
    """在现有 Chat/Task 执行段外包装 workflow phase 状态。"""

    def __init__(
        self,
        *,
        event_store: RunEventStorePort,
        workflow_registry: WorkflowRegistryPort,
        workflow_serializer: WorkflowSerializerPort,
        approval_store: ApprovalStateStorePort | None = None,
        run_store: RunStorePort | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化 workflow phase 编排器。"""

        self._event_store = event_store
        self._workflow_registry = workflow_registry
        self._workflow_serializer = workflow_serializer
        self._approval_store = approval_store
        self._run_store = run_store
        self._now = now or (lambda: datetime.now(UTC))

    async def execute_phase(
        self,
        *,
        snapshot: RunSnapshot,
        execute_existing: ExecuteExisting,
    ) -> RunExecutionOutcome:
        """推进当前 workflow phase，并委托既有执行函数完成阶段内执行。"""

        if snapshot.workflow_run_state is None:
            return await execute_existing(snapshot)

        try:
            workflow_name, phase = _workflow_identity(snapshot)
            workflow = self._workflow_registry.require_definition(workflow_name)
            phase_index = _phase_index(workflow, phase)
            started_at = _phase_started_at(snapshot.workflow_run_state, self._now())
            attempt = _phase_attempt(snapshot.workflow_run_state, phase)
            capability_outcome = await self._capability_rejection_outcome(
                snapshot=snapshot,
                workflow=workflow,
                phase=phase,
                phase_index=phase_index,
                started_at=started_at,
            )
            if capability_outcome is not None:
                return capability_outcome
            child_reconciliation_outcome = await self._child_run_reconciliation_outcome(
                snapshot=snapshot,
                workflow=workflow,
                phase=phase,
                phase_index=phase_index,
                started_at=started_at,
            )
            if child_reconciliation_outcome is not None:
                return child_reconciliation_outcome
            child_waiting_outcome = await self._child_run_waiting_outcome(
                snapshot=snapshot,
                workflow=workflow,
                phase=phase,
                phase_index=phase_index,
                started_at=started_at,
            )
            if child_waiting_outcome is not None:
                return child_waiting_outcome
            if _revise_limit_hit(snapshot.workflow_run_state, workflow, phase):
                return await self._limit_hit_outcome(
                    snapshot=snapshot,
                    workflow=workflow,
                    phase=phase,
                    started_at=started_at,
                    attempt=attempt,
                )
        except Exception as exc:
            return _failed_outcome(
                snapshot.workflow_run_state,
                "workflow_phase_invalid",
                exc,
            )

        await self._append_event(
            snapshot.run_id,
            RunEventType.WORKFLOW_PHASE_STARTED,
            {
                "workflow_name": workflow.name,
                "phase": phase.value,
                "role": workflow.phases[phase_index].role,
                "attempt": attempt,
                "started_at": started_at,
            },
        )

        outcome = await execute_existing(
            replace(
                snapshot,
                workflow_run_state={
                    **snapshot.workflow_run_state,
                    "phase_started_at": started_at.isoformat(),
                },
            )
        )
        if outcome.status is RunStatus.SUCCEEDED:
            return await self._completed_outcome(
                snapshot=snapshot,
                workflow=workflow,
                phase=phase,
                phase_index=phase_index,
                started_at=started_at,
                attempt=attempt,
                outcome=outcome,
            )
        if outcome.status is RunStatus.FAILED:
            return await self._failed_phase_outcome(
                snapshot=snapshot,
                workflow=workflow,
                phase=phase,
                started_at=started_at,
                attempt=attempt,
                outcome=outcome,
            )
        return await self._interrupted_phase_outcome(
            snapshot=snapshot,
            workflow=workflow,
            phase=phase,
            started_at=started_at,
            attempt=attempt,
            outcome=outcome,
        )

    async def _capability_rejection_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        phase_index: int,
        started_at: datetime,
    ) -> RunExecutionOutcome | None:
        """在真实 phase 执行前按 role capability 拒绝越权动作。"""

        state = snapshot.workflow_run_state or {}
        if not workflow.execution_policy.role_capability_enabled:
            return None
        action = _capability_action_from_state(state)
        if action is None:
            return None
        role = _active_role_for_phase(state, workflow, phase, phase_index)
        target = _capability_target_from_state(state, action)
        decision = evaluate_role_capability(
            roles=workflow.roles,
            check=WorkflowCapabilityCheck(
                action=action,
                role=role,
                target=target,
            ),
        )
        if decision.allowed:
            return None

        rejected_state = _capability_rejected_state(
            state,
            workflow=workflow,
            phase=phase,
            started_at=started_at,
            decision=self._workflow_serializer.workflow_capability_decision_to_dict(decision),
        )
        approval_id = await self._save_capability_approval(
            snapshot=snapshot,
            workflow=workflow,
            phase=phase,
            decision=self._workflow_serializer.workflow_capability_decision_to_dict(decision),
            state=rejected_state,
        )
        payload = {
            "workflow_name": workflow.name,
            "phase": phase.value,
            "active_role": role,
            "action": action.value,
            "target": target,
            "reason": decision.reason,
            "approval_id": approval_id,
            "workflow_run_state": rejected_state,
        }
        await self._append_event(
            snapshot.run_id,
            RunEventType.ROLE_CAPABILITY_REJECTED,
            payload,
        )
        return _awaiting_capability_approval_outcome(
            approval_id=approval_id,
            payload=payload,
            workflow_run_state=rejected_state,
        )

    async def _child_run_reconciliation_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        phase_index: int,
        started_at: datetime,
    ) -> RunExecutionOutcome | None:
        """父 Run 恢复时观察 child Run 终态并写入 reconciliation 节点。"""

        state = snapshot.workflow_run_state or {}
        child_state = state.get("child_run_state")
        if not isinstance(child_state, dict):
            return None
        if child_state.get("ownership_status") != "parent_waiting_child":
            return None
        if child_state.get("reconciliation_status") == "reconciled":
            return None
        child_run_id = child_state.get("child_run_id")
        if not isinstance(child_run_id, str) or not child_run_id.strip():
            return _recoverable_child_failure_outcome(
                state,
                reason="child_run_id_missing",
            )
        if self._run_store is None:
            return _recoverable_child_failure_outcome(
                state,
                reason="child_run_store_unavailable",
            )
        child_snapshot = await self._run_store.get_run(child_run_id)
        if child_snapshot is None:
            return _recoverable_child_failure_outcome(
                state,
                reason="child_run_missing",
            )
        if child_snapshot.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.LOST,
        }:
            return _child_run_waiting_outcome(
                child_run_id=child_run_id,
                workflow_run_state=state,
            )

        role = _active_role_for_phase(state, workflow, phase, phase_index)
        reconciled_child_state = self._workflow_serializer.child_run_orchestration_state_to_dict(
            ChildRunOrchestrationState(
                parent_run_id=snapshot.run_id,
                child_run_id=child_run_id,
                phase=phase,
                role=role,
                ownership_status="parent_resumed_after_child",
                reconciliation_status="reconciled",
                reason=f"child_run_terminal:{child_snapshot.status.value}",
                updated_at=self._now(),
            )
        )
        updated_state = {
            **state,
            "workflow_name": workflow.name,
            "current_phase": phase.value,
            "phase_started_at": started_at.isoformat(),
            "active_role": role,
            "child_run_state": reconciled_child_state,
            "phase_result_summary": {
                "status": child_snapshot.status.value,
                "terminal_reason": child_snapshot.terminal_reason,
                "child_run_id": child_run_id,
            },
            "phase_error_summary": None,
            "phase_history": _history(state),
            "revise_counts": _revise_counts(state),
        }
        await self._append_event(
            snapshot.run_id,
            RunEventType.CHILD_RUN_RECONCILED,
            {
                "parent_run_id": snapshot.run_id,
                "child_run_id": child_run_id,
                "child_status": child_snapshot.status.value,
                "phase": phase.value,
                "role": role,
                "reconciliation_status": "reconciled",
                "workflow_run_state": updated_state,
            },
        )
        return _child_run_reconciled_outcome(
            child_run_id=child_run_id,
            workflow_run_state=updated_state,
        )

    async def _child_run_waiting_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        phase_index: int,
        started_at: datetime,
    ) -> RunExecutionOutcome | None:
        """显式 child run 策略开启时写入链接并让父 Run 保守等待。"""

        state = snapshot.workflow_run_state or {}
        if not workflow.execution_policy.child_run_enabled:
            return None
        if not _child_run_requested(state):
            return None
        role = _active_role_for_phase(state, workflow, phase, phase_index)
        capability_decision = (
            evaluate_role_capability(
                roles=workflow.roles,
                check=WorkflowCapabilityCheck(
                    action=WorkflowCapabilityAction.CHILD_RUN,
                    role=role,
                    target=_capability_target_from_state(state, WorkflowCapabilityAction.CHILD_RUN),
                ),
            )
            if workflow.execution_policy.role_capability_enabled
            else None
        )
        if capability_decision is not None and not capability_decision.allowed:
            rejected_state = _capability_rejected_state(
                state,
                workflow=workflow,
                phase=phase,
                started_at=started_at,
                decision=self._workflow_serializer.workflow_capability_decision_to_dict(
                    capability_decision
                ),
            )
            approval_id = await self._save_capability_approval(
                snapshot=snapshot,
                workflow=workflow,
                phase=phase,
                decision=self._workflow_serializer.workflow_capability_decision_to_dict(
                    capability_decision
                ),
                state=rejected_state,
            )
            payload = {
                "workflow_name": workflow.name,
                "phase": phase.value,
                "active_role": role,
                "action": WorkflowCapabilityAction.CHILD_RUN.value,
                "target": capability_decision.target,
                "reason": capability_decision.reason,
                "approval_id": approval_id,
                "workflow_run_state": rejected_state,
            }
            await self._append_event(
                snapshot.run_id, RunEventType.ROLE_CAPABILITY_REJECTED, payload
            )
            return _awaiting_capability_approval_outcome(
                approval_id=approval_id,
                payload=payload,
                workflow_run_state=rejected_state,
            )
        child_run_id = await self._ensure_child_run(
            snapshot=snapshot,
            state=state,
            workflow=workflow,
            phase=phase,
            role=role,
        )
        child_state = self._workflow_serializer.child_run_orchestration_state_to_dict(
            ChildRunOrchestrationState(
                parent_run_id=snapshot.run_id,
                child_run_id=child_run_id,
                phase=phase,
                role=role,
                ownership_status="parent_waiting_child",
                reconciliation_status="waiting",
                reason="child_run_policy_enabled",
                updated_at=self._now(),
            )
        )
        updated_state = {
            **state,
            "workflow_name": workflow.name,
            "current_phase": phase.value,
            "phase_started_at": started_at.isoformat(),
            "active_role": role,
            "child_run_state": child_state,
            "phase_result_summary": {
                "status": RunStatus.PAUSED.value,
                "terminal_reason": "child_run_waiting",
                "child_run_id": child_run_id,
            },
            "phase_error_summary": None,
            "phase_history": _history(state),
            "revise_counts": _revise_counts(state),
        }
        await self._checkpoint_child_waiting_state(updated_state)
        linked_payload = {
            "parent_run_id": snapshot.run_id,
            "child_run_id": child_run_id,
            "phase": phase.value,
            "role": role,
            "reason": "child_run_policy_enabled",
            "ownership_status": "parent_waiting_child",
            "created_at": child_state["updated_at"],
            "workflow_run_state": updated_state,
        }
        await self._append_event(snapshot.run_id, RunEventType.CHILD_RUN_LINKED, linked_payload)
        await self._append_event(
            snapshot.run_id,
            RunEventType.CHILD_RUN_WAITING,
            {
                **linked_payload,
                "checkpoint_required": True,
            },
        )
        return _child_run_waiting_outcome(
            child_run_id=child_run_id,
            workflow_run_state=updated_state,
        )

    async def _ensure_child_run(
        self,
        *,
        snapshot: RunSnapshot,
        state: dict[str, Any],
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        role: str | None,
    ) -> str:
        """通过 RunStore 创建或验证真实 child Run，并返回实际 child_run_id。"""

        requested = _child_run_id(snapshot)
        if self._run_store is None:
            return requested
        existing = await self._run_store.get_run(requested)
        if existing is not None:
            return existing.run_id
        child_request = RunCreateRequest(
            payload=replace(snapshot.payload, model=snapshot.payload.model),
            client_request_id=(
                f"{snapshot.run_id}:child:{workflow.name}:{phase.value}:{role or 'none'}"
            ),
            payload_hash=None,
            created_by=snapshot.run_id,
            task_classification=snapshot.task_classification,
            workflow_name=workflow.name,
            workflow_run_state={
                "workflow_name": workflow.name,
                "current_phase": phase.value,
                "phase_started_at": None,
                "phase_history": [],
                "phase_result_summary": None,
                "phase_error_summary": None,
                "revise_counts": {},
                "active_role": role,
                "parent_run_id": snapshot.run_id,
            },
            collaboration_summary={
                "latest_steps": [],
                "child_links": [],
                "delegation_count": 0,
                "handoff_count": 0,
                "max_depth_seen": 0,
                "limit_hit_reason": None,
            },
        )
        child = await self._run_store.create_run(child_request)
        return child.run_id

    async def _checkpoint_child_waiting_state(self, workflow_run_state: dict[str, Any]) -> None:
        """在父 Run 进入 child waiting 前保存恢复所需 workflow state。"""

        checkpoint_context = get_run_checkpoint_context()
        if checkpoint_context is None:
            return
        context = ConversationContext()
        await checkpoint_context.sink.segment_done(
            context=context,
            segment_metadata={
                "segment_index": checkpoint_context.segment_index,
                "segment_stop_reason": "child_run_waiting",
                "workflow_run_state": workflow_run_state,
            },
            usage=dict(checkpoint_context.usage or {}),
        )

    async def _save_capability_approval(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        decision: dict[str, Any],
        state: dict[str, Any],
    ) -> str:
        """复用既有审批存储保存 role capability 审批中断。"""

        if self._approval_store is None:
            raise RuntimeError("workflow role capability approval store is not configured")
        approval_id = f"workflow_{uuid4().hex}"
        action = PendingActionRequest(
            tool_call_id=approval_id,
            tool_name="workflow_role_capability",
            arguments="{}",
            allowed_decisions=frozenset({"approve", "reject"}),
            reason=str(decision.get("reason") or "role_capability_rejected"),
        )
        now_epoch = time()
        interrupt = ApprovalInterrupt(
            session_id=_approval_session_id(snapshot),
            approval_id=approval_id,
            actions=(action,),
            context_snapshot={},
            round_num=0,
            model=snapshot.payload.model or "",
            usage_so_far={},
            created_at_epoch=now_epoch,
            metadata={
                "source": "workflow_role_capability",
                "workflow_name": workflow.name,
                "phase": phase.value,
                "run_id": snapshot.run_id,
                "decision": decision,
                "workflow_run_state": state,
            },
        )
        await self._approval_store.save(interrupt)
        return approval_id

    async def _record_handoff_if_needed(
        self,
        *,
        run_id: str,
        previous_state: dict[str, Any],
        workflow: WorkflowDefinition,
        source_phase: WorkflowPhase,
        target_phase: WorkflowPhase | None,
        resulting_state: dict[str, Any],
    ) -> dict[str, Any]:
        """当 workflow 控制权发生角色转移时记录 handoff 事件与状态。"""

        if target_phase is None:
            return resulting_state
        source_role = _active_role_for_phase(
            previous_state,
            workflow,
            source_phase,
            _phase_index(workflow, source_phase),
        )
        target_role = _phase_role(workflow, target_phase)
        required_target = workflow.execution_policy.phase_handoff_required.get(source_phase.value)
        if required_target:
            target_role = required_target
        if not target_role or target_role == source_role:
            return resulting_state

        handoff_state = {
            "status": "completed",
            "source_role": source_role,
            "target_role": target_role,
            "target_agent": _agent_for_role(workflow, target_role),
            "source_phase": source_phase.value,
            "target_phase": target_phase.value,
            "reason": "phase_handoff_required" if required_target else "phase_role_changed",
        }
        updated_state = {
            **resulting_state,
            "active_role": target_role,
            "handoff_state": handoff_state,
        }
        payload = {
            "workflow_name": workflow.name,
            "phase": target_phase.value,
            "source_role": source_role,
            "target_role": target_role,
            "target_agent": handoff_state["target_agent"],
            "reason": handoff_state["reason"],
            "workflow_run_state": updated_state,
        }
        await self._append_event(
            run_id,
            RunEventType.WORKFLOW_HANDOFF_RECORDED,
            payload,
        )
        return updated_state

    async def _completed_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        phase_index: int,
        started_at: datetime,
        attempt: int,
        outcome: RunExecutionOutcome,
    ) -> RunExecutionOutcome:
        """处理 phase 成功完成。"""

        completed_at = self._now()
        next_phase = _policy_next_phase(workflow, phase, phase_index)
        previous_state = snapshot.workflow_run_state or {}
        state = _completed_state(
            previous_state,
            workflow=workflow,
            phase=phase,
            started_at=started_at,
            completed_at=completed_at,
            outcome=outcome,
            next_phase=next_phase,
        )
        await self._append_event(
            snapshot.run_id,
            RunEventType.WORKFLOW_PHASE_COMPLETED,
            _phase_event_payload(
                workflow=workflow,
                phase=phase,
                role=workflow.phases[phase_index].role,
                attempt=attempt,
                status=outcome.status,
                started_at=started_at,
                completed_at=completed_at,
                outcome=outcome,
            ),
        )
        state = await self._record_handoff_if_needed(
            run_id=snapshot.run_id,
            previous_state=previous_state,
            workflow=workflow,
            source_phase=phase,
            target_phase=next_phase,
            resulting_state=state,
        )
        if next_phase is None:
            return replace(outcome, workflow_run_state=state)
        return replace(
            outcome,
            status=RunStatus.PAUSED,
            terminal_reason="workflow_phase_completed",
            can_continue=True,
            workflow_run_state=state,
        )

    async def _failed_phase_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        started_at: datetime,
        attempt: int,
        outcome: RunExecutionOutcome,
    ) -> RunExecutionOutcome:
        """处理 phase 失败。"""

        completed_at = self._now()
        state = _active_state(
            snapshot.workflow_run_state or {},
            phase=phase,
            started_at=started_at,
            outcome=outcome,
            error=True,
        )
        await self._append_event(
            snapshot.run_id,
            RunEventType.WORKFLOW_PHASE_FAILED,
            _phase_event_payload(
                workflow=workflow,
                phase=phase,
                role=_phase_role(workflow, phase),
                attempt=attempt,
                status=outcome.status,
                started_at=started_at,
                completed_at=completed_at,
                outcome=outcome,
            ),
        )
        return replace(outcome, workflow_run_state=state)

    async def _interrupted_phase_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        started_at: datetime,
        attempt: int,
        outcome: RunExecutionOutcome,
    ) -> RunExecutionOutcome:
        """处理 paused、awaiting approval 或 cancelled 等非成功终态。"""

        completed_at = self._now()
        state = _active_state(
            snapshot.workflow_run_state or {},
            phase=phase,
            started_at=started_at,
            outcome=outcome,
            error=False,
        )
        await self._append_event(
            snapshot.run_id,
            RunEventType.WORKFLOW_PHASE_COMPLETED,
            _phase_event_payload(
                workflow=workflow,
                phase=phase,
                role=_phase_role(workflow, phase),
                attempt=attempt,
                status=outcome.status,
                started_at=started_at,
                completed_at=completed_at,
                outcome=outcome,
            ),
        )
        return replace(outcome, workflow_run_state=state)

    async def _limit_hit_outcome(
        self,
        *,
        snapshot: RunSnapshot,
        workflow: WorkflowDefinition,
        phase: WorkflowPhase,
        started_at: datetime,
        attempt: int,
    ) -> RunExecutionOutcome:
        """处理 revise 次数限制命中。"""

        reason = (
            f"workflow phase revise limit exceeded: "
            f"{workflow.collaboration_limit.max_revise_per_phase}"
        )
        completed_at = self._now()
        outcome = _failed_limit_outcome(reason)
        state = _active_state(
            snapshot.workflow_run_state or {},
            phase=phase,
            started_at=started_at,
            outcome=outcome,
            error=True,
        )
        await self._append_event(
            snapshot.run_id,
            RunEventType.COLLABORATION_LIMIT_HIT,
            {
                "workflow_name": workflow.name,
                "phase": phase.value,
                "reason": reason,
                "attempt": attempt,
            },
        )
        await self._append_event(
            snapshot.run_id,
            RunEventType.WORKFLOW_PHASE_FAILED,
            _phase_event_payload(
                workflow=workflow,
                phase=phase,
                role=_phase_role(workflow, phase),
                attempt=attempt,
                status=outcome.status,
                started_at=started_at,
                completed_at=completed_at,
                outcome=outcome,
            ),
        )
        return replace(outcome, workflow_run_state=state)

    async def _append_event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
    ) -> None:
        """写入 JSON-safe workflow phase 事件。"""

        await self._event_store.append_event(run_id, event_type, _json_safe(payload))


def _child_run_requested(state: dict[str, Any]) -> bool:
    """判断 workflow state 是否显式请求 child run 编排。"""

    return bool(
        state.get("request_child_run")
        or state.get("pending_child_run")
        or state.get("pending_capability_action") == WorkflowCapabilityAction.CHILD_RUN.value
    )


def _child_run_id(snapshot: RunSnapshot) -> str:
    """返回待链接的 child run id；未提供时生成保守本地标识。"""

    state = snapshot.workflow_run_state or {}
    for key in ("child_run_id", "pending_child_run_id"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"child_{snapshot.run_id}"


def _child_run_waiting_outcome(
    *,
    child_run_id: str,
    workflow_run_state: dict[str, Any],
) -> RunExecutionOutcome:
    """构造父 Run 等待 child run 的 paused outcome。"""

    return RunExecutionOutcome(
        status=RunStatus.PAUSED,
        result={
            "kind": "workflow_child_run",
            "status": "waiting",
            "child_run_id": child_run_id,
        },
        terminal_reason="child_run_waiting",
        can_continue=True,
        segment_metadata={
            "segment_stop_reason": "child_run_waiting",
            "workflow_run_state": workflow_run_state,
        },
        workflow_run_state=workflow_run_state,
    )


def _child_run_reconciled_outcome(
    *,
    child_run_id: str,
    workflow_run_state: dict[str, Any],
) -> RunExecutionOutcome:
    """构造 child run 已对账后的可继续 paused outcome。"""

    return RunExecutionOutcome(
        status=RunStatus.PAUSED,
        result={
            "kind": "workflow_child_run",
            "status": "reconciled",
            "child_run_id": child_run_id,
        },
        terminal_reason="child_run_reconciled",
        can_continue=True,
        segment_metadata={
            "segment_stop_reason": "child_run_reconciled",
            "workflow_run_state": workflow_run_state,
        },
        workflow_run_state=workflow_run_state,
    )


def _recoverable_child_failure_outcome(
    state: dict[str, Any],
    *,
    reason: str,
) -> RunExecutionOutcome:
    """构造 child run 对账缺失时的保守可恢复失败状态。"""

    workflow_state = {
        **state,
        "phase_error_summary": {
            "status": RunStatus.PAUSED.value,
            "terminal_reason": "child_run_recoverable_failure",
            "reason": reason,
        },
    }
    return RunExecutionOutcome(
        status=RunStatus.PAUSED,
        result={"kind": "workflow_child_run", "status": "recoverable_failure", "reason": reason},
        terminal_reason="child_run_recoverable_failure",
        can_continue=True,
        segment_metadata={
            "segment_stop_reason": "child_run_recoverable_failure",
            "workflow_run_state": workflow_state,
        },
        workflow_run_state=workflow_state,
    )


def _capability_action_from_state(
    state: dict[str, Any],
) -> WorkflowCapabilityAction | None:
    """从 workflow_run_state 读取待判定动作。"""

    raw = state.get("pending_capability_action")
    if raw is None:
        raw = state.get("requested_capability_action")
    if raw is None:
        raw = state.get("next_action")
    if raw is None:
        return None
    try:
        return WorkflowCapabilityAction(str(raw))
    except ValueError:
        return None


def _capability_target_from_state(
    state: dict[str, Any],
    action: WorkflowCapabilityAction,
) -> str | None:
    """从 workflow_run_state 中读取动作目标。"""

    if action is WorkflowCapabilityAction.TOOL:
        keys = ("pending_tool_name", "tool_name", "target_tool")
    elif action is WorkflowCapabilityAction.DELEGATION:
        keys = ("pending_delegate_agent", "delegate_agent", "target_agent")
    elif action is WorkflowCapabilityAction.HANDOFF:
        keys = ("pending_handoff_agent", "handoff_agent", "target_agent")
    else:
        keys = ("pending_child_run_target", "child_run_target", "target_agent")
    for key in keys:
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _active_role_for_phase(
    state: dict[str, Any],
    workflow: WorkflowDefinition,
    phase: WorkflowPhase,
    phase_index: int,
) -> str | None:
    """读取当前活动角色；切换后优先使用 state.active_role 并回退 phase role。"""

    active = state.get("active_role")
    if isinstance(active, str) and active.strip():
        return active.strip()
    phase_role = workflow.phases[phase_index].role
    if phase_role:
        return phase_role
    return _phase_role(workflow, phase)


def _capability_rejected_state(
    state: dict[str, Any],
    *,
    workflow: WorkflowDefinition,
    phase: WorkflowPhase,
    started_at: datetime,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """构造 capability 拒绝后的 workflow_run_state。"""

    action = str(decision.get("action") or "")
    target = decision.get("target")
    reason = _safe_text(decision.get("reason")) or "role_capability_rejected"
    rejected = {
        **state,
        "workflow_name": workflow.name,
        "current_phase": phase.value,
        "phase_started_at": started_at.isoformat(),
        "active_role": decision.get("role"),
        "phase_result_summary": None,
        "phase_error_summary": {
            "status": RunStatus.AWAITING_APPROVAL.value,
            "terminal_reason": "role_capability_rejected",
            "action": action,
            "target": target,
            "reason": reason,
        },
        "phase_history": _history(state),
        "revise_counts": _revise_counts(state),
    }
    if action in {
        WorkflowCapabilityAction.HANDOFF.value,
        WorkflowCapabilityAction.DELEGATION.value,
    }:
        rejected["handoff_state"] = {
            "status": "rejected",
            "source_role": decision.get("role"),
            "target_agent": target,
            "action": action,
            "reason": reason,
        }
    return rejected


def _awaiting_capability_approval_outcome(
    *,
    approval_id: str,
    payload: dict[str, Any],
    workflow_run_state: dict[str, Any],
) -> RunExecutionOutcome:
    """构造 role capability 拒绝后的 awaiting approval outcome。"""

    return RunExecutionOutcome(
        status=RunStatus.AWAITING_APPROVAL,
        result={
            "kind": "workflow_role_capability",
            "status": "approval_required",
            "approval_id": approval_id,
            "reason": payload.get("reason"),
            "action": payload.get("action"),
            "target": payload.get("target"),
            "action_requests": [
                {
                    "tool_call_id": approval_id,
                    "tool_name": "workflow_role_capability",
                    "arguments": "{}",
                    "allowed_decisions": ["approve", "reject"],
                    "reason": payload.get("reason"),
                }
            ],
        },
        terminal_reason="role_capability_rejected",
        can_continue=False,
        approval_id=approval_id,
        segment_metadata={
            "risk_gate_required": True,
            "guardrail_reason": "role_capability_rejected",
        },
        workflow_run_state=workflow_run_state,
    )


def _approval_session_id(snapshot: RunSnapshot) -> str:
    """返回 workflow capability 审批复用的 session_id。"""

    if snapshot.payload.session_id:
        return snapshot.payload.session_id
    task_payload = snapshot.payload.task or {}
    chat_payload = snapshot.payload.chat or {}
    for payload in (task_payload, chat_payload):
        value = payload.get("session_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return snapshot.run_id


def _workflow_identity(snapshot: RunSnapshot) -> tuple[str, WorkflowPhase]:
    """从快照提取 workflow 名称和当前 phase。"""

    state = snapshot.workflow_run_state or {}
    workflow_name = state.get("workflow_name") or snapshot.workflow_name
    if not isinstance(workflow_name, str) or not workflow_name.strip():
        raise ValueError("workflow_run_state 缺少 workflow_name")
    raw_phase = state.get("current_phase")
    if raw_phase is None:
        raise ValueError("workflow_run_state 缺少 current_phase")
    return workflow_name.strip(), WorkflowPhase(str(raw_phase))


def _phase_index(workflow: WorkflowDefinition, phase: WorkflowPhase) -> int:
    """返回 phase 在定义中的位置。"""

    for index, definition in enumerate(workflow.phases):
        if definition.phase is phase:
            return index
    raise ValueError(f"workflow {workflow.name} 不包含 phase {phase.value}")


def _phase_role(workflow: WorkflowDefinition, phase: WorkflowPhase) -> str | None:
    """返回 phase role。"""

    return workflow.phases[_phase_index(workflow, phase)].role


def _next_phase(
    workflow: WorkflowDefinition,
    phase_index: int,
) -> WorkflowPhase | None:
    """返回后续 phase。"""

    next_index = phase_index + 1
    if next_index >= len(workflow.phases):
        return None
    return workflow.phases[next_index].phase


def _policy_next_phase(
    workflow: WorkflowDefinition,
    phase: WorkflowPhase,
    phase_index: int,
) -> WorkflowPhase | None:
    """按执行策略返回后续 phase。"""

    raw_revise = workflow.execution_policy.revise_target_phase.get(phase.value)
    if raw_revise:
        try:
            return WorkflowPhase(raw_revise)
        except ValueError:
            return _next_phase(workflow, phase_index)
    if phase.value in workflow.execution_policy.review_required_phases:
        try:
            return WorkflowPhase.EVALUATE
        except ValueError:  # pragma: no cover - enum 常量防御。
            return _next_phase(workflow, phase_index)
    return _next_phase(workflow, phase_index)


def _agent_for_role(workflow: WorkflowDefinition, role: str | None) -> str | None:
    """返回 role 声明中的首个 agent 名称。"""

    if role is None:
        return None
    for capability in workflow.roles:
        if capability.role == role and capability.agent_names:
            return capability.agent_names[0]
    return None


def _phase_started_at(state: dict[str, Any], fallback: datetime) -> datetime:
    """读取当前 phase started_at；缺失或非法时使用 fallback。"""

    raw = state.get("phase_started_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return fallback
    return fallback


def _phase_attempt(state: dict[str, Any], phase: WorkflowPhase) -> int:
    """根据 phase history 计算当前 phase 尝试次数。"""

    history = state.get("phase_history")
    if not isinstance(history, list):
        return 1
    count = sum(
        1 for item in history if isinstance(item, dict) and item.get("phase") == phase.value
    )
    return count + 1


def _revise_limit_hit(
    state: dict[str, Any],
    workflow: WorkflowDefinition,
    phase: WorkflowPhase,
) -> bool:
    """判断 revise phase 是否超过配置上限。"""

    if phase is not WorkflowPhase.REVISE:
        return False
    revise_counts = state.get("revise_counts")
    count = revise_counts.get("revise", 0) if isinstance(revise_counts, dict) else 0
    return int(count) >= workflow.collaboration_limit.max_revise_per_phase


def _completed_state(
    state: dict[str, Any],
    *,
    workflow: WorkflowDefinition,
    phase: WorkflowPhase,
    started_at: datetime,
    completed_at: datetime,
    outcome: RunExecutionOutcome,
    next_phase: WorkflowPhase | None,
) -> dict[str, Any]:
    """构造 phase 完成后的 workflow state。"""

    history = _history(state)
    revise_counts = _revise_counts(state)
    revise_count = revise_counts.get(phase.value, 0)
    if phase is WorkflowPhase.REVISE:
        revise_count += 1
        revise_counts[phase.value] = revise_count

    history.append(
        {
            "phase": phase.value,
            "status": "completed",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "summary": _outcome_summary(outcome),
            "error": None,
            "revise_count": revise_count,
        }
    )
    effective_phase = next_phase if next_phase is not None else phase
    return {
        **state,
        "workflow_name": workflow.name,
        "current_phase": effective_phase.value,
        "phase_started_at": None,
        "phase_history": history,
        "phase_result_summary": _outcome_summary(outcome),
        "phase_error_summary": None,
        "revise_counts": revise_counts,
        "active_role": _phase_role(workflow, effective_phase),
    }


def _active_state(
    state: dict[str, Any],
    *,
    phase: WorkflowPhase,
    started_at: datetime,
    outcome: RunExecutionOutcome,
    error: bool,
) -> dict[str, Any]:
    """构造 phase 未完成但已有执行结果后的 workflow state。"""

    summary = _outcome_summary(outcome)
    return {
        **state,
        "current_phase": phase.value,
        "phase_started_at": started_at.isoformat(),
        "phase_result_summary": None if error else summary,
        "phase_error_summary": summary if error else None,
        "phase_history": _history(state),
        "revise_counts": _revise_counts(state),
    }


def _history(state: dict[str, Any]) -> list[dict[str, Any]]:
    """返回 JSON-safe phase history 副本。"""

    raw = state.get("phase_history")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _revise_counts(state: dict[str, Any]) -> dict[str, int]:
    """返回 revise_counts 副本。"""

    raw = state.get("revise_counts")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _phase_event_payload(
    *,
    workflow: WorkflowDefinition,
    phase: WorkflowPhase,
    role: str | None,
    attempt: int,
    status: RunStatus,
    started_at: datetime,
    completed_at: datetime,
    outcome: RunExecutionOutcome,
) -> dict[str, Any]:
    """构造 workflow phase 事件 payload。"""

    return {
        "workflow_name": workflow.name,
        "phase": phase.value,
        "role": role,
        "attempt": attempt,
        "status": status.value,
        "started_at": started_at,
        "completed_at": completed_at,
        "summary": _outcome_summary(outcome),
    }


def _outcome_summary(outcome: RunExecutionOutcome) -> dict[str, Any]:
    """提取不包含原始大 payload 的 outcome 摘要。"""

    result = outcome.result if isinstance(outcome.result, dict) else {}
    error = outcome.error if isinstance(outcome.error, dict) else {}
    return {
        "status": outcome.status.value,
        "terminal_reason": _safe_text(outcome.terminal_reason),
        "can_continue": outcome.can_continue,
        "approval_id": outcome.approval_id,
        "result_kind": _safe_text(result.get("kind")),
        "error_type": _safe_text(error.get("type")),
        "error_message": _safe_text(error.get("message")),
    }


def _failed_limit_outcome(reason: str) -> RunExecutionOutcome:
    """构造 revise limit failed outcome。"""

    return RunExecutionOutcome(
        status=RunStatus.FAILED,
        error={"message": reason, "type": "RunCollaborationLimitExceededError"},
        terminal_reason="workflow_collaboration_limit_hit",
        can_continue=False,
        segment_metadata={},
    )


def _failed_outcome(
    state: dict[str, Any] | None,
    reason: str,
    exc: Exception,
) -> RunExecutionOutcome:
    """构造 workflow state 解析失败 outcome。"""

    workflow_state = dict(state or {})
    workflow_state["phase_error_summary"] = {
        "status": RunStatus.FAILED.value,
        "terminal_reason": reason,
        "error_type": type(exc).__name__,
        "error_message": _safe_text(str(exc)),
    }
    return RunExecutionOutcome(
        status=RunStatus.FAILED,
        error={"message": str(exc), "type": type(exc).__name__},
        terminal_reason=reason,
        can_continue=False,
        segment_metadata={},
        workflow_run_state=workflow_state,
    )


def _safe_text(value: Any, *, max_length: int = 200) -> str | None:
    """返回短文本摘要。"""

    if value is None:
        return None
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _json_safe(value: Any) -> Any:
    """递归转换为 JSON-safe 结构。"""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (StrEnum, Enum)):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return _json_safe(value.__dict__)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)
