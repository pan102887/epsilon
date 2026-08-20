"""阶段六前端 workflow Run View 静态契约测试。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2].parent
CLIENT_ROOT = REPO_ROOT / "epsilon-client" / "src"
CHAT_API = CLIENT_ROOT / "lib" / "chat-api.ts"
RUN_VIEW = CLIENT_ROOT / "components" / "run" / "run-view.tsx"
RUN_EVENT_LIST = CLIENT_ROOT / "components" / "run" / "run-event-list.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_chat_api_exposes_workflow_run_contract_fields() -> None:
    text = _read(CHAT_API)

    for token in (
        "export interface WorkflowRunState",
        "export interface CollaborationSummary",
        "workflow_name: string | null",
        "workflow_run_state: WorkflowRunState | null",
        "collaboration_summary: CollaborationSummary | null",
        "workflow_name?: string",
        "handoff_state?: Record<string, unknown> | null",
        "latest_steps?: Array<Record<string, unknown>>",
    ):
        assert token in text


def test_run_view_displays_workflow_phase_and_collaboration_summary() -> None:
    text = _read(RUN_VIEW)

    for token in (
        "Workflow",
        "Workflow_Phase",
        "workflowName(snapshot)",
        "workflowPhase(snapshot)",
        "Workflow state",
        "workflowStateText(snapshot)",
        "workflowHandoffText(state)",
        "active_role",
        "handoff_state",
        "Latest collaboration",
        "collaborationText(snapshot)",
        "snapshot.workflow_run_state",
        "snapshot.collaboration_summary",
        "summary.latest_steps",
    ):
        assert token in text


def test_run_event_list_labels_workflow_and_collaboration_events() -> None:
    text = _read(RUN_EVENT_LIST)

    for token in (
        "workflow_selected",
        "workflow_selection_skipped",
        "workflow_phase_started",
        "workflow_phase_completed",
        "workflow_phase_failed",
        "workflow_handoff_recorded",
        "Workflow handoff",
        "role_capability_rejected",
        "child_run_linked",
        "child_run_waiting",
        "child_run_reconciled",
        "collaboration_step_recorded",
        "collaboration_limit_hit",
        "eventSummary(event)",
        "workflowStateSummary",
    ):
        assert token in text


def test_frontend_does_not_implement_workflow_runtime_logic() -> None:
    combined = "\n".join([_read(CHAT_API), _read(RUN_VIEW), _read(RUN_EVENT_LIST)])

    forbidden = (
        "StaticWorkflowSelector",
        "WorkflowRunOrchestrator",
        "selectWorkflow",
        "advancePhase",
        "max_recursion_depth",
        "max_parallel_delegations",
        "max_handoff_count",
        "collaborationLimit",
    )
    for token in forbidden:
        assert token not in combined
