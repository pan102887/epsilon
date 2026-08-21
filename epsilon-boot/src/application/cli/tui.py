"""基于 Textual 的 TUI 适配器模块。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any, ClassVar, cast

from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static, TextArea

from domain.agent.value_objects import AgentStreamEvent, ApprovalDecision
from domain.run.exceptions import RunEventReplayExpiredError
from domain.run.value_objects import RunEvent, RunSnapshot, RunStatus
from domain.run.workflow import canonicalize_collaboration_summary

from .approval_mode import evaluate_approval_mode
from .approval_screen import ApprovalScreen
from .commands import SlashCommandRouter
from .rich_rendering import render_markdown_body, render_plain_body
from .runtime import CliRuntime
from .session import TuiSessionState

_RUN_STATUS_LABELS = {
    RunStatus.QUEUED: "排队中",
    RunStatus.RUNNING: "运行中",
    RunStatus.PAUSED: "已暂停",
    RunStatus.AWAITING_APPROVAL: "等待审批",
    RunStatus.CANCEL_REQUESTED: "取消请求中",
    RunStatus.CANCELLED: "已取消",
    RunStatus.SUCCEEDED: "已成功",
    RunStatus.FAILED: "已失败",
    RunStatus.LOST: "已丢失",
}


class TuiApp:
    """CLI 入口使用的 TUI 兼容包装器。"""

    def __init__(self, runtime: CliRuntime) -> None:
        self._runtime = runtime

    async def run(self) -> int:
        """运行交互式 Textual 应用。"""
        result = await EpsilonTextualApp(self._runtime).run_async(mouse=True)
        return int(result or 0)


class EpsilonTextualApp(App[int]):
    """epsilon CLI 的全屏聊天交互界面。"""

    CSS_PATH = "tui.css"

    BINDINGS: ClassVar = [
        ("ctrl+s", "submit", "Send"),
        ("ctrl+c", "cancel", "Cancel"),
        ("ctrl+y", "copy_last_assistant", "Copy last"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, runtime: CliRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._state = TuiSessionState()
        self._commands = SlashCommandRouter(runtime)
        self._current_task: asyncio.Task[None] | None = None
        # 保留后台 fire-and-forget 任务的强引用，避免任务被 GC 提前回收（RUF006）。
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._active_run_id: str | None = None
        self._messages: VerticalScroll | None = None
        self._status: Static | None = None
        self._composer: TextArea | None = None
        self._transcript_entries: list[str] = []
        self._last_assistant_text = ""
        self._active_assistant_text = ""

    def compose(self) -> ComposeResult:
        """组合终端 UI 组件。"""
        yield Header(show_clock=True)
        with Vertical(id="root"):
            yield VerticalScroll(id="messages")
            yield Static("", id="status")
            yield TextArea("", id="composer")
        yield Footer()

    @property
    def current_task(self) -> asyncio.Task[None] | None:
        """返回当前前台交互任务。"""
        return self._current_task

    @property
    def session_state(self) -> TuiSessionState:
        """返回当前 TUI 会话状态。"""
        return self._state

    def set_composer_text(self, text: str) -> None:
        """设置输入框文本。"""
        self._set_composer_text(text)

    @property
    def active_run_id(self) -> str | None:
        """返回当前正在观察的 Run ID。"""
        return self._active_run_id

    @property
    def clipboard_text(self) -> str:
        """返回 Textual 当前剪贴板文本。"""
        return self._clipboard

    def start_run_watch(self, run_id: str) -> None:
        """启动并登记 Run 事件观察任务。"""
        self._current_task = asyncio.create_task(self._watch_run(run_id))

    def attach_active_run_task(self, run_id: str, task: asyncio.Task[None]) -> None:
        """登记外部创建的活动 Run 任务。"""
        self._active_run_id = run_id
        self._current_task = task

    def set_last_assistant_text(self, text: str) -> None:
        """设置最近一次完整助手回复文本。"""
        self._last_assistant_text = text

    def set_active_assistant_text(self, text: str) -> None:
        """设置当前流式助手回复文本。"""
        self._active_assistant_text = text

    def record_transcript(self, title: str, body: str) -> None:
        """向可复制会话记录追加一项。"""
        self._record_transcript(title, body)

    @staticmethod
    def message_renderable(title: str, body: RenderableType, style: str) -> Group:
        """构建 TUI 消息的 Rich 可渲染对象。"""
        return EpsilonTextualApp._message_renderable(title, body, style)

    async def on_mount(self) -> None:
        """缓存界面组件并初始化展示状态。"""
        self._messages = self.query_one("#messages", VerticalScroll)
        self._status = self.query_one("#status", Static)
        self._composer = self.query_one("#composer", TextArea)
        self._set_status(
            "epsilon TUI | mouse scroll enabled | /copy last|all | "
            "Ctrl+S send | Ctrl+Y copy last | Ctrl+C cancel | Ctrl+Q quit"
        )
        self._composer.focus()

    async def action_submit(self) -> None:
        """提交当前输入框内容。"""
        if self._current_task is not None and not self._current_task.done():
            self._set_status("A request is already running")
            return

        text = self._composer_text().strip()
        if not text:
            return

        self._set_composer_text("")
        if text.startswith("/") and await self._handle_local_command(text):
            return

        await self._append_panel("You", text, "cyan")

        if text.startswith("/"):
            if text.startswith("/run watch "):
                run_id = text.removeprefix("/run watch ").strip()
                if not run_id:
                    await self._append_panel("Command", "用法: /run watch <run_id>", "green")
                    return
                self._current_task = asyncio.create_task(self._watch_run(run_id))
                return

            result = await self._commands.handle(text, self._state)
            await self._append_panel("Command", result.message, "green")
            self._refresh_status()
            if result.should_exit:
                self.exit(0)
            return

        self._current_task = asyncio.create_task(self._run_agent_turn(text))

    def action_cancel(self) -> None:
        """取消当前正在运行的交互轮次。"""
        if self._current_task is None or self._current_task.done():
            self._set_status("No active request")
            return
        if self._active_run_id is not None:
            self._spawn_background(self.request_cancel_active_run())
            self._set_status(f"Requesting run cancel: {self._active_run_id}")
            return
        self._current_task.cancel()
        self._set_status("Cancelling active request")

    async def action_quit(self) -> None:
        """退出 TUI 应用。"""
        if self._current_task is not None and not self._current_task.done():
            if self._active_run_id is None:
                self._current_task.cancel()
            else:
                self._spawn_background(self.request_cancel_active_run())
        self.exit(0)

    async def action_copy_last_assistant(self) -> None:
        """复制最近一条 assistant 回复。"""
        self._copy_text(self._last_assistant_text or self._active_assistant_text, "last assistant")

    async def action_copy_transcript(self) -> None:
        """复制当前 TUI 会话可见文本记录。"""
        self._copy_text(self._render_transcript(), "transcript")

    async def _handle_local_command(self, text: str) -> bool:
        """处理只依赖本地 TUI 状态的斜杠命令。"""
        normalized = " ".join(text.split()).lower()
        if normalized in {"/copy", "/copy last"}:
            await self.action_copy_last_assistant()
            return True
        if normalized in {"/copy all", "/copy transcript"}:
            await self.action_copy_transcript()
            return True
        return False

    def _spawn_background(self, coro: Any) -> None:
        """启动后台任务并保留强引用，任务完成后自动从集合移除（RUF006）。"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def request_cancel_active_run(self) -> RunSnapshot | None:
        """请求取消当前 Run，同时保留状态监听任务。"""
        if self._active_run_id is None:
            return None
        snapshot = await self._runtime.cancel_run(self._active_run_id)
        await self._append_panel(
            "Run cancel",
            render_run_snapshot(snapshot),
            "yellow",
            classes="message run",
        )
        return snapshot

    async def _run_agent_turn(self, text: str) -> None:
        assistant = Static("", classes="message")
        assistant_content: list[str] = []
        await self._append_widget(assistant)
        self._set_status("Agent is working")

        try:
            event_source = self._runtime.stream_main_agent_events(text, self._state)
            await self._drive_events(event_source, assistant, assistant_content)
        except asyncio.CancelledError:
            await self._append_panel("Cancelled", "已中止当前请求", "yellow")
            return
        except Exception as exc:
            await self._append_panel("Error", str(exc), "red", classes="message error")
        finally:
            assistant_text = "".join(assistant_content).strip()
            if assistant_text:
                self._last_assistant_text = assistant_text
                self._record_transcript("Assistant", assistant_text)
            self._active_assistant_text = ""
            if self._current_task is asyncio.current_task():
                self._current_task = None
            self._refresh_status()
            if self._composer is not None:
                self._composer.focus()

    async def _drive_events(
        self,
        event_source: AsyncIterator[AgentStreamEvent],
        assistant: Static,
        assistant_content: list[str],
    ) -> None:
        """驱动单条事件流并在审批中断处闭环切换到续播流。

        以「事件驱动的续跑」串接首轮 ``stream_main_agent_events`` 与后续
        ``resume_main_agent_events``：正常事件交给 ``_handle_event`` 渲染；
        遇到 ``approval_required`` 事件时不在渲染函数内处理，而是在此统一
        解析待审批动作、按审批模式自动放行或打开 ``ApprovalScreen`` 收集决策，
        随后把事件源切换为续播流继续渲染，实现再次中断的闭环（需求 4.1/4.2）。

        Args:
            event_source: 当前待驱动的结构化事件异步迭代器。
            assistant: 承载 assistant 累加文本的展示组件。
            assistant_content: assistant 已累加的文本分片列表。
        """
        while True:
            next_source: AsyncIterator[AgentStreamEvent] | None = None
            async for event in event_source:
                if event.kind == "approval_required":
                    next_source = await self._resolve_approval(event)
                    break
                await self._handle_event(event, assistant, assistant_content)
            if next_source is None:
                break
            event_source = next_source

    async def _resolve_approval(
        self,
        event: AgentStreamEvent,
    ) -> AsyncIterator[AgentStreamEvent] | None:
        """处理一次审批中断并在需要续播时返回续播事件源。

        读取审批批次的完整待审批动作：批次已过期/清理（动作为空）时回退到
        用事件 metadata 的 ``action_summaries`` 渲染纯文本提示（无 arguments，
        edit 不可用，不崩溃），返回 ``None`` 结束本轮续播；否则按审批模式判定
        是否自动放行，必要时打开 ``ApprovalScreen`` 收集决策：用户取消（Esc）
        时返回 ``None``（不提交决策、不消费审批状态，保留可再次恢复），否则以
        决策序列构造续播事件源返回（需求 2.1/4.1/6.5）。

        Args:
            event: 收到的 ``kind="approval_required"`` 事件。

        Returns:
            需要续播时返回 ``resume_main_agent_events`` 事件源；无需续播
            （批次为空或用户取消）时返回 ``None``。
        """
        session_id = str(event.metadata.get("session_id") or "")
        approval_id = str(event.metadata.get("approval_id") or "")
        actions = await self._runtime.load_pending_actions(session_id, approval_id)
        if not actions:
            await self._render_approval_summary(event)
            return None

        decisions = evaluate_approval_mode(
            self._state.approval_mode, actions, self._runtime.policy_for
        )
        if decisions is None:
            risk_labels = {
                action.tool_name: self._runtime.policy_for(action.tool_name).risk_label
                for action in actions
            }
            decisions = await self._await_approval_screen(ApprovalScreen(actions, risk_labels))
        if decisions is None:
            await self._append_panel("Approval cancelled", "已取消本次审批", "yellow")
            return None

        return self._runtime.resume_main_agent_events(
            session_id,
            approval_id,
            decisions,
            model=self._state.model,
        )

    async def _await_approval_screen(
        self,
        screen: ApprovalScreen,
    ) -> list[ApprovalDecision] | None:
        """打开审批面板并等待其决策结果。

        以回调 + Future 的方式挂载 ``ApprovalScreen`` 并阻塞等待其
        ``dismiss`` 值，等价于 ``push_screen_wait`` 的语义，但不依赖 Textual
        worker 上下文（本轮恢复运行在 ``_run_agent_turn`` 的 ``asyncio.Task``
        内，以复用既有 ``self._current_task.cancel()`` 取消路径，见需求 4.3）。
        面板全部动作完成时返回 ``list[ApprovalDecision]``；用户取消（Esc）时
        返回 ``None``。

        Args:
            screen: 待挂载并等待结果的审批面板实例。

        Returns:
            面板产出的决策序列，或用户取消时的 ``None``。
        """
        future: asyncio.Future[list[ApprovalDecision] | None] = (
            asyncio.get_running_loop().create_future()
        )

        def _on_dismiss(result: list[ApprovalDecision] | None) -> None:
            if not future.done():
                future.set_result(result)

        await self.push_screen(screen, _on_dismiss)
        try:
            return await future
        except asyncio.CancelledError:
            # 恢复流被取消（用户 Ctrl+C）时，面板可能仍挂载且尚未决策；
            # 显式将其移出屏幕栈，避免遗留悬空面板（不改变取消语义）。
            if self.screen is screen:
                self.pop_screen()
            raise

    async def _render_approval_summary(self, event: AgentStreamEvent) -> None:
        """在待审批动作缺失时回退渲染事件 metadata 的纯文本审批摘要。

        当审批批次已过期或被清理，无法读取完整动作（含 arguments）时，用事件
        metadata 中的 ``action_summaries`` 展示只读摘要提示（无 arguments、
        edit 不可用），保证流程不崩溃（见错误处理表·空 tuple 分支）。

        Args:
            event: 收到的 ``kind="approval_required"`` 事件。
        """
        lines = ["当前请求等待人工审批，但审批批次已失效，请重新发起。"]
        session_id = event.metadata.get("session_id")
        approval_id = event.metadata.get("approval_id")
        if session_id:
            lines.append(f"session_id={session_id}")
        if approval_id:
            lines.append(f"approval_id={approval_id}")
        for summary in event.metadata.get("action_summaries", []):
            if isinstance(summary, dict):
                summary = cast(dict[str, Any], summary)
                lines.append(
                    self._compact(
                        f"{summary.get('tool_name', 'unknown')} "
                        f"allowed={summary.get('allowed_decisions', [])}"
                    )
                )
        await self._append_panel(
            "Approval required",
            "\n".join(lines),
            "yellow",
            classes="message tool",
        )

    async def _watch_run(self, run_id: str) -> None:
        """渲染 Run 快照并跟随其事件流。"""
        self._active_run_id = run_id
        cursor: int | None = None
        self._set_status(f"Watching run {run_id}")
        try:
            snapshot = await self._runtime.get_run(run_id)
            cursor = snapshot.latest_event_cursor
            await self._append_panel(
                "Run",
                render_run_snapshot(snapshot),
                "magenta",
                classes="message run",
            )
            async for event in self._runtime.watch_run_events(run_id, cursor):
                cursor = event.cursor
                await self._append_panel(
                    "Run event",
                    render_run_event(event),
                    "magenta",
                    classes="message run",
                )
        except RunEventReplayExpiredError:
            snapshot = await self._runtime.get_run(run_id)
            await self._append_panel(
                "Run watch",
                "事件历史已过期，已回退到最新快照。\n" + render_run_snapshot(snapshot),
                "yellow",
                classes="message run",
            )
        except asyncio.CancelledError:
            await self._append_panel("Cancelled", "已停止本地 Run 订阅", "yellow")
            return
        except Exception as exc:
            await self._append_panel("Run error", str(exc), "red", classes="message error")
        finally:
            self._active_run_id = None
            if self._current_task is asyncio.current_task():
                self._current_task = None
            self._refresh_status()
            if self._composer is not None:
                self._composer.focus()

    async def _handle_event(
        self,
        event: AgentStreamEvent,
        assistant: Static,
        assistant_content: list[str],
    ) -> None:
        if event.kind == "assistant_delta":
            assistant_content.append(event.content)
            self._active_assistant_text = "".join(assistant_content)
            self._last_assistant_text = self._active_assistant_text.strip()
            assistant.update(
                self._message_renderable(
                    "Assistant",
                    render_markdown_body(self._active_assistant_text),
                    "blue",
                )
            )
            self._scroll_end()
            return

        if event.kind == "assistant_done":
            usage = self._format_usage(event.usage)
            self._set_status(usage or "Ready")
            return

        if event.kind == "tool_start":
            await self._append_tool_event("Tool", event, "yellow")
            return

        if event.kind == "tool_result":
            await self._append_tool_event("Tool result", event, "green")
            return

        if event.kind == "tool_error":
            await self._append_tool_event("Tool error", event, "red")
            return

        if event.kind == "approval_required":
            # approval_required 由 _drive_events/_resolve_approval 统一拦截处理
            # （打开 ApprovalScreen 并切换续播事件源），不在纯渲染函数内处理。
            await self._render_approval_summary(event)
            return

        if event.kind == "status":
            self._set_status(event.content)
            return

        if event.kind == "error":
            await self._append_panel("Error", event.content, "red", classes="message error")

    async def _append_tool_event(
        self,
        title: str,
        event: AgentStreamEvent,
        border_style: str,
    ) -> None:
        tool = event.tool_name or "unknown"
        lines = [f"{tool}"]
        if event.arguments:
            lines.append(self._compact(event.arguments))
        if event.content:
            lines.append(self._compact(event.content))
        await self._append_panel(title, "\n".join(lines), border_style, classes="message tool")

    async def _append_panel(
        self,
        title: str,
        body: str,
        border_style: str,
        *,
        classes: str = "message",
        rich: bool = False,
        record: bool = True,
    ) -> None:
        if record:
            self._record_transcript(title, body)
        renderable = render_markdown_body(body) if rich else render_plain_body(body)
        await self._append_widget(
            Static(
                self._message_renderable(title, renderable, border_style),
                classes=classes,
            )
        )

    async def _append_widget(self, widget: Static) -> None:
        if self._messages is None:
            return
        await self._messages.mount(widget)
        self._scroll_end()

    def _scroll_end(self) -> None:
        if self._messages is not None:
            self._messages.scroll_end(animate=False)

    def _composer_text(self) -> str:
        if self._composer is None:
            return ""
        return self._composer.text

    def _set_composer_text(self, text: str) -> None:
        if self._composer is not None:
            self._composer.text = text

    def _set_status(self, text: str) -> None:
        if self._status is not None:
            self._status.update(text)

    @staticmethod
    def _message_renderable(title: str, body: RenderableType, style: str) -> Group:
        return Group(Text(title, style=f"bold {style}"), body)

    def _copy_text(self, text: str, label: str) -> None:
        normalized = text.strip()
        if not normalized:
            self._set_status(f"No {label} content to copy")
            return
        self.copy_to_clipboard(normalized)
        self._set_status(f"Copied {label} ({len(normalized)} chars)")

    def _record_transcript(self, title: str, body: str) -> None:
        normalized = body.strip()
        if normalized:
            self._transcript_entries.append(f"{title}:\n{normalized}")

    def _render_transcript(self) -> str:
        entries = list(self._transcript_entries)
        active_assistant = self._active_assistant_text.strip()
        if active_assistant:
            active_entry = f"Assistant:\n{active_assistant}"
            if not entries or entries[-1] != active_entry:
                entries.append(active_entry)
        return "\n\n".join(entries)

    def _refresh_status(self) -> None:
        model = self._state.model or self._runtime.default_model()
        short_session = self._state.session_id.removeprefix("tui-")[:8]
        self._set_status(f"Ready | model={model} | session={short_session}")

    @staticmethod
    def _compact(text: str, *, limit: int = 1200) -> str:
        compacted = text.strip()
        if len(compacted) <= limit:
            return compacted
        return compacted[: limit - 1] + "..."

    @staticmethod
    def _format_usage(usage: dict[str, int] | None) -> str:
        if not usage:
            return ""
        total = usage.get("total_tokens")
        if total is not None:
            return f"Done | total_tokens={total}"
        return "Done | " + ", ".join(f"{key}={value}" for key, value in sorted(usage.items()))


