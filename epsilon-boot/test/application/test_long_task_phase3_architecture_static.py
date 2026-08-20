"""阶段三后台 Run runtime 架构边界静态测试。"""

from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path

BOOT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BOOT_ROOT.parent
SRC_ROOT = BOOT_ROOT / "src"

DOMAIN_RUN_ROOT = SRC_ROOT / "domain" / "run"
TUI_RUNTIME_PATH = SRC_ROOT / "application" / "cli" / "runtime.py"
PYPROJECT_PATH = BOOT_ROOT / "pyproject.toml"

FORBIDDEN_DOMAIN_IMPORT_PREFIXES = (
    "application",
    "infrastructure",
    "fastapi",
    "redis",
    "aioredis",
)
FORBIDDEN_TUI_HTTP_IMPORT_PREFIXES = (
    "aiohttp",
    "http.client",
    "httpx",
    "requests",
    "urllib",
    "urllib3",
)
FORBIDDEN_WORKFLOW_DEPENDENCIES = {
    "celery",
    "dapr",
    "dapr-ext-workflow",
    "langgraph",
    "temporal",
    "temporalio",
}
FORBIDDEN_WORKFLOW_IMPORT_PREFIXES = (
    "celery",
    "dapr",
    "langgraph",
    "temporal",
    "temporalio",
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _string_literals(path: Path) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def _has_prefix(module: str, prefixes: Iterable[str]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _dependency_name(requirement: str) -> str:
    normalized = requirement.strip().lower()
    normalized = re.split(r"[<>=!~;\[]", normalized, maxsplit=1)[0]
    return normalized.replace("_", "-")


def _dependency_names(pyproject_text: str) -> set[str]:
    data = tomllib.loads(pyproject_text)
    names = {_dependency_name(item) for item in data["project"].get("dependencies", [])}
    for group_items in data.get("dependency-groups", {}).values():
        names.update(_dependency_name(item) for item in group_items)
    return names


def _git_head_pyproject_text() -> str | None:
    result = subprocess.run(
        ["git", "show", "HEAD:epsilon-boot/pyproject.toml"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def test_domain_run_does_not_depend_on_outer_layers_or_runtime_clients() -> None:
    """领域层 Run 模块不得反向依赖应用层、基础设施、FastAPI 或 Redis 客户端。"""
    violations: dict[str, list[str]] = {}

    for path in _python_files(DOMAIN_RUN_ROOT):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if _has_prefix(module, FORBIDDEN_DOMAIN_IMPORT_PREFIXES)
        )
        if forbidden:
            violations[str(path.relative_to(BOOT_ROOT))] = forbidden

    assert violations == {}


def test_optional_fastapi_run_adapter_does_not_import_run_infrastructure() -> None:
    """可选 Run HTTP router 只能调用应用服务，不得直接导入 infrastructure.run。"""
    optional_router_paths = [
        SRC_ROOT / "application" / "api" / "routers" / "runs.py",
        SRC_ROOT / "application" / "routers" / "runs.py",
    ]
    violations: dict[str, list[str]] = {}

    for path in optional_router_paths:
        if not path.exists():
            continue
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module == "infrastructure.run" or module.startswith("infrastructure.run.")
        )
        if forbidden:
            violations[str(path.relative_to(BOOT_ROOT))] = forbidden

    assert violations == {}


def test_tui_runtime_uses_shared_application_service_not_http_runs_api() -> None:
    """TUI runtime 通过共享应用服务接入 Run，不得调用 HTTP client 或 /api/runs。"""
    imports = _imported_modules(TUI_RUNTIME_PATH)
    forbidden_imports = sorted(
        module for module in imports if _has_prefix(module, FORBIDDEN_TUI_HTTP_IMPORT_PREFIXES)
    )
    forbidden_literals = sorted(
        value for value in _string_literals(TUI_RUNTIME_PATH) if "/api/runs" in value
    )

    assert forbidden_imports == []
    assert forbidden_literals == []
    assert "application.run.run_application_service" in imports


def test_pyproject_has_no_phase3_added_workflow_runtime_dependencies() -> None:
    """阶段三不得新增 Celery、Temporal、LangGraph、Dapr Workflow 等运行时依赖。"""
    current_names = _dependency_names(PYPROJECT_PATH.read_text(encoding="utf-8"))
    head_text = _git_head_pyproject_text()

    if head_text is None:
        prohibited_current = current_names & (FORBIDDEN_WORKFLOW_DEPENDENCIES - {"langgraph"})
        assert prohibited_current == set()
        return

    baseline_names = _dependency_names(head_text)
    added_prohibited = (current_names - baseline_names) & FORBIDDEN_WORKFLOW_DEPENDENCIES

    assert added_prohibited == set()


def test_phase3_run_runtime_does_not_import_workflow_runtime_modules() -> None:
    """Run runtime 实现不得借用外部 durable workflow runtime。"""
    runtime_roots = [
        SRC_ROOT / "domain" / "run",
        SRC_ROOT / "application" / "run",
        SRC_ROOT / "infrastructure" / "run",
        SRC_ROOT / "application" / "cli" / "runtime.py",
    ]
    paths: list[Path] = []
    for root in runtime_roots:
        paths.extend(_python_files(root) if root.is_dir() else [root])

    violations: dict[str, list[str]] = {}
    for path in paths:
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if _has_prefix(module, FORBIDDEN_WORKFLOW_IMPORT_PREFIXES)
        )
        if forbidden:
            violations[str(path.relative_to(BOOT_ROOT))] = forbidden

    assert violations == {}
