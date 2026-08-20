"""受控文件读取工具模块。

提供 :class:`ReadFileTool` 实现，继承自 :class:`domain.agent.tools.Tool`
抽象基类，为 LLM Agent 提供在 ``Workspace`` 边界内的受控文件读取能力。

本工具**不直接**访问宿主文件系统：构造时注入 ``Workspace`` 端口实例，
所有路径解析与字节读取通过 Port 完成；工具层负责入参校验、行号拼装
和错误翻译（领域错误 → ``ToolExecutionError``）。

依赖白名单（守住需求 6.1 / Property 6）：
    ``typing`` / ``domain.agent.*`` / ``domain.workspace.*`` /
    ``infrastructure.tools.filesystem._context`` /
    ``infrastructure.tools.filesystem._rendering``。
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
from infrastructure.tools.filesystem._rendering import _render_with_line_numbers


class ReadFileTool(Tool):
    """受控文件读取工具，支持按行范围分页读取。

    作为基础设施层适配器，将 ``Workspace`` Port 的 ``read`` 方法适配为
    ``Tool`` 抽象接口。可注册到 ``ToolRegistry`` 供 LLM Agent 调用。

    参数映射：``offset`` / ``limit`` → ``start_line`` / ``end_line``：

        ``start_line = offset``
        ``end_line   = offset + limit - 1``

    错误处理策略：

    - ``offset < 1`` 或 ``limit < 1`` → ``ToolExecutionError``；
    - ``WorkspaceConfinementViolation`` → ``ToolExecutionError``
      （中文文案，不含宿主绝对路径）；
    - ``WorkspaceNotFoundError`` → ``ToolExecutionError``；
    - ``WorkspaceIoError`` → ``ToolExecutionError``（``reason`` 仅
      用于内部日志，不拼入对 LLM 可见的消息）。
    """

    def __init__(self, workspace: Workspace) -> None:
        """初始化受控文件读取工具。

        Args:
            workspace: 注入的 ``Workspace`` Port 实例；工具通过该实例
                完成路径解析与字节读取，本身不访问宿主文件系统。
        """
        self._workspace: Workspace = workspace

    @property
    def name(self) -> str:
        """返回工具名称 ``"read_file"``。"""
        return "read_file"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """读取工具为低风险。"""
        return ToolRiskLevel.LOW

    @property
    def description(self) -> str:
        """返回工具的动态中文功能描述。

        每次访问时从注入的 ``Workspace`` 读取 ``display_root_hint()``，
        将具体的工作区根展示值拼入描述，引导 LLM 正确使用相对路径（需求 6.5）。
        """
        workspace_root = self._workspace.display_root_hint()
        return (
            f"Read text from a file inside the workspace. Paths are resolved relative "
            f"to workspace root {workspace_root} and must use POSIX / separators. "
            "Use offset and limit to inspect large files in pages."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        三个参数：

        - ``file_path``：必填，工作区相对路径。
        - ``offset``：可选，起始行号（1 起），默认 ``1``。
        - ``limit``：可选，最大行数，默认 ``200``。
        """
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Workspace-relative file path using POSIX / separators.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based starting line number, inclusive. Defaults to 1.",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. Defaults to 200.",
                    "default": 200,
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行受控文件读取操作。

        流程：

        1. 校验 ``offset >= 1`` 与 ``limit >= 1``；
        2. 构造观测上下文 ``context``（白名单字段：``tool_name`` /
           ``trace_id`` / ``agent_id``），``trace_id`` / ``agent_id`` 当前
           恒为 ``None``，跳过写入以避免让白名单过滤后出现显式 ``None``；
        3. ``ws_path = workspace.resolve_path(file_path)``；
        4. ``raw = await workspace.read(ws_path, start_line=offset,
           end_line=offset+limit-1, context=context)``；
        5. ``raw.decode("utf-8", errors="replace")`` → 行号拼装。

        错误翻译：

        - ``WorkspaceConfinementViolation`` → "路径 {file_path} 超出工作区边界"；
        - ``WorkspaceNotFoundError``       → "路径 {file_path} 不存在"；
        - ``WorkspaceIoError``             → "读取文件 {file_path} 失败"。

        **关键红线**：错误消息**不得**引用 ``context`` 的任何字段值，
        也不得拼入宿主绝对路径（需求 4.4 / 7.4 / 8.6）。

        Args:
            **kwargs: 工具参数，包含 ``file_path``（必填）、``offset``
                （默认 1）、``limit``（默认 200）。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为带行号前缀的文件内容
            字符串；``metadata`` 含以下键：

            - ``logical_path`` (str): workspace 相对 POSIX 路径。
            - ``operation`` (str): 固定字面值 ``"read"``。
            - ``line_range`` (list[int]): ``[offset, offset+limit-1]``。
            - ``lines_returned`` (int): 实际返回行数。

        Raises:
            ToolExecutionError: 参数非法、路径越界、文件不存在或 I/O 失败时抛出。
        """
        file_path: str = kwargs["file_path"]
        offset: int = kwargs.get("offset", 1)
        limit: int = kwargs.get("limit", 200)

        if offset < 1:
            raise ToolExecutionError(
                message=f"offset 必须大于等于 1，当前值：{offset}",
                tool_name=self.name,
            )
        if limit < 1:
            raise ToolExecutionError(
                message=f"limit 必须大于等于 1，当前值：{limit}",
                tool_name=self.name,
            )

        # 观测上下文白名单字段：tool_name 恒存在；trace_id / agent_id 可选。
        context: dict[str, Any] = {"tool_name": self.name}
        trace_id = _current_trace_id_or_none()
        if trace_id is not None:
            context["trace_id"] = trace_id
        agent_id = _current_agent_id_or_none()
        if agent_id is not None:
            context["agent_id"] = agent_id

        try:
            ws_path = self._workspace.resolve_path(file_path)
            raw = await self._workspace.read(
                ws_path,
                start_line=offset,
                end_line=offset + limit - 1,
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
                message=f"读取文件 {file_path} 失败",
                tool_name=self.name,
            ) from e

        text = raw.decode("utf-8", errors="replace")
        content = _render_with_line_numbers(text, start_line=offset)
        # lines_returned 与 _render_with_line_numbers 的切分口径保持一致：
        # 空内容渲染为空串（0 行），非空按 splitlines() 计行。
        lines_returned = len(text.splitlines()) if text else 0
        return ToolExecutionResult(
            content=content,
            metadata={
                "logical_path": ws_path.to_posix(),
                "operation": "read",
                "line_range": [offset, offset + limit - 1],
                "lines_returned": lines_returned,
            },
        )
