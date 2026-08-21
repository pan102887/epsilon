"""EditFileTool 单元测试。

覆盖 Phase 9.6 契约：

1. 成功（精确或模糊匹配）：返回 "成功编辑文件 {ws_path}，共 N 字节"；
2. ``WorkspaceIoError(reason="no_match")`` → 专属文案 "未在文件 {file_path} 中找到匹配文本"；
3. ``WorkspaceIoError(reason="lock_failed")`` → 专属文案 "文件 {file_path} 锁获取失败，请稍后重试"；
4. ``old_str == ""`` → 工具层直接拒绝（workspace.edit 不被调用）；
5. 越界 → ``ToolExecutionError``；
6. mock 验证 ``context["tool_name"] == "edit_file"``；
7. 源码 AST 扫描不 import ``os`` / ``pathlib``。
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
from domain.workspace.value_objects import WorkspacePath
from infrastructure.tools.filesystem.edit_file_tool import EditFileTool


def _make_ws_path(s: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(s))


def _fake_workspace(*, root_hint: str = "/tmp/ws") -> MagicMock:
    ws = MagicMock(name="Workspace")
    ws.display_root_hint.return_value = root_hint
    def _resolve(value: str) -> WorkspacePath:
        return _make_ws_path(value if value.startswith("/") else f"/{value}")

    ws.resolve_path.side_effect = _resolve
    ws.edit = AsyncMock(return_value=0)
    return ws


@pytest.mark.asyncio
async def test_happy_path_success_message_uses_workspace_path() -> None:
    """成功消息使用 WorkspacePath 逻辑形式。"""
    ws = _fake_workspace(root_hint="/host/secret")
    ws.edit = AsyncMock(return_value=42)
    tool = EditFileTool(workspace=ws)

    result = await tool.execute(file_path="a.txt", old_str="foo", new_str="bar")

    assert result.content == "成功编辑文件 /a.txt，共 42 字节"
    assert "/host/secret" not in result.content
    assert result.metadata["logical_path"] == "/a.txt"
    assert result.metadata["operation"] == "edit"
    assert result.metadata["bytes_written"] == 42


@pytest.mark.asyncio
async def test_fuzzy_match_success_returns_same_format() -> None:
    """模糊匹配成功同样返回字节数；工具层对精确/模糊分支无感。"""
    ws = _fake_workspace()
    ws.edit = AsyncMock(return_value=30)
    tool = EditFileTool(workspace=ws)

    result = await tool.execute(
        file_path="a.py",
        old_str="  def foo():\n    pass",
        new_str="def foo():\n    return 1",
    )

    assert result.content == "成功编辑文件 /a.py，共 30 字节"


@pytest.mark.asyncio
async def test_no_match_raises_dedicated_message() -> None:
    """WorkspaceIoError(reason="no_match") → 专属文案。"""
    ws = _fake_workspace()
    ws.edit = AsyncMock(
        side_effect=WorkspaceIoError(
            operation="edit",
            workspace_path=_make_ws_path("/a.txt"),
            reason="no_match",
        )
    )
    tool = EditFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="a.txt", old_str="x", new_str="y")

    assert exc_info.value.message == "未在文件 a.txt 中找到匹配文本"


@pytest.mark.asyncio
async def test_lock_failed_raises_dedicated_message() -> None:
    """WorkspaceIoError(reason="lock_failed") → 专属文案。"""
    ws = _fake_workspace()
    ws.edit = AsyncMock(
        side_effect=WorkspaceIoError(
            operation="edit",
            workspace_path=_make_ws_path("/a.txt"),
            reason="lock_failed",
        )
    )
    tool = EditFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="a.txt", old_str="x", new_str="y")

    assert exc_info.value.message == "文件 a.txt 锁获取失败，请稍后重试"


@pytest.mark.asyncio
async def test_empty_old_str_rejected_without_calling_workspace() -> None:
    """old_str == "" → 拒绝，且 workspace.edit 未被调用。"""
    ws = _fake_workspace()
    tool = EditFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="a.txt", old_str="", new_str="y")

    assert "old_str" in exc_info.value.message
    ws.edit.assert_not_called()


@pytest.mark.asyncio
async def test_out_of_boundary_raises_tool_execution_error() -> None:
    """越界 → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.resolve_path.side_effect = WorkspaceConfinementViolation(
        requested_path="/etc/passwd",
        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
    )
    tool = EditFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="/etc/passwd", old_str="root", new_str="nobody")

    assert "超出工作区边界" in exc_info.value.message


@pytest.mark.asyncio
async def test_not_found_translated() -> None:
    """WorkspaceNotFoundError → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.edit = AsyncMock(
        side_effect=WorkspaceNotFoundError(workspace_path=_make_ws_path("/ghost.txt"))
    )
    tool = EditFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="ghost.txt", old_str="x", new_str="y")

    assert exc_info.value.message == "路径 ghost.txt 不存在"


@pytest.mark.asyncio
async def test_execute_passes_context_with_tool_name() -> None:
    """mock 验证 edit 调用时 context["tool_name"] == "edit_file"。"""
    ws = _fake_workspace()
    ws.edit = AsyncMock(return_value=5)
    tool = EditFileTool(workspace=ws)

    await tool.execute(file_path="a.txt", old_str="foo", new_str="bar")

    assert ws.edit.await_count == 1
    args, kwargs = ws.edit.await_args
    # old_str / new_str 以 bytes 形式传递
    assert args[1] == b"foo"
    assert args[2] == b"bar"
    assert kwargs["context"]["tool_name"] == "edit_file"


def test_source_does_not_import_os_or_pathlib() -> None:
    """AST 扫描 EditFileTool 源码：不得 import os / pathlib / common_tools。"""
    source_file = inspect.getsourcefile(EditFileTool)
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


def test_description_contains_display_root_hint() -> None:
    """description 含 display_root_hint() 返回值。"""
    ws = _fake_workspace(root_hint="/sandbox/workdir")
    tool = EditFileTool(workspace=ws)
    assert "/sandbox/workdir" in tool.description


def test_name_and_parameters() -> None:
    """name 与 parameters 基础结构。"""
    tool = EditFileTool(workspace=_fake_workspace())
    assert tool.name == "edit_file"
    params = tool.parameters
    assert set(params["required"]) == {"file_path", "old_str", "new_str"}
