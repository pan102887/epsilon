"""CLI runtime facade tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import application.cli.runtime as runtime_module
from application.cli.runtime import CliRuntime
from application.cli.session import TuiSessionState
from application.run.run_application_service import RunApplicationService
from domain.agent.ports import (
    ApprovalPolicyPort,
    ApprovalStateStorePort,
    ArtifactStorePort,
    TraceStorePort,
)
from domain.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from domain.agent.trace_value_objects import SessionTrace, ToolCallTrace
from domain.agent.value_objects import (
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalPolicy,
    PendingActionRequest,
)
from domain.chat.ports import ChatServicePort, SessionContextStorePort, SessionIndexPort
from domain.chat.value_objects import (
    ApprovalResumeRequestVO,
    ChatRequestVO,
    ChatResponseVO,
    SessionMetadata,
)
from domain.model_access.ports import ModelRegistryPort
from domain.model_access.value_objects import ModelInfo, StreamingChunk
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import Task, TaskResult, TaskStatus
from domain.workspace.ports import Workspace


class FakeContainer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.chat = FakeChatService()
        self.task = FakeTaskAgent()
        self.models = FakeModelRegistry()
        self.workspace = FakeWorkspace()
        self.runs = FakeRunService()
        self.session_store = FakeSessionStore()
        self.session_index = FakeSessionIndex()
        self.approvals = FakeApprovalStore()
        self.approval_policy = FakeApprovalPolicy()
        self.trace_store = FakeTraceStore()
        self.artifact_store = FakeArtifactStore()
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(FakeGitDiffTool())

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def resolve(self, abstract_type, *, name=None):  # type: ignore[no-untyped-def]
        mapping = {
            ChatServicePort: self.chat,
            TaskAgentPort: self.task,
            ModelRegistryPort: self.models,
            Workspace: self.workspace,
            RunApplicationService: self.runs,
            SessionContextStorePort: self.session_store,
            SessionIndexPort: self.session_index,
            ApprovalStateStorePort: self.approvals,
            ApprovalPolicyPort: self.approval_policy,
            TraceStorePort: self.trace_store,
            ArtifactStorePort: self.artifact_store,
            ToolRegistry: self.tool_registry,
        }
        return mapping[abstract_type]


class FakeChatService:
    prompt_id = "chat-default@v1"

    def __init__(self) -> None:
        self.cleared: list[str] = []

    async def chat(self, request: ChatRequestVO) -> ChatResponseVO:
        return ChatResponseVO(
            session_id=request.session_id,
            reply="ok",
            model=request.model or "glm-4.7",
            usage={},
            prompt_id=self.prompt_id,
        )

    async def clear_session(self, session_id: str) -> None:
        self.cleared.append(session_id)

    async def _chunks(self) -> AsyncIterator[StreamingChunk]:
        yield StreamingChunk(delta_content="he")
        yield StreamingChunk(delta_content="llo", finished=True)

    def stream_chat(self, request: ChatRequestVO) -> AsyncIterator[StreamingChunk]:
        self.last_request = request
        return self._chunks()

    async def _resume_events(self) -> AsyncIterator[AgentStreamEvent]:
        yield AgentStreamEvent(kind="assistant_delta", content="re")
        yield AgentStreamEvent(kind="assistant_delta", content="sumed")
        yield AgentStreamEvent(kind="assistant_done")

    def stream_resume_approval(
        self,
        request: ApprovalResumeRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        self.last_resume_request = request
        return self._resume_events()


class FakeTaskAgent:
    async def execute(self, task: Task) -> TaskResult:
        self.last_task = task
        return TaskResult(
            content=f"done: {task.goal}",
            status=TaskStatus.SUCCESS,
            model=task.model or "glm-4.7",
            prompt_id="task-template@v1",
        )


class FakeModelRegistry:
    def register_provider(self, provider_name, adapter, models):  # type: ignore[no-untyped-def]
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="glm-4.7", owned_by="cliproxy")]

    def get_adapter_for_model(self, model):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def get_default_model(self) -> str:
        return "glm-4.7"


class FakeWorkspace:
    def display_root_hint(self) -> str:
        return "/tmp/workspace"


class FakeRunService:
    def __init__(self) -> None:
        self.created_requests = []
        self.calls = []
        self.events = [
            RunEvent(
                run_id="run-1",
                cursor=2,
                event_type=RunEventType.SEGMENT_STARTED,
                payload={"segment": 1},
                created_at=datetime.now(UTC),
            )
        ]

    async def create_run(self, request):
        self.created_requests.append(request)
        return _snapshot(
            "run-1",
            request.payload.kind,
            request.payload,
            client_request_id=request.client_request_id,
        )

    async def get_run(self, run_id: str) -> RunSnapshot:
        self.calls.append(("get_run", run_id))
        return _snapshot(run_id)

    def stream_events(self, run_id: str, after_cursor: int | None) -> AsyncIterator[RunEvent]:
        self.calls.append(("stream_events", run_id, after_cursor))
        return self._stream_events()

    async def _stream_events(self) -> AsyncIterator[RunEvent]:
        for event in self.events:
            yield event

    async def continue_run(self, run_id: str, model: str | None = None) -> RunSnapshot:
        self.calls.append(("continue_run", run_id, model))
        return _snapshot(run_id, status=RunStatus.QUEUED)

    async def resume_approval_run(
        self,
        run_id: str,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> RunSnapshot:
        self.calls.append(("resume_approval_run", run_id, decisions, model))
        return _snapshot(run_id, status=RunStatus.QUEUED)

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        self.calls.append(("request_cancel", run_id))
        return _snapshot(run_id, status=RunStatus.CANCEL_REQUESTED)


class FakeSessionStore:
    def __init__(self) -> None:
        self.existing: set[str] = {"tui-test", "empty-session"}

    async def exists(self, session_id: str) -> bool:
        return session_id in self.existing


class FakeSessionIndex:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.metadata: dict[str, SessionMetadata] = {
            "tui-test": SessionMetadata(
                session_id="tui-test",
                updated_at_epoch_ms=2000,
                message_count=4,
                preview="hello",
                created_at_epoch_ms=1000,
                model="qwen3",
            ),
            "empty-session": SessionMetadata(
                session_id="empty-session",
                updated_at_epoch_ms=1500,
                message_count=0,
                preview="(空会话)",
                created_at_epoch_ms=1500,
            ),
            "stale-session": SessionMetadata(
                session_id="stale-session",
                updated_at_epoch_ms=1000,
                message_count=3,
                preview="stale",
            ),
        }

    async def get(self, session_id: str) -> SessionMetadata | None:
        return self.metadata.get(session_id)

    async def list_recent(self, limit: int = 20) -> list[SessionMetadata]:
        return sorted(
            self.metadata.values(),
            key=lambda item: item.updated_at_epoch_ms,
            reverse=True,
        )[:limit]

    async def delete(self, session_id: str) -> None:
        self.deleted.append(session_id)
        self.metadata.pop(session_id, None)


class FakeApprovalStore:
    def __init__(self) -> None:
        self.summaries: dict[str, list[ApprovalInterruptSummary]] = {
            "tui-test": [
                ApprovalInterruptSummary(
                    session_id="tui-test",
                    approval_id="approval-1",
                    action_count=2,
                    created_at_epoch=1.0,
                    expires_at_epoch=2.0,
                    expired=False,
                    tool_names=("shell", "edit"),
                )
            ]
        }
        self.interrupts: dict[tuple[str, str], ApprovalInterrupt] = {
            ("tui-test", "approval-1"): ApprovalInterrupt(
                session_id="tui-test",
                approval_id="approval-1",
                actions=(
                    PendingActionRequest(
                        tool_call_id="call-1",
                        tool_name="shell",
                        arguments='{"cmd": "ls"}',
                        allowed_decisions=frozenset({"approve", "reject"}),
                    ),
                    PendingActionRequest(
                        tool_call_id="call-2",
                        tool_name="edit",
                        arguments='{"path": "a.txt"}',
                        allowed_decisions=frozenset({"approve", "edit", "reject"}),
                    ),
                ),
                context_snapshot={},
                round_num=1,
                model="qwen3",
            )
        }
        self.consumed: list[tuple[str, str]] = []

    async def list_pending_by_session(
        self,
        session_id: str,
    ) -> list[ApprovalInterruptSummary]:
        return list(self.summaries.get(session_id, []))

    async def load(
        self,
        session_id: str,
        approval_id: str,
    ) -> ApprovalInterrupt | None:
        return self.interrupts.get((session_id, approval_id))

    async def consume(
        self,
        session_id: str,
        approval_id: str,
    ) -> ApprovalInterrupt | None:
        self.consumed.append((session_id, approval_id))
        return self.interrupts.get((session_id, approval_id))


class FakeApprovalPolicy:
    def __init__(self) -> None:
        self.queried: list[str] = []
        self.policies: dict[str, ApprovalPolicy] = {
            "shell": ApprovalPolicy(
                tool_name="shell",
                interrupt=True,
                allowed_decisions=frozenset({"approve", "reject"}),
                risk_label="高风险",
            )
        }

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        self.queried.append(tool_name)
        return self.policies.get(
            tool_name,
            ApprovalPolicy(
                tool_name=tool_name,
                interrupt=False,
                allowed_decisions=frozenset({"approve"}),
            ),
        )


class FakeTraceStore:
    def __init__(self) -> None:
        self.traces: dict[str, SessionTrace] = {
            "tui-test": SessionTrace(
                session_id="tui-test",
                started_at_epoch=1.0,
                steps=[
                    ToolCallTrace(
                        round_num=1,
                        tool_name="read_file",
                        tool_call_id="call-read",
                        arguments_summary='{"path":"src/app.py"}',
                        result_summary="ok",
                        success=True,
                        latency_ms=1.0,
                        timestamp_epoch=1.0,
                        metadata={"operation": "read_file", "logical_path": "src/app.py"},
                    ),
                    ToolCallTrace(
                        round_num=2,
                        tool_name="shell_exec",
                        tool_call_id="call-test",
                        arguments_summary="uv run pytest test/application/cli",
                        result_summary="1 passed",
                        success=True,
                        latency_ms=2.0,
                        timestamp_epoch=2.0,
                        metadata={
                            "operation": "shell_exec",
                            "command_summary": "uv run pytest test/application/cli",
                            "working_dir": ".",
                            "exit_code": 0,
                        },
                    ),
                    ToolCallTrace(
                        round_num=3,
                        tool_name="edit_file",
                        tool_call_id="call-edit",
                        arguments_summary='{"path":"src/app.py"}',
                        result_summary="edited",
                        success=True,
                        latency_ms=1.0,
                        timestamp_epoch=3.0,
                        metadata={"operation": "edit_file", "logical_path": "src/app.py"},
                    ),
                ],
            )
        }

    async def append_step(self, session_id, step, *, tier=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def get_session_trace(self, session_id, *, tier=None):  # type: ignore[no-untyped-def]
        return self.traces.get(session_id)

    async def list_traces(self, limit=20, *, tier=None):  # type: ignore[no-untyped-def]
        return list(self.traces.values())[:limit]


class FakeArtifactStore:
    async def append_artifact(self, session_id, artifact, *, tier=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def list_artifacts(self, session_id, *, tier=None):  # type: ignore[no-untyped-def]
        return []


class FakeGitDiffTool(Tool):
    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return "Read git diff in tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            content="diff --git a/src/app.py b/src/app.py",
            metadata={"truncated": False},
        )


def _snapshot(
    run_id: str,
    kind: RunKind = RunKind.CHAT,
    payload: RunPayload | None = None,
    *,
    status: RunStatus = RunStatus.QUEUED,
    client_request_id: str | None = None,
) -> RunSnapshot:
    now = datetime.now(UTC)
    payload = payload or RunPayload(
        kind=kind,
        session_id="tui-test",
        chat={"message": "hi"} if kind is RunKind.CHAT else None,
        task={"goal": "goal"} if kind is RunKind.TASK else None,
        model="qwen3",
    )
    return RunSnapshot(
        run_id=run_id,
        kind=kind,
        status=status,
        payload=payload,
        client_request_id=client_request_id,
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={"segment_count": 1},
        latest_event_cursor=1,
        can_continue=status is RunStatus.PAUSED,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
    )


async def test_runtime_start_resolves_shared_ports(monkeypatch) -> None:
    fake_container = FakeContainer()
    monkeypatch.setattr(runtime_module, "_container_configured", True)

    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    await runtime.start()
    doctor = runtime.doctor(TuiSessionState(session_id="tui-test"))
    await runtime.stop()

    assert fake_container.started
    assert fake_container.stopped
    assert runtime.list_models() == ["glm-4.7"]
    assert doctor.agent_mode == "main_agent"
    assert doctor.workspace == "/tmp/workspace"
    assert runtime.run_service is fake_container.runs
    assert runtime.session_store is fake_container.session_store
    assert runtime.session_index is fake_container.session_index
    assert runtime.approval_store is fake_container.approvals
    assert runtime.approval_policy is fake_container.approval_policy


async def test_runtime_stream_main_agent_uses_tui_state() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.chat_service = fake_container.chat

    state = TuiSessionState(session_id="tui-test", model="qwen3")
    chunks = [chunk.delta_content async for chunk in runtime.stream_main_agent("hi", state)]

    assert chunks == ["he", "llo"]
    assert fake_container.chat.last_request.session_id == "tui-test"
    assert fake_container.chat.last_request.model == "qwen3"


async def test_runtime_stream_main_agent_events_wraps_legacy_chunks() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.chat_service = fake_container.chat

    state = TuiSessionState(session_id="tui-test", model="qwen3")
    events = [event async for event in runtime.stream_main_agent_events("hi", state)]

    assert [event.kind for event in events] == [
        "assistant_delta",
        "assistant_delta",
        "assistant_done",
    ]
    assert [event.content for event in events[:2]] == ["he", "llo"]
    assert fake_container.chat.last_request.session_id == "tui-test"
    assert fake_container.chat.last_request.model == "qwen3"


async def test_runtime_resume_main_agent_events_forwards_stream() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.chat_service = fake_container.chat

    decision = ApprovalDecision(type="approve", tool_call_id="call-1")
    events = [
        event
        async for event in runtime.resume_main_agent_events(
            "tui-test",
            "approval-1",
            [decision],
            model="qwen3",
        )
    ]

    assert [event.kind for event in events] == [
        "assistant_delta",
        "assistant_delta",
        "assistant_done",
    ]
    assert [event.content for event in events[:2]] == ["re", "sumed"]
    request = fake_container.chat.last_resume_request
    assert isinstance(request, ApprovalResumeRequestVO)
    assert request.session_id == "tui-test"
    assert request.approval_id == "approval-1"
    assert request.decisions == (decision,)
    assert request.model == "qwen3"


async def test_runtime_resume_main_agent_events_symmetric_with_stream_events() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.chat_service = fake_container.chat

    state = TuiSessionState(session_id="tui-test", model="qwen3")
    stream_events = [event async for event in runtime.stream_main_agent_events("hi", state)]
    resume_events = [
        event
        async for event in runtime.resume_main_agent_events(
            "tui-test",
            "approval-1",
            [ApprovalDecision(type="approve", tool_call_id="call-1")],
            model="qwen3",
        )
    ]

    assert all(isinstance(event, AgentStreamEvent) for event in stream_events)
    assert all(isinstance(event, AgentStreamEvent) for event in resume_events)
    assert [event.kind for event in stream_events] == [event.kind for event in resume_events]


async def test_runtime_load_pending_actions_returns_full_actions() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.approval_store = fake_container.approvals

    actions = await runtime.load_pending_actions("tui-test", "approval-1")

    assert tuple(action.tool_call_id for action in actions) == ("call-1", "call-2")
    assert actions[0].arguments == '{"cmd": "ls"}'
    assert fake_container.approvals.consumed == []


async def test_runtime_load_pending_actions_missing_batch_returns_empty() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.approval_store = fake_container.approvals

    actions = await runtime.load_pending_actions("tui-test", "missing")

    assert actions == ()
    assert fake_container.approvals.consumed == []


async def test_runtime_load_pending_actions_without_store_returns_empty() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.approval_store = None

    actions = await runtime.load_pending_actions("tui-test", "approval-1")

    assert actions == ()


def test_runtime_policy_for_delegates_to_policy_port() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.approval_policy = fake_container.approval_policy

    policy = runtime.policy_for("shell")

    assert policy is fake_container.approval_policy.policies["shell"]
    assert fake_container.approval_policy.queried == ["shell"]


async def test_runtime_execute_once_builds_task() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.task_agent = fake_container.task

    result = await runtime.execute_once("summarize", model="glm-4.7")

    assert result.status is TaskStatus.SUCCESS
    assert fake_container.task.last_task.goal == "summarize"
    assert fake_container.task.last_task.model == "glm-4.7"


async def test_runtime_execute_once_json_maps_task_result() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.task_agent = fake_container.task
    runtime.artifact_store = fake_container.artifact_store  # type: ignore[assignment]

    result = await runtime.execute_once_json("summarize", model="glm-4.7")

    data = result.to_dict()
    assert data["status"] == "success"
    assert data["content"] == "done: summarize"
    assert data["model"] == "glm-4.7"
    assert data["prompt_id"] == "task-template@v1"
    assert data["trace_ref"] == {"available": False, "step_count": 0}
    assert data["artifact_ref"] == {"available": True}


async def test_runtime_lists_sessions_from_index() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.session_index = fake_container.session_index

    sessions = await runtime.list_sessions(limit=2)

    assert [item.session_id for item in sessions] == ["tui-test", "empty-session"]


async def test_runtime_resume_session_returns_metadata_and_approvals() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.session_store = fake_container.session_store
    runtime.session_index = fake_container.session_index
    runtime.approval_store = fake_container.approvals

    result = await runtime.resume_session("tui-test")

    assert result.found
    assert result.metadata is not None
    assert result.metadata.session_id == "tui-test"
    assert result.approval_summaries is not None
    assert result.approval_summaries[0].approval_id == "approval-1"


async def test_runtime_resume_session_missing_index_does_not_probe_context() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.session_store = fake_container.session_store
    runtime.session_index = fake_container.session_index

    result = await runtime.resume_session("missing")

    assert not result.found
    assert result.missing_reason == "missing_index"


async def test_runtime_resume_session_deletes_stale_index_when_context_missing() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.session_store = fake_container.session_store
    runtime.session_index = fake_container.session_index

    result = await runtime.resume_session("stale-session")

    assert not result.found
    assert result.missing_reason == "expired_or_missing"
    assert fake_container.session_index.deleted == ["stale-session"]


async def test_runtime_resume_indexed_empty_session_succeeds() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.session_store = fake_container.session_store
    runtime.session_index = fake_container.session_index

    result = await runtime.resume_session("empty-session")

    assert result.found
    assert result.metadata is not None
    assert result.metadata.message_count == 0


async def test_runtime_delete_session_returns_previous_existence_and_delegates() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.chat_service = fake_container.chat
    runtime.session_store = fake_container.session_store
    runtime.session_index = fake_container.session_index

    assert await runtime.delete_session("tui-test") is True
    assert await runtime.delete_session("missing") is False
    assert fake_container.chat.cleared == ["tui-test", "missing"]


async def test_runtime_create_chat_run_builds_run_payload() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.run_service = fake_container.runs  # type: ignore[assignment]
    state = TuiSessionState(session_id="tui-test", model="qwen3")

    snapshot = await runtime.create_chat_run("hello", state)

    request = fake_container.runs.created_requests[-1]
    assert snapshot.run_id == "run-1"
    assert request.payload.kind is RunKind.CHAT
    assert request.payload.session_id == "tui-test"
    assert request.payload.chat == {"message": "hello"}
    assert request.payload.model == "qwen3"
    assert request.client_request_id.startswith("tui:chat:tui-test:")


async def test_runtime_create_task_run_builds_run_payload() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.run_service = fake_container.runs  # type: ignore[assignment]
    state = TuiSessionState(session_id="tui-test", model="qwen3")

    await runtime.create_task_run("summarize repo", state)

    request = fake_container.runs.created_requests[-1]
    assert request.payload.kind is RunKind.TASK
    assert request.payload.task == {"goal": "summarize repo"}
    assert request.payload.chat is None
    assert request.client_request_id.startswith("tui:task:tui-test:")


async def test_runtime_run_methods_delegate_to_run_service() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.run_service = fake_container.runs  # type: ignore[assignment]
    decision = ApprovalDecision(type="approve", tool_call_id="call-1")

    await runtime.get_run("run-1")
    events = [event async for event in runtime.watch_run_events("run-1", 1)]
    await runtime.continue_run("run-1", "qwen3")
    await runtime.resume_approval_run("run-1", [decision], "qwen3")
    await runtime.cancel_run("run-1")

    assert events == fake_container.runs.events
    assert ("get_run", "run-1") in fake_container.runs.calls
    assert ("stream_events", "run-1", 1) in fake_container.runs.calls
    assert ("continue_run", "run-1", "qwen3") in fake_container.runs.calls
    assert fake_container.runs.calls[-1] == ("request_cancel", "run-1")


async def test_runtime_coding_status_reads_pending_and_trace() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.model_registry = fake_container.models
    runtime.workspace = fake_container.workspace
    runtime.approval_store = fake_container.approvals
    runtime.trace_store = fake_container.trace_store  # type: ignore[assignment]

    snapshot = await runtime.coding_status(TuiSessionState(session_id="tui-test"))

    assert snapshot.session_id == "tui-test"
    assert snapshot.model == "glm-4.7"
    assert snapshot.workspace == "/tmp/workspace"
    assert snapshot.pending_approval_count == 1
    assert snapshot.trace_step_count == 3
    assert snapshot.latest_trace_kind == "tool_call"


async def test_runtime_coding_diff_uses_registered_git_diff_tool() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.tool_registry = fake_container.tool_registry

    snapshot = await runtime.coding_diff()

    assert snapshot.available
    assert "diff --git" in snapshot.content
    assert not snapshot.truncated


async def test_runtime_coding_diff_reports_missing_tool_without_shell_fallback() -> None:
    runtime = CliRuntime(di_container=FakeContainer())  # type: ignore[arg-type]
    runtime.tool_registry = ToolRegistry()

    snapshot = await runtime.coding_diff()

    assert not snapshot.available
    assert snapshot.error == "git_diff 工具未注册，无法读取 diff"


async def test_runtime_coding_tests_and_files_extract_from_trace() -> None:
    fake_container = FakeContainer()
    runtime = CliRuntime(di_container=fake_container)  # type: ignore[arg-type]
    runtime.trace_store = fake_container.trace_store  # type: ignore[assignment]

    tests = await runtime.coding_tests(TuiSessionState(session_id="tui-test"))
    files = await runtime.coding_files(TuiSessionState(session_id="tui-test"))

    assert tests.trace_available
    assert len(tests.records) == 1
    assert tests.records[0].command == "uv run pytest test/application/cli"
    assert tests.records[0].success
    assert tests.records[0].exit_code == 0
    assert files.trace_available
    assert files.groups["write"] == ("src/app.py",)
    assert files.groups["read"] == ("src/app.py",)
    assert files.groups["execute"] == (".",)


def test_runtime_does_not_import_http_clients() -> None:
    source = Path("src/application/cli/runtime.py").read_text(encoding="utf-8")

    assert "httpx" not in source
    assert "requests" not in source
    assert "FastAPI" not in source
    assert "/api/runs" not in source
