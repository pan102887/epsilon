"""阶段三可选前端 Run View 静态契约测试。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2].parent
CLIENT_ROOT = REPO_ROOT / "epsilon-client" / "src"
CHAT_API = CLIENT_ROOT / "lib" / "chat-api.ts"
RUN_VIEW = CLIENT_ROOT / "components" / "run" / "run-view.tsx"
RUN_EVENT_LIST = CLIENT_ROOT / "components" / "run" / "run-event-list.tsx"
USE_RUN = CLIENT_ROOT / "hooks" / "use-run.ts"
CHAT_PANEL = CLIENT_ROOT / "components" / "chat" / "chat-panel.tsx"
TASK_WORKSPACE = CLIENT_ROOT / "components" / "task" / "task-workspace.tsx"
PAGE = CLIENT_ROOT / "app" / "page.tsx"

RUN_STATUSES = {
    "queued",
    "running",
    "paused",
    "awaiting_approval",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
    "lost",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_chat_api_exposes_run_types_and_functions() -> None:
    """chat-api.ts 暴露 Run 类型与 client 函数。"""

    text = _read(CHAT_API)

    for token in (
        "export type RunStatus",
        "export type RunKind",
        "export interface RunSnapshot",
        "export interface RunEvent",
        "export interface RunCreateRequest",
        "export interface RunEventsResponse",
        "export async function createRun",
        "export async function fetchRun",
        "export async function fetchRunEvents",
        "export function streamRunEvents",
        "export async function cancelRun",
        "export async function continueRun",
    ):
        assert token in text

    for token in (
        "task_classification: string | null",
        "guardrail_summary: Record<string, unknown> | null",
        '"guardrail_blocked"',
    ):
        assert token in text


def test_run_view_handles_all_run_statuses_and_replay_fallback() -> None:
    """Run View 覆盖全部状态并包含 replay_expired fallback。"""

    combined = "\n".join(
        [
            _read(RUN_VIEW),
            _read(RUN_EVENT_LIST),
            _read(USE_RUN),
            _read(CHAT_API),
        ]
    )

    for status in RUN_STATUSES:
        assert status in combined

    assert "replay_expired" in combined
    assert "fetchRunEvents" in combined
    assert "polling" in combined


def test_run_view_displays_guardrail_fields_without_policy_logic() -> None:
    text = _read(RUN_VIEW)

    for token in (
        "Task_Class",
        "snapshot.task_classification",
        "Guardrail",
        "Guardrail summary",
        "snapshot.guardrail_summary",
        "guardrailText(snapshot)",
        "summary.action",
        "summary.reason",
        "summary.runtime_stats",
    ):
        assert token in text
    assert "ToolRiskLevel" not in text
    assert "GuardrailDecision" not in text


def test_run_event_list_labels_guardrail_events_with_safe_summary() -> None:
    text = _read(RUN_EVENT_LIST)

    for token in (
        "guardrail_evaluated",
        "guardrail_blocked",
        "Guardrail evaluated",
        "Guardrail blocked",
        "EVENT_SAFE_FIELDS",
        "runtimeStatsSummary",
    ):
        assert token in text
    safe_fields_block = text.split("const GENERIC_BLOCKED_FIELD_FRAGMENTS", maxsplit=1)[0]
    for forbidden in (
        '"arguments"',
        '"content"',
        '"input"',
        '"message"',
        '"prompt"',
        '"api_key"',
    ):
        assert forbidden not in safe_fields_block


def test_existing_sync_chat_and_task_api_functions_remain_available() -> None:
    """现有同步/流式 chat/task API 函数仍存在。"""

    text = _read(CHAT_API)

    for token in (
        "export function streamChat",
        "export function streamContinueChat",
        "export async function clearSession",
        "export async function executeTask",
        "export async function continueTask",
    ):
        assert token in text


def test_frontend_adds_background_run_entry_without_replacing_sync_flow() -> None:
    """页面接入 Run View，同时保留同步 Chat/Task 默认入口。"""

    chat_panel = _read(CHAT_PANEL)
    task_workspace = _read(TASK_WORKSPACE)
    page = _read(PAGE)

    assert "onRunCreated" in chat_panel
    assert "createRun" in chat_panel
    assert "sendMessage" in chat_panel
    assert "onRunCreated" in task_workspace
    assert "createRun" in task_workspace
    assert "executeTask" in task_workspace
    assert "<RunView" in page
