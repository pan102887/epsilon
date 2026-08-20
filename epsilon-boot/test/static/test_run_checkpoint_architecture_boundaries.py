"""Static architecture boundaries for phase four Run checkpointing."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
SPEC_ROOT = REPO_ROOT / "docs/spec/long-task-continuation-phase4"


def test_domain_run_does_not_import_infrastructure_frameworks() -> None:
    forbidden_prefixes = (
        "redis",
        "fastapi",
        "pydantic",
        "infrastructure",
        "application",
        "pathlib",
    )
    for path in (ROOT / "src/domain/run").glob("*.py"):
        imports = _imports(path)
        assert not any(
            item == prefix or item.startswith(f"{prefix}.")
            for item in imports
            for prefix in forbidden_prefixes
        ), f"{path} imports forbidden dependency: {imports}"


def test_run_routers_do_not_import_checkpoint_infrastructure() -> None:
    for relative in (
        "src/application/api/routers/runs.py",
        "src/application/routers/runs.py",
    ):
        imports = _imports(ROOT / relative)
        assert "application.run.run_checkpoint_recovery_service" not in imports
        assert not any(
            item.startswith("infrastructure.run.") and "checkpoint" in item for item in imports
        )


def test_tui_does_not_call_fastapi_or_recovery_service_for_observation() -> None:
    for relative in (
        "src/application/cli/runtime.py",
        "src/application/cli/tui.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "/api/runs" not in source
        assert "RunRecoveryService" not in source
        assert "sweep_expired_leases" not in source
        assert "enqueue_recovery" not in source


def test_phase_four_does_not_introduce_external_workflow_runtime() -> None:
    forbidden = ("celery", "temporal", "langgraph", "dapr")
    for path in (ROOT / "src").rglob("*.py"):
        imports = _imports(path)
        assert not any(
            item == name or item.startswith(f"{name}.") for item in imports for name in forbidden
        ), f"{path} imports external workflow runtime: {imports}"


def test_spec_declares_non_exactly_once_and_read_only_observation() -> None:
    combined = "\n".join(
        (SPEC_ROOT / name).read_text(encoding="utf-8").lower()
        for name in ("requirement.md", "design.md", "tasks.md")
    )

    assert "non-exactly-once" in combined
    assert "observation reattach" in combined
    assert "只读" in combined or "read-only" in combined


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports
