"""Run 执行协调器单元测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from application.run.run_execution_coordinator import RunExecutionCoordinator
from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentRunMetadata,
    SegmentStopReason,
)
from domain.chat.ports import ChatServicePort
from domain.chat.value_objects import ChatContinueRequestVO, ChatRequestVO, ChatResponseVO
from domain.run.value_objects import RunKind, RunPayload, RunSnapshot, RunStatus
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import (
    Task,
    TaskContinueRequest,
    TaskResult,
    TaskStatus,
    TraceEntry,
)
from infrastructure.run.run_serialization_adapters import SegmentSerializerAdapter

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeProgress:
    """测试用 RunProgressSink fake。"""

    def __init__(self) -> None:
        self.started: list[tuple[str, int]] = []
        self.done: list[tuple[str, dict[str, Any]]] = []

    async def segment_started(self, run_id: str, segment_index: int) -> None:
        self.started.append((run_id, segment_index))

    async def segment_done(self, run_id: str, metadata: dict[str, Any]) -> None:
        self.done.append((run_id, metadata))


class _FakeChatService:
    """记录调用的 ChatServicePort fake。"""

    def __init__(self) -> None:
        self.chat_calls: list[ChatRequestVO] = []
        self.continue_calls: list[ChatContinueRequestVO] = []
        self.chat_response = ChatResponseVO(
            session_id="s-chat",
            reply="done",
            model="m1",
            usage={"total_tokens": 3},
            prompt_id="chat-default@v1",
            segment_metadata=_metadata(1, "completed"),
        )
        self.continue_response = ChatResponseVO(
            session_id="s-chat",
            reply="continued",
            model="m2",
            usage={"total_tokens": 5},
            prompt_id="chat-default@v1",
            segment_metadata=_metadata(2, "completed"),
        )
        self.raise_on_chat: Exception | None = None
        self.raise_on_continue: Exception | None = None

    async def chat(self, request: ChatRequestVO) -> ChatResponseVO:
        if self.raise_on_chat is not None:
            raise self.raise_on_chat
        self.chat_calls.append(request)
        return self.chat_response

    async def continue_chat(self, request: ChatContinueRequestVO) -> ChatResponseVO:
        if self.raise_on_continue is not None:
            raise self.raise_on_continue
        self.continue_calls.append(request)
        return self.continue_response


class _FakeTaskAgent:
    """记录调用的 TaskAgentPort fake。"""

    def __init__(self) -> None:
        self.execute_calls: list[Task] = []
        self.continue_calls: list[TaskContinueRequest] = []
        self.execute_response = TaskResult(
            content="task done",
            status=TaskStatus.SUCCESS,
            model="m1",
            prompt_id="task-template@v1",
            usage={"total_tokens": 4},
            trace=[TraceEntry(step=1, action="llm_response", detail="ok", timestamp_ms=1.0)],
            segment_metadata=_metadata(1, "completed"),
        )
        self.continue_response = TaskResult(
            content="task continued",
            status=TaskStatus.SUCCESS,
            model="m2",
            prompt_id="task-template@v1",
            usage={"total_tokens": 6},
            trace=[TraceEntry(step=2, action="tool_result", detail="ok", timestamp_ms=2.0)],
            segment_metadata=_metadata(2, "completed"),
        )
        self.raise_on_execute: Exception | None = None
        self.raise_on_continue: Exception | None = None

    async def execute(self, task: Task) -> TaskResult:
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        self.execute_calls.append(task)
        return self.execute_response

    async def continue_task(self, request: TaskContinueRequest) -> TaskResult:
        if self.raise_on_continue is not None:
            raise self.raise_on_continue
        self.continue_calls.append(request)
        return self.continue_response


def _metadata(count: int, reason: SegmentStopReason) -> SegmentRunMetadata:
    """构造分段元数据。"""

    return SegmentRunMetadata(
        segment_index=count,
        segment_count=count,
        segment_stop_reason=reason,
        budget_usage=SegmentBudgetUsage(segment_count=count, total_tokens=count * 10),
    )


def _snapshot(
    *,
    kind: RunKind,
    status: RunStatus = RunStatus.RUNNING,
    result: dict[str, Any] | None = None,
    payload_model: str | None = "m1",
    segment_metadata: dict[str, Any] | None = None,
) -> RunSnapshot:
    """构造测试用 RunSnapshot。"""

    payload = (
        RunPayload(
            kind=RunKind.CHAT,
            session_id="s-chat",
            chat={"session_id": "s-chat", "message": "hello", "model": "m1"},
            model=payload_model,
        )
        if kind is RunKind.CHAT
        else RunPayload(
            kind=RunKind.TASK,
            session_id="s-task",
            task={
                "goal": "ship feature",
                "input_data": {"ticket": "T-1"},
                "constraints": ["keep scope"],
                "output_format": "summary",
                "tool_names": ["search"],
                "model": "m1",
            },
            model=payload_model,
        )
    )
    return RunSnapshot(
        run_id=f"run-{kind.value}",
        kind=kind,
        status=status,
        payload=payload,
        client_request_id=None,
        payload_hash=None,
        result=result,
        error=None,
        approval_id=None,
        segment_metadata=segment_metadata,
        latest_event_cursor=None,
        can_continue=status is RunStatus.PAUSED,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
    )


def _coordinator(
    chat_service: _FakeChatService | None = None,
    task_agent: _FakeTaskAgent | None = None,
) -> tuple[RunExecutionCoordinator, _FakeChatService, _FakeTaskAgent, _FakeProgress]:
    """构造协调器和 fake 依赖。"""

    chat = chat_service or _FakeChatService()
    task = task_agent or _FakeTaskAgent()
    progress = _FakeProgress()
    return (
        RunExecutionCoordinator(
            chat_service=cast(ChatServicePort, chat),
            task_agent=cast(TaskAgentPort, task),
            segment_serializer=SegmentSerializerAdapter(),
        ),
        chat,
        task,
        progress,
    )


async def test_chat_first_execution_uses_create_payload_and_maps_completed() -> None:
    """chat 首次执行使用 ChatRequestVO 并映射成功 outcome。"""

    coordinator, chat, task, progress = _coordinator()
    snapshot = _snapshot(kind=RunKind.CHAT)

    outcome = await coordinator.execute(snapshot, progress)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.segment_metadata is not None
    assert outcome.result["reply"] == "done"
    assert outcome.result["usage"] == {"total_tokens": 3}
    assert outcome.segment_metadata["segment_count"] == 1
    assert chat.chat_calls == [ChatRequestVO(session_id="s-chat", message="hello", model="m1")]
    assert chat.continue_calls == []
    assert task.execute_calls == []
    assert progress.started == [("run-chat", 1)]
    assert progress.done[0][1]["segment_stop_reason"] == "completed"


async def test_chat_paused_continue_calls_continue_chat_without_original_message() -> None:
    """chat paused 继续只调用 continue_chat，不重复原始用户消息。"""

    coordinator, chat, _, _ = _coordinator()
    snapshot = _snapshot(
        kind=RunKind.CHAT,
        status=RunStatus.PAUSED,
        result={"reply": ""},
        payload_model="m2",
        segment_metadata={"segment_count": 1},
    )

    outcome = await coordinator.execute(snapshot, _FakeProgress())

    assert outcome.status is RunStatus.SUCCEEDED
    assert chat.chat_calls == []
    assert chat.continue_calls == [
        ChatContinueRequestVO(session_id="s-chat", stream=False, model="m2")
    ]
    assert not hasattr(chat.continue_calls[0], "message")


async def test_chat_requeued_paused_run_continues_same_run() -> None:
    """paused run 重新入队后仍识别为同一 Run 的 continue 路径。"""

    coordinator, chat, _, _ = _coordinator()
    snapshot = _snapshot(
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        result={"reply": "", "status": "paused"},
        segment_metadata={"segment_count": 1},
    )

    await coordinator.execute(snapshot, _FakeProgress())

    assert chat.chat_calls == []
    assert [call.session_id for call in chat.continue_calls] == ["s-chat"]


async def test_task_first_execution_uses_task_payload_and_maps_trace() -> None:
    """task 首次执行使用 Task，不走 continue_task。"""

    coordinator, _, task, progress = _coordinator()
    snapshot = _snapshot(kind=RunKind.TASK)

    outcome = await coordinator.execute(snapshot, progress)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.result["content"] == "task done"
    assert outcome.result["trace"][0]["action"] == "llm_response"
    assert outcome.result["trace_id"] == "s-task"
    assert outcome.result["trace_ref"] == {
        "available": True,
        "trace_id": "s-task",
        "url": "/api/traces/s-task",
    }
    assert outcome.result["artifact_ref"] == {
        "available": True,
        "session_id": "s-task",
        "url": "/api/artifacts/s-task",
    }
    assert task.continue_calls == []
    assert task.execute_calls == [
        Task(
            goal="ship feature",
            input_data={"ticket": "T-1"},
            constraints=["keep scope"],
            output_format="summary",
            model="m1",
            session_id="s-task",
            tool_names=frozenset({"search"}),
        )
    ]
    assert progress.done[0][1]["budget_usage"]["total_tokens"] == 10


async def test_task_paused_continue_calls_continue_task_without_original_goal() -> None:
    """task paused 继续只调用 continue_task，不重复原始 goal。"""

    coordinator, _, task, _ = _coordinator()
    snapshot = _snapshot(
        kind=RunKind.TASK,
        status=RunStatus.PAUSED,
        result={"content": ""},
        payload_model="m2",
        segment_metadata={"segment_count": 1},
    )

    outcome = await coordinator.execute(snapshot, _FakeProgress())

    assert outcome.status is RunStatus.SUCCEEDED
    assert task.execute_calls == []
    assert task.continue_calls == [TaskContinueRequest(session_id="s-task", model="m2")]
    assert not hasattr(task.continue_calls[0], "goal")


async def test_approval_waiting_maps_chat_approval_metadata() -> None:
    """approval_required 映射为 awaiting_approval 并保留 approval_id。"""

    chat = _FakeChatService()
    chat.chat_response = replace(
        chat.chat_response,
        reply="",
        status="approval_required",
        approval_id="approval-1",
        can_continue=False,
        segment_metadata=_metadata(1, "approval_required"),
    )
    coordinator, chat, _, _ = _coordinator(chat_service=chat)

    outcome = await coordinator.execute(_snapshot(kind=RunKind.CHAT), _FakeProgress())

    assert outcome.status is RunStatus.AWAITING_APPROVAL
    assert outcome.approval_id == "approval-1"
    assert outcome.terminal_reason == "completed"
    assert outcome.can_continue is False
    assert outcome.segment_metadata is not None
    assert outcome.segment_metadata["segment_stop_reason"] == "approval_required"


async def test_approval_resume_requeued_run_uses_continue_chat_same_run() -> None:
    """审批恢复后 queued 快照带既有 result 时继续同一 run。"""

    coordinator, chat, _, _ = _coordinator()
    snapshot = _snapshot(
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        result={"approval_id": "approval-1", "status": "approval_required"},
        segment_metadata={"segment_count": 1},
    )

    await coordinator.execute(snapshot, _FakeProgress())

    assert chat.chat_calls == []
    assert len(chat.continue_calls) == 1
    assert chat.continue_calls[0].session_id == "s-chat"


async def test_task_approval_resume_requeued_run_uses_continue_task_same_run() -> None:
    """任务审批恢复后重新入队时应继续同一 run，而不重复原始 goal。"""

    coordinator, _, task, _ = _coordinator()
    snapshot = _snapshot(
        kind=RunKind.TASK,
        status=RunStatus.QUEUED,
        result={"task_status": "human_intervention_required", "approval_id": "approval-1"},
        payload_model="m2",
        segment_metadata={"segment_count": 1},
    )

    outcome = await coordinator.execute(snapshot, _FakeProgress())

    assert outcome.status is RunStatus.SUCCEEDED
    assert task.execute_calls == []
    assert task.continue_calls == [TaskContinueRequest(session_id="s-task", model="m2")]


async def test_task_human_intervention_uses_explicit_approval_id_field() -> None:
    """任务审批等待优先透传 TaskResult.approval_id 字段。"""

    task = _FakeTaskAgent()
    task.execute_response = replace(
        task.execute_response,
        content="need approval approval_id=stale-content",
        status=TaskStatus.HUMAN_INTERVENTION_REQUIRED,
        approval_id="approval-field",
        segment_metadata=_metadata(1, "approval_required"),
    )
    coordinator, _, _, _ = _coordinator(task_agent=task)

    outcome = await coordinator.execute(_snapshot(kind=RunKind.TASK), _FakeProgress())

    assert outcome.status is RunStatus.AWAITING_APPROVAL
    assert outcome.approval_id == "approval-field"
    assert outcome.segment_metadata is not None
    assert outcome.segment_metadata["segment_stop_reason"] == "approval_required"


async def test_task_human_intervention_falls_back_to_content_approval_id() -> None:
    """旧任务结果缺少 approval_id 字段时仍兼容 content 提取。"""

    task = _FakeTaskAgent()
    task.execute_response = replace(
        task.execute_response,
        content="need approval approval_id=approval-from-content",
        status=TaskStatus.HUMAN_INTERVENTION_REQUIRED,
        approval_id=None,
        segment_metadata=_metadata(1, "approval_required"),
    )
    coordinator, _, _, _ = _coordinator(task_agent=task)

    outcome = await coordinator.execute(_snapshot(kind=RunKind.TASK), _FakeProgress())

    assert outcome.status is RunStatus.AWAITING_APPROVAL
    assert outcome.approval_id == "approval-from-content"


async def test_task_failed_result_maps_failed_outcome() -> None:
    """TaskResult FAILED 映射为 failed outcome。"""

    task = _FakeTaskAgent()
    task.execute_response = replace(
        task.execute_response,
        content="tool failed",
        status=TaskStatus.FAILED,
        terminated_reason="completed",
    )
    coordinator, _, _, _ = _coordinator(task_agent=task)

    outcome = await coordinator.execute(_snapshot(kind=RunKind.TASK), _FakeProgress())

    assert outcome.status is RunStatus.FAILED
    assert outcome.error == {"message": "tool failed", "task_status": "failed"}
    assert outcome.terminal_reason == "failed"


async def test_failed_exception_maps_failed_outcome_and_still_reports_segment_done() -> None:
    """端口异常不会逃逸，映射为 failed outcome。"""

    chat = _FakeChatService()
    chat.raise_on_chat = RuntimeError("model unavailable")
    coordinator, _, _, progress = _coordinator(chat_service=chat)

    outcome = await coordinator.execute(_snapshot(kind=RunKind.CHAT), progress)

    assert outcome.status is RunStatus.FAILED
    assert outcome.error == {"type": "RuntimeError", "message": "model unavailable"}
    assert outcome.can_continue is False
    assert progress.started == [("run-chat", 1)]
    assert progress.done == [("run-chat", {})]
