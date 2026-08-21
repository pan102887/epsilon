from __future__ import annotations

import ast
import inspect
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.value_objects import WorkspacePath, WorkspaceStatEntry
from infrastructure.tools.glob.glob_tool import GlobTool


def _path(value: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(value))


def _resolve_path(value: str) -> WorkspacePath:
    return _path(value if value.startswith("/") else f"/{value}")


def _entry(value: str, *, is_file: bool = True) -> WorkspaceStatEntry:
    return WorkspaceStatEntry(
        path=_path(value),
        is_file=is_file,
        is_dir=not is_file,
        size=1,
        mtime=None,
    )


def _workspace() -> MagicMock:
    ws = MagicMock()
    ws.display_root_hint.return_value = "/tmp/ws"
    ws.resolve_path.side_effect = _resolve_path
    ws.list_dir = AsyncMock(return_value=[])
    return ws


@pytest.mark.asyncio
async def test_glob_returns_sorted_matches_and_metadata() -> None:
    ws = _workspace()
    ws.list_dir = AsyncMock(return_value=[_entry("/b.py"), _entry("/a.py"), _entry("/README.md")])
    tool = GlobTool(ws)

    result = await tool.execute(pattern="**/*.py")

    assert result.content == "/a.py\n/b.py"
    assert set(result.metadata) == {
        "operation",
        "pattern",
        "directory_path",
        "match_count",
        "truncated",
    }
    assert result.metadata == {
        "operation": "glob",
        "pattern": "**/*.py",
        "directory_path": "/",
        "match_count": 2,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_glob_empty_result_returns_empty_string() -> None:
    tool = GlobTool(_workspace())

    result = await tool.execute(pattern="**/*.py")

    assert result.content == ""
    assert result.metadata["match_count"] == 0


@pytest.mark.asyncio
async def test_glob_truncates_max_results() -> None:
    ws = _workspace()
    ws.list_dir = AsyncMock(return_value=[_entry("/a.py"), _entry("/b.py")])
    tool = GlobTool(ws)

    result = await tool.execute(pattern="**/*.py", max_results=1)

    assert result.content == "/a.py\n[truncated: more paths not shown]"
    assert result.metadata["match_count"] == 1
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_glob_rejects_unsafe_pattern_before_listing() -> None:
    ws = _workspace()
    tool = GlobTool(ws)

    with pytest.raises(ToolExecutionError):
        await tool.execute(pattern="../*.py")

    ws.list_dir.assert_not_called()


def test_glob_description_schema_and_risk_semantics() -> None:
    tool = GlobTool(_workspace())

    assert tool.name == "glob"
    assert tool.risk_level is ToolRiskLevel.LOW
    assert tool.side_effect_level is ToolSideEffectLevel.NONE
    assert tool.replay_policy is ToolReplayPolicy.REPLAY_RESULT
    assert "/tmp/ws" in tool.description
    assert "workspace" in tool.description.lower()
    assert set(tool.parameters["properties"]) == {"pattern", "directory_path", "max_results"}


def test_glob_source_does_not_import_host_filesystem_api() -> None:
    source_file = inspect.getsourcefile(GlobTool)
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
