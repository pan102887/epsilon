"""RunApplicationService workflow 选择单元测试。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_application_service import RunApplicationService
from domain.run.exceptions import RunIdempotencyConflictError, RunUnknownWorkflowError
from domain.run.ports import RunEventStorePort, RunStorePort, WorkflowSelection
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunKind,
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
from infrastructure.run.run_serialization_adapters import WorkflowSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _MemoryRunStore:
    """创建路径测试用 RunStore fake。"""

    def __init__(self) -> None:
        self.snapshots: dict[str, RunSnapshot] = {}
        self.client_index: dict[str, str] = {}
        self.create_calls: list[RunCreateRequest] = []

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """创建 queued 快照并保存 workflow 字段。"""

        self.create_calls.append(request)
        if request.client_request_id in self.client_index:
            return self.snapshots[self.client_index[request.client_request_id]]
        run_id = f"run-{len(self.snapshots) + 1}"
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
            task_classification=request.task_classification,
            guardrail_summary=request.guardrail_summary,
            workflow_name=request.workflow_name,
            workflow_run_state=request.workflow_run_state,
            collaboration_summary=request.collaboration_summary,
        )
        self.snapshots[run_id] = snapshot
        if request.client_request_id is not None:
            self.client_index[request.client_request_id] = run_id
        return snapshot

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        """按幂等键查询。"""

        run_id = self.client_index.get(client_request_id)
        return self.snapshots.get(run_id) if run_id is not None else None

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        """统计状态。"""

        return sum(1 for snapshot in self.snapshots.values() if snapshot.status in statuses)


class _MemoryEventStore:
    """创建路径测试用 RunEventStore fake。"""

    def __init__(self) -> None:
        self.events: dict[str, list[RunEvent]] = {}

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        """追加事件。"""

        events = self.events.setdefault(run_id, [])
        event = RunEvent(
            run_id=run_id,
            cursor=(events[-1].cursor + 1) if events else 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        events.append(event)
        return event

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        """创建路径测试不裁剪事件。"""


class _WorkflowSelector:
    """可配置 workflow selector fake。"""

    def __init__(self, selection: WorkflowSelection | Exception) -> None:
        self.selection = selection
        self.requests: list[RunCreateRequest] = []

    def select(self, request: RunCreateRequest) -> WorkflowSelection:
        """记录请求并返回预设选择。"""

        self.requests.append(request)
        if isinstance(self.selection, Exception):
            raise self.selection
        return self.selection


class _Classifier:
    """固定返回 task classification 的 guardrail fake。"""

    def __init__(self, value: str) -> None:
        self.value = value

    def classify_payload(self, payload: RunPayload, *, has_tools: bool) -> str:
        """返回固定分类。"""

        return self.value


def _service(
    store: _MemoryRunStore,
    event_store: _MemoryEventStore,
    selector: _WorkflowSelector,
    *,
    classifier: _Classifier | None = None,
) -> RunApplicationService:
    """构造 RunApplicationService。"""

    return RunApplicationService(
        run_store=cast(RunStorePort, store),
        event_store=cast(RunEventStorePort, event_store),
        capacity_policy=RunCapacityPolicy(max_queued_runs=10, max_running_runs=10),
        event_retention_policy=EventRetentionPolicy(
            max_event_count=100,
            ttl_seconds=3600,
        ),
        workflow_serializer=WorkflowSerializerAdapter(),
        workflow_selector=selector,
        guardrail_policy=classifier,
    )


def _request(
    *,
    client_request_id: str | None = "client-1",
    workflow_name: str | None = None,
) -> RunCreateRequest:
    """构造创建请求。"""

    return RunCreateRequest(
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": "please change code"},
            model="model-a",
        ),
        client_request_id=client_request_id,
        workflow_name=workflow_name,
    )


def _selection(
    workflow_name: str = "code_change",
    *,
    explicit: bool = False,
    reason: str = "rule_match",
) -> WorkflowSelection:
    """构造命中选择结果。"""

    return WorkflowSelection(
        workflow=_workflow(workflow_name),
        explicit=explicit,
        reason=reason,
    )


def _workflow(name: str) -> WorkflowDefinition:
    """构造最小合法 workflow 定义。"""

    roles = (
        AgentRoleCapability(role="planner"),
        AgentRoleCapability(role="executor"),
        AgentRoleCapability(role="evaluator"),
    )
    phases = (
        WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
        WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
        WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="evaluator"),
        WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="planner"),
    )
    workflow = WorkflowDefinition(
        name=name,
        description=f"{name} workflow",
        applicable=WorkflowApplicableCondition(),
        phases=phases,
        roles=roles,
        collaboration_limit=CollaborationLimit(),
        default_strategy_summary="default strategy",
    )
    workflow.validate()
    return workflow


async def test_create_run_persists_selected_workflow_and_appends_event() -> None:
    """选择成功时应初始化 workflow state 并写 WORKFLOW_SELECTED。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(store, events, _WorkflowSelector(_selection()))

    snapshot = await service.create_run(_request())

    assert snapshot.workflow_name == "code_change"
    assert snapshot.workflow_run_state == {
        "workflow_name": "code_change",
        "current_phase": "plan",
        "phase_started_at": None,
        "phase_history": [],
        "phase_result_summary": None,
        "phase_error_summary": None,
        "revise_counts": {},
        "active_role": None,
        "handoff_state": None,
    }
    assert [event.event_type for event in events.events[snapshot.run_id]] == [
        RunEventType.WORKFLOW_SELECTED,
        RunEventType.RUN_CREATED,
        RunEventType.RUN_QUEUED,
    ]
    assert events.events[snapshot.run_id][0].payload == {
        "reason": "rule_match",
        "explicit": False,
        "workflow_name": "code_change",
        "first_phase": "plan",
    }


