"""PythonExecTool 单元测试（Phase 11.2 契约）。

覆盖要点：

1. ``Workspace.capabilities().local_materialization=False`` → 立即
   ``ToolExecutionError``；
2. 子进程 ``cwd`` 等于 ``workspace.materialize_cwd(resolve_path("/"))``
   返回值；
3. AST 黑名单保持不变（``analyze_code`` / ``BLOCKED_CALLS`` 未受 Workspace
   影响，需求 6.10）；
4. ``description`` 动态拼接 ``workspace.display_root_hint()``；
5. Phase 10/11 Property 6：工具源不 import ``LocalFilesystemWorkspace``。

测试通过 mock :class:`Workspace` Port 与 mock ``asyncio.create_subprocess_exec``
避免真实启动子进程；临时文件使用 ``tmp_path`` 作为 ``host_cwd`` 方向的
隔离位置。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.value_objects import WorkspaceCapabilities, WorkspacePath
from infrastructure.tools.python_exec.python_exec_tool import (
    BLOCKED_CALLS,
    PythonExecTool,
    analyze_code,
)


def _ws_path(s: str) -> WorkspacePath:
    """构造 :class:`WorkspacePath`，绕过 Policy 便于测试。"""
    return WorkspacePath(_posix=PurePosixPath(s))


def _fake_workspace(
    *,
    root_hint: str = "/tmp/ws",
    local_materialization: bool = True,
    materialize_return: str = "/tmp/ws",
) -> MagicMock:
    """构造 mock Workspace。"""
    ws = MagicMock(name="Workspace")
    ws.display_root_hint.return_value = root_hint
    ws.capabilities.return_value = WorkspaceCapabilities(
        supports_symlinks=False,
        supports_atomic_write=True,
        supports_append=True,
        supports_streaming=False,
        supports_large_files=True,
        local_materialization=local_materialization,
    )

    def resolve_path(value: str) -> WorkspacePath:
        return _ws_path(value if value.startswith("/") else f"/{value}")

    ws.resolve_path.side_effect = resolve_path
    ws.materialize_cwd = MagicMock(return_value=materialize_return)
    return ws


def _fake_subprocess(*, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    """伪造 asyncio subprocess 进程对象。"""
    proc = MagicMock(name="Process")
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# 1. local_materialization 守卫
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_when_local_materialization_false() -> None:
    """``local_materialization=False`` 时 PythonExecTool 应立即拒绝。"""
    ws = _fake_workspace(local_materialization=False)
    tool = PythonExecTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(code="print('hi')")

    assert "不支持本地命令执行" in exc_info.value.message
    assert exc_info.value.tool_name == "python_exec"
    # 守卫应阻断 materialize_cwd 调用
    ws.materialize_cwd.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 子进程 cwd 正确性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subprocess_cwd_equals_materialize_cwd_of_root(tmp_path: Path) -> None:
    """子进程 ``cwd`` 严格等于 ``workspace.materialize_cwd(resolve_path("/"))``。"""
    ws = _fake_workspace(materialize_return=str(tmp_path))
    tool = PythonExecTool(workspace=ws)

    fake_exec = AsyncMock(return_value=_fake_subprocess(stdout=b"hi"))
    with patch(
        "infrastructure.tools.python_exec.python_exec_tool.asyncio.create_subprocess_exec",
        new=fake_exec,
    ):
        await tool.execute(code="print('hi')")

    # resolve_path 入参是 "/"
    ws.resolve_path.assert_called_once_with("/")
    ws.materialize_cwd.assert_called_once()
    # create_subprocess_exec 接收的 cwd 与 materialize_cwd 返回值一致
    assert fake_exec.call_args.kwargs["cwd"] == str(tmp_path)


# ---------------------------------------------------------------------------
# 6. ToolExecutionResult metadata 契约（structured-tool-result 需求 3.2）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_execution_result_with_metadata(tmp_path: Path) -> None:
    """execute() 返回 ToolExecutionResult，metadata 字段名与类型对齐 design §3.3。"""
    ws = _fake_workspace(materialize_return=str(tmp_path))
    tool = PythonExecTool(workspace=ws)

    code = "print('hello world')"
    fake_exec = AsyncMock(
        return_value=_fake_subprocess(stdout=b"hello world\n", stderr=b"", returncode=0)
    )
    with patch(
        "infrastructure.tools.python_exec.python_exec_tool.asyncio.create_subprocess_exec",
        new=fake_exec,
    ):
        result = await tool.execute(code=code)

    from domain.agent.tools import ToolExecutionResult

    assert isinstance(result, ToolExecutionResult)
    # content 仍为格式化文本（回灌 LLM 语义不变）
    assert "Exit Code: 0" in result.content
    assert "hello world" in result.content

    md = result.metadata
    assert md["code_summary"] == code[:128]
    assert isinstance(md["code_summary"], str)
    assert md["exit_code"] == 0
    assert isinstance(md["exit_code"], int)
    # stdout_bytes 为截断前原始字节数
    assert md["stdout_bytes"] == len(b"hello world\n")
    assert isinstance(md["stdout_bytes"], int)
    assert md["stderr_bytes"] == 0
    assert isinstance(md["stderr_bytes"], int)
    assert isinstance(md["memory_limited"], bool)
    assert md["truncated"] is False
    # metadata 键集合与设计文档严格一致
    assert set(md.keys()) == {
        "code_summary",
        "exit_code",
        "stdout_bytes",
        "stderr_bytes",
        "memory_limited",
        "truncated",
    }


@pytest.mark.asyncio
async def test_execute_metadata_truncated_true_when_output_exceeds_limit(
    tmp_path: Path,
) -> None:
    """输出超过 max_output_size 时 metadata.truncated 为 True。"""
    ws = _fake_workspace(materialize_return=str(tmp_path))
    tool = PythonExecTool(workspace=ws, max_output_size=8)

    big_stdout = b"0123456789ABCDEF"  # 16 字节 > max_output_size=8
    fake_exec = AsyncMock(
        return_value=_fake_subprocess(stdout=big_stdout, stderr=b"", returncode=0)
    )
    with patch(
        "infrastructure.tools.python_exec.python_exec_tool.asyncio.create_subprocess_exec",
        new=fake_exec,
    ):
        result = await tool.execute(code="print('x' * 100)")

    assert result.metadata["truncated"] is True
    # 截断标志不影响原始字节数记录
    assert result.metadata["stdout_bytes"] == len(big_stdout)


# ---------------------------------------------------------------------------
# 3. AST 黑名单保持不变（需求 6.10）
# ---------------------------------------------------------------------------


def test_blocked_calls_contains_expected_functions() -> None:
    """``BLOCKED_CALLS`` 应包含既有的危险函数集合。"""
    expected = {
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "open",
        "breakpoint",
        "exit",
        "quit",
    }
    assert expected.issubset(BLOCKED_CALLS)


def test_analyze_code_rejects_open_call() -> None:
    """``open`` 调用应被 ``analyze_code`` 拒绝。"""
    result = analyze_code("open('/etc/passwd').read()")
    assert result.ok is False
    assert "open" in result.reason


def test_analyze_code_rejects_exec_call() -> None:
    """``exec`` 调用应被拒绝。"""
    result = analyze_code("exec('x = 1')")
    assert result.ok is False
    assert "exec" in result.reason


def test_analyze_code_rejects_disallowed_module() -> None:
    """导入不在白名单的模块应被拒绝。"""
    result = analyze_code("import subprocess")
    assert result.ok is False
    assert "subprocess" in result.reason


def test_analyze_code_accepts_allowed_standard_library() -> None:
    """允许白名单中的模块。"""
    result = analyze_code("import math\nprint(math.sqrt(4))")
    assert result.ok is True


@pytest.mark.asyncio
async def test_execute_rejects_dangerous_code_before_workspace_guard() -> None:
    """AST 检查应先于 Workspace 守卫触发（AST 是独立于 Workspace 的）。"""
    ws = _fake_workspace()
    tool = PythonExecTool(workspace=ws)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(code="exec('bad')")

    assert "代码安全检查未通过" in exc_info.value.message
    # AST 拒绝发生时不应触达 Workspace.capabilities
    ws.capabilities.assert_not_called()


# ---------------------------------------------------------------------------
# 4. description 动态拼接
# ---------------------------------------------------------------------------


def test_description_contains_display_root_hint() -> None:
    ws = _fake_workspace(root_hint="/tmp/custom-root")
    tool = PythonExecTool(workspace=ws)

    desc = tool.description

    assert "/tmp/custom-root" in desc
    assert "POSIX" in desc


def test_description_is_dynamic_on_each_access() -> None:
    ws = _fake_workspace()
    tool = PythonExecTool(workspace=ws)

    _ = tool.description
    _ = tool.description

    assert ws.display_root_hint.call_count == 2


# ---------------------------------------------------------------------------
# 5. 构造签名契约
# ---------------------------------------------------------------------------


def test_construct_requires_workspace() -> None:
    """构造 :class:`PythonExecTool` 必须传入 ``workspace`` 参数。"""
    with pytest.raises(TypeError):
        PythonExecTool()  # type: ignore[call-arg]


def test_construct_does_not_accept_working_dir() -> None:
    """Phase 11 改造后，``working_dir`` 不再是 ``PythonExecTool`` 的构造参数
    （子进程 cwd 由 Workspace 托管）。"""
    ws = _fake_workspace()
    with pytest.raises(TypeError):
        PythonExecTool(workspace=ws, working_dir="/tmp")  # type: ignore[call-arg]
