"""epsilon TUI 斜杠命令路由模块。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from common.exceptions import BizException
from domain.agent.value_objects import ApprovalDecision, ApprovalInterruptSummary
from domain.chat.value_objects import SessionMetadata
from domain.run.exceptions import (
    RunCancelUnavailableError,
    RunContinuationUnavailableError,
    RunEventReplayExpiredError,
    RunIdempotencyConflictError,
    RunInvalidTransitionError,
    RunLeaseConflictError,
    RunNotFoundError,
    RunPayloadValidationError,
    RunQueueFullError,
    RunStoreUnavailableError,
)
from domain.run.value_objects import RunSnapshot
from domain.run.workflow import canonicalize_collaboration_summary

from .approval_mode import _APPROVAL_MODES
from .runtime import CliRuntime
from .session import TuiSessionState

HELP_TEXT = """可用命令:
/help             显示帮助
/new              开始新会话
/sessions         列出可恢复会话
/resume <id>      恢复指定会话
/delete! <id>     不可逆删除指定会话
/model            显示当前模型
/model <name>     切换当前模型
/approval         查看审批模式与待处理审批
/approval mode <ask|auto|manual> 切换本地审批模式
/status          展示当前 coding workflow 状态
/diff            展示当前 git diff 摘要
/tests           展示最近测试/验证命令记录
/files           展示本会话触达文件清单
/config doctor    显示本地运行时诊断
/run chat <消息>   创建后台聊天 Run
/run task <目标>   创建后台任务 Run
/runs             列出当前会话已知 Run
/run status <id>  查询 Run 状态
/run watch <id>   订阅 Run 事件
/run continue <id> 继续 paused Run
/run approve <id> [tool_call_id] 审批恢复 Run
/run cancel <id>  取消 Run
/quit             退出"""

RESUME_USAGE = "用法: /resume <session_id>"
DELETE_USAGE = "用法: /delete! <session_id>"
DELETE_EXPLICIT_HINT = "删除会话是不可逆操作，请使用: /delete! <session_id>"
SESSION_MISSING_MESSAGE = "会话不存在或已过期"
NO_SESSIONS_MESSAGE = "暂无可恢复会话"
NO_PENDING_APPROVAL_MESSAGE = "暂无待处理审批"
APPROVAL_USAGE = "用法: /approval | /approval mode <ask|auto|manual>"
APPROVAL_MODE_USAGE = "用法: /approval mode <ask|auto|manual>"


@dataclass(frozen=True)
class CommandResult:
    """斜杠命令返回的执行结果。"""

    message: str
    should_exit: bool = False


class SlashCommandRouter:
    """在不调用模型的情况下路由 TUI 斜杠命令。"""

    def __init__(self, runtime: CliRuntime) -> None:
        self._runtime = runtime

    async def handle(self, raw: str, state: TuiSessionState) -> CommandResult:
        """解析并执行一条斜杠命令。"""
        command = raw.strip()
        try:
            if command == "/help":
                return CommandResult(HELP_TEXT)

            if command == "/quit":
                state.should_exit = True
                return CommandResult("bye", should_exit=True)

            if command == "/new":
                state.reset_session()
                return CommandResult(f"已开始新会话: {state.session_id}")

            if command == "/sessions":
                sessions = await self._runtime.list_sessions()
                if not sessions:
                    return CommandResult(NO_SESSIONS_MESSAGE)
                return CommandResult("\n".join(_format_session(item) for item in sessions))

            if command == "/resume" or command.startswith("/resume "):
                session_id = command.removeprefix("/resume").strip()
                if not session_id:
                    return CommandResult(RESUME_USAGE)
                result = await self._runtime.resume_session(session_id)
                if not result.found or result.metadata is None:
                    return CommandResult(f"{SESSION_MISSING_MESSAGE}: {session_id}")
                state.session_id = session_id
                return CommandResult(
                    _format_resume_result(
                        result.metadata,
                        result.approval_summaries or [],
                    )
                )

            if command == "/delete" or command.startswith("/delete "):
                return CommandResult(DELETE_EXPLICIT_HINT)

            if command == "/delete!" or command.startswith("/delete! "):
                session_id = command.removeprefix("/delete!").strip()
                if not session_id:
                    return CommandResult(DELETE_USAGE)
                existed = await self._runtime.delete_session(session_id)
                if not existed:
                    return CommandResult(f"会话不存在或已删除: {session_id}")
                if session_id == state.session_id:
                    deleted_session_id = state.reset_session()
                    return CommandResult(
                        f"已删除会话: {deleted_session_id}\n当前会话已切换: {state.session_id}"
                    )
                return CommandResult(f"已删除会话: {session_id}")

            if command == "/model":
                model = state.model or self._runtime.default_model()
                return CommandResult(f"当前模型: {model}")

            if command.startswith("/model "):
                model = command.removeprefix("/model ").strip()
                if not model:
                    return CommandResult("用法: /model <name>")
                state.model = model
                return CommandResult(f"已切换模型: {model}")

            if command == "/approval" or command.startswith("/approval "):
                return await self._handle_approval_command(command, state)

            if command == "/config doctor":
                doctor = self._runtime.doctor(state)
                return CommandResult(
                    "\n".join(
                        [
                            f"session_id: {doctor.session_id}",
                            f"model: {doctor.model}",
                            f"agent_mode: {doctor.agent_mode}",
                            f"workspace: {doctor.workspace}",
                        ]
                    )
                )

            if command == "/status":
                snapshot = await self._runtime.coding_status(state)
                latest = snapshot.latest_trace_kind or "暂无"
                return CommandResult(
                    "\n".join(
                        [
                            f"session_id: {snapshot.session_id}",
                            f"model: {snapshot.model}",
                            f"workspace: {snapshot.workspace}",
                            f"pending_approval: {snapshot.pending_approval_count}",
                            f"trace_steps: {snapshot.trace_step_count}",
                            f"latest_trace: {latest}",
                        ]
                    )
                )

            if command == "/diff":
                snapshot = await self._runtime.coding_diff()
                if not snapshot.available:
                    return CommandResult(f"无法读取 diff: {snapshot.error or 'unknown'}")
                if not snapshot.content.strip():
                    return CommandResult("当前工作区暂无 git diff")
                suffix = "\n[diff 已截断]" if snapshot.truncated else ""
                return CommandResult(snapshot.content.rstrip() + suffix)

            if command == "/tests":
                snapshot = await self._runtime.coding_tests(state)
                if not snapshot.trace_available:
                    return CommandResult("当前会话暂无 trace，无法展示测试记录")
                if not snapshot.records:
                    return CommandResult("当前会话暂无测试/验证命令记录")
                lines = ["最近测试/验证命令:"]
                for record in snapshot.records:
                    status = "PASS" if record.success else "FAIL"
                    exit_code = (
                        f" exit_code={record.exit_code}"
                        if record.exit_code is not None
                        else ""
                    )
                    lines.append(
                        f"- [{status}] {record.tool_name}{exit_code}: {record.command}"
                    )
                    if record.result_summary:
                        lines.append(f"  {record.result_summary}")
                return CommandResult("\n".join(lines))

            if command == "/files":
                snapshot = await self._runtime.coding_files(state)
                if not snapshot.trace_available:
                    return CommandResult("当前会话暂无 trace，无法展示文件清单")
                if not snapshot.groups:
                    return CommandResult("当前会话 trace 暂无文件触达记录")
                labels = {
                    "read": "读取",
                    "write": "写入",
                    "execute": "执行/工作目录",
                    "other": "其他",
                }
                lines: list[str] = []
                for group in ("write", "read", "execute", "other"):
                    paths = snapshot.groups.get(group)
                    if not paths:
                        continue
                    lines.append(f"{labels[group]}:")
                    lines.extend(f"- {path}" for path in paths)
                return CommandResult("\n".join(lines))

            if command == "/runs":
                runs = self._runtime.list_known_runs()
                if not runs:
                    return CommandResult("当前 TUI 会话暂无已知 Run")
                return CommandResult("\n\n".join(_format_run_snapshot(run) for run in runs))

            if command.startswith("/run "):
                return await self._handle_run_command(command, state)
        except _RUN_ERRORS as exc:
            return CommandResult(_format_run_error(exc))

        return CommandResult(f"未知命令: {command}\n输入 /help 查看可用命令")

    async def _handle_run_command(self, command: str, state: TuiSessionState) -> CommandResult:
        """执行一条 Run 相关斜杠命令。"""
        rest = command.removeprefix("/run ").strip()
        action, _, arg = rest.partition(" ")
        arg = arg.strip()

        if action == "chat":
            if not arg:
                return CommandResult("用法: /run chat <message>")
            snapshot = await self._runtime.create_chat_run(arg, state)
            return CommandResult(_format_run_snapshot(snapshot))

        if action == "task":
            if not arg:
                return CommandResult("用法: /run task <goal>")
            snapshot = await self._runtime.create_task_run(arg, state)
            return CommandResult(_format_run_snapshot(snapshot))

        if action == "status":
            if not arg:
                return CommandResult("用法: /run status <run_id>")
            snapshot = await self._runtime.get_run(arg)
            return CommandResult(_format_run_snapshot(snapshot))

        if action == "watch":
            if not arg:
                return CommandResult("用法: /run watch <run_id>")
            snapshot = await self._runtime.get_run(arg)
            return CommandResult(
                "开始订阅 Run 事件；Ctrl+C 可请求取消。\n" + _format_run_snapshot(snapshot)
            )

        if action == "continue":
            if not arg:
                return CommandResult("用法: /run continue <run_id>")
            snapshot = await self._runtime.continue_run(arg, state.model)
            return CommandResult(_format_run_snapshot(snapshot))

        if action == "approve":
            parts = arg.split()
            if not parts:
                return CommandResult("用法: /run approve <run_id> [tool_call_id]")
            tool_call_id = parts[1] if len(parts) > 1 else "__tui_approval__"
            snapshot = await self._runtime.resume_approval_run(
                parts[0],
                [ApprovalDecision(type="approve", tool_call_id=tool_call_id)],
                state.model,
            )
            return CommandResult(_format_run_snapshot(snapshot))

        if action == "cancel":
            if not arg:
                return CommandResult("用法: /run cancel <run_id>")
            snapshot = await self._runtime.cancel_run(arg)
            return CommandResult(_format_run_snapshot(snapshot))

        return CommandResult(f"未知 Run 命令: /run {rest}\n输入 /help 查看可用命令")

    async def _handle_approval_command(
        self, command: str, state: TuiSessionState
    ) -> CommandResult:
        """处理 /approval：无参=查看模式+pending 概览；mode <value>=切换本地审批模式。"""
        rest = command.removeprefix("/approval").strip()
        if not rest:
            return await self._render_approval_overview(state)
        action, _, value = rest.partition(" ")
        if action == "mode":
            value = value.strip()
            if value not in _APPROVAL_MODES:
                return CommandResult(APPROVAL_MODE_USAGE)
            state.approval_mode = value
            return CommandResult(f"已切换审批模式: {value}")
        return CommandResult(APPROVAL_USAGE)

    async def _render_approval_overview(self, state: TuiSessionState) -> CommandResult:
        """展示当前审批模式与本会话未过期 pending approval（只读，不消费）。"""
        summaries = await self._runtime.list_pending_approvals(state.session_id)
        lines = [f"当前审批模式: {state.approval_mode}"]
        if not summaries:
            lines.append(NO_PENDING_APPROVAL_MESSAGE)
            return CommandResult("\n".join(lines))
        lines.append(f"待处理 approval: {len(summaries)} 个")
        for summary in summaries:
            tool_names = ",".join(summary.tool_names) if summary.tool_names else "unknown"
            lines.append(
                f"approval_id={summary.approval_id} "
                f"tool_names={tool_names} "
                f"expires_at={_format_epoch(summary.expires_at_epoch)}"
            )
        return CommandResult("\n".join(lines))


def _format_session(metadata: SessionMetadata) -> str:
    """Format one session metadata row for `/sessions`."""
    return (
        f"{_format_epoch_ms(metadata.updated_at_epoch_ms)} | "
        f"messages={metadata.message_count} | "
        f"{metadata.session_id} | "
        f"{metadata.preview}"
    )


def _format_resume_result(
    metadata: SessionMetadata,
    approval_summaries: list[ApprovalInterruptSummary],
) -> str:
    """Format successful `/resume` output."""
    lines = [
        f"已恢复会话: {metadata.session_id}",
        f"messages: {metadata.message_count}",
        f"updated_at: {_format_epoch_ms(metadata.updated_at_epoch_ms)}",
    ]
    if metadata.preview:
        lines.append(f"preview: {metadata.preview}")
    if approval_summaries:
        lines.append(f"待处理 approval: {len(approval_summaries)} 个")
        for summary in approval_summaries[:3]:
            tool_names = ",".join(summary.tool_names) if summary.tool_names else "unknown"
            lines.append(
                f"approval_id={summary.approval_id} "
                f"tool_names={tool_names} "
                f"expires_at={_format_epoch(summary.expires_at_epoch)}"
            )
    return "\n".join(lines)


def _format_epoch_ms(epoch_ms: int) -> str:
    """Format epoch milliseconds for CLI display."""
    return _format_epoch(epoch_ms / 1000)


def _format_epoch(epoch: float) -> str:
    """Format epoch seconds for CLI display."""
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def _format_run_snapshot(snapshot: RunSnapshot) -> str:
    """Format a Run snapshot for slash command output."""
    lines = [
        f"run_id: {snapshot.run_id}",
        f"status: {snapshot.status.value}",
        f"can_continue: {str(snapshot.can_continue).lower()}",
        f"latest_cursor: {snapshot.latest_event_cursor}",
    ]
    if snapshot.latest_checkpoint_id is not None:
        lines.append(f"latest_checkpoint_id: {snapshot.latest_checkpoint_id}")
    if snapshot.recoverable:
        lines.append("recoverable: true")
    if snapshot.recovery_attempt_count:
        lines.append(f"recovery_attempt_count: {snapshot.recovery_attempt_count}")
    if snapshot.last_recovery_error:
        lines.append(f"last_recovery_error: {_error_summary(snapshot.last_recovery_error)}")
    if snapshot.task_classification:
        lines.append(f"task_classification: {snapshot.task_classification}")
    if snapshot.guardrail_summary:
        lines.append(f"guardrail_summary: {snapshot.guardrail_summary}")
    lines.extend(_render_workflow_metadata(snapshot))
    if snapshot.error:
        lines.append(f"error: {_error_summary(snapshot.error)}")
    elif snapshot.terminal_reason:
        lines.append(f"terminal_reason: {snapshot.terminal_reason}")
    return "\n".join(lines)


def _error_summary(error: dict[str, object]) -> str:
    for key in ("summary", "message", "error", "reason"):
        value = error.get(key)
        if value:
            return str(value)
    return str(error)


def _render_workflow_metadata(snapshot: RunSnapshot) -> list[str]:
    """Render workflow fields already present on the Run snapshot."""

    lines: list[str] = []
    state = snapshot.workflow_run_state or {}
    workflow_name = snapshot.workflow_name or _string_value(state.get("workflow_name"))
    workflow_phase = _string_value(state.get("current_phase"))
    if workflow_name:
        lines.append(f"workflow_name: {workflow_name}")
    if workflow_phase:
        lines.append(f"workflow_phase: {workflow_phase}")
    phase_history = _phase_history_summary(state.get("phase_history"))
    if phase_history:
        lines.append(f"workflow_phase_history: {phase_history}")
    collaboration = _collaboration_summary(snapshot.collaboration_summary)
    if collaboration:
        lines.append(f"latest_collaboration_summary: {collaboration}")
    return lines


def _phase_history_summary(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    phases: list[str] = []
    for item in value[-4:]:
        if not isinstance(item, dict):
            continue
        phase = _string_value(item.get("phase"))
        status = _string_value(item.get("status"))
        if phase and status:
            phases.append(f"{phase}:{status}")
        elif phase:
            phases.append(phase)
    return ", ".join(phases)


def _collaboration_summary(value: dict[str, Any] | None) -> str:
    """渲染规范协作摘要，并兼容历史 recent_steps 快照。"""

    canonical = canonicalize_collaboration_summary(value)
    if not canonical:
        return ""
    latest_steps = canonical.get("latest_steps")
    if isinstance(latest_steps, list) and latest_steps:
        steps: list[str] = []
        for item in latest_steps[-3:]:
            if not isinstance(item, dict):
                continue
            action = _string_value(item.get("action"))
            target = _string_value(item.get("target_agent"))
            result = _string_value(item.get("result_summary") or item.get("task_summary"))
            parts = [part for part in (action, target, result) if part]
            if parts:
                steps.append(" / ".join(parts))
        if steps:
            return "; ".join(steps)
    counters = []
    for key in ("delegation_count", "handoff_count", "max_depth_seen", "limit_hit_reason"):
        if key in canonical:
            counters.append(f"{key}={canonical[key]}")
    return ", ".join(counters)


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _format_run_error(exc: BizException) -> str:
    message = getattr(exc, "message", str(exc))
    return f"Run 操作失败：{message}"


_RUN_ERRORS = (
    RunNotFoundError,
    RunQueueFullError,
    RunInvalidTransitionError,
    RunContinuationUnavailableError,
    RunCancelUnavailableError,
    RunLeaseConflictError,
    RunEventReplayExpiredError,
    RunPayloadValidationError,
    RunStoreUnavailableError,
    RunIdempotencyConflictError,
)
