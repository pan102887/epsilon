"""指标 4：Real_Task_Golden_Success_Rate（真实任务 golden set 成功率）。

本指标读取 ``tests/evaluation/datasets/real_task_golden.jsonl``，用声明式 case
驱动真实 Run 应用服务、worker、workflow 选择与 checkpoint recovery 服务。
模型和外部工具保持 deterministic stub，避免网络、凭证和随机性进入 CI。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from application.run.run_application_service import (
    ApprovalResumeResult,
    RunApplicationService,
)
from application.run.run_checkpoint_recovery_service import RunRecoveryService
from application.run.run_execution_coordinator import RunExecutionOutcome
from domain.agent.value_objects import ApprovalDecision
from domain.chat.context import ConversationContext
from domain.run.exceptions import RunContinuationUnavailableError, RunLeaseConflictError
from domain.run.ports import ApprovalResumeStoreResult, WorkflowSelection
from domain.run.value_objects import (
    CheckpointPhase,
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunKind,
    RunLease,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from domain.run.workflow import (
    AgentRoleCapability,
    CollaborationLimit,
    WorkflowApplicableCondition,
    WorkflowDefinition,
    WorkflowPhase,
    WorkflowPhaseDefinition,
)
from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter
from infrastructure.run.run_worker import RunWorker
from tests.evaluation.errors import SampleExecutionError
from tests.evaluation.runner.models import (
    EvalCase,
    EvalSampleResult,
    MetricId,
    SampleOutcome,
)
from tests.evaluation.runner.sample_sink import SampleSink

pytestmark = pytest.mark.evaluation

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_DATASET = Path(__file__).resolve().parents[1] / "datasets" / "real_task_golden.jsonl"


def _load_cases() -> list[EvalCase]:
    """读取 JSONL golden set 并转为 EvalCase。"""

    cases: list[EvalCase] = []
    for line_no, raw in enumerate(_DATASET.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        data = json.loads(raw)
        _validate_case(data, line_no)
        cases.append(
            EvalCase(
                case_id=data["id"],
                metric=MetricId.REAL_TASK_GOLDEN_SUCCESS_RATE,
                description=data["description"],
                inputs={
                    "entrypoint": data["entrypoint"],
                    "input": data["input"],
                    "script": data["script"],
                },
                expected=data["expected"],
            )
        )
    return cases


def _validate_case(data: dict[str, Any], line_no: int) -> None:
    """对 case 做最小 schema 校验，避免拼写错误静默通过。"""

    for key in ("id", "description", "entrypoint", "input", "script", "expected"):
        if key not in data:
            raise ValueError(f"real_task_golden.jsonl:{line_no} 缺少字段 {key!r}")
    if data["entrypoint"] not in {"run", "recovery"}:
        raise ValueError(f"real_task_golden.jsonl:{line_no} entrypoint 非法")
    if "final_status" not in data["expected"]:
        raise ValueError(f"real_task_golden.jsonl:{line_no} expected 缺少 final_status")
    RunStatus(data["expected"]["final_status"])
    for event in data["expected"].get("must_events", []):
        RunEventType(event)
    for event in data["expected"].get("forbidden_events", []):
        RunEventType(event)


REAL_TASK_GOLDEN_CASES = _load_cases()


@pytest.mark.parametrize("case", REAL_TASK_GOLDEN_CASES, ids=lambda c: c.case_id)
def test_real_task_golden_case(case: EvalCase, sample_sink: SampleSink) -> None:
    """执行单条真实任务 golden case 并写入样本 sink。"""

    try:
        details = asyncio.run(_run_case(case))
    except Exception as exc:
        sample_sink.append(
            EvalSampleResult(
                case_id=case.case_id,
                metric=MetricId.REAL_TASK_GOLDEN_SUCCESS_RATE,
                outcome=SampleOutcome.ERROR,
                numerator=0,
                denominator=0,
                error=str(SampleExecutionError(case.case_id, exc)),
            )
        )
        return

    failures = _evaluate_details(details, case.expected)
    sample_sink.append(
        EvalSampleResult(
            case_id=case.case_id,
            metric=MetricId.REAL_TASK_GOLDEN_SUCCESS_RATE,
            outcome=SampleOutcome.PASS if not failures else SampleOutcome.FAIL,
            numerator=1 if not failures else 0,
            denominator=1,
            details={**details, "failures": failures},
        )
    )


async def _run_case(case: EvalCase) -> dict[str, Any]:
    """按 entrypoint 执行 case，返回统一观测详情。"""

    entrypoint = case.inputs["entrypoint"]
    if entrypoint == "run":
        return await _run_runtime_case(case)
    if entrypoint == "recovery":
        return await _run_recovery_case(case)
    raise ValueError(f"未知 entrypoint: {entrypoint!r}")


async def _run_runtime_case(case: EvalCase) -> dict[str, Any]:
    """执行 RunApplicationService + RunWorker 路径。"""

    script = case.inputs["script"]
    outcomes = [_outcome(item) for item in script.get("outcomes", [])]
    coordinator = _SequenceCoordinator(outcomes)
    service, _, events, worker = _runtime_fixture(
        coordinator,
        workflow_script=script.get("workflow"),
    )
    created = await service.create_run(_create_request(case))

    await _drain_worker(worker)
    for action in script.get("actions", []):
        if action == "continue":
            await service.continue_run(created.run_id)
        elif action == "approve":
            await service.resume_approval_run(
                created.run_id,
                [ApprovalDecision(type="approve", tool_call_id="call-1")],
            )
        else:
            raise ValueError(f"未知 case action: {action!r}")
        await _drain_worker(worker)

    snapshot = await service.get_run(created.run_id)
    if snapshot is None:
        raise AssertionError(f"Run {created.run_id} 不存在")
    return _snapshot_details(snapshot, events.events.get(created.run_id, []))


async def _run_recovery_case(case: EvalCase) -> dict[str, Any]:
    """执行 RunRecoveryService 路径。"""

    checkpoint_script = case.inputs["script"].get("checkpoint", {})
    snapshot = _leased_snapshot(
        case,
        latest_checkpoint_id=checkpoint_script.get("latest_checkpoint_id"),
    )
    run_store = _RecoveryRunStore([snapshot])
    event_store = _RecoveryEventStore()
    checkpoint_store = _RecoveryCheckpointStore(
        _checkpoint(case, checkpoint_script) if checkpoint_script.get("present") else None
    )
    service = RunRecoveryService(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        event_store=event_store,
        retention_policy=CheckpointRetentionPolicy(10, 3600, 4096, 100),
        max_recovery_attempts=3,
        auto_recovery_enabled=True,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )

    recovered = await service.sweep_expired_leases(now=_NOW)
    if not recovered:
        raise AssertionError("recovery service 未返回快照")
    return _snapshot_details(recovered[0], event_store.events)


async def _drain_worker(worker: RunWorker) -> None:
    """执行一个 queued run，没有 queued 项则失败。"""

    ran = await worker.run_once()
    if not ran:
        raise AssertionError("worker 未执行任何 queued run")


def _outcome(data: dict[str, Any]) -> RunExecutionOutcome:
    """把 JSON case outcome 转为 RunExecutionOutcome。"""

    return RunExecutionOutcome(
        status=RunStatus(data["status"]),
        result=data.get("result"),
        error=data.get("error"),
        terminal_reason=data.get("terminal_reason"),
        can_continue=bool(data.get("can_continue", False)),
        approval_id=data.get("approval_id"),
        segment_metadata=data.get("segment_metadata"),
        workflow_run_state=data.get("workflow_run_state"),
        collaboration_summary=data.get("collaboration_summary"),
    )


def _create_request(case: EvalCase) -> RunCreateRequest:
    """根据 case input 构造 RunCreateRequest。"""

    data = case.inputs["input"]
    kind = RunKind(data["kind"])
    payload = RunPayload(
        kind=kind,
        session_id=data.get("session_id"),
        chat=(
            {"session_id": data.get("session_id"), "message": data.get("message", "")}
            if kind is RunKind.CHAT
            else None
        ),
        task=({"goal": data.get("goal", "")} if kind is RunKind.TASK else None),
        model=data.get("model"),
    )
    return RunCreateRequest(
        payload=payload,
        client_request_id=f"client-{case.case_id}",
        guardrail_summary=data.get("guardrail_summary"),
        workflow_name=data.get("workflow_name"),
    )


def _snapshot_details(snapshot: RunSnapshot, events: list[RunEvent]) -> dict[str, Any]:
    """把运行快照与事件转换成断言友好的 dict。"""

    return {
        "run_id": snapshot.run_id,
        "final_status": snapshot.status.value,
        "terminal_reason": snapshot.terminal_reason,
        "result": snapshot.result,
        "error": snapshot.error,
        "can_continue": snapshot.can_continue,
        "approval_id": snapshot.approval_id,
        "latest_checkpoint_id": snapshot.latest_checkpoint_id,
        "recovery_attempt_count": snapshot.recovery_attempt_count,
        "workflow_name": snapshot.workflow_name,
        "workflow_run_state": snapshot.workflow_run_state,
        "guardrail_summary": snapshot.guardrail_summary,
        "events": [event.event_type.value for event in events],
        "event_payloads": [event.payload for event in events],
    }


def _evaluate_details(details: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """按 expected 字段校验详情，返回失败原因列表。"""

    failures: list[str] = []
    if details["final_status"] != expected["final_status"]:
        failures.append(
            f"final_status expected={expected['final_status']} actual={details['final_status']}"
        )
    if "terminal_reason" in expected and details["terminal_reason"] != expected["terminal_reason"]:
        failures.append(
            "terminal_reason expected="
            f"{expected['terminal_reason']} actual={details['terminal_reason']}"
        )
    events = details["events"]
    for event in expected.get("must_events", []):
        if event not in events:
            failures.append(f"missing event {event}")
    for event in expected.get("forbidden_events", []):
        if event in events:
            failures.append(f"forbidden event {event} observed")
    for event, count in expected.get("event_counts", {}).items():
        if events.count(event) != count:
            failures.append(f"event {event} count expected={count} actual={events.count(event)}")
    if "max_event_count" in expected and len(events) > expected["max_event_count"]:
        failures.append(f"event count {len(events)} > {expected['max_event_count']}")
    _contains(details.get("result") or {}, expected.get("result_contains", {}), "result", failures)
    _contains(
        details.get("workflow_run_state") or {},
        expected.get("workflow_state_contains", {}),
        "workflow_run_state",
        failures,
    )
    _contains(
        details.get("guardrail_summary") or {},
        expected.get("guardrail_summary_contains", {}),
        "guardrail_summary",
        failures,
    )
    for key in ("workflow_name", "latest_checkpoint_id", "recovery_attempt_count"):
        if key in expected and details.get(key) != expected[key]:
            failures.append(f"{key} expected={expected[key]!r} actual={details.get(key)!r}")
    return failures


def _contains(
    actual: dict[str, Any],
    expected: dict[str, Any],
    label: str,
    failures: list[str],
) -> None:
    """校验 expected 的键值均存在于 actual。"""

    for key, value in expected.items():
        if actual.get(key) != value:
            failures.append(f"{label}.{key} expected={value!r} actual={actual.get(key)!r}")


class _RuntimeRunStore:
    """评测用内存 RunStore，覆盖 worker 必需方法。"""

    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}
        self.client_index: dict[str, str] = {}
        self.next_run_number = 1
        self._lock = asyncio.Lock()

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        async with self._lock:
            if (
                request.client_request_id is not None
                and request.client_request_id in self.client_index
            ):
                return self.snapshots[self.client_index[request.client_request_id]]
            run_id = f"run-{self.next_run_number}"
            self.next_run_number += 1
            snapshot = RunSnapshot(
                run_id=run_id,
                kind=request.payload.kind,
                status=RunStatus.QUEUED,
                payload=request.payload,
                client_request_id=request.client_request_id,
                payload_hash=request.effective_payload_hash(),
                result=None,
                error=None,
                approval_id=None,
                segment_metadata={"segment_count": 0},
                latest_event_cursor=None,
                can_continue=False,
                terminal_reason=None,
                lease=None,
                created_at=_NOW,
                updated_at=_NOW,
                version=1,
                guardrail_summary=request.guardrail_summary,
                workflow_name=request.workflow_name,
                workflow_run_state=request.workflow_run_state,
            )
            self.snapshots[run_id] = snapshot
            if request.client_request_id is not None:
                self.client_index[request.client_request_id] = run_id
            return snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        return self.snapshots.get(run_id)

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        run_id = self.client_index.get(client_request_id)
        return self.snapshots.get(run_id) if run_id is not None else None

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        return sum(1 for snapshot in self.snapshots.values() if snapshot.status in statuses)

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        async with self._lock:
            for snapshot in self.snapshots.values():
                if snapshot.status is not RunStatus.QUEUED:
                    continue
                now = datetime.now(UTC)
                updated = replace(
                    snapshot,
                    status=RunStatus.RUNNING,
                    lease=RunLease(
                        owner_id=owner_id,
                        lease_until=now + timedelta(seconds=lease_seconds),
                        heartbeat_at=now,
                    ),
                    can_continue=False,
                    updated_at=now,
                    version=snapshot.version + 1,
                )
                self.snapshots[snapshot.run_id] = updated
                return updated
        return None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        snapshot = self._require_owner(run_id, owner_id)
        now = datetime.now(UTC)
        updated = replace(
            snapshot,
            lease=RunLease(
                owner_id=owner_id,
                lease_until=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
            ),
            updated_at=now,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def acquire_approval_resume_lease(
        self, *, run_id: str, owner_id: str, lease_seconds: int
    ) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.status is not RunStatus.AWAITING_APPROVAL:
            raise RunContinuationUnavailableError(run_id, snapshot.status.value)
        now = datetime.now(UTC)
        updated = replace(
            snapshot,
            lease=RunLease(owner_id, now + timedelta(seconds=lease_seconds), now),
            updated_at=now,
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    async def release_approval_resume_lease(self, *, run_id: str, owner_id: str) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            return snapshot
        updated = replace(snapshot, lease=None, updated_at=datetime.now(UTC))
        self.snapshots[run_id] = updated
        return updated

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        updated = replace(snapshot, status=RunStatus.CANCEL_REQUESTED, updated_at=datetime.now(UTC))
        self.snapshots[run_id] = updated
        return updated

    async def mark_succeeded(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.SUCCEEDED,
            result=result,
            error=None,
            can_continue=False,
            terminal_reason="completed",
            workflow_run_state=workflow_run_state,
        )

    async def mark_failed(
        self,
        *,
        run_id: str,
        owner_id: str,
        error: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.FAILED,
            result=None,
            error=error,
            can_continue=False,
            terminal_reason="failed",
            workflow_run_state=workflow_run_state,
        )

    async def mark_paused(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.PAUSED,
            result=result,
            error=None,
            can_continue=True,
            terminal_reason=None,
            workflow_run_state=workflow_run_state,
        )

    async def mark_awaiting_approval(
        self,
        *,
        run_id: str,
        owner_id: str,
        approval_id: str,
        result: dict[str, Any],
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        updated = self._worker_transition(
            run_id,
            owner_id,
            RunStatus.AWAITING_APPROVAL,
            result=result,
            error=None,
            can_continue=True,
            terminal_reason=None,
            workflow_run_state=workflow_run_state,
        )
        updated = replace(updated, approval_id=approval_id)
        self.snapshots[run_id] = updated
        return updated

    async def mark_cancelled(
        self,
        *,
        run_id: str,
        owner_id: str,
        reason: str,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        return self._worker_transition(
            run_id,
            owner_id,
            RunStatus.CANCELLED,
            result={"reason": reason},
            error=None,
            can_continue=False,
            terminal_reason=reason,
            workflow_run_state=workflow_run_state,
        )

    async def resolve_approval_resume(
        self, *, run_id: str, owner_id: str, result: ApprovalResumeStoreResult
    ) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if result.status == "queued":
            updated = replace(
                snapshot,
                status=RunStatus.QUEUED,
                result=result.result or snapshot.result,
                approval_id=None,
                can_continue=False,
                lease=None,
                updated_at=datetime.now(UTC),
                version=snapshot.version + 1,
            )
        else:
            updated = replace(
                snapshot,
                status=RunStatus.SUCCEEDED,
                result=result.result or {"approval": "resolved"},
                approval_id=None,
                can_continue=False,
                terminal_reason=result.terminal_reason or "completed",
                lease=None,
                updated_at=datetime.now(UTC),
                version=snapshot.version + 1,
            )
        self.snapshots[run_id] = updated
        return updated

    async def enqueue_continue(self, *, run_id: str, model: str | None = None) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        updated = replace(
            snapshot,
            status=RunStatus.QUEUED,
            can_continue=False,
            lease=None,
            approval_id=None,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    def update_latest_cursor(self, run_id: str, cursor: int) -> None:
        snapshot = self.snapshots.get(run_id)
        if snapshot is not None:
            self.snapshots[run_id] = replace(snapshot, latest_event_cursor=cursor)

    def _worker_transition(
        self,
        run_id: str,
        owner_id: str,
        status: RunStatus,
        *,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        can_continue: bool,
        terminal_reason: str | None,
        workflow_run_state: dict[str, Any] | None,
    ) -> RunSnapshot:
        snapshot = self._require_owner(run_id, owner_id)
        updated = replace(
            snapshot,
            status=status,
            result=result,
            error=error,
            lease=None,
            can_continue=can_continue,
            terminal_reason=terminal_reason,
            segment_metadata=(result or {}).get("segment_metadata", snapshot.segment_metadata),
            workflow_run_state=workflow_run_state or snapshot.workflow_run_state,
            updated_at=datetime.now(UTC),
            version=snapshot.version + 1,
        )
        self.snapshots[run_id] = updated
        return updated

    def _require_owner(self, run_id: str, owner_id: str) -> RunSnapshot:
        snapshot = self.snapshots[run_id]
        if snapshot.lease is None or snapshot.lease.owner_id != owner_id:
            raise RunLeaseConflictError(run_id, owner_id)
        return snapshot


class _RuntimeEventStore:
    """内存 RunEventStore，并同步 latest cursor。"""

    def __init__(self, run_store: _RuntimeRunStore) -> None:
        self._run_store = run_store
        self.events: dict[str, list[RunEvent]] = {}

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        run_events = self.events.setdefault(run_id, [])
        event = RunEvent(
            run_id=run_id,
            cursor=run_events[-1].cursor + 1 if run_events else 1,
            event_type=event_type,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        run_events.append(event)
        self._run_store.update_latest_cursor(run_id, event.cursor)
        return event

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        return [
            event
            for event in self.events.get(run_id, [])
            if after_cursor is None or event.cursor > after_cursor
        ][:limit]

    async def wait_events(
        self, run_id: str, after_cursor: int | None, timeout_seconds: float
    ) -> list[RunEvent]:
        return await self.list_events(run_id, after_cursor, 100)

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        events = self.events.get(run_id, [])
        if len(events) > policy.max_event_count:
            self.events[run_id] = events[-policy.max_event_count :]

    async def first_cursor(self, run_id: str) -> int | None:
        events = self.events.get(run_id, [])
        return events[0].cursor if events else None


class _SequenceCoordinator:
    """按顺序返回预设 outcome 的 Run coordinator。"""

    def __init__(self, outcomes: list[RunExecutionOutcome]) -> None:
        self._outcomes = outcomes

    async def execute(self, snapshot: RunSnapshot, progress) -> RunExecutionOutcome:
        if not self._outcomes:
            raise AssertionError(f"Run {snapshot.run_id} 没有剩余 scripted outcome")
        outcome = self._outcomes.pop(0)
        await progress.segment_done(
            snapshot.run_id,
            outcome.segment_metadata or {"segment_count": 1},
        )
        return outcome


class _WorkflowSelector:
    """按 case script 返回 workflow selection。"""

    def __init__(self, script: dict[str, Any] | None) -> None:
        self._script = script

    def select(self, request: RunCreateRequest) -> WorkflowSelection:
        if not self._script or not self._script.get("enabled", False):
            return WorkflowSelection(workflow=None, explicit=False, reason="disabled")
        name = self._script.get("selected_name") or request.workflow_name or "code_change"
        return WorkflowSelection(
            workflow=_workflow(name),
            explicit=bool(self._script.get("explicit", False)),
            reason="golden_set",
        )


def _workflow(name: str) -> WorkflowDefinition:
    """构造最小合法 workflow 定义。"""

    workflow = WorkflowDefinition(
        name=name,
        description=f"{name} golden workflow",
        applicable=WorkflowApplicableCondition(),
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="evaluator"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="planner"),
        ),
        roles=(
            AgentRoleCapability(role="planner"),
            AgentRoleCapability(role="executor"),
            AgentRoleCapability(role="evaluator"),
        ),
        collaboration_limit=CollaborationLimit(),
        default_strategy_summary="golden workflow",
    )
    workflow.validate()
    return workflow


def _runtime_fixture(
    coordinator: _SequenceCoordinator,
    *,
    workflow_script: dict[str, Any] | None,
) -> tuple[RunApplicationService, _RuntimeRunStore, _RuntimeEventStore, RunWorker]:
    """构造 runtime case 依赖。"""

    store = _RuntimeRunStore()
    events = _RuntimeEventStore(store)

    async def approval_resumer(
        snapshot: RunSnapshot,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> ApprovalResumeResult:
        del snapshot, model
        assert decisions
        return ApprovalResumeResult(status="queued", result={"approval": "resolved"})

    service = RunApplicationService(
        run_store=store,
        event_store=events,
        capacity_policy=RunCapacityPolicy(max_queued_runs=20, max_running_runs=20),
        event_retention_policy=EventRetentionPolicy(max_event_count=100, ttl_seconds=3600),
        approval_resumer=approval_resumer,
        workflow_selector=_WorkflowSelector(workflow_script),
        event_stream_wait_seconds=0.01,
    )
    worker = RunWorker(
        run_store=store,
        event_store=events,
        coordinator=coordinator,  # type: ignore[arg-type]
        lease_seconds=30,
        heartbeat_interval_seconds=10,
        owner_id="golden-worker",
    )
    return service, store, events, worker


class _RecoveryRunStore:
    """RunRecoveryService 用内存 RunStore。"""

    def __init__(self, snapshots: list[RunSnapshot]) -> None:
        self.snapshots = snapshots

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        del now
        return list(self.snapshots)

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
        snapshot = self.snapshots[0]
        updated = replace(
            snapshot,
            status=RunStatus.QUEUED,
            latest_checkpoint_id=latest_checkpoint_id,
            recoverable=True,
            recovery_attempt_count=recovery_attempt_count,
            guardrail_summary=guardrail_summary or snapshot.guardrail_summary,
            workflow_run_state=workflow_run_state or snapshot.workflow_run_state,
            collaboration_summary=collaboration_summary or snapshot.collaboration_summary,
            updated_at=_NOW,
        )
        self.snapshots[0] = updated
        return updated

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        snapshot = self.snapshots[0]
        updated = replace(
            snapshot,
            status=RunStatus.LOST,
            recoverable=False,
            last_recovery_error=recovery_error or {"reason": reason},
            updated_at=_NOW,
        )
        self.snapshots[0] = updated
        return updated


class _RecoveryEventStore:
    """RunRecoveryService 用内存 event store。"""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(run_id, len(self.events) + 1, event_type, payload, _NOW)
        self.events.append(event)
        return event


class _RecoveryCheckpointStore:
    """RunRecoveryService 用 checkpoint store。"""

    def __init__(self, checkpoint: DurableCheckpoint | None) -> None:
        self._checkpoint = checkpoint

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        del run_id
        return self._checkpoint

    async def list_tool_ledger(self, run_id: str) -> list[Any]:
        del run_id
        return []


def _leased_snapshot(case: EvalCase, *, latest_checkpoint_id: str | None) -> RunSnapshot:
    """构造 recovery case 的过期 leased 快照。"""

    request = _create_request(case)
    return RunSnapshot(
        run_id="run-1",
        kind=request.payload.kind,
        status=RunStatus.RUNNING,
        payload=request.payload,
        client_request_id=request.client_request_id,
        payload_hash=request.effective_payload_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={},
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=RunLease("expired-worker", _NOW - timedelta(seconds=1), _NOW),
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        latest_checkpoint_id=latest_checkpoint_id,
    )


def _checkpoint(case: EvalCase, script: dict[str, Any]) -> DurableCheckpoint:
    """构造 DurableCheckpoint。"""

    ctx = ConversationContext()
    ctx.add_user_message(case.inputs["input"].get("message", "golden recovery"))
    return DurableCheckpoint(
        run_id="run-1",
        checkpoint_id=script.get("id", "chk-1"),
        sequence=1,
        phase=CheckpointPhase(script.get("phase", "model_completed")),
        context_snapshot=ctx.to_dict(),
        round_num=1,
        usage={},
        trace_summary={},
        segment_metadata={},
        tool_execution_key=None,
        tool_result_ref=None,
        schema_version=1,
        sanitized=False,
        truncated_fields=(),
        created_at=_NOW,
    )
