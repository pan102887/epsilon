"""ReadFileTool 单元测试。

覆盖 Phase 9.2 契约：

1. happy-path：相对路径 / 绝对 ``/notes.md`` 均读取成功；
2. 越界路径 → ``ToolExecutionError``（文案含"超出工作区边界"、**不含**宿主绝对路径）；
3. ``WorkspaceNotFoundError`` → ``ToolExecutionError``（文案形如
   "路径 /xxx 不存在"）；
4. ``description`` 动态拼接 ``workspace.display_root_hint()``；
5. 源码 AST 扫描：不 import ``os`` / ``pathlib``；
6. ``workspace.read`` 被调用时 ``context`` 关键字参数包含
   ``{"tool_name": "read_file"}``（mock 断言）。

测试使用 :class:`unittest.mock.MagicMock` / :class:`unittest.mock.AsyncMock`
伪造 ``Workspace`` Port，避免引入 ``LocalFilesystemWorkspace`` 实体依赖
（守住 Property 6 红线）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock
from typing import cast

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.value_objects import WorkspacePath
from infrastructure.tools.filesystem.read_file_tool import ReadFileTool


def _make_ws_path(s: str) -> WorkspacePath:
    """构造用于测试的 :class:`WorkspacePath`，绕过 Policy。"""
    return WorkspacePath(_posix=PurePosixPath(s))


def _fake_workspace(*, root_hint: str = "/tmp/ws") -> MagicMock:
    """构造 mock workspace；所有 async I/O 用 AsyncMock。"""
    ws = MagicMock(name="Workspace")
    ws.display_root_hint.return_value = root_hint
    def _resolve(value: str) -> WorkspacePath:
        return _make_ws_path(value if value.startswith("/") else f"/{value}")

    ws.resolve_path.side_effect = _resolve
    ws.read = AsyncMock(return_value=b"")
    return ws


@pytest.mark.asyncio
async def test_happy_path_relative_file_path_returns_rendered_lines() -> None:
    """相对路径读取成功：返回带行号前缀的文本。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(return_value=b"alpha\nbeta\n")
    tool = ReadFileTool(workspace=ws)

    result = await tool.execute(file_path="notes.md", offset=1, limit=10)

    assert "alpha" in result.content
    assert "beta" in result.content
    # 行号前缀：右对齐宽度 4
    assert "   1 |" in result.content
    assert "   2 |" in result.content


@pytest.mark.asyncio
async def test_execute_returns_execution_result_with_metadata() -> None:
    """execute() 返回 ToolExecutionResult，metadata 字段名与类型对齐 design §3.4。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(return_value=b"alpha\nbeta\ngamma\n")
    tool = ReadFileTool(workspace=ws)

    result = await tool.execute(file_path="notes.md", offset=1, limit=10)

    from domain.agent.tools import ToolExecutionResult

    assert isinstance(result, ToolExecutionResult)
    md = result.metadata
    # logical_path 为 workspace 相对 POSIX 路径
    assert md["logical_path"] == "/notes.md"
    assert isinstance(md["logical_path"], str)
    assert md["operation"] == "read"
    # line_range = [offset, offset + limit - 1]
    assert md["line_range"] == [1, 10]
    assert isinstance(md["line_range"], list)
    assert all(isinstance(v, int) for v in cast(list[object], md["line_range"]))
    # 三行内容 → lines_returned == 3
    assert md["lines_returned"] == 3
    assert isinstance(md["lines_returned"], int)
    assert set(md.keys()) == {"logical_path", "operation", "line_range", "lines_returned"}


@pytest.mark.asyncio
async def test_execute_metadata_line_range_reflects_offset_and_limit() -> None:
    """line_range 随 offset/limit 变化：[offset, offset+limit-1]。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(return_value=b"only-one-line")
    tool = ReadFileTool(workspace=ws)

    result = await tool.execute(file_path="a.txt", offset=5, limit=3)

    assert result.metadata["line_range"] == [5, 7]


@pytest.mark.asyncio
async def test_execute_metadata_lines_returned_zero_for_empty_content() -> None:
    """空内容时 lines_returned 为 0，与渲染口径一致。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(return_value=b"")
    tool = ReadFileTool(workspace=ws)

    result = await tool.execute(file_path="empty.txt", offset=1, limit=10)

    assert result.metadata["lines_returned"] == 0


@pytest.mark.asyncio
async def test_happy_path_absolute_file_path_returns_rendered_lines() -> None:
    """绝对 /notes.md 也能被 resolve 并读取。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(return_value=b"hello")
    tool = ReadFileTool(workspace=ws)

    result = await tool.execute(file_path="/notes.md")

    assert "hello" in result.content
    # 验证传给 resolve_path 的字符串保留原 "/"
    ws.resolve_path.assert_called_once_with("/notes.md")


