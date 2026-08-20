"""受控文件编辑工具模块。

提供 :class:`EditFileTool` 实现，继承自 :class:`domain.agent.tools.Tool`
抽象基类，为 LLM Agent 提供在 ``Workspace`` 边界内的受控文件"首个匹配替换"
能力。

两阶段匹配（精确匹配 → 行级去空白模糊回退）完全由 ``Workspace`` 后端实现
（见 ``infrastructure/workspace/`` 下的 ``_common_impl`` 字节级适配）；本
工具层仅负责入参 encode、``old_str==""`` 拒绝与错误翻译（含
``WorkspaceIoError`` 两类细分 ``reason`` 的专属文案）。

依赖白名单（守住需求 6.1 / Property 6）：
    ``typing`` / ``domain.agent.*`` / ``domain.workspace.*`` /
    ``infrastructure.tools.filesystem._context``。
    **禁止** import ``os`` / ``pathlib`` / ``open`` /
    具体后端实现类。
"""

from __future__ import annotations

from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.workspace.exceptions import (
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.ports import Workspace
from infrastructure.tools.filesystem._context import (
    _current_agent_id_or_none,
    _current_trace_id_or_none,
)


class EditFileTool(Tool):
    """受控文件编辑工具，首个匹配替换 + 模糊回退。

    作为基础设施层适配器，将 ``Workspace`` Port 的 ``edit`` 方法适配为
    ``Tool`` 抽象接口。可注册到 ``ToolRegistry`` 供 LLM Agent 调用。

    匹配策略（后端实现）：

    1. 精确字节匹配：在字节流中查找 ``old_str.encode("utf-8")`` 的首次出现；
    2. 行级去空白回退：若精确匹配失败且文件可 UTF-8 decode，逐行去
       前后空白后比较（仅当 ``old_str`` 含多行时有效）。

    错误处理策略：

    - ``old_str == ""`` 由工具层直接拒绝（不走后端）；
    - ``WorkspaceConfinementViolation`` → ``ToolExecutionError``；
    - ``WorkspaceNotFoundError``        → ``ToolExecutionError``；
    - ``WorkspaceIoError(reason="no_match")`` → 专属文案
      "未在文件 {file_path} 中找到匹配文本"；
    - ``WorkspaceIoError(reason="lock_failed")`` → 专属文案
      "文件 {file_path} 锁获取失败，请稍后重试"；
    - 其他 ``WorkspaceIoError`` → 泛化文案 "编辑文件 {file_path} 失败"。
    """

    def __init__(self, workspace: Workspace) -> None:
        """初始化受控文件编辑工具。

        Args:
            workspace: 注入的 ``Workspace`` Port 实例；工具通过该实例
                完成路径解析、首个匹配替换与原子回写，本身不访问宿主文件系统。
        """
        self._workspace: Workspace = workspace

    @property
    def name(self) -> str:
        """返回工具名称 ``"edit_file"``。"""
        return "edit_file"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """文件编辑工具为高风险。"""
        return ToolRiskLevel.HIGH

    @property
    def description(self) -> str:
        """返回工具的动态中文功能描述。

        每次访问时从注入的 ``Workspace`` 读取 ``display_root_hint()``，
        将具体的工作区根展示值拼入描述（需求 6.5）。
        """
        workspace_root = self._workspace.display_root_hint()
        return (
            "Edit a workspace file by replacing the first occurrence of old_str "
            f"with new_str. Paths are resolved relative to workspace root "
            f"{workspace_root} and must use POSIX / separators. If exact matching "
            "fails, the backend may fall back to line-level matching that ignores "
            "leading and trailing whitespace."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        三个必填参数：

        - ``file_path``：工作区相对路径。
        - ``old_str``：要查找并替换的文本（空串会被工具层直接拒绝）。
        - ``new_str``：替换后的新文本（空串表示删除）。
        """
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Workspace-relative target file path using POSIX / separators.",
                },
                "old_str": {
                    "type": "string",
                    "description": (
                        "Original text to replace. Only the first match is replaced. "
                        "Empty strings are rejected."
                    ),
                },
                "new_str": {
                    "type": "string",
                    "description": (
                        "Replacement text. Use an empty string to delete the matched text."
                    ),
                },
            },
            "required": ["file_path", "old_str", "new_str"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行受控文件编辑操作。

        流程：

        1. ``old_str == ""`` → 直接抛出 ``ToolExecutionError``；
        2. 构造观测上下文 ``context``；
        3. ``ws_path = workspace.resolve_path(file_path)``；
        4. ``n = await workspace.edit(ws_path, old_str.encode("utf-8"),
           new_str.encode("utf-8"), context=context)``；
        5. 返回 ``"成功编辑文件 {ws_path}，共 N 字节"``，``ws_path`` 使用
           ``WorkspacePath.to_posix()`` 形式（需求 7.4）。

        错误翻译：见类 docstring 的错误处理策略表。

        **关键红线**：成功消息与错误消息均**不得**引用 ``context`` 字段值
        或宿主绝对路径。

        Args:
            **kwargs: 工具参数，包含 ``file_path`` / ``old_str`` / ``new_str``
                （均必填）。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为形如
            "成功编辑文件 /relative/path.txt，共 N 字节" 的成功消息；
            ``metadata`` 含以下键：

            - ``logical_path`` (str): workspace 相对 POSIX 路径。
            - ``operation`` (str): 固定字面值 ``"edit"``。
            - ``bytes_written`` (int): 回写字节数。

        Raises:
            ToolExecutionError: ``old_str`` 为空、路径越界、未找到匹配、
                锁获取失败或 I/O 失败时抛出。
        """
        file_path: str = kwargs["file_path"]
        old_str: str = kwargs["old_str"]
        new_str: str = kwargs["new_str"]

        if old_str == "":
            raise ToolExecutionError(
                message="old_str 不能为空",
                tool_name=self.name,
            )

        context: dict[str, Any] = {"tool_name": self.name}
        trace_id = _current_trace_id_or_none()
        if trace_id is not None:
            context["trace_id"] = trace_id
        agent_id = _current_agent_id_or_none()
        if agent_id is not None:
            context["agent_id"] = agent_id

        try:
            ws_path = self._workspace.resolve_path(file_path)
            written = await self._workspace.edit(
                ws_path,
                old_str.encode("utf-8"),
                new_str.encode("utf-8"),
                context=context,
            )
        except WorkspaceConfinementViolation as e:
            raise ToolExecutionError(
                message=f"路径 {file_path} 超出工作区边界",
                tool_name=self.name,
            ) from e
        except WorkspaceNotFoundError as e:
            raise ToolExecutionError(
                message=f"路径 {file_path} 不存在",
                tool_name=self.name,
            ) from e
        except WorkspaceIoError as e:
            if e.reason == "no_match":
                raise ToolExecutionError(
                    message=f"未在文件 {file_path} 中找到匹配文本",
                    tool_name=self.name,
                ) from e
            if e.reason == "lock_failed":
                raise ToolExecutionError(
                    message=f"文件 {file_path} 锁获取失败，请稍后重试",
                    tool_name=self.name,
                ) from e
            raise ToolExecutionError(
                message=f"编辑文件 {file_path} 失败",
                tool_name=self.name,
            ) from e

        return ToolExecutionResult(
            content=f"成功编辑文件 {ws_path.to_posix()}，共 {written} 字节",
            metadata={
                "logical_path": ws_path.to_posix(),
                "operation": "edit",
                "bytes_written": written,
            },
        )
