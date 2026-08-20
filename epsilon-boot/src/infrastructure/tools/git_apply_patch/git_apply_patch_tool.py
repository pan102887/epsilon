"""Git apply patch 工具。"""

from __future__ import annotations

from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from domain.workspace.ports import Workspace
from infrastructure.tools._git_runner import run_git

_DEFAULT_MAX_OUTPUT_CHARS = 20000
_MAX_OUTPUT_CHARS_LIMIT = 200000
_MAX_PATCH_CHARS = 2_000_000


class GitApplyPatchTool(Tool):
    """通过 Git apply 应用 unified diff 的受控写工具。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_apply_patch"

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.HIGH

    @property
    def side_effect_level(self) -> ToolSideEffectLevel:
        return ToolSideEffectLevel.LOCAL_WRITE

    @property
    def replay_policy(self) -> ToolReplayPolicy:
        return ToolReplayPolicy.MANUAL_REVIEW

    @property
    def description(self) -> str:
        workspace_root = self._workspace.display_root_hint()
        return (
            "Apply a unified diff using fixed git apply arguments. Use check_only=true to "
            "validate without writing. This tool may modify workspace files under "
            f"{workspace_root}; it does not run arbitrary shell."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "Unified diff patch text to pass to git apply via stdin.",
                    "maxLength": _MAX_PATCH_CHARS,
                },
                "check_only": {
                    "type": "boolean",
                    "description": "Validate the patch with git apply --check without writing.",
                    "default": False,
                },
                "max_output_chars": {
                    "type": "integer",
                    "description": "Maximum stdout/stderr summary characters. Defaults to 20000.",
                    "default": _DEFAULT_MAX_OUTPUT_CHARS,
                    "minimum": 1,
                    "maximum": _MAX_OUTPUT_CHARS_LIMIT,
                },
            },
            "required": ["patch"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 Git apply。

        metadata 键：``operation``、``check_only``、``exit_code``、``patch_bytes``、
        ``stdout_bytes``、``stderr_bytes``、``truncated``。
        """
        patch: str = kwargs["patch"]
        check_only: bool = kwargs.get("check_only", False)
        max_output_chars: int = kwargs.get("max_output_chars", _DEFAULT_MAX_OUTPUT_CHARS)
        if not patch:
            raise ToolExecutionError(message="patch 不能为空", tool_name=self.name)
        if len(patch) > _MAX_PATCH_CHARS:
            raise ToolExecutionError(
                message=f"patch 不能超过 {_MAX_PATCH_CHARS} 字符",
                tool_name=self.name,
            )
        if max_output_chars < 1 or max_output_chars > _MAX_OUTPUT_CHARS_LIMIT:
            raise ToolExecutionError(
                message=f"max_output_chars 必须在 1..{_MAX_OUTPUT_CHARS_LIMIT} 范围内",
                tool_name=self.name,
            )

        args = ["apply"]
        if check_only:
            args.append("--check")
        args.append("-")
        result = await run_git(
            self._workspace,
            args=args,
            tool_name=self.name,
            input_text=patch,
            max_chars=max_output_chars,
        )
        fallback = "Patch applies cleanly." if check_only else "Patch applied."
        content = (result.stdout + result.stderr).strip() or fallback
        return ToolExecutionResult(
            content=content,
            metadata={
                "operation": "git_apply_patch",
                "check_only": check_only,
                "exit_code": result.exit_code,
                "patch_bytes": len(patch.encode("utf-8")),
                "stdout_bytes": result.stdout_bytes,
                "stderr_bytes": result.stderr_bytes,
                "truncated": result.truncated,
            },
        )
