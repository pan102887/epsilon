"""Workspace POSIX glob 路径检索工具。"""

from __future__ import annotations

from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.ports import Workspace
from infrastructure.tools._workspace_search import (
    clamp_text,
    list_file_candidates,
    validate_workspace_pattern,
)

_SUMMARY_MAX_LEN = 128
_DEFAULT_GLOB_MAX_RESULTS = 200
_MAX_GLOB_RESULTS = 1000


class GlobTool(Tool):
    """按 POSIX glob pattern 查找工作区文件路径的只读工具。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "glob"

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
            "Find files in the workspace by POSIX glob pattern. Paths are resolved relative "
            f"to workspace root {workspace_root}; returns sorted workspace-relative POSIX paths."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Workspace-relative POSIX glob pattern, for example **/*.py.",
                },
                "directory_path": {
                    "type": "string",
                    "description": "Workspace-relative directory to scan. Defaults to /.",
                    "default": "/",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of paths to return. Defaults to 200.",
                    "default": _DEFAULT_GLOB_MAX_RESULTS,
                    "minimum": 1,
                    "maximum": _MAX_GLOB_RESULTS,
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行路径模式匹配。

        metadata 键：
        - ``operation`` (str): 固定为 ``"glob"``。
        - ``pattern`` (str): 截断后的 pattern 摘要。
        - ``directory_path`` (str): 扫描目录入参。
        - ``match_count`` (int): 返回的匹配路径数。
        - ``truncated`` (bool): 是否因上限截断。
        """
        pattern: str = kwargs["pattern"]
        directory_path: str = kwargs.get("directory_path", "/")
        max_results: int = kwargs.get("max_results", _DEFAULT_GLOB_MAX_RESULTS)
        if max_results < 1 or max_results > _MAX_GLOB_RESULTS:
            raise ToolExecutionError(
                message=f"max_results 必须在 1..{_MAX_GLOB_RESULTS} 范围内",
                tool_name=self.name,
            )
        validate_workspace_pattern(pattern, field_name="pattern")
        candidates, truncated = await list_file_candidates(
            self._workspace,
            directory_path=directory_path,
            include_pattern=pattern,
            max_files=max_results,
            context={"tool_name": self.name},
        )
        lines = [candidate.posix_path for candidate in candidates]
        if truncated:
            lines.append("[truncated: more paths not shown]")
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata={
                "operation": "glob",
                "pattern": clamp_text(pattern, max_chars=_SUMMARY_MAX_LEN).text,
                "directory_path": directory_path,
                "match_count": len(candidates),
                "truncated": truncated,
            },
        )