def render_run_snapshot(snapshot: RunSnapshot) -> str:
    """为 TUI Run 面板渲染 Run 快照。"""
    lines = [
        f"run_id: {snapshot.run_id}",
        f"status: {snapshot.status.value} ({_RUN_STATUS_LABELS[snapshot.status]})",
        f"can_continue: {str(snapshot.can_continue).lower()}",
        f"latest_cursor: {snapshot.latest_event_cursor}",
    ]
    lines.extend(_render_workflow_metadata(snapshot))
    lines.extend(_render_segment_metadata(snapshot.segment_metadata))
    lines.extend(_render_recovery_metadata(snapshot))
    if snapshot.approval_id:
        lines.append(f"approval_id: {snapshot.approval_id}")
    if snapshot.error:
        lines.append(f"error: {_run_error_summary(snapshot.error)}")
    if snapshot.terminal_reason:
        lines.append(f"terminal_reason: {snapshot.terminal_reason}")
    if snapshot.result:
        summary = _run_error_summary(snapshot.result)
        if summary:
            lines.append(f"result: {summary}")
    return "\n".join(lines)


def render_run_event(event: RunEvent) -> str:
    """为 TUI 事件日志渲染单条 Run 事件。"""
    lines = [
        f"cursor: {event.cursor}",
        f"type: {event.event_type.value}",
    ]
    if event.payload:
        lines.append(f"payload: {_run_error_summary(event.payload)}")
    return "\n".join(lines)


