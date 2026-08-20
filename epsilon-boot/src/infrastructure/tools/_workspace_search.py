"""Workspace 代码检索工具共享 helper。"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import StrEnum

from domain.agent.exceptions import ToolExecutionError
from domain.workspace.exceptions import WorkspaceConfinementViolation
from domain.workspace.ports import Workspace
from domain.workspace.value_objects import WorkspacePath


class SearchMode(StrEnum):
    """内容搜索模式。"""

    LITERAL = "literal"
    REGEX = "regex"


@dataclass(frozen=True, slots=True)
class SearchFileCandidate:
    """可被代码检索工具读取的文件候选。"""

    path: WorkspacePath
    posix_path: str
    size: int | None


@dataclass(frozen=True, slots=True)
class BoundedText:
    """受输出上限约束后的文本片段。"""

    text: str
    truncated: bool


_WINDOWS_DRIVE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:")


def validate_workspace_pattern(pattern: str, *, field_name: str) -> None:
    """校验 POSIX pattern 不含 Workspace 越界段或非法字符。"""
    if "\x00" in pattern:
        raise ToolExecutionError(
            message=f"{field_name} 包含非法 NUL 字符",
            tool_name="workspace_search",
        )
    if "\\" in pattern:
        raise ToolExecutionError(
            message=f"{field_name} 必须使用 POSIX / 分隔符",
            tool_name="workspace_search",
        )
    if _WINDOWS_DRIVE_RE.match(pattern):
        raise ToolExecutionError(
            message=f"{field_name} 不允许使用 Windows 盘符",
            tool_name="workspace_search",
        )
    parts = [part for part in pattern.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ToolExecutionError(
            message=f"{field_name} 不允许包含 .. 路径段",
            tool_name="workspace_search",
        )


def pattern_matches(posix_path: str, pattern: str) -> bool:
    """判断 Workspace POSIX 路径是否匹配 glob 风格 pattern。"""
    normalized_pattern = pattern if pattern.startswith("/") else f"/{pattern}"
    if fnmatch.fnmatchcase(posix_path, normalized_pattern):
        return True
    relative_path = posix_path.removeprefix("/")
    relative_pattern = pattern.removeprefix("/")
    if fnmatch.fnmatchcase(relative_path, relative_pattern):
        return True
    if relative_pattern.startswith("**/"):
        return fnmatch.fnmatchcase(relative_path, relative_pattern.removeprefix("**/"))
    return False


async def list_file_candidates(
    workspace: Workspace,
    *,
    directory_path: str,
    include_pattern: str,
    max_files: int,
    context: dict[str, object],
) -> tuple[list[SearchFileCandidate], bool]:
    """列出目录下匹配 include_pattern 的文件候选。"""
    validate_workspace_pattern(include_pattern, field_name="include_pattern")
    try:
        root = workspace.resolve_path(directory_path)
        entries = await workspace.list_dir(root, recursive=True, context=context)
    except WorkspaceConfinementViolation as exc:
        raise ToolExecutionError(
            message=f"路径 {directory_path} 超出工作区边界",
            tool_name="workspace_search",
        ) from exc

    candidates: list[SearchFileCandidate] = []
    truncated = False
    for entry in sorted(entries, key=lambda item: item.path.to_posix()):
        if not entry.is_file:
            continue
        posix_path = entry.path.to_posix()
        if not pattern_matches(posix_path, include_pattern):
            continue
        if len(candidates) >= max_files:
            truncated = True
            break
        candidates.append(
            SearchFileCandidate(
                path=entry.path,
                posix_path=posix_path,
                size=entry.size,
            )
        )
    return candidates, truncated


def clamp_text(text: str, *, max_chars: int) -> BoundedText:
    """按字符上限截断文本，并返回是否截断。"""
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    if len(text) <= max_chars:
        return BoundedText(text=text, truncated=False)
    return BoundedText(text=text[:max_chars], truncated=True)


def render_file_header(posix_path: str) -> str:
    """生成批量读取输出中的文件标题行。"""
    return f"===== {posix_path} ====="
