"""工具层无后端类型判断的属性测试（Property 6）。

对应 tasks.md 9.9。用 :mod:`ast` 扫描 6 个工具源码文件，断言：

1. **禁止**出现 ``LocalFilesystemWorkspace`` 字面量（Name / Attribute）；
2. **禁止**出现 ``isinstance(..., LocalFilesystemWorkspace)`` 或等价表达式；
3. 允许出现 ``LocallyMaterializable`` 类型检查（shell_exec / python_exec
   用于判定"本地物化"能力的结构型协议是合法的）。

扫描目标：

- ``read_file_tool.py`` / ``write_file_tool.py`` / ``edit_file_tool.py`` /
  ``list_dir_tool.py``（本批次 9.1 / 9.3 / 9.5 / 9.7 已改造）；
- ``shell_exec_tool.py`` / ``python_exec_tool.py``（Phase 10 / 11 尚未改造；
  本测试对两者**同样**生效——原始实现本来就不 import
  ``LocalFilesystemWorkspace``，故本测试对它们是"预防性守护"，
  改造后仍必须保持通过）。

**关键设计**：对工具文件做**字符串**与 **AST** 双重扫描。字符串层面
扫描是为了捕获注释、docstring 中可能无意写入的类名提及；AST 层面
扫描捕获真实的代码引用。为避免把合理的 docstring 注释误判，字符串
扫描仅覆盖非注释/非字符串的源码 token。
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


def _source_path(cls: type[object]) -> Path:
    source_file = inspect.getsourcefile(cls)
    assert source_file is not None
    return Path(source_file)


def _iter_tool_source_paths() -> list[Path]:
    """构造 6 个工具源文件路径列表。

    对 4 个 filesystem 工具，通过 :func:`inspect.getsourcefile` 定位源文件，
    消除仓库相对路径的硬编码；对 shell_exec / python_exec，采用源文件相
    对当前测试文件的已知布局推导。
    """
    fs_paths = [
        _source_path(cls)
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool)
    ]
    # 从 filesystem 工具文件推导 src/infrastructure/tools 根。
    tools_root = fs_paths[0].parent.parent
    shell_path = tools_root / "shell_exec" / "shell_exec_tool.py"
    python_path = tools_root / "python_exec" / "python_exec_tool.py"
    return [*fs_paths, shell_path, python_path]


class _TypeCheckVisitor(ast.NodeVisitor):
    """AST Visitor：收集 LocalFilesystemWorkspace 的 Name / Attribute 访问，
    以及 isinstance(...) 调用中第二参数是否为 LocalFilesystemWorkspace。
    """

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "LocalFilesystemWorkspace":
            self.violations.append(f"Name 'LocalFilesystemWorkspace' at line {node.lineno}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "LocalFilesystemWorkspace":
            self.violations.append(f"Attribute '.LocalFilesystemWorkspace' at line {node.lineno}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "isinstance":
            # 第二参数允许是 LocallyMaterializable，但不得是 LocalFilesystemWorkspace
            for arg in node.args[1:]:
                self._collect_banned_type_in_arg(arg, node.lineno)
        self.generic_visit(node)

    def _collect_banned_type_in_arg(self, arg: ast.expr, lineno: int) -> None:
        """扫描 isinstance 的类型参数（可能是 Tuple），禁用
        LocalFilesystemWorkspace；LocallyMaterializable 允许。
        """
        if isinstance(arg, ast.Name):
            if arg.id == "LocalFilesystemWorkspace":
                self.violations.append(
                    f"isinstance(..., LocalFilesystemWorkspace) at line {lineno}"
                )
        elif isinstance(arg, ast.Attribute):
            if arg.attr == "LocalFilesystemWorkspace":
                self.violations.append(
                    f"isinstance(..., *.LocalFilesystemWorkspace) at line {lineno}"
                )
        elif isinstance(arg, ast.Tuple):
            for elt in arg.elts:
                self._collect_banned_type_in_arg(elt, lineno)


@pytest.mark.parametrize("source_path", _iter_tool_source_paths())
def test_tool_source_has_no_backend_type_branch(source_path: Path) -> None:
    """6 个工具源代码不得出现 LocalFilesystemWorkspace 字面量/isinstance 检查。"""
    assert source_path.exists(), f"期待的源文件缺失：{source_path}"
    src = source_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    visitor = _TypeCheckVisitor()
    visitor.visit(tree)

    assert not visitor.violations, (
        f"{source_path.name} 违反 Property 6（无后端类型判断）：\n"
        + "\n".join(f"  - {v}" for v in visitor.violations)
    )


@pytest.mark.parametrize("source_path", _iter_tool_source_paths())
def test_tool_source_does_not_literal_mention_local_filesystem_workspace(
    source_path: Path,
) -> None:
    """字符串层面扫描：源文件中不得出现 'LocalFilesystemWorkspace' 子串。

    包含 docstring / 注释 / 代码。此测试比 AST 扫描更严格，防止后续维护
    者在 docstring 中"示例性"地引用该类名而产生隐式耦合。
    """
    assert source_path.exists()
    src = source_path.read_text(encoding="utf-8")
    assert "LocalFilesystemWorkspace" not in src, (
        f"{source_path.name} 字面包含 'LocalFilesystemWorkspace'，违反 Property 6"
    )