def render_run_event_log(events: Iterable[RunEvent]) -> str:
    """渲染紧凑格式的 Run 事件日志。"""
    rendered = [render_run_event(event) for event in events]
    return "\n\n".join(rendered)


def _render_segment_metadata(metadata: dict[str, object] | None) -> list[str]:
    if not metadata:
        return []
    lines = ["segment_metadata:"]
    for key in sorted(metadata):
        lines.append(f"  {key}: {metadata[key]}")
    return lines


def _render_recovery_metadata(snapshot: RunSnapshot) -> list[str]:
    lines: list[str] = []
    if snapshot.latest_checkpoint_id is not None:
        lines.append(f"latest_checkpoint_id: {snapshot.latest_checkpoint_id}")
    if snapshot.recoverable:
        lines.append("recoverable: true")
    if snapshot.recovery_attempt_count:
        lines.append(f"recovery_attempt_count: {snapshot.recovery_attempt_count}")
    if snapshot.last_recovery_error:
        lines.append(f"last_recovery_error: {_run_error_summary(snapshot.last_recovery_error)}")
    if snapshot.task_classification:
        lines.append(f"task_classification: {snapshot.task_classification}")
    if snapshot.guardrail_summary:
        lines.append(f"guardrail_summary: {snapshot.guardrail_summary}")
    return lines


