"""工具层在调用 Workspace Port I/O 方法时必须注入 ``context`` 的静态检查
（Phase 13.3 可选任务）。

扫描 6 个工具的 ``execute`` 方法 AST，断言：

1. 对 Workspace 的 7 个 I/O 方法（``exists`` / ``stat`` / ``read`` / ``write`` /
   ``edit`` / ``list_dir`` / ``delete``）的 ``await`` 调用处，必须出现
   ``context=<expr>`` 关键字实参；
2. 非 I/O 方法（``resolve_path`` / ``capabilities`` / ``display_root_hint`` /
   ``materialize_cwd``）**不得**携带 ``context`` 关键字实参（Protocol 约束）；
3. 工具内构造的 ``context`` 字典字面量或同函数内赋值的 ``context`` 变量
   必须包含字符串键 ``"tool_name"``。

本测试主要价值在于维护期防止工具改造漂移；不阻塞主线合并。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from infrastructure.tools.filesystem.edit_file_tool import EditFileTool
from infrastructure.tools.filesystem.list_dir_tool import ListDirTool
from infrastructure.tools.filesystem.read_file_tool import ReadFileTool
from infrastructure.tools.filesystem.write_file_tool import WriteFileTool

_IO_METHODS: frozenset[str] = frozenset(
    {"exists", "stat", "read", "write", "edit", "list_dir", "delete"}
)
"""需要携带 ``context`` 的 7 个 I/O 方法。"""

_NON_IO_METHODS: frozenset[str] = frozenset(
    {"resolve_path", "capabilities", "display_root_hint", "materialize_cwd"}
)
"""不得携带 ``context`` 的非 I/O 方法。"""


def _source_path(cls: type[object]) -> Path:
    source_file = inspect.getsourcefile(cls)
    assert source_file is not None
    return Path(source_file)


def _find_tool_source_paths() -> list[Path]:
    """定位 6 个工具源文件。"""
    fs_paths = [
        _source_path(cls)
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool)
    ]
    tools_root = fs_paths[0].parent.parent
    return [
        *fs_paths,
        tools_root / "shell_exec" / "shell_exec_tool.py",
        tools_root / "python_exec" / "python_exec_tool.py",
    ]


def _iter_workspace_io_calls(
    tree: ast.AST,
) -> list[tuple[ast.Call, str]]:
    """收集所有形如 ``await self._workspace.<method>(...)`` 的调用。

    返回 ``(Call, method_name)`` 二元组列表，便于对不同方法做差异化断言。
    """
    hits: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        # 形如 `self._workspace.<method>`
        inner = func.value
        if not (
            isinstance(inner, ast.Attribute)
            and inner.attr == "_workspace"
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "self"
        ):
            continue
        hits.append((node, func.attr))
    return hits


def _has_context_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "context" for kw in call.keywords)


@pytest.mark.parametrize("source_path", _find_tool_source_paths())
def test_tool_source_has_context_on_io_methods(source_path: Path) -> None:
    """对每个工具源文件扫描：7 个 I/O 方法必须携带 ``context=``，
    非 I/O 方法不得携带。"""
    src = source_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    calls = _iter_workspace_io_calls(tree)

    for call, method in calls:
        if method in _IO_METHODS:
            assert _has_context_kwarg(call), (
                f"{source_path.name}: self._workspace.{method}(...) 缺少 "
                f"`context=` 关键字实参（Phase 13.3 约束）"
            )
        elif method in _NON_IO_METHODS:
            assert not _has_context_kwarg(call), (
                f"{source_path.name}: self._workspace.{method}(...) 不得携带 "
                f"`context=`（Port 约定）"
            )
        # 其他方法（理论不应出现）不做硬断言，留给人工审阅


@pytest.mark.parametrize(
    "source_path",
    [
        p
        for p in _find_tool_source_paths()
        if p.name
        in {
            "read_file_tool.py",
            "write_file_tool.py",
            "edit_file_tool.py",
            "list_dir_tool.py",
            "shell_exec_tool.py",
            "python_exec_tool.py",
        }
    ],
)
def test_tool_source_context_contains_tool_name(source_path: Path) -> None:
    """工具内 ``context`` 构造字面量或赋值必须包含字符串键 ``"tool_name"``。

    扫描规则：只要源文件中存在形如 ``context: ... = {"tool_name": ...}`` 的
    AnnAssign 或 ``context = {"tool_name": ...}`` 的 Assign，即视为合规；
    或源文件中出现 ``"tool_name"`` 字符串字面量紧邻 ``context`` 赋值的场景。
    ShellExecTool / PythonExecTool 目前由后端内部生成默认 ``context``
    （不在调用点显式构造字典），本断言对其不强制——采用"源码 text 含
    ``context=`` 到后端调用"即视为合规。
    """
    src = source_path.read_text(encoding="utf-8")

    if source_path.name in {"shell_exec_tool.py", "python_exec_tool.py"}:
        # 当前 Phase 10/11 版本不显式构造 context 字典（不调用 Port 的 I/O
        # 方法——仅调 resolve_path / materialize_cwd，两者按协议不接受 context）。
        # 因此这里仅做"不倒退"断言：确认工具源文件不含 `context=` 向非 I/O
        # 方法注入的误用（已由 test_tool_source_has_context_on_io_methods 覆盖）。
        return

    # 4 个 filesystem 工具：必须构造 context 字典且含 "tool_name"
    tree = ast.parse(src)
    found_tool_name_key = False

    for node in ast.walk(tree):
        # context: dict[str, Any] = {"tool_name": self.name}
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "context"
            and isinstance(node.value, ast.Dict)
        ):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and key.value == "tool_name":
                    found_tool_name_key = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "context"
                    and isinstance(node.value, ast.Dict)
                ):
                    for key in node.value.keys:
                        if isinstance(key, ast.Constant) and key.value == "tool_name":
                            found_tool_name_key = True

    assert found_tool_name_key, (
        f"{source_path.name}: 未在 execute 方法中找到 "
        '`context = {"tool_name": ...}` 的构造（Phase 13.3 约束）'
    )
