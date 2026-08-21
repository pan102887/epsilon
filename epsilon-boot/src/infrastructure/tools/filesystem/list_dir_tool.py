"""受控目录列举工具模块。

提供 :class:`ListDirTool` 实现，继承自 :class:`domain.agent.tools.Tool`
抽象基类，为 LLM Agent 提供在 ``Workspace`` 边界内的受控目录浏览能力。

本工具**不直接**访问宿主文件系统：构造时注入 ``Workspace`` Port 实例，
路径解析与目录遍历通过 Port 完成；工具层仅负责空串 / ``"."`` / ``"/"``
统一映射到工作区根、错误翻译和 POSIX 形式输出（需求 6.4 / 7.2 / 7.4）。

返回文本每行一个条目，路径以工作区根为基准（``WorkspacePath.to_posix()``
形式，始终以 ``/`` 起始），**不泄露**宿主绝对路径。

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
    current_agent_id_or_none,
    current_trace_id_or_none,
)


class ListDirTool(Tool):
    """受控目录列举工具，递归列出条目。

    作为基础设施层适配器，将 ``Workspace`` Port 的 ``list_dir`` 方法适配
    为 ``Tool`` 抽象接口。空串 / ``"."`` / ``"/"`` 统一映射到工作区根
    （需求 6.4 / 7.2）；返回文本中每条目的路径使用 ``WorkspacePath.to_posix()``
    形式（需求 7.4），不得泄露宿主绝对路径。

    错误处理策略：

    - ``WorkspaceConfinementViolation`` → ``ToolExecutionError``；
    - ``WorkspaceNotFoundError``        → ``ToolExecutionError``；
    - ``WorkspaceIoError``              → ``ToolExecutionError``。
    """

    def __init__(self, workspace: Workspace) -> None:
        """初始化受控目录列举工具。

        Args:
            workspace: 注入的 ``Workspace`` Port 实例；工具通过该实例
                完成路径解析与目录遍历，本身不访问宿主文件系统。
        """
        self._workspace: Workspace = workspace

    @property
    def name(self) -> str:
        """返回工具名称 ``"list_dir"``。"""
        return "list_dir"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """目录读取工具为低风险。"""
        return ToolRiskLevel.LOW

    @property
    def description(self) -> str:
        """返回工具的动态中文功能描述。

        每次访问时从注入的 ``Workspace`` 读取 ``display_root_hint()``，
        将具体的工作区根展示值拼入描述（需求 6.5）。
        """
        workspace_root = self._workspace.display_root_hint()
        return (
            f"List directory entries inside the workspace. Paths are resolved "
            f"relative to workspace root {workspace_root} and must use POSIX / "
            'separators. Use an empty string, ".", or "/" to list the workspace root.'
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        两个参数：

        - ``directory_path``：可选（兼容空串、``"."``、``"/"``），
          传入空串时默认列出工作区根。
        - ``recursive``：可选，是否递归展示嵌套子目录，默认 ``true``。
        """
        return {
            "type": "object",
            "properties": {
                "directory_path": {
                    "type": "string",
                    "description": (
                        "Workspace-relative directory path using POSIX / separators. "
                        "An empty string, \".\", or \"/\" means the workspace root."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "description": (
                        "Whether to list nested directories recursively. Defaults to true."
                    ),
                    "default": True,
                },
            },
            "required": ["directory_path"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行受控目录列举操作。

        流程：

        1. 归一化 ``directory_path``：空串 / ``"."`` / ``"/"`` → ``"/"``；
        2. 构造观测上下文 ``context``；
        3. ``ws_path = workspace.resolve_path(directory_path)``；
        4. ``entries = await workspace.list_dir(ws_path, recursive=recursive,
           context=context)``；
        5. 每条目按 ``WorkspaceStatEntry.path.to_posix()`` + 后缀 ``/``
           （目录）拼装为一行文本，使用 ``\\n`` 连接。

        错误翻译：

        - ``WorkspaceConfinementViolation`` → "路径 {directory_path} 超出工作区边界"；
        - ``WorkspaceNotFoundError``        → "路径 {directory_path} 不存在"；
        - ``WorkspaceIoError``              → "列举目录 {directory_path} 失败"。

        **关键红线**：返回的文本行中每条路径均以 ``/`` 起始（``WorkspacePath``
        形式），绝不含宿主绝对路径；错误消息同样不引用 ``context`` 字段值。

        Args:
            **kwargs: 工具参数，``directory_path`` 必填（空串允许）、
                ``recursive`` 默认 ``True``。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为每行一个条目的多行
            文本（空目录返回空串）；``metadata`` 含以下键：

            - ``logical_path`` (str): workspace 相对 POSIX 路径。
            - ``operation`` (str): 固定字面值 ``"list"``。
            - ``recursive`` (bool): 是否递归列举。
            - ``entries_count`` (int): 返回的条目数量。

        Raises:
            ToolExecutionError: 路径越界、目录不存在或 I/O 失败时抛出。
        """
        raw_path = kwargs.get("directory_path", "") or ""
        recursive: bool = kwargs.get("recursive", True)

        # 归一化：空串 / "." / "/" 统一映射到工作区根。
        directory_path = "/" if raw_path in ("", ".", "/") else raw_path

        context: dict[str, Any] = {"tool_name": self.name}
        trace_id = current_trace_id_or_none()
        if trace_id is not None:
            context["trace_id"] = trace_id
        agent_id = current_agent_id_or_none()
        if agent_id is not None:
            context["agent_id"] = agent_id

        try:
            ws_path = self._workspace.resolve_path(directory_path)
            entries = await self._workspace.list_dir(
                ws_path,
                recursive=recursive,
                context=context,
            )
        except WorkspaceConfinementViolation as e:
            raise ToolExecutionError(
                message=f"路径 {directory_path} 超出工作区边界",
                tool_name=self.name,
            ) from e
        except WorkspaceNotFoundError as e:
            raise ToolExecutionError(
                message=f"路径 {directory_path} 不存在",
                tool_name=self.name,
            ) from e
        except WorkspaceIoError as e:
            raise ToolExecutionError(
                message=f"列举目录 {directory_path} 失败",
                tool_name=self.name,
            ) from e

        # 按逻辑路径字典序排序以获得平台无关的稳定输出。
        # 目录条目以 "/" 后缀区分于文件条目，帮助 LLM 直观识别。
        sorted_entries = sorted(entries, key=lambda e: e.path.to_posix())
        lines: list[str] = []
        for entry in sorted_entries:
            rendered = entry.path.to_posix()
            if entry.is_dir:
                rendered = f"{rendered}/"
            lines.append(rendered)
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata={
                "logical_path": ws_path.to_posix(),
                "operation": "list",
                "recursive": recursive,
                "entries_count": len(lines),
            },
        )
