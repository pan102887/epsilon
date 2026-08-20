"""DDD 分层导入边界静态测试。"""

from __future__ import annotations

import ast
from pathlib import Path

BOOT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = BOOT_ROOT / "src"
DOMAIN_ROOT = SRC_ROOT / "domain"
COMMON_ROOT = SRC_ROOT / "common"
APPLICATION_ROOT = SRC_ROOT / "application"
INFRASTRUCTURE_ROOT = SRC_ROOT / "infrastructure"
FORBIDDEN_APPLICATION_PREFIX = "application"
FORBIDDEN_INFRASTRUCTURE_PREFIX = "infrastructure"
FORBIDDEN_DOMAIN_CONFIGURATION_PREFIX = "common.configuration"
APPLICATION_COMPOSITION_ROOT_PATHS: frozenset[Path] = frozenset(
    {
        SRC_ROOT / "application" / "container_config.py",
        *sorted((SRC_ROOT / "application" / "container").glob("*.py")),
        SRC_ROOT / "application" / "api" / "server_app.py",
        *(
            path
            for path in (
                SRC_ROOT / "application" / "server_app.py",
                SRC_ROOT / "application" / "cli" / "main.py",
            )
            if path.exists()
        ),
    }
)

# 受控迁移例外：Run 应用层曾复用既有 infrastructure serializer 把
# segment/workflow/guardrail 运行时摘要转成持久化或事件 payload。
# 该批例外已由 ddd-followup-refinements 切片 A 全部消除——Run 应用层改为依赖
# `application/run/serialization_ports.py` 的序列化 Protocol，由组合根注入
# `infrastructure/run/run_serialization_adapters.py` 的 delegating adapter，
# 序列化实现仍留基础设施层（ADR-0008）。本表现已收敛为空。
# 精确范围：只允许下列表中的仓库相对路径导入对应精确模块，不允许前缀白名单。
# 本静态测试要求实际命中与本表完全相等（空集），防止路径或模块静默扩大。
APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS: dict[str, tuple[str, ...]] = {}


def _python_files(root: Path) -> list[Path]:
    """返回目录下全部 Python 源文件。"""

    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _parse(path: Path) -> ast.Module:
    """将 Python 源码解析为 AST，不执行模块导入。"""

    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    """提取文件中的绝对导入模块名。"""

    modules: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _has_prefix(module: str, prefix: str) -> bool:
    """按精确前缀分段规则判断模块名是否命中。"""

    return module == prefix or module.startswith(prefix + ".")


def _relative(path: Path) -> str:
    """返回后端仓库相对路径，便于断言失败输出。"""

    return str(path.relative_to(BOOT_ROOT))


def _collect_forbidden_import_pairs(
    root: Path,
    forbidden_prefix: str,
) -> dict[str, tuple[str, ...]]:
    """收集目录下命中禁止前缀的仓库相对路径与精确模块列表。"""

    violations: dict[str, tuple[str, ...]] = {}
    for path in _python_files(root):
        forbidden = tuple(
            sorted(
                module
                for module in _imports(path)
                if _has_prefix(module, forbidden_prefix)
            )
        )
        if forbidden:
            violations[_relative(path)] = forbidden
    return violations


def _composition_root_relative_paths() -> set[str]:
    """返回允许导入基础设施实现的组合根仓库相对路径。"""

    return {_relative(path) for path in APPLICATION_COMPOSITION_ROOT_PATHS}


def _application_infrastructure_imports_outside_composition_roots() -> dict[
    str,
    tuple[str, ...],
]:
    """收集非组合根应用层文件到基础设施层的导入。"""

    composition_roots = _composition_root_relative_paths()
    return {
        path: modules
        for path, modules in _collect_forbidden_import_pairs(
            APPLICATION_ROOT,
            FORBIDDEN_INFRASTRUCTURE_PREFIX,
        ).items()
        if path not in composition_roots
    }


def test_domain_layer_does_not_import_application_layer() -> None:
    """领域层不得导入应用层模块。"""

    violations = _collect_forbidden_import_pairs(
        DOMAIN_ROOT,
        FORBIDDEN_APPLICATION_PREFIX,
    )

    assert violations == {}


def test_domain_layer_does_not_import_infrastructure_layer() -> None:
    """领域层不得导入基础设施层模块。"""

    violations = _collect_forbidden_import_pairs(
        DOMAIN_ROOT,
        FORBIDDEN_INFRASTRUCTURE_PREFIX,
    )

    assert violations == {}


def test_domain_layer_does_not_import_common_configuration() -> None:
    """领域层不得导入运行时配置机制。"""

    violations = _collect_forbidden_import_pairs(
        DOMAIN_ROOT,
        FORBIDDEN_DOMAIN_CONFIGURATION_PREFIX,
    )

    assert violations == {}


def test_common_layer_does_not_import_application_layer() -> None:
    """公共层不得导入应用层模块。"""

    violations = _collect_forbidden_import_pairs(
        COMMON_ROOT,
        FORBIDDEN_APPLICATION_PREFIX,
    )

    assert violations == {}


def test_common_layer_does_not_import_infrastructure_layer() -> None:
    """公共层不得导入基础设施层。"""

    violations = _collect_forbidden_import_pairs(
        COMMON_ROOT,
        FORBIDDEN_INFRASTRUCTURE_PREFIX,
    )

    assert violations == {}


def test_infrastructure_layer_does_not_import_application_layer() -> None:
    """基础设施层不得导入应用层模块。"""

    violations = _collect_forbidden_import_pairs(
        INFRASTRUCTURE_ROOT,
        FORBIDDEN_APPLICATION_PREFIX,
    )

    assert violations == {}


def test_application_layer_imports_infrastructure_only_through_declared_exceptions() -> None:
    """应用层导入基础设施层必须来自组合根或精确迁移例外。"""

    violations = _application_infrastructure_imports_outside_composition_roots()

    assert violations == APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS


def test_application_infrastructure_exception_scope_is_exact() -> None:
    """迁移例外不得通过新增路径或新增模块静默扩大。"""

    violations = _application_infrastructure_imports_outside_composition_roots()
    exception_hits = {
        path: modules
        for path, modules in violations.items()
        if path in APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS
    }
    overlapping_composition_roots = sorted(
        set(APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS)
        & _composition_root_relative_paths()
    )

    assert exception_hits == APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS
    assert overlapping_composition_roots == []
