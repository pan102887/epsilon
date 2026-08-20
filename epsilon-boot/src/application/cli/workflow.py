"""Coding workflow 命令的只读快照与 trace 提取辅助。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from domain.agent.trace_value_objects import SessionTrace, ToolCallTrace

WorkflowFileGroup = Literal["read", "write", "execute", "other"]

_TEST_PATTERNS = (
    "pytest",
    "ruff",
    "pyright",
    "mypy",
    "bun run",
    "npm run",
    "pnpm",
    "yarn",
    "uv run",
    "cargo test",
    "go test",
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WRITE_OPERATIONS = frozenset({"write_file", "edit_file", "git_apply_patch"})
_READ_OPERATIONS = frozenset(
    {"read_file", "read_many_files", "list_dir", "glob", "grep", "git_status", "git_diff"}
)
_EXECUTE_OPERATIONS = frozenset({"shell_exec", "python_exec"})


@dataclass(frozen=True, slots=True)
class CodingStatusSnapshot:
    """`/status` 命令展示的运行时状态快照。"""

    session_id: str
    model: str
    workspace: str
    pending_approval_count: int
    trace_step_count: int
    latest_trace_kind: str | None


@dataclass(frozen=True, slots=True)
class CodingDiffSnapshot:
    """`/diff` 命令展示的 Git diff 快照。"""

    content: str
    available: bool
    truncated: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowTestRecord:
    """从 trace 中提取的一条测试/验证命令记录。"""

    command: str
    tool_name: str
    success: bool
    exit_code: int | None
    result_summary: str


@dataclass(frozen=True, slots=True)
class CodingTestsSnapshot:
    """`/tests` 命令展示的最近测试记录快照。"""

    records: tuple[WorkflowTestRecord, ...] = ()
    trace_available: bool = False


@dataclass(frozen=True, slots=True)
class CodingFilesSnapshot:
    """`/files` 命令展示的会话文件触达快照。"""

    groups: dict[WorkflowFileGroup, tuple[str, ...]] = field(default_factory=dict)
    trace_available: bool = False


def extract_test_records(trace: SessionTrace | None, *, limit: int = 5) -> CodingTestsSnapshot:
    """从 session trace 中提取最近的测试/验证类工具调用。

    Args:
        trace: 当前会话的完整结构化 trace；为 None 时表示 trace 不可用。
        limit: 最多返回的最近记录数。

    Returns:
        包含测试记录与 trace 可用性的快照。
    """
    if trace is None:
        return CodingTestsSnapshot(trace_available=False)

    records: list[WorkflowTestRecord] = []
    for step in reversed(trace.steps):
        if not isinstance(step, ToolCallTrace):
            continue
        command = _command_summary(step)
        if not command or not _looks_like_test_command(command):
            continue
        records.append(
            WorkflowTestRecord(
                command=command,
                tool_name=step.tool_name,
                success=step.success,
                exit_code=_int_metadata(step.metadata.get("exit_code")),
                result_summary=step.result_summary,
            )
        )
        if len(records) >= limit:
            break
    return CodingTestsSnapshot(records=tuple(records), trace_available=True)


def extract_file_snapshot(
    trace: SessionTrace | None,
    *,
    limit_per_group: int = 30,
) -> CodingFilesSnapshot:
    """从 session trace 中提取工作区逻辑文件清单。

    Args:
        trace: 当前会话的完整结构化 trace；为 None 时表示 trace 不可用。
        limit_per_group: 每个分组最多保留的路径数量。

    Returns:
        按 read/write/execute/other 分组的文件清单快照。
    """
    if trace is None:
        return CodingFilesSnapshot(trace_available=False)

    grouped: dict[WorkflowFileGroup, list[str]] = {
        "read": [],
        "write": [],
        "execute": [],
        "other": [],
    }
    seen: set[tuple[WorkflowFileGroup, str]] = set()
    for step in trace.steps:
        if not isinstance(step, ToolCallTrace):
            continue
        group = _file_group(step)
        for path in _metadata_paths(step.metadata):
            key = (group, path)
            if key in seen:
                continue
            seen.add(key)
            if len(grouped[group]) < limit_per_group:
                grouped[group].append(path)

    return CodingFilesSnapshot(
        groups={name: tuple(paths) for name, paths in grouped.items() if paths},
        trace_available=True,
    )


def latest_trace_kind(trace: SessionTrace | None) -> str | None:
    """返回 trace 最新一步类型；无 trace 或无步骤时返回 None。"""
    if trace is None or not trace.steps:
        return None
    return trace.steps[-1].kind


def _command_summary(step: ToolCallTrace) -> str:
    """从工具 trace 中提取命令/代码摘要。"""
    for key in ("command_summary", "code_summary"):
        value = step.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return step.arguments_summary.strip()


def _looks_like_test_command(command: str) -> bool:
    lower = command.lower()
    return any(pattern in lower for pattern in _TEST_PATTERNS)


def _int_metadata(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _file_group(step: ToolCallTrace) -> WorkflowFileGroup:
    operation = step.metadata.get("operation")
    operation_name = operation if isinstance(operation, str) else step.tool_name
    if operation_name in _WRITE_OPERATIONS or step.tool_name in _WRITE_OPERATIONS:
        return "write"
    if operation_name in _READ_OPERATIONS or step.tool_name in _READ_OPERATIONS:
        return "read"
    if operation_name in _EXECUTE_OPERATIONS or step.tool_name in _EXECUTE_OPERATIONS:
        return "execute"
    return "other"


def _metadata_paths(metadata: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    for key in ("logical_path", "file_path", "path", "working_dir"):
        value = metadata.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for key in ("file_paths", "paths"):
        value = metadata.get(key)
        if isinstance(value, list | tuple):
            candidates.extend(item for item in value if isinstance(item, str))
    return tuple(path for path in (_sanitize_path(item) for item in candidates) if path)


def _sanitize_path(path: str) -> str | None:
    normalized = path.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or _WINDOWS_DRIVE.match(normalized):
        return None
    if normalized in {".", "./"}:
        return "."
    return normalized.lstrip("./")