def _render_workflow_metadata(snapshot: RunSnapshot) -> list[str]:
    """渲染 Run 快照中已存在的 workflow 字段。"""

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
        lines.append("workflow_phase_history:")
        lines.extend(f"  {item}" for item in phase_history)
    collaboration = _collaboration_lines(snapshot.collaboration_summary)
    if collaboration:
        lines.append("latest_collaboration_summary:")
        lines.extend(f"  {item}" for item in collaboration)
    return lines


def _phase_history_summary(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    value = cast(list[Any], value)
    phases: list[str] = []
    for item in value[-5:]:
        if not isinstance(item, dict):
            continue
        item = cast(dict[str, Any], item)
        phase = _string_value(item.get("phase"))
        status = _string_value(item.get("status"))
        if phase and status:
            phases.append(f"{phase}: {status}")
        elif phase:
            phases.append(phase)
    return phases


def _collaboration_lines(value: dict[str, Any] | None) -> list[str]:
    """渲染规范协作摘要行，并兼容历史 recent_steps 快照。"""

    canonical = canonicalize_collaboration_summary(value)
    if not canonical:
        return []
    latest_steps = canonical.get("latest_steps")
    if isinstance(latest_steps, list) and latest_steps:
        latest_steps = cast(list[Any], latest_steps)
        lines: list[str] = []
        for item in latest_steps[-5:]:
            if not isinstance(item, dict):
                continue
            item = cast(dict[str, Any], item)
            action = _string_value(item.get("action"))
            target = _string_value(item.get("target_agent"))
            result = _string_value(item.get("result_summary") or item.get("task_summary"))
            parts = [part for part in (action, target, result) if part]
            if parts:
                lines.append(" / ".join(parts))
        if lines:
            return lines
    counters: list[str] = []
    for key in ("delegation_count", "handoff_count", "max_depth_seen", "limit_hit_reason"):
        if key in canonical:
            counters.append(f"{key}: {canonical[key]}")
    return counters


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _run_error_summary(payload: dict[str, object]) -> str:
    for key in ("summary", "message", "error", "reason", "content"):
        value = payload.get(key)
        if value:
            return str(value)
    return str(payload)


# Backward-compatible alias for existing extensions; new code should use the public name.
_EpsilonTextualApp = EpsilonTextualApp