async def test_create_run_appends_selection_skipped_event_without_workflow() -> None:
    """未匹配 workflow 时保持字段为空并写跳过事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(
        store,
        events,
        _WorkflowSelector(WorkflowSelection(workflow=None, explicit=False, reason="no_match")),
    )

    snapshot = await service.create_run(_request())

    assert snapshot.workflow_name is None
    assert snapshot.workflow_run_state is None
    assert events.events[snapshot.run_id][0].event_type is (RunEventType.WORKFLOW_SELECTION_SKIPPED)
    assert events.events[snapshot.run_id][0].payload == {
        "reason": "no_match",
        "explicit": False,
        "workflow_name": None,
        "first_phase": None,
    }


async def test_explicit_unknown_workflow_does_not_create_snapshot_or_event() -> None:
    """显式未知 workflow 应直接抛业务异常，不创建快照或事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    service = _service(
        store,
        events,
        _WorkflowSelector(RunUnknownWorkflowError("missing")),
    )

    with pytest.raises(RunUnknownWorkflowError):
        await service.create_run(_request(workflow_name="missing"))

    assert store.create_calls == []
    assert events.events == {}


async def test_idempotency_hit_does_not_select_or_append_duplicate_events() -> None:
    """幂等命中直接返回既有 Run，不重复选择或写事件。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    selector = _WorkflowSelector(_selection())
    service = _service(store, events, selector)

    first = await service.create_run(_request())
    second = await service.create_run(_request())

    assert second.run_id == first.run_id
    assert len(selector.requests) == 1
    assert len(events.events[first.run_id]) == 3


async def test_task_classification_runs_before_workflow_selection() -> None:
    """workflow selector 应看到已填充的 task_classification。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    selector = _WorkflowSelector(_selection(reason="task_classification_match"))
    service = _service(
        store,
        events,
        selector,
        classifier=_Classifier("code_task"),
    )

    snapshot = await service.create_run(_request())

    assert selector.requests[0].task_classification == "code_task"
    assert snapshot.task_classification == "code_task"
    assert [event.event_type for event in events.events[snapshot.run_id]] == [
        RunEventType.TASK_CLASSIFIED,
        RunEventType.WORKFLOW_SELECTED,
        RunEventType.RUN_CREATED,
        RunEventType.RUN_QUEUED,
    ]


async def test_same_idempotency_key_with_different_explicit_workflow_conflicts() -> None:
    """同一幂等键和 payload 不得复用不同显式 workflow 语义。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    selector = _WorkflowSelector(_selection(explicit=True, reason="explicit_workflow"))
    service = _service(store, events, selector)
    await service.create_run(_request(workflow_name="code_change"))

    with pytest.raises(RunIdempotencyConflictError):
        await service.create_run(_request(workflow_name="report"))

    assert len(selector.requests) == 1


async def test_same_explicit_workflow_with_idempotency_key_returns_existing() -> None:
    """同一显式 workflow 和 payload 可以正常幂等命中。"""

    store = _MemoryRunStore()
    events = _MemoryEventStore()
    selector = _WorkflowSelector(_selection(explicit=True, reason="explicit_workflow"))
    service = _service(store, events, selector)
    first = await service.create_run(_request(workflow_name="code_change"))

    store.snapshots[first.run_id] = replace(first, latest_event_cursor=3)
    second = await service.create_run(_request(workflow_name=" code_change "))

    assert second.run_id == first.run_id
    assert len(selector.requests) == 1
