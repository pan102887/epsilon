"""Git 工具共享执行器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.exceptions import (
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.ports import LocallyMaterializable, Workspace

_TRUNCATED_MARKER = "\n[truncated: git output not shown]"


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    """Git 子进程执行结果。"""

    stdout: str
    stderr: str
    exit_code: int
    stdout_bytes: int
    stderr_bytes: int
    truncated: bool


def materialize_git_cwd(workspace: Workspace, *, tool_name: str) -> str:
    """校验 Workspace 本地物化能力并返回 Git cwd。"""
    caps = workspace.capabilities()
    if not caps.local_materialization or not isinstance(workspace, LocallyMaterializable):
        raise ToolExecutionError(
            message="当前工作区后端不支持 Git 工具",
            tool_name=tool_name,
        )
    try:
        root = workspace.resolve_path("/")
        return workspace.materialize_cwd(root)
    except WorkspaceConfinementViolation as exc:
        raise ToolExecutionError(
            message="工作区根路径超出边界",
            tool_name=tool_name,
        ) from exc
    except (WorkspaceNotFoundError, WorkspaceIoError) as exc:
        raise ToolExecutionError(
            message="工作区根路径不可用",
            tool_name=tool_name,
        ) from exc


async def run_git(
    workspace: Workspace,
    *,
    args: list[str],
    tool_name: str,
    input_text: str | None = None,
    max_chars: int,
    timeout_seconds: int = 30,
) -> GitCommandResult:
    """执行固定 Git 参数数组并返回有界输出。"""
    host_cwd = materialize_git_cwd(workspace, tool_name=tool_name)
    input_bytes = input_text.encode("utf-8") if input_text is not None else None
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=host_cwd,
        )
        stdout_bytes, stderr_bytes = await _communicate_with_timeout(
            process,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
            tool_name=tool_name,
        )
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(
            message="Git 命令执行失败",
            tool_name=tool_name,
        ) from exc

    result = _build_result(
        process_returncode=process.returncode,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        max_chars=max_chars,
    )
    _raise_on_nonzero_exit(result, tool_name=tool_name)
    return result


async def _communicate_with_timeout(
    process: asyncio.subprocess.Process,
    *,
    input_bytes: bytes | None,
    timeout_seconds: int,
    tool_name: str,
) -> tuple[bytes, bytes]:
    """等待 Git 子进程完成，超时则终止进程。"""
    try:
        return await asyncio.wait_for(
            process.communicate(input_bytes),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise ToolExecutionError(
            message=f"Git 命令执行超时（{timeout_seconds} 秒）",
            tool_name=tool_name,
        ) from exc


def _build_result(
    *,
    process_returncode: int | None,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
    max_chars: int,
) -> GitCommandResult:
    """解码并截断 Git 输出，生成结果值对象。"""
    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    stdout_text, stderr_text, truncated = _clamp_streams(
        stdout_text,
        stderr_text,
        max_chars=max_chars,
    )
    return GitCommandResult(
        stdout=stdout_text,
        stderr=stderr_text,
        exit_code=process_returncode if process_returncode is not None else -1,
        stdout_bytes=len(stdout_bytes) if stdout_bytes else 0,
        stderr_bytes=len(stderr_bytes) if stderr_bytes else 0,
        truncated=truncated,
    )


def _raise_on_nonzero_exit(result: GitCommandResult, *, tool_name: str) -> None:
    """Git 非零退出码统一翻译为工具执行错误。"""
    if result.exit_code == 0:
        return
    detail = result.stderr or result.stdout
    raise ToolExecutionError(
        message=f"Git 命令执行失败（exit_code={result.exit_code}）\n{detail}",
        tool_name=tool_name,
    )


def _clamp_streams(stdout: str, stderr: str, *, max_chars: int) -> tuple[str, str, bool]:
    """按总字符数限制 stdout/stderr。"""
    combined = stdout + stderr
    if len(combined) <= max_chars:
        return stdout, stderr, False
    clipped = combined[:max_chars]
    return f"{clipped}{_TRUNCATED_MARKER}", "", True
