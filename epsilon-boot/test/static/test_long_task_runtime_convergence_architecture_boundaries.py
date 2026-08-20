"""长任务运行时收敛的架构边界静态测试。

这些测试只解析源码文本和 AST，不启动 FastAPI、Redis、前端构建或其他外部服务。
它们用于守住 P0 收敛后的 DDD 边界：领域层保持纯净，HTTP/CLI/TUI/Web
只展示 RunSnapshot 与 Run_Event_Stream 中的事实，不复制 guardrail 或 workflow
策略判断逻辑，并确保本特性新增公共接口具备中文文档。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

BOOT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BOOT_ROOT.parent
SRC_ROOT = BOOT_ROOT / "src"
CLIENT_ROOT = REPO_ROOT / "epsilon-client"
CONFIG_PROPERTIES = BOOT_ROOT / "config.properties"

DOMAIN_ROOT = SRC_ROOT / "domain"
RUN_ADAPTER_VIEW_PYTHON_PATHS = (
    SRC_ROOT / "application" / "api" / "routers" / "runs.py",
    SRC_ROOT / "application" / "cli" / "runtime.py",
    SRC_ROOT / "application" / "cli" / "commands.py",
    SRC_ROOT / "application" / "cli" / "tui.py",
)
RUN_ADAPTER_VIEW_FRONTEND_PATHS = tuple(
    sorted(
        path
        for pattern in (
            "src/lib/chat-api.ts",
            "src/hooks/use-run.ts",
            "src/components/run/*.tsx",
        )
        for path in CLIENT_ROOT.glob(pattern)
    )
)
ADAPTER_VIEW_PATHS = RUN_ADAPTER_VIEW_PYTHON_PATHS + RUN_ADAPTER_VIEW_FRONTEND_PATHS

FEATURE_PUBLIC_PYTHON_FILES = (
    SRC_ROOT / "domain" / "agent" / "guardrails.py",
    SRC_ROOT / "domain" / "agent" / "segmented_execution.py",
    SRC_ROOT / "domain" / "agent" / "ports.py",
    SRC_ROOT / "domain" / "run" / "ports.py",
    SRC_ROOT / "domain" / "run" / "runtime_context.py",
    SRC_ROOT / "domain" / "run" / "workflow.py",
    SRC_ROOT / "domain" / "task" / "ports.py",
    SRC_ROOT / "domain" / "task" / "value_objects.py",
    SRC_ROOT / "application" / "api" / "routers" / "runs.py",
    SRC_ROOT / "application" / "cli" / "runtime.py",
    SRC_ROOT / "application" / "cli" / "commands.py",
    SRC_ROOT / "application" / "cli" / "tui.py",
    SRC_ROOT / "application" / "run" / "run_guardrail_recorder.py",
    SRC_ROOT / "application" / "run" / "run_approval_resumer.py",
    SRC_ROOT / "application" / "run" / "run_execution_coordinator.py",
    SRC_ROOT / "application" / "run" / "run_checkpoint_recovery_service.py",
    SRC_ROOT / "infrastructure" / "agent" / "react_agent_adapter.py",
    SRC_ROOT / "infrastructure" / "chat" / "chat_service_adapter.py",
    SRC_ROOT / "infrastructure" / "task" / "task_agent_adapter.py",
    SRC_ROOT / "infrastructure" / "run" / "local_file_run_store_adapter.py",
    SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
    SRC_ROOT / "infrastructure" / "run" / "run_config.py",
)
PUBLIC_DOCSTRING_ALLOWLIST = {
    # 历史流式 Agent 接口，非本特性新增；后续文档治理单独收敛。
    (SRC_ROOT / "domain" / "agent" / "ports.py", "AgentPort.run_events"),
    # 历史 Redis RunStore/EventStore 完整端口实现，非本特性新增；本特性新增方法仍扫描。
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.get_run",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.get_by_client_request_id",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.count_by_status",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.refresh_lease",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.request_cancel",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.enqueue_continue",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.mark_lost_expired_leases",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.list_expired_leased_runs",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.mark_lost_expired_run",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.append_event",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.list_events",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.wait_events",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.trim_events",
    ),
    (
        SRC_ROOT / "infrastructure" / "run" / "redis_run_store_adapter.py",
        "RedisRunStoreAdapter.first_cursor",
    ),
}

FORBIDDEN_DOMAIN_IMPORT_PREFIXES = (
    "application",
    "infrastructure",
    "fastapi",
    "redis",
    "aioredis",
    "temporal",
    "temporalio",
    "langgraph",
    "dapr",
    "celery",
)
FORBIDDEN_WORKFLOW_ENGINE_TOKENS = (
    "temporal",
    "temporalio",
    "langgraph",
    "dapr",
    "celery",
)
FORBIDDEN_ADAPTER_IMPORT_PREFIXES = (
    "domain.agent.guardrails",
    "domain.agent.segmented_execution",
    "application.run.workflow_orchestrator",
    "infrastructure.run.static_workflow_selector",
)
FORBIDDEN_ADAPTER_POLICY_TOKENS = (
    "AgentRoleCapability",
    "GuardrailAction",
    "GuardrailDecision",
    "GuardrailEvaluationContext",
    "GuardrailMode",
    "GuardrailPolicy",
    "GuardrailReason",
    "StaticWorkflowSelector",
    "WorkflowExecutionPolicy",
    "WorkflowRunOrchestrator",
    "allowed_delegate_agents",
    "allowed_handoff_agents",
    "allowed_tool_names",
    "can_create_child_run",
    "decide_next_segment",
    "evaluate_model_completed",
    "evaluate_run_start",
    "evaluate_tool_after_execution",
    "evaluate_tool_before_execution",
    "max_child_runs",
    "max_consecutive_failures",
    "max_consecutive_paused",
    "max_context_growth_messages",
    "max_duration_seconds",
    "max_handoff_count",
    "max_no_progress_segments",
    "max_parallel_delegations",
    "max_recursion_depth",
    "max_repeated_tool_calls",
    "max_revise_per_phase",
    "max_total_tokens",
    "phase_handoff_required",
    "review_required_phases",
    "revise_target_phase",
    "role_capability_enabled",
)
_CHINESE_RE = re.compile(r"[一-鿿]")


def _python_files(root: Path) -> list[Path]:
    """返回目录下所有 Python 源文件。"""

    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _parse(path: Path) -> ast.Module:
    """解析 Python 源文件为 AST。"""

    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    """提取 Python 文件中的 import 模块名称。"""

    modules: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _has_prefix(module: str, prefixes: Iterable[str]) -> bool:
    """判断模块名是否命中任一禁止前缀。"""

    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _relative(path: Path) -> str:
    """返回便于断言输出阅读的仓库相对路径。"""

    return str(path.relative_to(REPO_ROOT))


def _read(path: Path) -> str:
    """读取源码文本。"""

    return path.read_text(encoding="utf-8")


def _has_chinese_docstring(node: ast.AST) -> bool:
    """判断 AST 节点是否具备中文 docstring。"""

    docstring = ast.get_docstring(node)
    return bool(docstring and _CHINESE_RE.search(docstring))


def _top_level_classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    """按名称索引模块顶层类定义。"""

    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _top_level_functions(
    tree: ast.Module,
) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    """按名称索引模块顶层函数定义。"""

    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def _class_methods(
    class_node: ast.ClassDef,
) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    """按名称索引类中的函数或协程方法。"""

    return {
        node.name: node
        for node in class_node.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def test_domain_does_not_import_outer_layers_frameworks_or_workflow_engines() -> None:
    """领域层不得依赖基础设施、FastAPI、Redis 或外部 workflow engine。"""

    violations: dict[str, list[str]] = {}
    for path in _python_files(DOMAIN_ROOT):
        forbidden = sorted(
            module
            for module in _imports(path)
            if _has_prefix(module, FORBIDDEN_DOMAIN_IMPORT_PREFIXES)
        )
        if forbidden:
            violations[_relative(path)] = forbidden

    assert violations == {}


def test_runtime_convergence_config_uses_config_properties_as_default_source() -> None:
    """Run guardrail 收敛默认开关必须来自 config.properties。"""

    config_text = CONFIG_PROPERTIES.read_text(encoding="utf-8")

    assert "RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED=true" in config_text


def test_run_adapters_and_views_do_not_copy_guardrail_or_workflow_policy_logic() -> None:
    """HTTP、CLI/TUI 与前端只展示快照/事件，不复制策略判断。"""

    offenders: list[str] = []
    for path in ADAPTER_VIEW_PATHS:
        assert path.exists(), f"missing adapter/view path: {_relative(path)}"
        text = _read(path)
        if path.suffix == ".py":
            forbidden_imports = sorted(
                module
                for module in _imports(path)
                if _has_prefix(module, FORBIDDEN_ADAPTER_IMPORT_PREFIXES)
            )
            if forbidden_imports:
                offenders.append(f"{_relative(path)} imports policy modules {forbidden_imports}")
        for token in FORBIDDEN_ADAPTER_POLICY_TOKENS:
            if token in text:
                offenders.append(f"{_relative(path)} contains policy token {token}")

    assert offenders == []


def test_adapter_views_do_not_reference_external_workflow_engines() -> None:
    """Run adapter 与展示层不得引用外部 durable workflow engine。"""

    offenders: list[str] = []
    for path in ADAPTER_VIEW_PATHS:
        lowered = _read(path).lower()
        for token in FORBIDDEN_WORKFLOW_ENGINE_TOKENS:
            if token in lowered:
                offenders.append(f"{_relative(path)} references {token}")

    assert offenders == []


def test_feature_public_python_surface_has_chinese_docstrings() -> None:
    """本特性相关 Python 文件的公开接口面必须有中文 docstring。"""

    offenders: list[str] = []
    for path in FEATURE_PUBLIC_PYTHON_FILES:
        assert path.exists(), f"missing feature module: {_relative(path)}"
        tree = _parse(path)

        if not _has_chinese_docstring(tree):
            offenders.append(f"{_relative(path)} module")

        for class_node in _top_level_classes(tree).values():
            if class_node.name.startswith("_"):
                continue
            allowlisted = (path, class_node.name) in PUBLIC_DOCSTRING_ALLOWLIST
            if not allowlisted and not _has_chinese_docstring(class_node):
                offenders.append(f"{_relative(path)}::{class_node.name}")
            for method_node in _class_methods(class_node).values():
                if method_node.name.startswith("_"):
                    continue
                qualified_method = f"{class_node.name}.{method_node.name}"
                if (path, qualified_method) in PUBLIC_DOCSTRING_ALLOWLIST:
                    continue
                if not _has_chinese_docstring(method_node):
                    offenders.append(f"{_relative(path)}::{qualified_method}")

        for function_node in _top_level_functions(tree).values():
            if function_node.name.startswith("_"):
                continue
            if (path, function_node.name) in PUBLIC_DOCSTRING_ALLOWLIST:
                continue
            if not _has_chinese_docstring(function_node):
                offenders.append(f"{_relative(path)}::{function_node.name}")

    assert offenders == []
