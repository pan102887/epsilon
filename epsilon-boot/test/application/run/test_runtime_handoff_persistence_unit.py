"""真实 handoff_to_agent 运行时 workflow 状态持久化单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from application.run.run_execution_coordinator import RunExecutionCoordinator
from domain.agent.exceptions import HandoffPerformed
from domain.agent.value_objects import HandoffResult
from domain.chat.context import ConversationContext
from domain.chat.value_objects import ChatResponseVO
from domain.run.value_objects import (
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
from infrastructure.agent.handoff_context import reset_parent_context, set_parent_context
from infrastructure.agent.handoff_to_agent_tool import HandoffToAgentTool
from infrastructure.run.run_serialization_adapters import SegmentSerializerAdapter
from infrastructure.run.run_worker import RunWorker

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _EventStore:
    """记录 Run 事件的内存 fake。"""

    def __init__(self) -> None:
        """初始化事件列表。"""

        self.events: list[RunEvent] = []

    async def append_event(
        self,
        run_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
    ) -> RunEvent:
        """追加事件并按 run_id 分配单调 cursor。"""

        cursor = len([event for event in self.events if event.run_id == run_id]) + 1
        event = RunEvent(
            run_id=run_id,
            cursor=cursor,
            event_type=event_type,
            payload=payload,
            created_at=_NOW,
        )
        self.events.append(event)
        return event


class _Registry:
    """返回单个测试 workflow 的 registry fake。"""

    def __init__(self, workflow: WorkflowDefinition) -> None:
        """保存 workflow 定义。"""

        self.workflow = workflow

    def require_definition(self, name: str) -> WorkflowDefinition:
        """按名称返回 workflow 定义。"""

        if name != self.workflow.name:
            raise KeyError(name)
        return self.workflow


class _AgentRegistry:
    """Handoff 工具所需的 Agent 注册表 fake。"""

    def list_names(self) -> list[str]:
        """返回可 handoff 的 Agent 名称。"""

        return ["review_agent"]


class _Delegation:
    """成功执行 handoff 的 Delegation fake。"""

    def __init__(self) -> None:
        """初始化调用记录。"""

        self.handoff_calls: list[str] = []

    async def handoff(
        self,
        agent_name: str,
        messages: list[Any],
        *,
        delegation_depth: int,
        max_delegation_depth: int,
    ) -> HandoffResult:
        """记录目标 Agent 并返回成功 handoff 结果。"""

        self.handoff_calls.append(agent_name)
        assert messages
        assert delegation_depth == 1
        assert max_delegation_depth >= 1
        return HandoffResult(
            target_agent=agent_name,
            content="reviewer took control",
            success=True,
            usage={"total_tokens": 3},
            model="model-reviewer",
        )


class _ChatService:
    """在聊天执行中触发真实 HandoffToAgentTool 的 fake 服务。"""

    def __init__(self, *, event_store: _EventStore) -> None:
        """初始化 handoff 工具依赖。"""

        self.delegation = _Delegation()
        self.tool = HandoffToAgentTool(
            _AgentRegistry(),
            self.delegation,
            event_store=event_store,
            recent_collaboration_summary_limit=5,
        )

    async def chat(self, request: Any) -> ChatResponseVO:
        """模拟首段 chat，并通过 HandoffToAgentTool 完成控制转移。"""

        parent = ConversationContext()
        parent.session_id = request.session_id
        parent.add_user_message(request.message)
        token = set_parent_context(parent)
        try:
            with pytest.raises(HandoffPerformed) as raised:
                await self.tool.execute(agent_name="review_agent")
        finally:
            reset_parent_context(token)
        return ChatResponseVO(
            session_id=request.session_id,
            reply=raised.value.content,
            model=raised.value.model,
            usage=raised.value.usage,
            prompt_id="chat-default@v1",
        )


class _TaskAgent:
    """未被本用例调用的 TaskAgent fake。"""

    async def execute(self, task: Any) -> Any:
        """防御性失败。"""

        raise AssertionError("task path should not be called")

    async def continue_task(self, request: Any) -> Any:
        """防御性失败。"""

        raise AssertionError("task path should not be called")


class _RunStore:
    """RunWorker 所需的最小 RunStore fake。"""

    def __init__(self, snapshot: RunSnapshot) -> None:
        """保存可领取快照。"""

        self.snapshot = snapshot
        self.persisted: RunSnapshot | None = None
        self.claimed = False

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        """首次调用返回带 worker lease 的快照。"""

        if self.claimed:
            return None
        self.claimed = True
        lease = RunLease(
            owner_id=owner_id,
            lease_until=_NOW + timedelta(seconds=lease_seconds),
            heartbeat_at=_NOW,
        )
        self.snapshot = self._replace_snapshot(status=RunStatus.RUNNING, lease=lease)
        return self.snapshot

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        """返回当前快照。"""

        return self.snapshot if run_id == self.snapshot.run_id else None

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        """刷新租约；本测试通常不会触发。"""

        assert run_id == self.snapshot.run_id
        assert owner_id == self.snapshot.lease.owner_id
        self.snapshot = self._replace_snapshot(
            lease=RunLease(
                owner_id=owner_id,
                lease_until=_NOW + timedelta(seconds=lease_seconds),
                heartbeat_at=_NOW,
            )
        )
        return self.snapshot

    async def mark_succeeded(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """保存成功终态及 workflow/collaboration 摘要。"""

        assert run_id == self.snapshot.run_id
        assert owner_id == self.snapshot.lease.owner_id
        self.snapshot = self._replace_snapshot(
            status=RunStatus.SUCCEEDED,
            result=result,
            lease=None,
            terminal_reason="completed",
            workflow_run_state=workflow_run_state,
            collaboration_summary=collaboration_summary,
        )
        self.persisted = self.snapshot
        return self.snapshot

    async def mark_paused(self, **kwargs: Any) -> RunSnapshot:
        """防御性失败。"""

        raise AssertionError("paused path should not be called")

    async def mark_failed(self, **kwargs: Any) -> RunSnapshot:
        """防御性失败。"""

        raise AssertionError("failed path should not be called")

    async def mark_awaiting_approval(self, **kwargs: Any) -> RunSnapshot:
        """防御性失败。"""

        raise AssertionError("approval path should not be called")

    async def mark_cancelled(self, **kwargs: Any) -> RunSnapshot:
        """防御性失败。"""

        raise AssertionError("cancel path should not be called")

    def _replace_snapshot(self, **changes: Any) -> RunSnapshot:
        """使用 dataclass 字段字典构造更新后的快照。"""

        return RunSnapshot(
            **{**self.snapshot.__dict__, **changes, "version": self.snapshot.version + 1}
        )


async def test_successful_handoff_to_agent_persists_workflow_state_and_event() -> None:
    """真实 handoff_to_agent 成功应产生 workflow handoff 事件并持久化快照状态。"""

    events = _EventStore()
    workflow = _workflow()
    coordinator = RunExecutionCoordinator(
        chat_service=_ChatService(event_store=events),
        task_agent=_TaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        event_store=events,
        workflow_registry=_Registry(workflow),
    )
    run_store = _RunStore(_snapshot(workflow_state=_state(active_role="executor")))
    worker = RunWorker(
        run_store=run_store,
        event_store=events,
        executor=coordinator,
        lease_seconds=30,
        heartbeat_interval_seconds=60,
        owner_id="worker-1",
    )

    await worker.run_once()

    assert run_store.persisted is not None
    persisted_state = run_store.persisted.workflow_run_state
    assert persisted_state is not None
    assert persisted_state["handoff_state"]["status"] == "completed"
    assert persisted_state["handoff_state"]["source_role"] == "executor"
    assert persisted_state["handoff_state"]["target_role"] == "reviewer"
    assert persisted_state["handoff_state"]["target_agent"] == "review_agent"
    assert persisted_state["handoff_state"]["reason"] == "handoff_to_agent"
    assert run_store.persisted.collaboration_summary is not None
    assert run_store.persisted.collaboration_summary["handoff_count"] >= 1

    handoff_events = [
        event
        for event in events.events
        if event.event_type is RunEventType.WORKFLOW_HANDOFF_RECORDED
    ]
    assert len(handoff_events) == 1
    payload = handoff_events[0].payload
    assert payload["source_role"] == "executor"
    assert payload["target_role"] == "reviewer"
    assert payload["target_agent"] == "review_agent"
    assert payload["reason"] == "handoff_to_agent"
    assert payload["workflow_run_state"]["handoff_state"] == persisted_state["handoff_state"]
    assert run_store.persisted.result["reply"] == "reviewer took control"


async def test_successful_handoff_without_workflow_context_does_not_record_workflow_state() -> None:
    """未提供 workflow registry/policy context 时，handoff 仅保持既有控制转移语义。"""

    events = _EventStore()
    coordinator = RunExecutionCoordinator(
        chat_service=_ChatService(event_store=events),
        task_agent=_TaskAgent(),
        segment_serializer=SegmentSerializerAdapter(),
        event_store=events,
        workflow_registry=None,
    )
    run_store = _RunStore(_snapshot(workflow_state=None))
    worker = RunWorker(
        run_store=run_store,
        event_store=events,
        executor=coordinator,
        lease_seconds=30,
        heartbeat_interval_seconds=60,
        owner_id="worker-1",
    )

    await worker.run_once()

    assert run_store.persisted is not None
    assert run_store.persisted.result["reply"] == "reviewer took control"
    assert run_store.persisted.workflow_run_state is None
    assert [
        event
        for event in events.events
        if event.event_type is RunEventType.WORKFLOW_HANDOFF_RECORDED
    ] == []


def _workflow() -> WorkflowDefinition:
    """构造含 executor/reviewer 角色和 Agent 映射的 workflow。"""

    workflow = WorkflowDefinition(
        name="code_change",
        description="code workflow",
        applicable=WorkflowApplicableCondition(),
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
        ),
        roles=(
            AgentRoleCapability("planner", agent_names=("planner_agent",)),
            AgentRoleCapability("executor", agent_names=("executor_agent",)),
            AgentRoleCapability("reviewer", agent_names=("review_agent",)),
        ),
        collaboration_limit=CollaborationLimit(),
        default_strategy_summary="default strategy",
    )
    workflow.validate()
    return workflow


def _state(**extra: Any) -> dict[str, Any]:
    """构造 workflow_run_state。"""

    return {
        "workflow_name": "code_change",
        "current_phase": "execute",
        "phase_started_at": None,
        "phase_history": [],
        "phase_result_summary": None,
        "phase_error_summary": None,
        "revise_counts": {},
        **extra,
    }


def _snapshot(*, workflow_state: dict[str, Any] | None) -> RunSnapshot:
    """构造 Run 快照。"""

    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": "please handoff"},
            model="model-a",
        ),
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
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_name="code_change" if workflow_state is not None else None,
        workflow_run_state=workflow_state,
        collaboration_summary={
            "latest_steps": [],
            "child_links": [],
            "delegation_count": 0,
            "handoff_count": 0,
            "max_depth_seen": 0,
            "limit_hit_reason": None,
        }
        if workflow_state is not None
        else None,
    )
