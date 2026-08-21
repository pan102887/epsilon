"""Run 审批恢复分派器单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_approval_resumer import RunApprovalResumer
from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentRunMetadata,
    SegmentStopReason,
)
from domain.agent.value_objects import ApprovalDecision, PendingActionRequest
from domain.chat.ports import ChatServicePort
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatResponseVO
from domain.run.ports import ApprovalResumeStoreResult
from domain.run.value_objects import RunKind, RunPayload, RunSnapshot, RunStatus
from domain.run.workflow import WorkflowPhase
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import TaskApprovalResumeRequest, TaskResult, TaskStatus, TraceEntry

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeChatService:
    """记录审批恢复请求的 ChatService fake。"""

    def __init__(self) -> None:
        self.requests: list[ApprovalResumeRequestVO] = []
        self.response = ChatResponseVO(
            session_id="session-chat",
            reply="done",
            model="model-chat",
            usage={"total_tokens": 5},
            prompt_id="chat-default@v1",
            segment_metadata=_segment_metadata(1, "completed"),
        )
        self.error: Exception | None = None

    async def resume_approval(self, request: ApprovalResumeRequestVO) -> ChatResponseVO:
        """记录请求并返回预置响应。"""

        if self.error is not None:
            raise self.error
        self.requests.append(request)
        return self.response


class _FakeTaskAgent:
    """记录审批恢复请求的 TaskAgent fake。"""

    def __init__(self) -> None:
        self.requests: list[TaskApprovalResumeRequest] = []
        self.response = TaskResult(
            content="task done",
            status=TaskStatus.SUCCESS,
            model="model-task",
            prompt_id="task-template@v1",
            usage={"total_tokens": 7},
            trace=[TraceEntry(step=1, action="tool_result", detail="ok", timestamp_ms=1.0)],
            segment_metadata=_segment_metadata(1, "completed"),
        )
        self.error: Exception | None = None

    async def resume_approval(self, request: TaskApprovalResumeRequest) -> TaskResult:
        """记录请求并返回预置响应。"""

        if self.error is not None:
            raise self.error
        self.requests.append(request)
        return self.response


def _segment_metadata(index: int, reason: SegmentStopReason) -> SegmentRunMetadata:
    """构造测试用分段元数据。"""

    return SegmentRunMetadata(
        segment_index=index,
        segment_count=index,
        segment_stop_reason=reason,
        budget_usage=SegmentBudgetUsage(segment_count=index, total_tokens=index * 10),
    )


def _chat_snapshot(
    *,
    approval_id: str = "approval-1",
    workflow_run_state: dict[str, Any] | None = None,
) -> RunSnapshot:
    """构造聊天 awaiting_approval 快照。"""

    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-chat",
        chat={"session_id": "session-chat", "message": "hello"},
        model="model-chat",
    )
    return RunSnapshot(
        run_id="run-chat",
        kind=RunKind.CHAT,
        status=RunStatus.AWAITING_APPROVAL,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
        result={"status": "approval_required"},
        error=None,
        approval_id=approval_id,
        segment_metadata={"segment_count": 1},
        latest_event_cursor=5,
        can_continue=True,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        guardrail_summary={"action": "require_approval", "evaluation_count": 1},
        workflow_run_state=workflow_run_state,
        collaboration_summary={"latest_steps": [{"id": "step-1"}]},
    )


def _task_snapshot(*, approval_id: str = "approval-1") -> RunSnapshot:
    """构造任务 awaiting_approval 快照。"""

    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="session-task",
        task={"session_id": "session-task", "goal": "ship feature"},
        model="model-task",
    )
    return RunSnapshot(
        run_id="run-task",
        kind=RunKind.TASK,
        status=RunStatus.AWAITING_APPROVAL,
        payload=payload,
        client_request_id=None,
        payload_hash=payload.stable_hash(),
        result={"status": "approval_required"},
        error=None,
        approval_id=approval_id,
        segment_metadata={"segment_count": 2},
        latest_event_cursor=8,
        can_continue=True,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        guardrail_summary={"action": "require_approval", "evaluation_count": 2},
        workflow_run_state={"current_phase": WorkflowPhase.EXECUTE.value},
        collaboration_summary={"latest_steps": [{"id": "step-task"}]},
    )


def _decision() -> ApprovalDecision:
    """构造默认审批决策。"""

    return ApprovalDecision(type="approve", tool_call_id="call-1")


def _resumer(chat_service: _FakeChatService, task_agent: _FakeTaskAgent) -> RunApprovalResumer:
    return RunApprovalResumer(
        chat_service=cast(ChatServicePort, chat_service),
        task_agent=cast(TaskAgentPort, task_agent),
    )


async def test_chat_resume_dispatches_to_chat_service() -> None:
    """聊天 Run 应分派到 ChatServicePort.resume_approval。"""

    chat_service = _FakeChatService()
    task_agent = _FakeTaskAgent()
    resumer = _resumer(chat_service, task_agent)

    result = await resumer(_chat_snapshot(), [_decision()], model="model-override")

    assert chat_service.requests == [
        ApprovalResumeRequestVO(
            session_id="session-chat",
            approval_id="approval-1",
            decisions=(_decision(),),
            model="model-override",
        )
    ]
    assert task_agent.requests == []
    assert result == ApprovalResumeStoreResult(
        status="succeeded",
        result={
            "kind": "chat",
            "session_id": "session-chat",
            "reply": "done",
            "model": "model-chat",
            "prompt_id": "chat-default@v1",
            "usage": {"total_tokens": 5},
            "status": "completed",
            "terminated_reason": "completed",
            "action_requests": [],
        },
        terminal_reason="completed",
        guardrail_summary=None,
        workflow_run_state=None,
        collaboration_summary=None,
    )


async def test_chat_completed_with_workflow_next_phase_maps_to_queued() -> None:
    """workflow 未到 finalize 时，completed 也应重新入队到下一 phase。"""

    chat_service = _FakeChatService()
    task_agent = _FakeTaskAgent()
    resumer = _resumer(chat_service, task_agent)
    snapshot = _chat_snapshot(workflow_run_state={"current_phase": WorkflowPhase.EXECUTE.value})

    result = await resumer(snapshot, [_decision()])

    assert result == ApprovalResumeStoreResult(
        status="queued",
        result={
            "kind": "chat",
            "session_id": "session-chat",
            "reply": "done",
            "model": "model-chat",
            "prompt_id": "chat-default@v1",
            "usage": {"total_tokens": 5},
            "status": "completed",
            "terminated_reason": "completed",
            "action_requests": [],
        },
        terminal_reason="completed",
        guardrail_summary=None,
        workflow_run_state=None,
        collaboration_summary=None,
    )


async def test_chat_approval_required_maps_to_awaiting_approval_with_new_id() -> None:
    """聊天恢复后再次命中审批时应返回 awaiting_approval 与新 approval_id。"""

    chat_service = _FakeChatService()
    chat_service.response = replace(
        chat_service.response,
        reply="",
        status="approval_required",
        approval_id="approval-2",
        action_requests=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="shell_exec",
                arguments='{"cmd": "ls"}',
                allowed_decisions=frozenset({"approve", "reject"}),
                reason="need approval",
            ),
        ),
        segment_metadata=_segment_metadata(2, "approval_required"),
    )
    resumer = _resumer(chat_service, _FakeTaskAgent())

    result = await resumer(_chat_snapshot(), [_decision()])

    assert result.status == "awaiting_approval"
    assert result.approval_id == "approval-2"
    assert result.guardrail_summary is None
    assert result.workflow_run_state is None
    assert result.collaboration_summary is None
    assert result.result is not None
    assert result.result["kind"] == "chat"
    assert result.result["session_id"] == "session-chat"
    assert result.result["reply"] == ""
    assert result.result["model"] == "model-chat"
    assert result.result["prompt_id"] == "chat-default@v1"
    assert result.result["usage"] == {"total_tokens": 5}
    assert result.result["status"] == "approval_required"
    assert result.result["terminated_reason"] == "completed"
    assert len(result.result["action_requests"]) == 1
    action_request = result.result["action_requests"][0]
    assert action_request["tool_call_id"] == "call-1"
    assert action_request["tool_name"] == "shell_exec"
    assert action_request["arguments"] == '{"cmd": "ls"}'
    assert set(action_request["allowed_decisions"]) == {"approve", "reject"}
    assert action_request["reason"] == "need approval"


async def test_task_status_mapping_covers_queued_awaiting_approval_succeeded_and_failed() -> None:
    """任务审批恢复应按 TaskStatus 映射 Run 审批存储结果。"""

    chat_service = _FakeChatService()
    task_agent = _FakeTaskAgent()
    resumer = _resumer(chat_service, task_agent)
    snapshot = _task_snapshot()

    task_agent.response = replace(
        task_agent.response,
        content="paused",
        status=TaskStatus.PAUSED,
        terminated_reason="max_rounds",
        segment_metadata=_segment_metadata(2, "max_continuations_reached"),
    )
    queued = await resumer(snapshot, [_decision()])
    assert chat_service.requests == []
    assert task_agent.requests[0] == TaskApprovalResumeRequest(
        session_id="session-task",
        approval_id="approval-1",
        decisions=(_decision(),),
        model=None,
    )
    assert queued.status == "queued"
    assert queued.guardrail_summary is None
    assert queued.workflow_run_state is None
    assert queued.collaboration_summary is None
    assert queued.result == {
        "kind": "task",
        "content": "paused",
        "task_status": "paused",
        "model": "model-task",
        "prompt_id": "task-template@v1",
        "usage": {"total_tokens": 7},
        "trace": [{"step": 1, "action": "tool_result", "detail": "ok", "timestamp_ms": 1.0}],
        "latency_ms": 0.0,
        "terminated_reason": "max_rounds",
    }

    task_agent.response = replace(
        task_agent.response,
        content="need approval",
        status=TaskStatus.HUMAN_INTERVENTION_REQUIRED,
        approval_id="approval-3",
        segment_metadata=_segment_metadata(3, "approval_required"),
    )
    awaiting = await resumer(snapshot, [_decision()])
    assert awaiting.status == "awaiting_approval"
    assert awaiting.approval_id == "approval-3"
    assert awaiting.guardrail_summary is None
    assert awaiting.workflow_run_state is None
    assert awaiting.collaboration_summary is None

    task_agent.response = replace(
        task_agent.response,
        content="task done",
        status=TaskStatus.SUCCESS,
        terminated_reason="completed",
        approval_id=None,
        segment_metadata=_segment_metadata(4, "completed"),
    )
    succeeded = await resumer(snapshot, [_decision()])
    assert succeeded.status == "succeeded"
    assert succeeded.terminal_reason == "completed"
    assert succeeded.guardrail_summary is None
    assert succeeded.workflow_run_state is None
    assert succeeded.collaboration_summary is None

    task_agent.response = replace(
        task_agent.response,
        content="boom",
        status=TaskStatus.FAILED,
        terminated_reason="completed",
        approval_id=None,
        segment_metadata=_segment_metadata(5, "completed"),
    )
    failed = await resumer(snapshot, [_decision()])
    assert failed == ApprovalResumeStoreResult(
        status="failed",
        error={"message": "boom", "task_status": "failed"},
        terminal_reason="failed",
        guardrail_summary=None,
        workflow_run_state=None,
        collaboration_summary=None,
    )


async def test_chat_resume_preserves_existing_guardrail_waiting_state_fields() -> None:
    """聊天恢复再次审批时应透传新 approval_id，且不覆盖既有摘要字段。"""

    chat_service = _FakeChatService()
    chat_service.response = replace(
        chat_service.response,
        reply="",
        status="approval_required",
        approval_id="approval-2",
        action_requests=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="shell_exec",
                arguments='{"cmd": "ls"}',
                allowed_decisions=frozenset({"approve", "reject"}),
                reason="need approval",
            ),
        ),
        segment_metadata=_segment_metadata(2, "approval_required"),
    )
    snapshot = _chat_snapshot(workflow_run_state={"current_phase": WorkflowPhase.EXECUTE.value})
    existing_guardrail_summary = snapshot.guardrail_summary
    existing_workflow_state = snapshot.workflow_run_state
    existing_collaboration_summary = snapshot.collaboration_summary
    resumer = _resumer(chat_service, _FakeTaskAgent())

    result = await resumer(snapshot, [_decision()])

    assert result.status == "awaiting_approval"
    assert result.approval_id == "approval-2"
    assert result.guardrail_summary is None
    assert result.workflow_run_state is None
    assert result.collaboration_summary is None
    assert snapshot.guardrail_summary == existing_guardrail_summary
    assert snapshot.workflow_run_state == existing_workflow_state
    assert snapshot.collaboration_summary == existing_collaboration_summary


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ApprovalExpiredError("session-chat", "approval-1"), id="expired"),
        pytest.param(ApprovalConsumedError("session-chat", "approval-1"), id="consumed"),
        pytest.param(ApprovalNotFoundError("session-chat", "approval-1"), id="not-found"),
    ],
)
async def test_resumer_preserves_existing_approval_exceptions(error: Exception) -> None:
    """审批恢复相关异常应原样透传，不包装为新异常。"""

    chat_service = _FakeChatService()
    chat_service.error = error
    resumer = _resumer(chat_service, _FakeTaskAgent())

    with pytest.raises(type(error)):
        await resumer(_chat_snapshot(), [_decision()])
