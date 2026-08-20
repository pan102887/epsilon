from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.value_objects import WorkspacePath, WorkspaceStatEntry
from infrastructure.tools._workspace_search import (
    clamp_text,
    list_file_candidates,
    pattern_matches,
    render_file_header,
    validate_workspace_pattern,
)


def _path(value: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(value))


def _entry(value: str, *, is_file: bool = True) -> WorkspaceStatEntry:
    return WorkspaceStatEntry(
        path=_path(value),
        is_file=is_file,
        is_dir=not is_file,
        size=1,
        mtime=None,
    )


def test_validate_workspace_pattern_accepts_normal_patterns() -> None:
    validate_workspace_pattern("**/*.py", field_name="pattern")
    validate_workspace_pattern("/src/**/*.py", field_name="pattern")


@pytest.mark.parametrize("pattern", ["../x.py", "src/../x.py", "a\\b.py", "a\x00b", "C:/x.py"])
def test_validate_workspace_pattern_rejects_unsafe_patterns(pattern: str) -> None:
    with pytest.raises(ToolExecutionError):
        validate_workspace_pattern(pattern, field_name="pattern")


def test_pattern_matches_globs_exact_paths_and_case_sensitive() -> None:
    assert pattern_matches("/src/app.py", "**/*.py")
    assert pattern_matches("/src/app.py", "/src/app.py")
    assert not pattern_matches("/src/app.py", "**/*.md")
    assert not pattern_matches("/src/App.py", "**/app.py")


def test_clamp_text_handles_untruncated_truncated_and_boundary() -> None:
    assert clamp_text("abc", max_chars=3).text == "abc"
    assert clamp_text("abc", max_chars=3).truncated is False
    truncated = clamp_text("abcd", max_chars=3)
    assert truncated.text == "abc"
    assert truncated.truncated is True
    assert clamp_text("abc", max_chars=0).text == ""


@pytest.mark.asyncio
async def test_list_file_candidates_uses_workspace_and_filters_sorted() -> None:
    ws = MagicMock()
    ws.resolve_path.return_value = _path("/")
    ws.list_dir = AsyncMock(
        return_value=[
            _entry("/src/b.py"),
            _entry("/src/a.md"),
            _entry("/src/a.py"),
            _entry("/src/pkg", is_file=False),
        ]
    )

    candidates, truncated = await list_file_candidates(
        ws,
        directory_path="/",
        include_pattern="**/*.py",
        max_files=10,
        context={"tool_name": "test"},
    )

    assert [candidate.posix_path for candidate in candidates] == ["/src/a.py", "/src/b.py"]
    assert truncated is False
    ws.resolve_path.assert_called_once_with("/")
    ws.list_dir.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_file_candidates_truncates() -> None:
    ws = MagicMock()
    ws.resolve_path.return_value = _path("/")
    ws.list_dir = AsyncMock(return_value=[_entry("/a.py"), _entry("/b.py")])

    candidates, truncated = await list_file_candidates(
        ws,
        directory_path="/",
        include_pattern="**/*.py",
        max_files=1,
        context={"tool_name": "test"},
    )

    assert [candidate.posix_path for candidate in candidates] == ["/a.py"]
    assert truncated is True


def test_render_file_header() -> None:
    assert render_file_header("/src/a.py") == "===== /src/a.py ====="
