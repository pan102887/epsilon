"""RunExecutionCoordinator workflow 接入单元测试。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_execution_coordinator import RunExecutionCoordinator
from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from domain.chat.ports import ChatServicePort
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO, ChatResponseVO
from domain.run.checkpoint_context import get_run_checkpoint_context
from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import RunCheckpointStorePort, RunEventStorePort, WorkflowRegistryPort
from domain.run.value_objects import (
    CheckpointRetentionPolicy,
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
from domain.run.workflow_context import get_workflow_collaboration_context
from domain.task.ports import TaskAgentPort
from infrastructure.run.run_serialization_adapters import (
    SegmentSerializerAdapter,
    WorkflowSerializerAdapter,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _ObservingChatService:
    """记录 coordinator context 和调用路径的 Chat fake。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.chat_calls = 0
        self.continue_calls = 0
        self.checkpoint_run_ids: list[str | None] = []
        self.workflow_phases: list[str | None] = []
        self.workflow_roles: list[str | None] = []

    async def chat(self, request: ChatRequestVO) -> ChatResponseVO:
        """首次聊天路径。"""

        self.chat_calls += 1
        self._record_contexts()
        if self.fail:
            raise RuntimeError("boom")
        return ChatResponseVO(
            session_id=request.session_id,
            reply="ok",
            model=request.model or "test-model",
            usage={},
            prompt_id="chat-default@v1",
        )

    async def continue_chat(self, request: ChatContinueRequestVO) -> ChatResponseVO:
        """继续聊天路径。"""

        self.continue_calls += 1
        self._record_contexts()
        if self.fail:
            raise RuntimeError("boom")
        return ChatResponseVO(
            session_id=request.session_id,
            reply="continued",
            model=request.model or "test-model",
            usage={},
            prompt_id="chat-default@v1",
        )

    def _record_contexts(self) -> None:
        checkpoint = get_run_checkpoint_context()
        workflow = get_workflow_collaboration_context()
        self.checkpoint_run_ids.append(checkpoint.run_id if checkpoint is not None else None)
        self.workflow_phases.append(
            workflow.phase.value if workflow is not None and workflow.phase else None
        )
        self.workflow_roles.append(workflow.source_role if workflow is not None else None)


class _UnusedTaskAgent:
    pass


class _Progress:
    def __init__(self) -> None:
        self.started: list[tuple[str, int]] = []
        self.done: list[tuple[str, dict[str, Any]]] = []

    async def segment_started(self, run_id: str, segment_index: int) -> None:
        self.started.append((run_id, segment_index))

    async def segment_done(self, run_id: str, metadata: dict[str, Any]) -> None:
        self.done.append((run_id, metadata))


class _EventStore:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            cursor=len(self.events) + 1,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


class _CheckpointStore:
    async def latest_checkpoint(self, run_id: str) -> None:
        return None


class _Registry:
    def __init__(self, workflow: WorkflowDefinition | None = None) -> None:
        self.workflow = workflow or _workflow()

    def require_definition(self, name: str) -> WorkflowDefinition:
        if name != self.workflow.name:
            raise KeyError(name)
        return self.workflow


def _event_store_port(store: _EventStore) -> RunEventStorePort:
    return cast(RunEventStorePort, store)


def _registry_port(registry: _Registry) -> WorkflowRegistryPort:
    return cast(WorkflowRegistryPort, registry)


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_phase(
        self,
        *,
        snapshot: RunSnapshot,
        execute_existing: Callable[[RunSnapshot], Any],
    ) -> RunExecutionOutcome:
        self.calls.append(snapshot.run_id)
        outcome = await execute_existing(snapshot)
        return RunExecutionOutcome(
            status=outcome.status,
            result=outcome.result,
            error=outcome.error,
            terminal_reason=outcome.terminal_reason,
            can_continue=outcome.can_continue,
            approval_id=outcome.approval_id,
            segment_metadata=outcome.segment_metadata,
            workflow_run_state={"wrapped": True},
        )


def _coordinator(
    chat: _ObservingChatService,
    *,
    event_store: _EventStore | None = None,
    workflow_orchestrator: Any | None = None,
    workflow_registry: _Registry | None = None,
    checkpoint_enabled: bool = False,
) -> RunExecutionCoordinator:
    return RunExecutionCoordinator(
        chat_service=cast(ChatServicePort, chat),
        task_agent=cast(TaskAgentPort, _UnusedTaskAgent()),
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=(
            cast(RunCheckpointStorePort, _CheckpointStore()) if checkpoint_enabled else None
        ),
        event_store=_event_store_port(event_store) if event_store is not None else None,
        retention_policy=CheckpointRetentionPolicy(10, 3600, 4096, 100),
        checkpoint_enabled=checkpoint_enabled,
        workflow_orchestrator=workflow_orchestrator,
        workflow_registry=(
            _registry_port(workflow_registry) if workflow_registry is not None else None
        ),
    )


