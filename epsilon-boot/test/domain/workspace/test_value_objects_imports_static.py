"""静态检查：``value_objects.py`` 不得导入 ``domain.workspace.policy``。

该检查用于守住设计决策"``WorkspacePath.join`` 不依赖 ``WorkspacePolicy``"的闭环：
任何形态的 policy 模块导入（顶层、函数体内、``importlib`` 动态加载等）都会
被本测试捕获，阻止未来维护时不经意引入循环依赖。
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys

# ``value_objects.py`` 在仓库中的绝对路径。本测试文件位于
# ``test/domain/workspace/``，反向到达 ``epsilon-boot/`` 共 3 层，再进
# ``src/domain/workspace/value_objects.py``。
_VALUE_OBJECTS_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[3]
    / "src"
    / "domain"
    / "workspace"
    / "value_objects.py"
)


# 被禁止的模块名变体集合；任何以此前缀开头的 import 都视为违规。
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = ("domain.workspace.policy",)


def _all_import_nodes(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """递归遍历 AST，收集所有 ``Import`` / ``ImportFrom`` 节点。

    这样可以同时捕获顶层 import 与函数/方法体内的延迟 import。
    """
    nodes: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            nodes.append(node)
    return nodes


def _is_forbidden_import_from(node: ast.ImportFrom) -> bool:
    """判断 ``from X import Y`` 是否命中禁止名单。

    覆盖两种禁止形态：

    - ``from domain.workspace.policy import ...``（直接 from）
    - ``from domain.workspace import policy``（间接暴露模块）
    """
    module = node.module or ""
    if module.startswith("domain.workspace.policy"):
        return True
    if module == "domain.workspace":
        # 捕获 ``from domain.workspace import policy`` 形态。
        for alias in node.names:
            if alias.name == "policy":
                return True
    return False


def _is_forbidden_import(node: ast.Import) -> bool:
    """判断 ``import X`` 是否命中禁止名单（含别名）。"""
    for alias in node.names:
        name = alias.name
        for prefix in _FORBIDDEN_IMPORT_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                return True
    return False


def test_value_objects_does_not_import_policy_statically() -> None:
    """AST 层面断言 ``value_objects.py`` 没有 policy 相关 import。

    同时覆盖顶层与函数体内的所有 import / from-import 节点。
    """
    source = _VALUE_OBJECTS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_VALUE_OBJECTS_PATH))

    violations: list[str] = []
    for node in _all_import_nodes(tree):
        if isinstance(node, ast.ImportFrom) and _is_forbidden_import_from(node):
            violations.append(
                f"line {node.lineno}: from {node.module} import "
                f"{', '.join(a.name for a in node.names)}"
            )
        elif isinstance(node, ast.Import) and _is_forbidden_import(node):
            violations.append(f"line {node.lineno}: import {', '.join(a.name for a in node.names)}")

    assert not violations, (
        "value_objects.py 不得导入 domain.workspace.policy（避免循环依赖），"
        "但发现以下违规：\n" + "\n".join(violations)
    )


def test_value_objects_import_does_not_load_policy_module() -> None:
    """运行期断言：加载 ``value_objects`` 不会把 ``policy`` 模块带入 ``sys.modules``。

    该测试与静态 AST 扫描互补，用于捕获通过 ``importlib`` / ``__import__``
    等动态手段绕过 AST 扫描的情况。
    """
    # 保险：若此前测试运行加载过 policy 模块，先剔除以保证本测试独立有效。
    sys.modules.pop("domain.workspace.policy", None)

    importlib.import_module("domain.workspace.value_objects")

    assert "domain.workspace.policy" not in sys.modules, (
        "加载 domain.workspace.value_objects 不应把 domain.workspace.policy "
        "带入 sys.modules（疑似运行时延迟 import policy）。"
    )
