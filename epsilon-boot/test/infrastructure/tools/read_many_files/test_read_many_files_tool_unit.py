from __future__ import annotations

import ast
import inspect
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.value_objects import WorkspacePath
from infrastructure.tools.read_many_files.read_many_files_tool import ReadManyFilesTool


def _path(value: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(value))


def _resolve_path(value: str) -> WorkspacePath:
    return _path(value if value.startswith("/") else f"/{value}")


def _workspace(contents: dict[str, bytes | Exception]) -> MagicMock:
    ws = MagicMock()
    ws.display_root_hint.return_value = "/tmp/ws"
    ws.resolve_path.side_effect = _resolve_path

    async def read(path: WorkspacePath, **_: object) -> bytes:
        item = contents[path.to_posix()]
        if isinstance(item, Exception):
            raise item
        return item

    ws.read = AsyncMock(side_effect=read)
    return ws


@pytest.mark.asyncio
async def test_read_many_files_success_and_metadata() -> None:
    ws = _workspace({"/a.py": b"a1\na2\n", "/b.py": b"b1\n"})
    tool = ReadManyFilesTool(ws)

    result = await tool.execute(file_paths=["a.py", "b.py"])

    assert "===== /a.py =====" in result.content
    assert "   1 | a1" in result.content
    assert "===== /b.py =====" in result.content
    assert set(result.metadata) == {
        "operation",
        "requested_file_count",
        "files_read",
        "files_failed",
        "total_lines_returned",
        "truncated",
    }
    assert result.metadata["files_read"] == 2
    assert result.metadata["total_lines_returned"] == 3


@pytest.mark.asyncio
async def test_read_many_files_continues_after_missing_and_io_failure() -> None:
    ws = _workspace(
        {
            "/a.py": WorkspaceNotFoundError(_path("/a.py")),
            "/b.py": WorkspaceIoError(operation="read", workspace_path=_path("/b.py"), reason="x"),
            "/c.py": b"ok\n",
        }
    )
    tool = ReadManyFilesTool(ws)

    result = await tool.execute(file_paths=["a.py", "b.py", "c.py"])

    assert "[error] 路径 a.py 不存在" in result.content
    assert "[error] 读取文件 b.py 失败" in result.content
    assert "   1 | ok" in result.content
    assert result.metadata["files_failed"] == 2
    assert result.metadata["files_read"] == 1


@pytest.mark.asyncio
async def test_read_many_files_per_file_out_of_boundary_error() -> None:
    ws = _workspace({"/ok.py": b"ok"})
    ws.resolve_path.side_effect = [
        WorkspaceConfinementViolation(
            requested_path="../x",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        ),
        _path("/ok.py"),
    ]
    tool = ReadManyFilesTool(ws)

    result = await tool.execute(file_paths=["../x", "ok.py"])

    assert "[error] 路径 ../x 超出工作区边界" in result.content
    assert "===== /ok.py =====" in result.content
    assert result.metadata["files_failed"] == 1
    assert result.metadata["files_read"] == 1


@pytest.mark.asyncio
async def test_read_many_files_passes_offset_and_limit() -> None:
    ws = _workspace({"/a.py": b"line\n"})
    tool = ReadManyFilesTool(ws)

    await tool.execute(file_paths=["a.py"], offset=3, limit=5)

    _, kwargs = ws.read.await_args
    assert kwargs["start_line"] == 3
    assert kwargs["end_line"] == 7


@pytest.mark.asyncio
async def test_read_many_files_max_total_chars_truncates() -> None:
    ws = _workspace({"/a.py": b"x" * 2000})
    tool = ReadManyFilesTool(ws)

    result = await tool.execute(file_paths=["a.py"], max_total_chars=1000)

    assert len(result.content) <= 1000
    assert result.metadata["truncated"] is True


def test_read_many_files_source_does_not_import_host_filesystem_api() -> None:
    source_file = inspect.getsourcefile(ReadManyFilesTool)
    assert source_file is not None
    src_path = Path(source_file)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    banned_modules = {"os", "pathlib", "common.tools.common_tools"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned_modules
        if isinstance(node, ast.ImportFrom):
            assert node.module not in banned_modules
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open"
