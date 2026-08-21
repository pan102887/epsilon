"""ListDirTool 单元测试。

覆盖 Phase 9.8 契约：

1. 空串 / ``"."`` / ``"/"`` 均映射到工作区根（需求 6.4 / 7.2）；
2. 嵌套目录返回多行文本；每行路径以 ``/`` 起始；
3. 返回文本**不含**宿主绝对路径（需求 7.4）；
4. mock 验证 ``context["tool_name"] == "list_dir"``；
5. 源码 AST 扫描不 import ``os`` / ``pathlib``。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.value_objects import WorkspacePath, WorkspaceStatEntry
from infrastructure.tools.filesystem.list_dir_tool import ListDirTool


def _make_ws_path(s: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(s))


def _fake_workspace(*, root_hint: str = "/tmp/ws") -> MagicMock:
    ws = MagicMock(name="Workspace")
    ws.display_root_hint.return_value = root_hint
    def _resolve(value: str) -> WorkspacePath:
        return _make_ws_path(value if value.startswith("/") else f"/{value}")

    ws.resolve_path.side_effect = _resolve
    ws.list_dir = AsyncMock(return_value=[])
    return ws


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", ".", "/"])
async def test_empty_dot_and_slash_all_map_to_workspace_root(raw: str) -> None:
    """空串 / "." / "/" 都被归一化为 "/" 后传给 resolve_path。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(return_value=[])
    tool = ListDirTool(workspace=ws)

    await tool.execute(directory_path=raw)

    ws.resolve_path.assert_called_once_with("/")


@pytest.mark.asyncio
async def test_nested_directories_returned_in_posix_form() -> None:
    """嵌套目录：返回多行文本，每行以 / 起始；目录带 / 后缀。"""
    ws = _fake_workspace(root_hint="/host/secret/root")
    ws.list_dir = AsyncMock(
        return_value=[
            WorkspaceStatEntry(
                path=_make_ws_path("/sub"),
                is_file=False,
                is_dir=True,
                size=None,
                mtime=None,
            ),
            WorkspaceStatEntry(
                path=_make_ws_path("/sub/a.txt"),
                is_file=True,
                is_dir=False,
                size=10,
                mtime=1.0,
            ),
            WorkspaceStatEntry(
                path=_make_ws_path("/b.md"),
                is_file=True,
                is_dir=False,
                size=5,
                mtime=1.0,
            ),
        ]
    )
    tool = ListDirTool(workspace=ws)

    result = await tool.execute(directory_path="/")

    # 每行独立检查
    lines = result.content.split("\n")
    # 字典序排序后：/b.md, /sub/, /sub/a.txt
    assert "/b.md" in lines
    assert "/sub/" in lines
    assert "/sub/a.txt" in lines
    # 所有条目均以 "/" 起始
    for line in lines:
        assert line.startswith("/")
    # 绝不含宿主绝对路径
    assert "/host/secret/root" not in result.content
    assert result.metadata["logical_path"] == "/"
    assert result.metadata["operation"] == "list"
    assert result.metadata["entries_count"] == len(lines)
    # recursive 默认为 True（design §3.7），类型为 bool。
    assert result.metadata["recursive"] is True
    assert set(result.metadata.keys()) == {
        "logical_path",
        "operation",
        "recursive",
        "entries_count",
    }


@pytest.mark.asyncio
async def test_execute_metadata_recursive_false_when_disabled() -> None:
    """显式传入 recursive=False 时 metadata.recursive 为 False。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(return_value=[])
    tool = ListDirTool(workspace=ws)

    result = await tool.execute(directory_path="/", recursive=False)

    assert result.metadata["recursive"] is False


@pytest.mark.asyncio
async def test_execute_passes_context_with_tool_name() -> None:
    """mock 验证 list_dir 调用时 context["tool_name"] == "list_dir"。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(return_value=[])
    tool = ListDirTool(workspace=ws)

    await tool.execute(directory_path="")

    assert ws.list_dir.await_count == 1
    _, kwargs = ws.list_dir.await_args
    assert kwargs["context"]["tool_name"] == "list_dir"
    # recursive 默认 True
    assert kwargs["recursive"] is True


@pytest.mark.asyncio
async def test_out_of_boundary_raises_tool_execution_error() -> None:
    """越界 → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.resolve_path.side_effect = WorkspaceConfinementViolation(
        requested_path="../",
        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
    )
    tool = ListDirTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(directory_path="../")

    assert "超出工作区边界" in exc_info.value.message


@pytest.mark.asyncio
async def test_not_found_translated() -> None:
    """WorkspaceNotFoundError → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(
        side_effect=WorkspaceNotFoundError(workspace_path=_make_ws_path("/ghost"))
    )
    tool = ListDirTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(directory_path="ghost")

    assert exc_info.value.message == "路径 ghost 不存在"


@pytest.mark.asyncio
async def test_io_error_translated() -> None:
    """WorkspaceIoError → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(
        side_effect=WorkspaceIoError(
            operation="list_dir",
            workspace_path=_make_ws_path("/a"),
            reason="not_a_directory",
        )
    )
    tool = ListDirTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(directory_path="a")

    assert "列举目录" in exc_info.value.message
    assert "a" in exc_info.value.message


@pytest.mark.asyncio
async def test_recursive_false_passthrough() -> None:
    """recursive=False 被透传给 workspace.list_dir。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(return_value=[])
    tool = ListDirTool(workspace=ws)

    await tool.execute(directory_path="/", recursive=False)

    _, kwargs = ws.list_dir.await_args
    assert kwargs["recursive"] is False


@pytest.mark.asyncio
async def test_empty_entries_return_empty_string() -> None:
    """空目录返回空串。"""
    ws = _fake_workspace()
    ws.list_dir = AsyncMock(return_value=[])
    tool = ListDirTool(workspace=ws)

    result = await tool.execute(directory_path="/")

    assert result.content == ""
    assert result.metadata["entries_count"] == 0


def test_description_contains_display_root_hint() -> None:
    """description 含 display_root_hint() 返回值。"""
    ws = _fake_workspace(root_hint="/sandbox/workdir")
    tool = ListDirTool(workspace=ws)
    assert "/sandbox/workdir" in tool.description


def test_source_does_not_import_os_or_pathlib() -> None:
    """AST 扫描 ListDirTool 源码：不得 import os / pathlib / common_tools。"""
    source_file = inspect.getsourcefile(ListDirTool)
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


def test_name_and_parameters() -> None:
    """name 与 parameters 基础结构。"""
    tool = ListDirTool(workspace=_fake_workspace())
    assert tool.name == "list_dir"
    params = tool.parameters
    assert params["type"] == "object"
