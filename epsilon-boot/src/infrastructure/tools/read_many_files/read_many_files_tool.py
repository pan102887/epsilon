"""Workspace 批量文件读取工具。"""

from __future__ import annotations

from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.exceptions import (
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.ports import Workspace
from infrastructure.tools._workspace_search import clamp_text, render_file_header
from infrastructure.tools.filesystem._rendering import render_with_line_numbers

_DEFAULT_READ_MANY_FILE_LIMIT = 200
_DEFAULT_READ_MANY_MAX_TOTAL_CHARS = 60000
_MAX_FILE_COUNT = 50
_MAX_FILE_LIMIT = 1000
_MIN_TOTAL_CHARS = 1000
_MAX_TOTAL_CHARS = 200000


class ReadManyFilesTool(Tool):
    """批量读取多个工作区文本文件的只读工具。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "read_many_files"

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
            "Read bounded snippets from multiple workspace text files. Paths are resolved "
            f"relative to workspace root {workspace_root}; each file is rendered with a header."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": _MAX_FILE_COUNT,
                    "description": "Workspace-relative POSIX file paths, up to 50.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based starting line number, inclusive. Defaults to 1.",
                    "default": 1,
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum lines per file. Defaults to 200.",
                    "default": _DEFAULT_READ_MANY_FILE_LIMIT,
                    "minimum": 1,
                    "maximum": _MAX_FILE_LIMIT,
                },
                "max_total_chars": {
                    "type": "integer",
                    "description": "Maximum total characters returned. Defaults to 60000.",
                    "default": _DEFAULT_READ_MANY_MAX_TOTAL_CHARS,
                    "minimum": _MIN_TOTAL_CHARS,
                    "maximum": _MAX_TOTAL_CHARS,
                },
            },
            "required": ["file_paths"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """批量读取文件。

        metadata 键：``operation``、``requested_file_count``、``files_read``、
        ``files_failed``、``total_lines_returned``、``truncated``。
        """
        file_paths: list[str] = kwargs["file_paths"]
        offset: int = kwargs.get("offset", 1)
        limit: int = kwargs.get("limit", _DEFAULT_READ_MANY_FILE_LIMIT)
        max_total_chars: int = kwargs.get("max_total_chars", _DEFAULT_READ_MANY_MAX_TOTAL_CHARS)
        self._validate_args(file_paths, offset, limit, max_total_chars)

        chunks: list[str] = []
        files_read = 0
        files_failed = 0
        total_lines_returned = 0
        truncated = False
        for file_path in file_paths:
            if truncated:
                break
            chunk, ok, line_count = await self._read_one(file_path, offset=offset, limit=limit)
            if ok:
                files_read += 1
                total_lines_returned += line_count
            else:
                files_failed += 1
            candidate_content = "\n\n".join([*chunks, chunk]) if chunks else chunk
            bounded = clamp_text(candidate_content, max_chars=max_total_chars)
            chunks = [bounded.text]
            if bounded.truncated:
                truncated = True

        content = chunks[0] if chunks else ""
        if truncated and not content.endswith("[truncated: more content not shown]"):
            suffix = "\n[truncated: more content not shown]"
            content = clamp_text(content + suffix, max_chars=max_total_chars).text
        return ToolExecutionResult(
            content=content,
            metadata={
                "operation": "read_many_files",
                "requested_file_count": len(file_paths),
                "files_read": files_read,
                "files_failed": files_failed,
                "total_lines_returned": total_lines_returned,
                "truncated": truncated,
            },
        )

    async def _read_one(self, file_path: str, *, offset: int, limit: int) -> tuple[str, bool, int]:
        """读取单个文件并返回渲染块、成功标记与行数。"""
        try:
            ws_path = self._workspace.resolve_path(file_path)
            raw = await self._workspace.read(
                ws_path,
                start_line=offset,
                end_line=offset + limit - 1,
                context={"tool_name": self.name},
            )
            text = raw.decode("utf-8")
        except WorkspaceConfinementViolation:
            header = render_file_header(file_path)
            return f"{header}\n[error] 路径 {file_path} 超出工作区边界", False, 0
        except WorkspaceNotFoundError:
            header = render_file_header(file_path)
            return f"{header}\n[error] 路径 {file_path} 不存在", False, 0
        except (WorkspaceIoError, UnicodeDecodeError):
            header = render_file_header(file_path)
            return f"{header}\n[error] 读取文件 {file_path} 失败", False, 0
        rendered = render_with_line_numbers(text, start_line=offset)
        line_count = len(text.splitlines()) if text else 0
        return f"{render_file_header(ws_path.to_posix())}\n{rendered}", True, line_count

    def _validate_args(
        self,
        file_paths: list[str],
        offset: int,
        limit: int,
        max_total_chars: int,
    ) -> None:
        """校验批量读取输出边界参数。"""
        if not file_paths:
            raise ToolExecutionError(message="file_paths 不能为空", tool_name=self.name)
        if len(file_paths) > _MAX_FILE_COUNT:
            raise ToolExecutionError(
                message=f"file_paths 最多包含 {_MAX_FILE_COUNT} 个路径",
                tool_name=self.name,
            )
        if offset < 1:
            raise ToolExecutionError(
                message=f"offset 必须大于等于 1，当前值：{offset}",
                tool_name=self.name,
            )
        if limit < 1 or limit > _MAX_FILE_LIMIT:
            raise ToolExecutionError(
                message=f"limit 必须在 1..{_MAX_FILE_LIMIT} 范围内",
                tool_name=self.name,
            )
        if max_total_chars < _MIN_TOTAL_CHARS or max_total_chars > _MAX_TOTAL_CHARS:
            raise ToolExecutionError(
                message=f"max_total_chars 必须在 {_MIN_TOTAL_CHARS}..{_MAX_TOTAL_CHARS} 范围内",
                tool_name=self.name,
            )