@pytest.mark.asyncio
async def test_out_of_boundary_raises_tool_execution_error_without_host_path() -> None:
    """越界路径 → ToolExecutionError，文案含"超出工作区边界"且不含宿主绝对路径。"""
    ws = _fake_workspace(root_hint="/host/only/root")
    ws.resolve_path.side_effect = WorkspaceConfinementViolation(
        requested_path="../etc/passwd",
        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
    )
    tool = ReadFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="../etc/passwd")

    msg = exc_info.value.message
    assert "超出工作区边界" in msg
    assert "../etc/passwd" in msg
    # 不得泄露宿主绝对路径 display_root_hint 的值
    assert "/host/only/root" not in msg
    assert exc_info.value.tool_name == "read_file"


@pytest.mark.asyncio
async def test_not_found_error_translated_to_tool_execution_error() -> None:
    """WorkspaceNotFoundError → ToolExecutionError：文案"路径 /xxx 不存在"。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(
        side_effect=WorkspaceNotFoundError(
            workspace_path=_make_ws_path("/ghost.md"),
        )
    )
    tool = ReadFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="/ghost.md")

    assert exc_info.value.message == "路径 /ghost.md 不存在"


@pytest.mark.asyncio
async def test_workspace_io_error_translated_to_tool_execution_error() -> None:
    """WorkspaceIoError → ToolExecutionError 泛化文案。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(
        side_effect=WorkspaceIoError(
            operation="read",
            workspace_path=_make_ws_path("/a.bin"),
            reason="decode_failed",
        )
    )
    tool = ReadFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(file_path="/a.bin")

    assert "读取文件" in exc_info.value.message
    assert "/a.bin" in exc_info.value.message
    # 不得泄露内部 reason 字段值（decode_failed 是内部细节）
    # 注意：这个红线相对宽松，但我们避免主动暴露 reason 详情
    assert "decode_failed" not in exc_info.value.message


@pytest.mark.asyncio
async def test_description_contains_display_root_hint() -> None:
    """description 动态包含 workspace.display_root_hint() 的返回值。"""
    ws = _fake_workspace(root_hint="/sandbox/workdir")
    tool = ReadFileTool(workspace=ws)

    assert "/sandbox/workdir" in tool.description
    assert "workspace root" in tool.description


@pytest.mark.asyncio
async def test_execute_passes_context_with_tool_name_to_workspace_read() -> None:
    """mock workspace 验证 read 被调用时 context 含 tool_name=read_file。"""
    ws = _fake_workspace()
    ws.read = AsyncMock(return_value=b"data")
    tool = ReadFileTool(workspace=ws)

    await tool.execute(file_path="a.txt", offset=1, limit=5)

    assert ws.read.await_count == 1
    _, kwargs = ws.read.await_args
    context = kwargs["context"]
    assert context["tool_name"] == "read_file"
    # start_line / end_line 映射正确
    assert kwargs["start_line"] == 1
    assert kwargs["end_line"] == 5


@pytest.mark.asyncio
async def test_invalid_offset_rejected_before_workspace_call() -> None:
    """offset < 1 → ToolExecutionError，且 workspace.read 不被调用。"""
    ws = _fake_workspace()
    tool = ReadFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError):
        await tool.execute(file_path="a.txt", offset=0, limit=10)
    ws.read.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_limit_rejected_before_workspace_call() -> None:
    """limit < 1 → ToolExecutionError。"""
    ws = _fake_workspace()
    tool = ReadFileTool(workspace=ws)

    with pytest.raises(ToolExecutionError):
        await tool.execute(file_path="a.txt", offset=1, limit=0)
    ws.read.assert_not_called()


def test_source_does_not_import_os_or_pathlib() -> None:
    """AST 扫描 ReadFileTool 源码：不得 import os / pathlib / open / common_tools。"""
    source_file = inspect.getsourcefile(ReadFileTool)
    assert source_file is not None
    src_path = Path(source_file)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    banned_modules = {"os", "pathlib", "common.tools.common_tools"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned_modules, f"禁用 import {alias.name}"
        if isinstance(node, ast.ImportFrom):
            assert node.module not in banned_modules, f"禁用 from {node.module}"


def test_tool_name_property_is_read_file() -> None:
    """name 属性为 'read_file'。"""
    tool = ReadFileTool(workspace=_fake_workspace())
    assert tool.name == "read_file"


def test_parameters_schema_has_file_path_required() -> None:
    """parameters 中 file_path 为必填。"""
    tool = ReadFileTool(workspace=_fake_workspace())
    params = tool.parameters
    assert params["type"] == "object"
    assert "file_path" in params["required"]
    assert "file_path" in params["properties"]
