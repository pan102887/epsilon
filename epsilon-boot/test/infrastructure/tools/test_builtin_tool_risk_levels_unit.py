"""内置工具风险等级单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from domain.agent.guardrails import ToolRiskLevel
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel
from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool
from infrastructure.tools.filesystem.edit_file_tool import EditFileTool
from infrastructure.tools.filesystem.list_dir_tool import ListDirTool
from infrastructure.tools.filesystem.read_file_tool import ReadFileTool
from infrastructure.tools.filesystem.write_file_tool import WriteFileTool
from infrastructure.tools.git_apply_patch import GitApplyPatchTool
from infrastructure.tools.git_diff import GitDiffTool
from infrastructure.tools.git_status import GitStatusTool
from infrastructure.tools.glob import GlobTool
from infrastructure.tools.grep import GrepTool
from infrastructure.tools.http_request.http_request_tool import HttpRequestTool
from infrastructure.tools.python_exec.python_exec_tool import PythonExecTool
from infrastructure.tools.read_many_files import ReadManyFilesTool
from infrastructure.tools.shell_exec.shell_exec_tool import ShellExecTool
from infrastructure.tools.web_fetch.web_fetch_tool import WebFetchTool
from infrastructure.tools.web_search.web_search_tool import WebSearchTool


def test_builtin_tool_risk_levels_are_declared() -> None:
    workspace = MagicMock()

    assert ReadFileTool(workspace).risk_level is ToolRiskLevel.LOW
    assert ListDirTool(workspace).risk_level is ToolRiskLevel.LOW
    assert GlobTool(workspace).risk_level is ToolRiskLevel.LOW
    assert GrepTool(workspace).risk_level is ToolRiskLevel.LOW
    assert ReadManyFilesTool(workspace).risk_level is ToolRiskLevel.LOW
    assert GitStatusTool(workspace).risk_level is ToolRiskLevel.LOW
    assert GitDiffTool(workspace).risk_level is ToolRiskLevel.LOW
    assert WebFetchTool().risk_level is ToolRiskLevel.LOW
    assert WebSearchTool(MagicMock()).risk_level is ToolRiskLevel.LOW

    assert DelegateToAgentTool(MagicMock(), MagicMock()).risk_level is ToolRiskLevel.MEDIUM

    assert WriteFileTool(workspace).risk_level is ToolRiskLevel.HIGH
    assert EditFileTool(workspace).risk_level is ToolRiskLevel.HIGH
    assert GitApplyPatchTool(workspace).risk_level is ToolRiskLevel.HIGH
    assert HttpRequestTool().risk_level is ToolRiskLevel.HIGH

    assert ShellExecTool(workspace).risk_level is ToolRiskLevel.CRITICAL
    assert PythonExecTool(workspace).risk_level is ToolRiskLevel.CRITICAL


def test_code_search_tools_recovery_semantics_are_declared() -> None:
    workspace = MagicMock()

    for tool in (GlobTool(workspace), GrepTool(workspace), ReadManyFilesTool(workspace)):
        assert tool.side_effect_level is ToolSideEffectLevel.NONE
        assert tool.replay_policy is ToolReplayPolicy.REPLAY_RESULT


def test_git_tools_recovery_semantics_are_declared() -> None:
    workspace = MagicMock()

    for tool in (GitStatusTool(workspace), GitDiffTool(workspace)):
        assert tool.side_effect_level is ToolSideEffectLevel.NONE
        assert tool.replay_policy is ToolReplayPolicy.REPLAY_RESULT

    apply_tool = GitApplyPatchTool(workspace)
    assert apply_tool.side_effect_level is ToolSideEffectLevel.LOCAL_WRITE
    assert apply_tool.replay_policy is ToolReplayPolicy.MANUAL_REVIEW