def _snapshot(
    *,
    status: RunStatus = RunStatus.RUNNING,
    result: dict[str, Any] | None = None,
    workflow: bool = True,
) -> RunSnapshot:
    state: dict[str, Any] | None = (
        {
            "workflow_name": "code_change",
            "current_phase": "plan",
            "phase_started_at": None,
            "phase_history": [],
            "phase_result_summary": None,
            "phase_error_summary": None,
            "revise_counts": {},
        }
        if workflow
        else None
    )
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=status,
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"session_id": "session-1", "message": "hello", "model": "model-a"},
            model="model-a",
        ),
        client_request_id=None,
        payload_hash=None,
        result=result,
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=None,
        can_continue=status is RunStatus.PAUSED,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_name="code_change" if workflow else None,
        workflow_run_state=state,
        collaboration_summary={"delegation_count": 2, "handoff_count": 1},
    )


def _workflow() -> WorkflowDefinition:
    workflow = WorkflowDefinition(
        name="code_change",
        description="code change workflow",
        applicable=WorkflowApplicableCondition(),
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="planner"),
        ),
        roles=(
            AgentRoleCapability(role="planner"),
            AgentRoleCapability(role="executor"),
            AgentRoleCapability(role="reviewer"),
        ),
        collaboration_limit=CollaborationLimit(max_parallel_delegations=2),
        default_strategy_summary="default strategy",
    )
    workflow.validate()
    return workflow


async def test_workflow_orchestrator_is_called_and_outcome_is_returned() -> None:
    """注入 orchestrator 时 coordinator 应通过它包装执行。"""

    chat = _ObservingChatService()
    fake_orchestrator = _FakeOrchestrator()
    coordinator = _coordinator(
        chat,
        workflow_orchestrator=fake_orchestrator,
        workflow_registry=_Registry(),
    )

    outcome = await coordinator.execute(_snapshot(), _Progress())

    assert fake_orchestrator.calls == ["run-1"]
    assert chat.chat_calls == 1
    assert outcome.workflow_run_state == {"wrapped": True}


async def test_without_workflow_state_keeps_existing_chat_path() -> None:
    """没有 workflow state 时真实 orchestrator 直通旧 Chat 路径。"""

    chat = _ObservingChatService()
    events = _EventStore()
    registry = _Registry()
    coordinator = _coordinator(
        chat,
        event_store=events,
        workflow_orchestrator=WorkflowRunOrchestrator(
            event_store=_event_store_port(events),
            workflow_registry=_registry_port(registry),
            workflow_serializer=WorkflowSerializerAdapter(),
            now=lambda: _NOW,
        ),
        workflow_registry=registry,
    )

    outcome = await coordinator.execute(_snapshot(workflow=False), _Progress())

    assert outcome.status is RunStatus.SUCCEEDED
    assert chat.chat_calls == 1
    assert events.events == []
    assert chat.workflow_phases == [None]


async def test_checkpoint_and_workflow_contexts_are_visible_in_same_window() -> None:
    """checkpoint context 与 workflow collaboration context 应在同一调用窗口生效。"""

    chat = _ObservingChatService()
    events = _EventStore()
    registry = _Registry()
    coordinator = _coordinator(
        chat,
        event_store=events,
        workflow_orchestrator=WorkflowRunOrchestrator(
            event_store=_event_store_port(events),
            workflow_registry=_registry_port(registry),
            workflow_serializer=WorkflowSerializerAdapter(),
            now=lambda: _NOW,
        ),
        workflow_registry=registry,
        checkpoint_enabled=True,
    )

    await coordinator.execute(_snapshot(), _Progress())

    assert chat.checkpoint_run_ids == ["run-1"]
    assert chat.workflow_phases == ["plan"]
    assert chat.workflow_roles == ["planner"]
    assert get_run_checkpoint_context() is None
    assert get_workflow_collaboration_context() is None


async def test_contexts_reset_when_workflow_execution_fails() -> None:
    """执行异常被收敛为 failed outcome，两个 ContextVar 都必须 reset。"""

    chat = _ObservingChatService(fail=True)
    events = _EventStore()
    registry = _Registry()
    coordinator = _coordinator(
        chat,
        event_store=events,
        workflow_orchestrator=WorkflowRunOrchestrator(
            event_store=_event_store_port(events),
            workflow_registry=_registry_port(registry),
            workflow_serializer=WorkflowSerializerAdapter(),
            now=lambda: _NOW,
        ),
        workflow_registry=registry,
        checkpoint_enabled=True,
    )

    outcome = await coordinator.execute(_snapshot(), _Progress())

    assert outcome.status is RunStatus.FAILED
    assert get_run_checkpoint_context() is None
    assert get_workflow_collaboration_context() is None


async def test_workflow_continue_uses_continue_chat_without_readding_user_message() -> None:
    """workflow paused continue 仍走 continue_chat，不重复追加原始 user message。"""

    chat = _ObservingChatService()
    events = _EventStore()
    registry = _Registry()
    coordinator = _coordinator(
        chat,
        event_store=events,
        workflow_orchestrator=WorkflowRunOrchestrator(
            event_store=_event_store_port(events),
            workflow_registry=_registry_port(registry),
            workflow_serializer=WorkflowSerializerAdapter(),
            now=lambda: _NOW,
        ),
        workflow_registry=registry,
    )

    outcome = await coordinator.execute(
        _snapshot(status=RunStatus.PAUSED, result={"previous": "segment"}),
        _Progress(),
    )

    assert chat.chat_calls == 0
    assert chat.continue_calls == 1
    assert outcome.status is RunStatus.PAUSED
    assert outcome.can_continue is True
