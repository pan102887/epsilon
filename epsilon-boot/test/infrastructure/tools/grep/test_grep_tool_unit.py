from __future__ import annotations

import ast
import inspect
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.exceptions import WorkspaceIoError
from domain.workspace.value_objects import WorkspacePath, WorkspaceStatEntry
from infrastructure.tools.grep.grep_tool import GrepTool


def _path(value: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(value))


def _entry(value: str) -> WorkspaceStatEntry:
    return WorkspaceStatEntry(path=_path(value), is_file=True, is_dir=False, size=1, mtime=None)


def _workspace(
    entries: list[WorkspaceStatEntry],
    contents: dict[str, bytes | Exception],
) -> MagicMock:
    ws = MagicMock()
    ws.display_root_hint.return_value = "/tmp/ws"
    ws.resolve_path.side_effect = lambda s: _path(s if s.startswith("/") else f"/{s}")
    ws.list_dir = AsyncMock(return_value=entries)

    async def read(path: WorkspacePath, **_: object) -> bytes:
        value = path.to_posix()
        item = contents[value]
        if isinstance(item, Exception):
            raise item
        return item

    ws.read = AsyncMock(side_effect=read)
    return ws


@pytest.mark.asyncio
async def test_grep_literal_search_returns_matches_and_metadata() -> None:
    ws = _workspace([_entry("/a.py")], {"/a.py": b"alpha\nneedle here\n"})
    tool = GrepTool(ws)

    result = await tool.execute(query="needle")

    assert result.content == "/a.py:2: needle here"
    assert set(result.metadata) == {
        "operation",
        "query",
        "mode",
        "directory_path",
        "include_pattern",
        "files_scanned",
        "files_skipped",
        "matches_returned",
        "truncated",
    }
    assert result.metadata["matches_returned"] == 1
    assert result.metadata["files_scanned"] == 1


@pytest.mark.asyncio
async def test_grep_regex_and_case_insensitive_search() -> None:
    ws = _workspace([_entry("/a.py")], {"/a.py": b"Value: ABC\n"})
    tool = GrepTool(ws)

    result = await tool.execute(query=r"value: [a-z]+", mode="regex", case_sensitive=False)

    assert result.content == "/a.py:1: Value: ABC"
    assert result.metadata["mode"] == "regex"


@pytest.mark.asyncio
async def test_grep_invalid_regex_fails_before_scan() -> None:
    ws = _workspace([_entry("/a.py")], {"/a.py": b""})
    tool = GrepTool(ws)

    with pytest.raises(ToolExecutionError):
        await tool.execute(query="[", mode="regex")

    ws.list_dir.assert_not_called()


@pytest.mark.asyncio
async def test_grep_include_pattern_filters_files() -> None:
    ws = _workspace([_entry("/a.py"), _entry("/a.md")], {"/a.py": b"needle", "/a.md": b"needle"})
    tool = GrepTool(ws)

    result = await tool.execute(query="needle", include_pattern="**/*.md")

    assert result.content == "/a.md:1: needle"


@pytest.mark.asyncio
async def test_grep_skips_unreadable_and_binary_files() -> None:
    ws = _workspace(
        [_entry("/a.py"), _entry("/b.py"), _entry("/c.py")],
        {
            "/a.py": WorkspaceIoError(operation="read", workspace_path=_path("/a.py"), reason="x"),
            "/b.py": b"\xff",
            "/c.py": b"needle",
        },
    )
    tool = GrepTool(ws)

    result = await tool.execute(query="needle")

    assert result.content == "/c.py:1: needle"
    assert result.metadata["files_skipped"] == 2
    assert result.metadata["files_scanned"] == 1


@pytest.mark.asyncio
async def test_grep_max_matches_and_line_chars_truncate() -> None:
    ws = _workspace([_entry("/a.py")], {"/a.py": b"needle 1234567890\nneedle second\n"})
    tool = GrepTool(ws)

    result = await tool.execute(query="needle", max_matches=1, max_line_chars=40)

    assert result.content.endswith("[truncated: more matches not shown]")
    assert result.metadata["matches_returned"] == 1
    assert result.metadata["truncated"] is True


def test_grep_source_does_not_import_host_filesystem_api() -> None:
    source_file = inspect.getsourcefile(GrepTool)
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
