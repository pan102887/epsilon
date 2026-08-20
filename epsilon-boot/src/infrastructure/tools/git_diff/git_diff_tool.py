"""Git diff 工具。"""

from __future__ import annotations

from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.exceptions import WorkspaceConfinementViolation
from domain.workspace.ports import Workspace
from infrastructure.tools._git_runner import run_git

_DEFAULT_MAX_CHARS = 60000
_MAX_CHARS_LIMIT = 500000
_MAX_FILE_PATHS = 50


class GitDiffTool(Tool):
    """读取 Git diff 的只读工具。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_diff"

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
            "Read git diff or staged git diff with optional validated workspace pathspecs. "
            f"Scoped to workspace root {workspace_root}; it does not run arbitrary shell."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "staged": {
                    "type": "boolean",
                    "description": "When true, read staged diff via git diff --cached.",
                    "default": False,
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional workspace-relative POSIX pathspecs, up to 50.",
                    "default": [],
                    "maxItems": _MAX_FILE_PATHS,
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum output characters. Defaults to 60000.",
                    "default": _DEFAULT_MAX_CHARS,
                    "minimum": 1,
                    "maximum": _MAX_CHARS_LIMIT,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 Git diff。

        metadata 键：``operation``、``staged``、``file_count``、``exit_code``、
        ``stdout_bytes``、``stderr_bytes``、``truncated``。
        """
        staged: bool = kwargs.get("staged", False)
        file_paths: list[str] = kwargs.get("file_paths", [])
        max_chars: int = kwargs.get("max_chars", _DEFAULT_MAX_CHARS)
        if len(file_paths) > _MAX_FILE_PATHS:
            raise ToolExecutionError(
                message=f"file_paths 最多包含 {_MAX_FILE_PATHS} 个路径",
                tool_name=self.name,
            )
        if max_chars < 1 or max_chars > _MAX_CHARS_LIMIT:
            raise ToolExecutionError(
                message=f"max_chars 必须在 1..{_MAX_CHARS_LIMIT} 范围内",
                tool_name=self.name,
            )

        args = ["diff"]
        if staged:
            args.append("--cached")
        pathspecs = self._validated_pathspecs(file_paths)
        if pathspecs:
            args.append("--")
            args.extend(pathspecs)

        result = await run_git(
            self._workspace,
            args=args,
            tool_name=self.name,
            max_chars=max_chars,
        )
        return ToolExecutionResult(
            content=result.stdout,
            metadata={
                "operation": "git_diff",
                "staged": staged,
                "file_count": len(pathspecs),
                "exit_code": result.exit_code,
                "stdout_bytes": result.stdout_bytes,
                "stderr_bytes": result.stderr_bytes,
                "truncated": result.truncated,
            },
        )

    def _validated_pathspecs(self, file_paths: list[str]) -> list[str]:
        """校验并转换 workspace-relative pathspec。"""
        pathspecs: list[str] = []
        for file_path in file_paths:
            try:
                ws_path = self._workspace.resolve_path(file_path)
            except WorkspaceConfinementViolation as exc:
                raise ToolExecutionError(
                    message=f"路径 {file_path} 超出工作区边界",
                    tool_name=self.name,
                ) from exc
            pathspec = ws_path.to_posix().lstrip("/") or "."
            pathspecs.append(pathspec)
        return pathspecs
