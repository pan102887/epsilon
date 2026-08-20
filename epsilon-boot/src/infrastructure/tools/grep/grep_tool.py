"""Workspace 文本检索工具。"""

from __future__ import annotations

import re
from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.exceptions import WorkspaceIoError, WorkspaceNotFoundError
from domain.workspace.ports import Workspace
from infrastructure.tools._workspace_search import (
    SearchMode,
    clamp_text,
    list_file_candidates,
)

_SUMMARY_MAX_LEN = 128
_DEFAULT_GREP_MAX_MATCHES = 100
_DEFAULT_GREP_MAX_FILES = 2000
_DEFAULT_GREP_MAX_LINE_CHARS = 300
_MAX_GREP_MATCHES = 1000
_MAX_GREP_FILES = 10000
_MIN_LINE_CHARS = 40
_MAX_LINE_CHARS = 1000


class GrepTool(Tool):
    """在工作区文本文件中执行关键词或正则搜索的只读工具。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "grep"

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.LOW

    @property
    def side_effect_level(self) -> ToolSideEffectLevel:
        return ToolSideEffectLevel.NONE

    @property
    def replay_policy(self) -> ToolReplayPolicy:
        return ToolReplayPolicy.REPLAY_RESULT

    @property
    def description(self) -> str:
        workspace_root = self._workspace.display_root_hint()
        return (
            "Search text in workspace files using literal or regex mode. Paths are resolved "
            f"relative to workspace root {workspace_root}; returns path:line: preview matches."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text or regex pattern to search for."},
                "mode": {
                    "type": "string",
                    "enum": [SearchMode.LITERAL.value, SearchMode.REGEX.value],
                    "description": "Search mode: literal or regex. Defaults to literal.",
                    "default": SearchMode.LITERAL.value,
                },
                "directory_path": {
                    "type": "string",
                    "description": "Workspace-relative directory to scan. Defaults to /.",
                    "default": "/",
                },
                "include_pattern": {
                    "type": "string",
                    "description": "Workspace-relative POSIX file pattern. Defaults to **/*.",
                    "default": "**/*",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether matching is case-sensitive. Defaults to true.",
                    "default": True,
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Maximum number of matches to return. Defaults to 100.",
                    "default": _DEFAULT_GREP_MAX_MATCHES,
                    "minimum": 1,
                    "maximum": _MAX_GREP_MATCHES,
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum number of files to scan. Defaults to 2000.",
                    "default": _DEFAULT_GREP_MAX_FILES,
                    "minimum": 1,
                    "maximum": _MAX_GREP_FILES,
                },
                "max_line_chars": {
                    "type": "integer",
                    "description": "Maximum characters in each line preview. Defaults to 300.",
                    "default": _DEFAULT_GREP_MAX_LINE_CHARS,
                    "minimum": _MIN_LINE_CHARS,
                    "maximum": _MAX_LINE_CHARS,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行内容搜索。

        metadata 键：``operation``、``query``、``mode``、``directory_path``、
        ``include_pattern``、``files_scanned``、``files_skipped``、
        ``matches_returned``、``truncated``。
        """
        query: str = kwargs["query"]
        mode = SearchMode(kwargs.get("mode", SearchMode.LITERAL.value))
        directory_path: str = kwargs.get("directory_path", "/")
        include_pattern: str = kwargs.get("include_pattern", "**/*")
        case_sensitive: bool = kwargs.get("case_sensitive", True)
        max_matches: int = kwargs.get("max_matches", _DEFAULT_GREP_MAX_MATCHES)
        max_files: int = kwargs.get("max_files", _DEFAULT_GREP_MAX_FILES)
        max_line_chars: int = kwargs.get("max_line_chars", _DEFAULT_GREP_MAX_LINE_CHARS)
        self._validate_limits(max_matches, max_files, max_line_chars)

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(query, flags=flags) if mode is SearchMode.REGEX else None
        except re.error as exc:
            raise ToolExecutionError(message="正则表达式非法", tool_name=self.name) from exc
        literal_query = query if case_sensitive else query.lower()

        candidates, candidate_truncated = await list_file_candidates(
            self._workspace,
            directory_path=directory_path,
            include_pattern=include_pattern,
            max_files=max_files,
            context={"tool_name": self.name},
        )

        lines: list[str] = []
        matches_returned = 0
        files_scanned = 0
        files_skipped = 0
        truncated = candidate_truncated
        for candidate in candidates:
            if matches_returned >= max_matches:
                truncated = True
                break
            try:
                raw = await self._workspace.read(candidate.path, context={"tool_name": self.name})
                text = raw.decode("utf-8")
            except (UnicodeDecodeError, WorkspaceIoError, WorkspaceNotFoundError):
                files_skipped += 1
                continue
            files_scanned += 1
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                matched = (
                    regex.search(line) is not None
                    if regex is not None
                    else literal_query in haystack
                )
                if not matched:
                    continue
                preview = clamp_text(line, max_chars=max_line_chars)
                lines.append(f"{candidate.posix_path}:{line_number}: {preview.text}")
                matches_returned += 1
                if preview.truncated:
                    truncated = True
                if matches_returned >= max_matches:
                    truncated = True
                    break
        if truncated:
            lines.append("[truncated: more matches not shown]")
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata={
                "operation": "grep",
                "query": clamp_text(query, max_chars=_SUMMARY_MAX_LEN).text,
                "mode": mode.value,
                "directory_path": directory_path,
                "include_pattern": clamp_text(include_pattern, max_chars=_SUMMARY_MAX_LEN).text,
                "files_scanned": files_scanned,
                "files_skipped": files_skipped,
                "matches_returned": matches_returned,
                "truncated": truncated,
            },
        )

    def _validate_limits(self, max_matches: int, max_files: int, max_line_chars: int) -> None:
        """校验 grep 输出边界参数。"""
        if max_matches < 1 or max_matches > _MAX_GREP_MATCHES:
            raise ToolExecutionError(
                message=f"max_matches 必须在 1..{_MAX_GREP_MATCHES} 范围内",
                tool_name=self.name,
            )
        if max_files < 1 or max_files > _MAX_GREP_FILES:
            raise ToolExecutionError(
                message=f"max_files 必须在 1..{_MAX_GREP_FILES} 范围内",
                tool_name=self.name,
            )
        if max_line_chars < _MIN_LINE_CHARS or max_line_chars > _MAX_LINE_CHARS:
            raise ToolExecutionError(
                message=f"max_line_chars 必须在 {_MIN_LINE_CHARS}..{_MAX_LINE_CHARS} 范围内",
                tool_name=self.name,
            )
