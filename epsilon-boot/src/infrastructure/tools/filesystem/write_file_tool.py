"""受控文件写入工具模块。

提供 :class:`WriteFileTool` 实现，继承自 :class:`domain.agent.tools.Tool`
抽象基类，为 LLM Agent 提供在 ``Workspace`` 边界内的受控文件写入能力。

本工具**不直接**访问宿主文件系统：构造时注入 ``Workspace`` Port 实例，
所有路径解析与字节写入通过 Port 完成；工具层负责入参 encode、错误翻译
以及成功消息中使用 ``WorkspacePath`` 逻辑路径（不泄露宿主绝对路径）。

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


class WriteFileTool(Tool):
    """受控文件写入工具，支持自动创建父目录。

    作为基础设施层适配器，将 ``Workspace`` Port 的 ``write`` 方法适配为
    ``Tool`` 抽象接口。父目录的创建由后端实现保证；工具层不感知底层存储。

    错误处理策略：

    - ``WorkspaceConfinementViolation`` → ``ToolExecutionError``；
    - ``WorkspaceNotFoundError`` → ``ToolExecutionError``（极少触发，
      父目录不存在时后端通常自动创建，本分支为防御性捕获）；
    - ``WorkspaceIoError`` → ``ToolExecutionError``。
    """

    def __init__(self, workspace: Workspace) -> None:
        """初始化受控文件写入工具。

        Args:
            workspace: 注入的 ``Workspace`` Port 实例；工具通过该实例
                完成路径解析与字节写入，本身不访问宿主文件系统。
        """
        self._workspace: Workspace = workspace

    @property
    def name(self) -> str:
        """返回工具名称 ``"write_file"``。"""
        return "write_file"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """文件写入工具为高风险。"""
        return ToolRiskLevel.HIGH

    @property
    def description(self) -> str:
        """返回工具的动态中文功能描述。

        每次访问时从注入的 ``Workspace`` 读取 ``display_root_hint()``，
        将具体的工作区根展示值拼入描述（需求 6.5）。
        """
        workspace_root = self._workspace.display_root_hint()
        return (
            f"Write UTF-8 text to a file inside the workspace. Paths are resolved "
            f"relative to workspace root {workspace_root} and must use POSIX / "
            "separators. Parent directories may be created automatically. Existing "
            "files are overwritten."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        两个必填参数：

        - ``file_path``：工作区相对路径。
        - ``content``：要写入的文本内容（UTF-8 编码后落盘）。
        """
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Workspace-relative target file path using POSIX / separators.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write as UTF-8.",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行受控文件写入操作。

        流程：

        1. 构造观测上下文 ``context``（白名单字段：``tool_name`` /
           ``trace_id`` / ``agent_id``）；
        2. ``ws_path = workspace.resolve_path(file_path)``；
        3. ``n = await workspace.write(ws_path, content.encode("utf-8"),
           context=context)``；
        4. 返回 ``"成功写入文件 {ws_path}，共 N 字节"``，其中 ``ws_path``
           是 ``WorkspacePath.to_posix()`` 形式（需求 7.4）。

        错误翻译：

        - ``WorkspaceConfinementViolation`` → "路径 {file_path} 超出工作区边界"；
        - ``WorkspaceNotFoundError``        → "路径 {file_path} 不存在"；
        - ``WorkspaceIoError``              → "写入文件 {file_path} 失败"。

        **关键红线**：成功消息与错误消息均**不得**引用 ``context`` 字段值
        或宿主绝对路径；成功消息中的路径使用 ``WorkspacePath`` 逻辑形式。

        Args:
            **kwargs: 工具参数，包含 ``file_path`` 与 ``content``（均必填）。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为形如
            "成功写入文件 /relative/path.txt，共 N 字节" 的成功消息；
            ``metadata`` 含以下键：

            - ``logical_path`` (str): workspace 相对 POSIX 路径。
            - ``operation`` (str): 固定字面值 ``"write"``。
            - ``bytes_written`` (int): 写入字节数。

        Raises:
            ToolExecutionError: 路径越界、父路径异常或 I/O 失败时抛出。
        """
        file_path: str = kwargs["file_path"]
        content: str = kwargs["content"]

        context: dict[str, Any] = {"tool_name": self.name}
        trace_id = current_trace_id_or_none()
        if trace_id is not None:
            context["trace_id"] = trace_id
        agent_id = current_agent_id_or_none()
        if agent_id is not None:
            context["agent_id"] = agent_id

        try:
            ws_path = self._workspace.resolve_path(file_path)
            written = await self._workspace.write(
                ws_path,
                content.encode("utf-8"),
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
            raise ToolExecutionError(
                message=f"写入文件 {file_path} 失败",
                tool_name=self.name,
            ) from e

        return ToolExecutionResult(
            content=f"成功写入文件 {ws_path.to_posix()}，共 {written} 字节",
            metadata={
                "logical_path": ws_path.to_posix(),
                "operation": "write",
                "bytes_written": written,
            },
        )
