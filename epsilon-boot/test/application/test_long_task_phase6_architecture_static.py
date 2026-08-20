"""阶段六 workflow 架构边界静态测试。"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2].parent
BOOT_ROOT = REPO_ROOT / "epsilon-boot"
CLIENT_ROOT = REPO_ROOT / "epsilon-client"

DOMAIN_RUN = BOOT_ROOT / "src" / "domain" / "run"
BACKEND_ADAPTERS = (
    BOOT_ROOT / "src" / "application" / "api" / "routers" / "runs.py",
    BOOT_ROOT / "src" / "application" / "routers" / "runs.py",
    BOOT_ROOT / "src" / "application" / "cli" / "commands.py",
    BOOT_ROOT / "src" / "application" / "cli" / "tui.py",
)
FRONTEND_ADAPTERS = (
    CLIENT_ROOT / "src" / "lib" / "chat-api.ts",
    CLIENT_ROOT / "src" / "components" / "run" / "run-view.tsx",
    CLIENT_ROOT / "src" / "components" / "run" / "run-event-list.tsx",
)
MANIFESTS = (
    BOOT_ROOT / "pyproject.toml",
    BOOT_ROOT / "uv.lock",
    CLIENT_ROOT / "package.json",
)

DURABLE_RUNTIME_TOKENS = (
    "temporal",
    "temporalio",
    "langgraph",
    "dapr",
    "celery",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(_read(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_run_does_not_depend_on_outer_layers_or_durable_runtimes() -> None:
    forbidden_prefixes = (
        "application",
        "infrastructure",
        "fastapi",
        "redis",
        "temporal",
        "temporalio",
        "langgraph",
        "dapr",
        "celery",
    )
    offenders: list[str] = []
    for path in DOMAIN_RUN.rglob("*.py"):
        for module in _imports(path):
            if any(module == item or module.startswith(f"{item}.") for item in forbidden_prefixes):
                offenders.append(f"{path.relative_to(DOMAIN_RUN)} imports {module}")

    assert offenders == []


def test_dependency_manifests_do_not_add_durable_workflow_runtime() -> None:
    """阶段六不得在依赖清单 diff 中新增 durable workflow runtime。"""

    result = subprocess.run(
        [
            "git",
            "diff",
            "--",
            *(str(path.relative_to(REPO_ROOT)) for path in MANIFESTS),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    added_lines = [
        line.lower()
        for line in result.stdout.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    removed_lines = [
        line.lower()
        for line in result.stdout.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]

    # 仅当某 durable runtime token「净新增」时才算越界：即它出现在新增行、
    # 却不出现在任何删除行。这样纯粹的源地址重写（同一既有依赖行的 URL 从内网源
    # 改为公共 PyPI，token 在增/删两侧对称出现）不会误报，而真正引入新的 durable
    # runtime 依赖仍会被拦截。
    offenders = [
        token
        for token in DURABLE_RUNTIME_TOKENS
        if any(token in line for line in added_lines)
        and not any(token in line for line in removed_lines)
    ]
    assert offenders == []


def test_adapters_do_not_import_workflow_runtime_components() -> None:
    forbidden_modules = {
        "infrastructure.run.static_workflow_selector",
        "application.run.workflow_orchestrator",
    }
    forbidden_names = {
        "StaticWorkflowSelector",
        "WorkflowRunOrchestrator",
    }
    offenders: list[str] = []
    for path in BACKEND_ADAPTERS:
        imports = _imports(path)
        text = _read(path)
        if imports & forbidden_modules:
            offenders.append(f"{path.relative_to(REPO_ROOT)} imports runtime module")
        if any(name in text for name in forbidden_names):
            offenders.append(f"{path.relative_to(REPO_ROOT)} references runtime class")

    assert offenders == []


def test_adapters_do_not_copy_collaboration_limit_or_phase_progression_logic() -> None:
    forbidden_logic_tokens = (
        "max_recursion_depth",
        "max_parallel_delegations",
        "max_handoff_count",
        "selectWorkflow",
        "advancePhase",
        "WorkflowRunOrchestrator",
        "StaticWorkflowSelector",
        "collaborationLimit",
    )
    offenders: list[str] = []
    for path in (*BACKEND_ADAPTERS, *FRONTEND_ADAPTERS):
        text = _read(path)
        for token in forbidden_logic_tokens:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} contains {token}")

    assert offenders == []
