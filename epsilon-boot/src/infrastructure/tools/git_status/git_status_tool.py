"""Git status 工具。"""

from __future__ import annotations

from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.ports import Workspace
from infrastructure.tools._git_runner import run_git

_DEFAULT_MAX_CHARS = 20000
_MAX_CHARS_LIMIT = 200000


class GitStatusTool(Tool):
    """读取 Git 工作区状态的只读工具。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_status"

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
            "Read repository status using fixed git status arguments. This is a read-only "
            f"tool scoped to workspace root {workspace_root}; it does not run arbitrary shell."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum output characters. Defaults to 20000.",
                    "default": _DEFAULT_MAX_CHARS,
                    "minimum": 1,
                    "maximum": _MAX_CHARS_LIMIT,
                }
            },
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 Git status。

        metadata 键：``operation``、``exit_code``、``stdout_bytes``、
        ``stderr_bytes``、``truncated``。
        """
        max_chars: int = kwargs.get("max_chars", _DEFAULT_MAX_CHARS)
        if max_chars < 1 or max_chars > _MAX_CHARS_LIMIT:
            raise ToolExecutionError(
                message=f"max_chars 必须在 1..{_MAX_CHARS_LIMIT} 范围内",
                tool_name=self.name,
            )
        result = await run_git(
            self._workspace,
            args=["status", "--short", "--branch", "--untracked-files=all"],
            tool_name=self.name,
            max_chars=max_chars,
        )
        return ToolExecutionResult(
            content=result.stdout,
            metadata={
                "operation": "git_status",
                "exit_code": result.exit_code,
                "stdout_bytes": result.stdout_bytes,
                "stderr_bytes": result.stderr_bytes,
                "truncated": result.truncated,
            },
        )
