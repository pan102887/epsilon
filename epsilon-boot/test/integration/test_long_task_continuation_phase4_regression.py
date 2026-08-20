"""Phase four regression contracts for earlier long-task behavior."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent


def test_checkpoint_disabled_worker_manager_keeps_stage_three_lost_sweep() -> None:
    """Worker manager must keep the phase-three lost-sweep path when checkpoint is off."""

    source = (ROOT / "src/infrastructure/run/run_worker_manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert "checkpoint_enabled" in names
    assert "checkpoint_auto_recovery_enabled" in names
    assert "mark_lost_expired_leases" in names
    assert "_run_stage_three_lost_sweep" in source


def test_sync_chat_task_paths_do_not_import_checkpoint_context() -> None:
    """Synchronous Chat/Task adapters must not depend on checkpoint ContextVar."""

    for relative in (
        "src/infrastructure/chat/chat_service_adapter.py",
        "src/infrastructure/task/task_agent_adapter.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "domain.run.checkpoint_context" not in imports


def test_web_run_observation_uses_snapshot_events_not_recovery_endpoint() -> None:
    """Web Run observation must remain read-only over snapshot/events APIs."""

    source = (REPO_ROOT / "epsilon-client/src/hooks/use-run.ts").read_text(encoding="utf-8")

    assert "fetchRun(" in source
    assert "fetchRunEvents(" in source
    assert "streamRunEvents(" in source
    assert "recovery" not in source.lower()
    assert "enqueue" not in source.lower()
