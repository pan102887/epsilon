"""CLI/TUI Run 展示的协作摘要兼容测试。

本模块验证命令行展示层只消费 RunSnapshot 上的 canonical snapshot 字段，
并仅在读取历史快照时把 recent_steps 作为 latest_steps fallback。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from application.cli.commands import format_run_snapshot
from application.cli.tui import render_run_snapshot
from domain.run.value_objects import RunKind, RunPayload, RunSnapshot, RunStatus

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _snapshot(*, collaboration_summary: dict[str, object]) -> RunSnapshot:
    """构造用于 CLI/TUI 渲染断言的 Run 快照。"""

    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="cli-schema-session",
        task={"goal": "render collaboration summary"},
        model="test-model",
    )
    return RunSnapshot(
        run_id="run-cli-1",
        kind=RunKind.TASK,
        status=RunStatus.PAUSED,
        payload=payload,
        client_request_id="client-cli-1",
        payload_hash=payload.stable_hash(),
        result={"content": "phase completed"},
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=7,
        can_continue=True,
        terminal_reason="workflow_phase_completed",
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_name="code_change",
        workflow_run_state={
            "workflow_name": "code_change",
            "current_phase": "review",
        },
        collaboration_summary=collaboration_summary,
    )


def test_cli_command_rendering_reads_latest_steps_not_recent_steps() -> None:
    """Slash command 输出应以 latest_steps 为唯一规范协作步骤来源。"""

    rendered = format_run_snapshot(
        _snapshot(
            collaboration_summary={
                "latest_steps": [
                    {
                        "action": "handoff",
                        "target_agent": "reviewer",
                        "result_summary": "canonical step",
                    }
                ],
                "recent_steps": [
                    {
                        "action": "delegation",
                        "target_agent": "legacy",
                        "result_summary": "legacy step must be ignored",
                    }
                ],
            }
        )
    )

    assert "latest_collaboration_summary: handoff / reviewer / canonical step" in rendered
    assert "legacy step must be ignored" not in rendered
    assert "recent_collaboration_summary" not in rendered


def test_tui_rendering_reads_latest_steps_not_recent_steps() -> None:
    """TUI Run 面板应以 latest_steps 为唯一规范协作步骤来源。"""

    rendered = render_run_snapshot(
        _snapshot(
            collaboration_summary={
                "latest_steps": [
                    {
                        "action": "handoff",
                        "target_agent": "reviewer",
                        "result_summary": "canonical step",
                    }
                ],
                "recent_steps": [
                    {
                        "action": "delegation",
                        "target_agent": "legacy",
                        "result_summary": "legacy step must be ignored",
                    }
                ],
            }
        )
    )

    assert "latest_collaboration_summary:" in rendered
    assert "handoff / reviewer / canonical step" in rendered
    assert "legacy step must be ignored" not in rendered
    assert "recent_collaboration_summary" not in rendered


def test_cli_and_tui_rendering_fallback_to_historical_recent_steps() -> None:
    """历史快照只有 recent_steps 时，CLI 与 TUI 读取时应兼容展示。"""

    legacy_snapshot = _snapshot(
        collaboration_summary={
            "recent_steps": [
                {
                    "action": "delegation",
                    "target_agent": "legacy-reviewer",
                    "result_summary": "fallback step",
                }
            ],
            "delegation_count": 1,
        }
    )

    command_rendered = format_run_snapshot(legacy_snapshot)
    tui_rendered = render_run_snapshot(legacy_snapshot)

    assert "delegation / legacy-reviewer / fallback step" in command_rendered
    assert "delegation / legacy-reviewer / fallback step" in tui_rendered
    assert "recent_collaboration_summary" not in command_rendered
    assert "recent_collaboration_summary" not in tui_rendered


def test_cli_and_tui_rendering_use_counter_fallback_from_canonical_summary() -> None:
    """无协作步骤时，展示层应从 canonical summary 读取计数字段。"""

    snapshot = _snapshot(
        collaboration_summary={
            "latest_steps": [],
            "delegation_count": 2,
            "handoff_count": 1,
            "max_depth_seen": 3,
        }
    )

    command_rendered = format_run_snapshot(snapshot)
    tui_rendered = render_run_snapshot(snapshot)

    assert "delegation_count=2" in command_rendered
    assert "handoff_count=1" in command_rendered
    assert "max_depth_seen=3" in command_rendered
    assert "delegation_count: 2" in tui_rendered
    assert "handoff_count: 1" in tui_rendered
    assert "max_depth_seen: 3" in tui_rendered


def test_guardrail_and_workflow_snapshot_fields_remain_rendered() -> None:
    """CLI/TUI 应继续展示 RunSnapshot 中已有的 guardrail 与 workflow 字段。"""

    snapshot = replace(
        _snapshot(collaboration_summary={"latest_steps": []}),
        guardrail_summary={
            "action": "observe",
            "reason": "tool_risk_gate_required",
            "runtime_stats": {"total_tokens": 256},
        },
        workflow_run_state={"workflow_name": "code_change", "current_phase": "review"},
    )

    command_rendered = format_run_snapshot(snapshot)
    tui_rendered = render_run_snapshot(snapshot)

    assert "guardrail_summary:" in command_rendered
    assert "tool_risk_gate_required" in command_rendered
    assert "workflow_phase: review" in command_rendered
    assert "guardrail_summary:" in tui_rendered
    assert "tool_risk_gate_required" in tui_rendered
    assert "workflow_phase: review" in tui_rendered
