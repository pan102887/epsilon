from __future__ import annotations

import ast
import inspect
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)
from domain.workspace.value_objects import WorkspaceCapabilities, WorkspacePath
from infrastructure.tools._git_runner import run_git
from infrastructure.tools.git_apply_patch import GitApplyPatchTool
from infrastructure.tools.git_diff import GitDiffTool
from infrastructure.tools.git_status import GitStatusTool

_SUBPROCESS_PATCH_TARGET = (
    "infrastructure.tools._git_runner.asyncio.create_subprocess_exec"
)


def _path(value: str) -> WorkspacePath:
    return WorkspacePath(_posix=PurePosixPath(value))


def _resolve_path(value: str) -> WorkspacePath:
    return _path(value if value.startswith("/") else f"/{value}")


def _workspace(*, local_materialization: bool = True) -> MagicMock:
    ws = MagicMock(name="Workspace")
    ws.display_root_hint.return_value = "/tmp/ws"
    ws.capabilities.return_value = WorkspaceCapabilities(
        local_materialization=local_materialization,
    )
    ws.resolve_path.side_effect = _resolve_path
    ws.materialize_cwd = MagicMock(return_value="/tmp/ws")
    return ws


def _process(*, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock(name="Process")
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_git_status_uses_fixed_status_args_and_metadata() -> None:
    ws = _workspace()
    proc = _process(stdout=b"## main\n M a.py\n")
    fake_exec = AsyncMock(return_value=proc)

    with patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec):
        result = await GitStatusTool(ws).execute()

    fake_exec.assert_awaited_once()
    exec_args = fake_exec.await_args
    assert exec_args is not None
    assert exec_args.args == (
        "git",
        "status",
        "--short",
        "--branch",
        "--untracked-files=all",
    )
    assert exec_args.kwargs["cwd"] == "/tmp/ws"
    assert result.content == "## main\n M a.py\n"
    assert result.metadata == {
        "operation": "git_status",
        "exit_code": 0,
        "stdout_bytes": len(b"## main\n M a.py\n"),
        "stderr_bytes": 0,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_git_diff_supports_staged_and_validated_pathspecs() -> None:
    ws = _workspace()
    proc = _process(stdout=b"diff --git a/src/a.py b/src/a.py\n")
    fake_exec = AsyncMock(return_value=proc)

    with patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec):
        result = await GitDiffTool(ws).execute(
            staged=True,
            file_paths=["src/a.py", "/README.md"],
        )

    exec_args = fake_exec.await_args
    assert exec_args is not None
    assert exec_args.args == (
        "git",
        "diff",
        "--cached",
        "--",
        "src/a.py",
        "README.md",
    )
    assert result.metadata["operation"] == "git_diff"
    assert result.metadata["staged"] is True
    assert result.metadata["file_count"] == 2


@pytest.mark.asyncio
async def test_git_diff_rejects_out_of_boundary_path_before_subprocess() -> None:
    ws = _workspace()
    ws.resolve_path.side_effect = WorkspaceConfinementViolation(
        requested_path="../x",
        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
    )
    fake_exec = AsyncMock(return_value=_process())

    with (
        patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec),
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await GitDiffTool(ws).execute(file_paths=["../x"])

    assert "超出工作区边界" in exc_info.value.message
    fake_exec.assert_not_called()


@pytest.mark.asyncio
async def test_git_apply_patch_passes_patch_on_stdin_and_supports_check_only() -> None:
    ws = _workspace()
    proc = _process(stdout=b"")
    fake_exec = AsyncMock(return_value=proc)
    patch_text = "diff --git a/a.txt b/a.txt\n"

    with patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec):
        result = await GitApplyPatchTool(ws).execute(patch=patch_text, check_only=True)

    exec_args = fake_exec.await_args
    assert exec_args is not None
    assert exec_args.args == ("git", "apply", "--check", "-")
    assert exec_args.kwargs["stdin"] is not None
    assert proc.communicate.await_args.args == (patch_text.encode("utf-8"),)
    assert result.content == "Patch applies cleanly."
    assert result.metadata["operation"] == "git_apply_patch"
    assert result.metadata["check_only"] is True
    assert result.metadata["patch_bytes"] == len(patch_text.encode("utf-8"))


@pytest.mark.asyncio
async def test_git_apply_patch_apply_mode_uses_fixed_apply_args() -> None:
    ws = _workspace()
    proc = _process(stdout=b"")
    fake_exec = AsyncMock(return_value=proc)

    with patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec):
        result = await GitApplyPatchTool(ws).execute(patch="diff --git a/a b/a\n")

    exec_args = fake_exec.await_args
    assert exec_args is not None
    assert exec_args.args == ("git", "apply", "-")
    assert result.content == "Patch applied."
    assert result.metadata["check_only"] is False


@pytest.mark.asyncio
async def test_git_runner_rejects_non_local_workspace_before_subprocess() -> None:
    ws = _workspace(local_materialization=False)
    fake_exec = AsyncMock(return_value=_process())

    with (
        patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec),
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await run_git(ws, args=["status"], tool_name="git_status", max_chars=1000)

    assert "不支持 Git 工具" in exc_info.value.message
    fake_exec.assert_not_called()


@pytest.mark.asyncio
async def test_git_runner_truncates_output_and_raises_on_nonzero() -> None:
    ws = _workspace()
    proc = _process(stdout=b"abcdef", stderr=b"", returncode=0)
    fake_exec = AsyncMock(return_value=proc)

    with patch(_SUBPROCESS_PATCH_TARGET, new=fake_exec):
        result = await run_git(ws, args=["status"], tool_name="git_status", max_chars=3)

    assert result.stdout.startswith("abc")
    assert result.truncated is True

    failed = _process(stdout=b"", stderr=b"fatal: not a git repo", returncode=128)
    with (
        patch(_SUBPROCESS_PATCH_TARGET, new=AsyncMock(return_value=failed)),
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await run_git(ws, args=["status"], tool_name="git_status", max_chars=1000)
    assert "exit_code=128" in exc_info.value.message
    assert "/tmp/ws" not in exc_info.value.message


def test_git_tool_risk_and_recovery_semantics() -> None:
    ws = _workspace()

    for tool in (GitStatusTool(ws), GitDiffTool(ws)):
        assert tool.risk_level is ToolRiskLevel.LOW
        assert tool.side_effect_level is ToolSideEffectLevel.NONE
        assert tool.replay_policy is ToolReplayPolicy.REPLAY_RESULT

    apply_tool = GitApplyPatchTool(ws)
    assert apply_tool.risk_level is ToolRiskLevel.HIGH
    assert apply_tool.side_effect_level is ToolSideEffectLevel.LOCAL_WRITE
    assert apply_tool.replay_policy is ToolReplayPolicy.MANUAL_REVIEW


def test_git_sources_do_not_use_shell_or_arbitrary_command_entrypoints() -> None:
    for cls in (GitStatusTool, GitDiffTool, GitApplyPatchTool):
        source_file = inspect.getsourcefile(cls)
        assert source_file is not None
        tree = ast.parse(Path(source_file).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword):
                assert not (node.arg == "shell" and isinstance(node.value, ast.Constant))
