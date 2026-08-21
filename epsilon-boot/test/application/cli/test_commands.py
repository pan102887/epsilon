"""Slash command router tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

from application.cli.commands import SlashCommandRouter
from application.cli.runtime import DoctorResult, ResumeSessionResult
from application.cli.session import TuiSessionState
from application.cli.workflow import (
    CodingDiffSnapshot,
    CodingFilesSnapshot,
    CodingStatusSnapshot,
    CodingTestsSnapshot,
    WorkflowTestRecord,
)
from domain.agent.value_objects import ApprovalDecision, ApprovalInterruptSummary
from domain.chat.value_objects import SessionMetadata
from domain.run.exceptions import RunContinuationUnavailableError, RunNotFoundError
from domain.run.value_objects import RunKind, RunPayload, RunSnapshot, RunStatus


class FakeRuntime:
    def __init__(self) -> None:
        self.cleared: list[str] = []
        self.deleted: list[str] = []
        self.calls: list[tuple[object, ...]] = []
        self.pending_approvals: list[ApprovalInterruptSummary] = []
        self.sessions: list[SessionMetadata] = [
            SessionMetadata(
                session_id="tui-newer",
                updated_at_epoch_ms=2000,
                message_count=4,
                preview="newer preview",
            ),
            SessionMetadata(
                session_id="tui-older",
                updated_at_epoch_ms=1000,
                message_count=1,
                preview="older preview",
            ),
        ]

    async def clear_session(self, session_id: str) -> None:
        self.cleared.append(session_id)

    async def list_sessions(self, limit: int = 20) -> list[SessionMetadata]:
        return self.sessions[:limit]

    async def resume_session(self, session_id: str) -> ResumeSessionResult:
        if session_id == "missing":
            return ResumeSessionResult(found=False, missing_reason="missing_index")
        metadata = SessionMetadata(
            session_id=session_id,
            updated_at_epoch_ms=2000,
            message_count=4,
            preview="hello world",
            model="qwen3",
        )
        approvals: list[ApprovalInterruptSummary] = []
        if session_id == "with-approval":
            approvals.append(
                ApprovalInterruptSummary(
                    session_id=session_id,
                    approval_id="approval-1",
                    action_count=2,
                    created_at_epoch=1.0,
                    expires_at_epoch=2.0,
                    expired=False,
                    tool_names=("shell", "edit"),
                )
            )
        return ResumeSessionResult(
            found=True,
            metadata=metadata,
            approval_summaries=approvals,
        )

    async def delete_session(self, session_id: str) -> bool:
        self.deleted.append(session_id)
        return session_id != "missing"

    def default_model(self) -> str:
        return "glm-4.7"

    def doctor(self, state: TuiSessionState) -> DoctorResult:
        return DoctorResult(
            session_id=state.session_id,
            model=state.model or "glm-4.7",
            agent_mode="main_agent",
            workspace="/tmp/workspace",
        )

    def list_known_runs(self) -> list[RunSnapshot]:
        return [
            _snapshot(
                "run-known",
                RunKind.CHAT,
                status=RunStatus.RUNNING,
            )
        ]

    async def create_chat_run(self, message: str, state: TuiSessionState) -> RunSnapshot:
        self.calls.append(("create_chat_run", message, state.session_id))
        return _snapshot("run-chat", RunKind.CHAT)

    async def create_task_run(self, goal: str, state: TuiSessionState) -> RunSnapshot:
        self.calls.append(("create_task_run", goal, state.session_id))
        return _snapshot("run-task", RunKind.TASK)

    async def get_run(self, run_id: str) -> RunSnapshot:
        self.calls.append(("get_run", run_id))
        if run_id == "missing":
            raise RunNotFoundError(run_id)
        return _snapshot(run_id, RunKind.CHAT, status=RunStatus.RUNNING)

    async def continue_run(self, run_id: str, model: str | None = None) -> RunSnapshot:
        self.calls.append(("continue_run", run_id, model))
        if run_id == "bad":
            raise RunContinuationUnavailableError(run_id, "当前状态为 failed")
        return _snapshot(run_id, RunKind.CHAT, status=RunStatus.QUEUED)

    async def resume_approval_run(
        self, run_id: str, decisions: list[ApprovalDecision], model: str | None = None
    ) -> RunSnapshot:
        self.calls.append(("resume_approval_run", run_id, decisions, model))
        return _snapshot(run_id, RunKind.CHAT, status=RunStatus.QUEUED)

    async def cancel_run(self, run_id: str) -> RunSnapshot:
        self.calls.append(("cancel_run", run_id))
        return _snapshot(run_id, RunKind.CHAT, status=RunStatus.CANCEL_REQUESTED)

    async def list_pending_approvals(self, session_id: str) -> list[ApprovalInterruptSummary]:
        self.calls.append(("list_pending_approvals", session_id))
        return self.pending_approvals

    async def coding_status(self, state: TuiSessionState) -> CodingStatusSnapshot:
        self.calls.append(("coding_status", state.session_id))
        return CodingStatusSnapshot(
            session_id=state.session_id,
            model=state.model or "glm-4.7",
            workspace="/tmp/workspace",
            pending_approval_count=1,
            trace_step_count=3,
            latest_trace_kind="tool_call",
        )

    async def coding_diff(self) -> CodingDiffSnapshot:
        self.calls.append(("coding_diff",))
        return CodingDiffSnapshot(
            content="diff --git a/src/app.py b/src/app.py",
            available=True,
        )

    async def coding_tests(self, state: TuiSessionState) -> CodingTestsSnapshot:
        self.calls.append(("coding_tests", state.session_id))
        return CodingTestsSnapshot(
            trace_available=True,
            records=(
                WorkflowTestRecord(
                    command="uv run pytest test/application/cli",
                    tool_name="shell_exec",
                    success=True,
                    exit_code=0,
                    result_summary="1 passed",
                ),
            ),
        )

    async def coding_files(self, state: TuiSessionState) -> CodingFilesSnapshot:
        self.calls.append(("coding_files", state.session_id))
        return CodingFilesSnapshot(
            trace_available=True,
            groups={"write": ("src/app.py",), "read": ("src/app.py",)},
        )


def _snapshot(
    run_id: str,
    kind: RunKind,
    *,
    status: RunStatus = RunStatus.QUEUED,
    task_classification: str | None = None,
    guardrail_summary: dict[str, Any] | None = None,
) -> RunSnapshot:
    now = datetime.now(UTC)
    payload = RunPayload(
        kind=kind,
        session_id="tui-test",
        chat={"message": "hi"} if kind is RunKind.CHAT else None,
        task={"goal": "goal"} if kind is RunKind.TASK else None,
        model="glm-4.7",
    )
    return RunSnapshot(
        run_id=run_id,
        kind=kind,
        status=status,
        payload=payload,
        client_request_id="client-1",
        payload_hash=payload.stable_hash(),
        result=None,
        error={"message": "boom"} if status is RunStatus.FAILED else None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=7,
        can_continue=status is RunStatus.PAUSED,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
        task_classification=task_classification,
        guardrail_summary=guardrail_summary,
    )


async def test_help_command_lists_core_commands() -> None:
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]
    result = await router.handle("/help", TuiSessionState(session_id="tui-test"))

    assert "/config doctor" in result.message
    assert "/sessions" in result.message
    assert "/resume <id>" in result.message
    assert "/delete! <id>" in result.message
    assert "/run chat <消息>" in result.message
    assert "/approval" in result.message
    assert "/approval mode <ask|auto|manual>" in result.message
    assert "/status" in result.message
    assert "/diff" in result.message
    assert "/tests" in result.message
    assert "/files" in result.message
    assert "/tools list" not in result.message
    assert not result.should_exit


async def test_new_command_resets_without_deleting_old_session() -> None:
    runtime = FakeRuntime()
    state = TuiSessionState(session_id="tui-old")
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/new", state)

    assert runtime.cleared == []
    assert runtime.deleted == []
    assert state.session_id != "tui-old"
    assert state.session_id in result.message


async def test_sessions_command_lists_recent_metadata() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/sessions", TuiSessionState(session_id="tui-test"))

    assert "tui-newer" in result.message
    assert "messages=4" in result.message
    assert "newer preview" in result.message
    assert result.message.index("tui-newer") < result.message.index("tui-older")


async def test_sessions_command_handles_empty_index() -> None:
    runtime = FakeRuntime()
    runtime.sessions = []
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/sessions", TuiSessionState(session_id="tui-test"))

    assert result.message == "暂无可恢复会话"


async def test_resume_command_success_sets_state_and_reports_approvals() -> None:
    runtime = FakeRuntime()
    state = TuiSessionState(session_id="tui-old")
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/resume with-approval", state)

    assert state.session_id == "with-approval"
    assert "已恢复会话: with-approval" in result.message
    assert "messages: 4" in result.message
    assert "待处理 approval: 1 个" in result.message
    assert "approval_id=approval-1" in result.message


async def test_resume_command_missing_does_not_change_state() -> None:
    runtime = FakeRuntime()
    state = TuiSessionState(session_id="tui-old")
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    usage = await router.handle("/resume", state)
    missing = await router.handle("/resume missing", state)

    assert usage.message == "用法: /resume <session_id>"
    assert "会话不存在或已过期: missing" in missing.message
    assert state.session_id == "tui-old"


async def test_delete_command_requires_bang_and_deletes_non_current_session() -> None:
    runtime = FakeRuntime()
    state = TuiSessionState(session_id="tui-current")
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    hint = await router.handle("/delete tui-old", state)
    usage = await router.handle("/delete!", state)
    deleted = await router.handle("/delete! tui-old", state)

    assert hint.message == "删除会话是不可逆操作，请使用: /delete! <session_id>"
    assert usage.message == "用法: /delete! <session_id>"
    assert runtime.deleted == ["tui-old"]
    assert "已删除会话: tui-old" in deleted.message
    assert state.session_id == "tui-current"


async def test_delete_current_session_resets_state_and_missing_is_readable() -> None:
    runtime = FakeRuntime()
    state = TuiSessionState(session_id="tui-current")
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    deleted = await router.handle("/delete! tui-current", state)
    missing = await router.handle("/delete! missing", state)

    assert "已删除会话: tui-current" in deleted.message
    assert "当前会话已切换:" in deleted.message
    assert state.session_id != "tui-current"
    assert "会话不存在或已删除: missing" in missing.message


async def test_model_command_sets_state_model() -> None:
    state = TuiSessionState(session_id="tui-test")
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]

    result = await router.handle("/model qwen3", state)

    assert state.model == "qwen3"
    assert "qwen3" in result.message


async def test_tools_list_is_not_a_public_command() -> None:
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]

    result = await router.handle("/tools list", TuiSessionState(session_id="tui-test"))

    assert "未知命令" in result.message


async def test_config_doctor_reports_agent_mode() -> None:
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]

    result = await router.handle("/config doctor", TuiSessionState(session_id="tui-test"))

    assert "agent_mode: main_agent" in result.message
    assert "tool_count" not in result.message


async def test_status_command_reports_coding_workflow_snapshot() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/status", TuiSessionState(session_id="tui-test"))

    assert "session_id: tui-test" in result.message
    assert "model: glm-4.7" in result.message
    assert "workspace: /tmp/workspace" in result.message
    assert "pending_approval: 1" in result.message
    assert "trace_steps: 3" in result.message
    assert "latest_trace: tool_call" in result.message
    assert runtime.calls == [("coding_status", "tui-test")]


async def test_diff_command_reports_git_diff() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/diff", TuiSessionState(session_id="tui-test"))

    assert "diff --git" in result.message
    assert runtime.calls == [("coding_diff",)]


async def test_tests_command_reports_recent_test_records() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/tests", TuiSessionState(session_id="tui-test"))

    assert "最近测试/验证命令" in result.message
    assert "[PASS] shell_exec exit_code=0: uv run pytest test/application/cli" in result.message
    assert "1 passed" in result.message
    assert runtime.calls == [("coding_tests", "tui-test")]


async def test_files_command_reports_touched_files() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/files", TuiSessionState(session_id="tui-test"))

    assert "写入:" in result.message
    assert "- src/app.py" in result.message
    assert "读取:" in result.message
    assert runtime.calls == [("coding_files", "tui-test")]


async def test_quit_command_marks_state_exit() -> None:
    state = TuiSessionState(session_id="tui-test")
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]

    result = await router.handle("/quit", state)

    assert state.should_exit
    assert result.should_exit


async def test_run_chat_command_creates_run() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/run chat hello", TuiSessionState(session_id="tui-test"))

    assert runtime.calls == [("create_chat_run", "hello", "tui-test")]
    assert "run_id: run-chat" in result.message
    assert "status: queued" in result.message
    assert "can_continue: false" in result.message
    assert "latest_cursor: 7" in result.message


async def test_run_status_renders_guardrail_fields() -> None:
    class GuardrailRuntime(FakeRuntime):
        async def get_run(self, run_id: str) -> RunSnapshot:
            self.calls.append(("get_run", run_id))
            return _snapshot(
                run_id,
                RunKind.CHAT,
                status=RunStatus.RUNNING,
                task_classification="tool_task",
                guardrail_summary={"action": "observe", "reason": "none"},
            )

    runtime = GuardrailRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/run status run-1", TuiSessionState())

    assert "task_classification: tool_task" in result.message
    assert "guardrail_summary: {'action': 'observe', 'reason': 'none'}" in result.message


async def test_run_status_renders_workflow_fields() -> None:
    class WorkflowRuntime(FakeRuntime):
        async def get_run(self, run_id: str) -> RunSnapshot:
            self.calls.append(("get_run", run_id))
            return replace(
                _snapshot(run_id, RunKind.TASK, status=RunStatus.PAUSED),
                workflow_name="code_change",
                workflow_run_state={
                    "workflow_name": "code_change",
                    "current_phase": "execute",
                    "phase_history": [{"phase": "plan", "status": "succeeded"}],
                },
                collaboration_summary={
                    "delegation_count": 1,
                    "recent_steps": [
                        {
                            "action": "delegation",
                            "target_agent": "reviewer",
                            "result_summary": "review queued",
                        }
                    ],
                },
            )

    runtime = WorkflowRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle("/run status run-1", TuiSessionState())

    assert "workflow_name: code_change" in result.message
    assert "workflow_phase: execute" in result.message
    assert "workflow_phase_history: plan:succeeded" in result.message
    assert "latest_collaboration_summary: delegation / reviewer / review queued" in (result.message)
    assert "recent_collaboration_summary" not in result.message


async def test_run_task_command_creates_run() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]

    result = await router.handle(
        "/run task summarize repo",
        TuiSessionState(session_id="tui-test"),
    )

    assert runtime.calls == [("create_task_run", "summarize repo", "tui-test")]
    assert "run_id: run-task" in result.message


async def test_run_control_commands_delegate_to_runtime() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]
    state = TuiSessionState(session_id="tui-test", model="qwen3")

    status = await router.handle("/run status run-1", state)
    watch = await router.handle("/run watch run-1", state)
    continued = await router.handle("/run continue run-1", state)
    approved = await router.handle("/run approve run-1 call-1", state)
    cancelled = await router.handle("/run cancel run-1", state)

    assert "status: running" in status.message
    assert "开始订阅 Run 事件" in watch.message
    assert "status: queued" in continued.message
    assert "status: queued" in approved.message
    assert "status: cancel_requested" in cancelled.message
    approved_call = cast(list[ApprovalDecision], runtime.calls[3][2])
    assert runtime.calls == [
        ("get_run", "run-1"),
        ("get_run", "run-1"),
        ("continue_run", "run-1", "qwen3"),
        ("resume_approval_run", "run-1", approved_call, "qwen3"),
        ("cancel_run", "run-1"),
    ]
    assert approved_call[0].tool_call_id == "call-1"


async def test_run_command_maps_domain_errors_to_chinese_message() -> None:
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]

    missing = await router.handle("/run status missing", TuiSessionState())
    bad_continue = await router.handle("/run continue bad", TuiSessionState())

    assert "Run 操作失败" in missing.message
    assert "运行 missing 不存在" in missing.message
    assert "不可继续" in bad_continue.message


async def test_approval_command_without_args_shows_mode_and_pending_overview() -> None:
    runtime = FakeRuntime()
    runtime.pending_approvals = [
        ApprovalInterruptSummary(
            session_id="tui-test",
            approval_id="approval-1",
            action_count=2,
            created_at_epoch=1.0,
            expires_at_epoch=2.0,
            expired=False,
            tool_names=("shell_exec", "write_file"),
        )
    ]
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]
    state = TuiSessionState(session_id="tui-test", approval_mode="manual")

    result = await router.handle("/approval", state)

    assert "当前审批模式: manual" in result.message
    assert "approval_id=approval-1" in result.message
    assert "tool_names=shell_exec,write_file" in result.message
    assert "expires_at=" in result.message
    assert runtime.calls == [("list_pending_approvals", "tui-test")]


async def test_approval_command_without_pending_reports_empty_message() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]
    state = TuiSessionState(session_id="tui-test")

    result = await router.handle("/approval", state)

    assert "当前审批模式: ask" in result.message
    assert "暂无待处理审批" in result.message
    assert runtime.calls == [("list_pending_approvals", "tui-test")]


async def test_approval_mode_command_updates_state() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]
    state = TuiSessionState(session_id="tui-test")

    result = await router.handle("/approval mode manual", state)

    assert state.approval_mode == "manual"
    assert "已切换审批模式: manual" in result.message
    assert runtime.calls == []


async def test_approval_mode_command_rejects_invalid_value_without_state_change() -> None:
    runtime = FakeRuntime()
    router = SlashCommandRouter(runtime)  # type: ignore[arg-type]
    state = TuiSessionState(session_id="tui-test", approval_mode="ask")

    result = await router.handle("/approval mode bogus", state)

    assert state.approval_mode == "ask"
    assert result.message == "用法: /approval mode <ask|auto|manual>"
    assert runtime.calls == []


async def test_runs_command_lists_known_runs() -> None:
    router = SlashCommandRouter(FakeRuntime())  # type: ignore[arg-type]

    result = await router.handle("/runs", TuiSessionState())

    assert "run_id: run-known" in result.message
    assert "status: running" in result.message
    assert "can_continue: false" in result.message
    assert "latest_cursor: 7" in result.message
