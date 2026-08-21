"""WriteFileTool 单元测试。

覆盖 Phase 9.4 契约：

1. 成功消息包含 ``WorkspacePath`` 逻辑路径（需求 7.4），**不含**宿主绝对路径；
2. 越界路径 → ``ToolExecutionError``；
3. ``WorkspaceIoError`` → ``ToolExecutionError``；
4. ``description`` 动态拼接 ``display_root_hint()``；
5. 源码 AST 扫描不 import ``os`` / ``pathlib``；
6. ``workspace.write`` 被调用时 ``context["tool_name"] == "write_file"``。
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
from infrastructure.tools.filesystem.write_file_tool import WriteFileTool


def _make_ws_path(s: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(s))


def _fake_workspace(*, root_hint: str = "/tmp/ws") -> MagicMock:
    ws = MagicMock(name="Workspace")
    ws.display_root_hint.return_value = root_hint
    def _resolve(value: str) -> WorkspacePath:
        return _make_ws_path(value if value.startswith("/") else f"/{value}")

    ws.resolve_path.side_effect = _resolve
    ws.write = AsyncMock(return_value=0)
    return ws


@pytest.mark.asyncio
async def test_happy_path_returns_logical_workspace_path_in_message() -> None:
    """成功消息使用 WorkspacePath 逻辑形式，不含宿主绝对路径。"""
    ws = _fake_workspace(root_hint="/host/absolute/root")
    ws.write = AsyncMock(return_value=11)
    tool = WriteFileTool(workspace=ws)

    result = await tool.execute(file_path="nested/greeting.txt", content="hello world")

    assert result.content == "成功写入文件 /nested/greeting.txt，共 11 字节"
    # 关键红线：消息中不得出现宿主绝对路径
    assert "/host/absolute/root" not in result.content
    assert result.metadata["logical_path"] == "/nested/greeting.txt"
    assert result.metadata["operation"] == "write"
    assert result.metadata["bytes_written"] == 11


@pytest.mark.asyncio
async def test_execute_passes_context_with_tool_name_to_workspace_write() -> None:
    """mock 验证 write 调用时 context["tool_name"] == "write_file"。"""
    ws = _fake_workspace()
    ws.write = AsyncMock(return_value=3)
    tool = WriteFileTool(workspace=ws)

    await tool.execute(file_path="a.txt", content="abc")

    assert ws.write.await_count == 1
    args, kwargs = ws.write.await_args
    # content bytes 通过位置参数传递
    assert args[1] == b"abc"
    assert kwargs["context"]["tool_name"] == "write_file"


@pytest.mark.asyncio
async def test_out_of_boundary_raises_tool_execution_error() -> None:
    """越界 → ToolExecutionError（文案含"超出工作区边界"）。"""
    ws = _fake_workspace()
    ws.resolve_path.side_effect = WorkspaceConfinementViolation(
        requested_path="../escape",
        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
    )
    tool = WriteFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="../escape", content="x")

    assert "超出工作区边界" in exc_info.value.message
    assert exc_info.value.tool_name == "write_file"


@pytest.mark.asyncio
async def test_workspace_io_error_translated() -> None:
    """WorkspaceIoError → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.write = AsyncMock(
        side_effect=WorkspaceIoError(
            operation="write",
            workspace_path=_make_ws_path("/a.txt"),
            reason="permission_denied",
        )
    )
    tool = WriteFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="a.txt", content="x")

    assert "写入文件" in exc_info.value.message
    assert "a.txt" in exc_info.value.message


@pytest.mark.asyncio
async def test_workspace_not_found_error_translated() -> None:
    """WorkspaceNotFoundError → ToolExecutionError。"""
    ws = _fake_workspace()
    ws.write = AsyncMock(
        side_effect=WorkspaceNotFoundError(
            workspace_path=_make_ws_path("/missing/a.txt"),
        )
    )
    tool = WriteFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="missing/a.txt", content="x")

    assert "不存在" in exc_info.value.message


@pytest.mark.asyncio
async def test_description_contains_display_root_hint() -> None:
    """description 含 display_root_hint() 返回值。"""
    ws = _fake_workspace(root_hint="/sandbox/workdir")
    tool = WriteFileTool(workspace=ws)
    assert "/sandbox/workdir" in tool.description
    assert "workspace root" in tool.description


def test_source_does_not_import_os_or_pathlib() -> None:
    """AST 扫描：不得 import os / pathlib / common_tools。"""
    source_file = inspect.getsourcefile(WriteFileTool)
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
    tool = WriteFileTool(workspace=_fake_workspace())
    assert tool.name == "write_file"
    params = tool.parameters
    assert params["type"] == "object"
    assert "file_path" in params["required"]
    assert "content" in params["required"]


@pytest.mark.asyncio
async def test_content_is_encoded_as_utf8_bytes() -> None:
    """content str 被 encode("utf-8") 后传给 workspace.write。"""
    ws = _fake_workspace()
    ws.write = AsyncMock(return_value=6)
    tool = WriteFileTool(workspace=ws)

    await tool.execute(file_path="a.txt", content="中文")

    args, _ = ws.write.await_args
    assert args[1] == "中文".encode()
