"""Workspace 端口定义。

本模块定义与存储介质无关的 ``Workspace`` Port 协议，以及本地后端专属的
``LocallyMaterializable`` 子协议。实现者位于 ``infrastructure/workspace/``
下（本期为 ``LocalFilesystemWorkspace``）。

- ``Workspace``：暴露受控工具所需的 10 个操作（7 个 I/O + 3 个元数据/纯函数）；
  所有路径参数均为 ``WorkspacePath``，不暴露任何宿主绝对路径或 ``bucket+key``。
- ``LocallyMaterializable``：仅由 ``local_materialization=True`` 的后端实现，
  专供 ``ShellExecTool`` / ``PythonExecTool`` 取得子进程 ``cwd`` 使用。

两个 Protocol 均使用 ``@runtime_checkable`` 修饰，便于测试中的结构类型
``isinstance`` 判断（配合 ``unittest.mock.MagicMock``）。

依赖白名单：``typing.Protocol`` / ``typing.runtime_checkable`` /
``domain.workspace.value_objects``。**禁止** import 任何 ``infrastructure/``
模块、FastAPI 组件、存储 SDK 或其他外部依赖。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from domain.workspace.value_objects import (
    WorkspaceCapabilities,
    WorkspacePath,
    WorkspaceStatEntry,
)


@runtime_checkable
class Workspace(Protocol):
    """工作区端口协议。

    与存储介质无关，暴露受控工具真正需要的 10 个操作：7 个 I/O 方法
    （``exists`` / ``stat`` / ``read`` / ``write`` / ``edit`` /
    ``list_dir`` / ``delete``）+ 3 个非 I/O 方法（``resolve_path`` /
    ``capabilities`` / ``display_root_hint``）。所有路径参数均为
    ``WorkspacePath``；本协议不暴露任何宿主绝对路径或 ``bucket+key``。

    观测上下文参数 ``context`` 的语义：
        7 个 I/O 方法末位统一接受 ``context: dict | None = None``
        （keyword-only 参数），作为纯观测透传通道，用于让后端把调用方的
        元数据合并进结构化日志。典型白名单字段：

        - ``tool_name: str`` —— 触发本次 I/O 的工具名（需求 8.1 / 8.2
          明确要求结构化日志包含该字段）；
        - ``trace_id: str``  —— 当前请求的链路追踪 ID；
        - ``agent_id: str``  —— （可选）Agent 标识。

        后端实现约束：

        - 后端**可以**把 ``context`` 中的白名单字段合并进结构化日志
          （``logger.*(extra=...)``）；
        - 后端**不得**据 ``context`` 改变 I/O 行为或分支（纯观测透传，
          不改变返回值、不改变错误类型、不影响重试等控制流）；
        - 后端应容忍 ``context=None``、空字典、未知 key、缺失约定字段；
        - **禁止**把 ``context`` 原样或其中任一字段拼入异常 ``message``
          或其他对 LLM 可见的出口（防止 ``trace_id`` 等内部标识意外泄露
          到 LLM 上下文），只允许将白名单字段取值用于服务端日志。

        ``resolve_path`` / ``capabilities`` / ``display_root_hint`` 是
        纯函数式归一化或静态元数据查询，不产生需要结构化日志关联的
        I/O 事件，因此**不**接受 ``context`` 参数。

        ``context`` 与 ``WorkspaceCapabilities`` 的区别：前者是"本次调用的
        观测元数据"（每次 I/O 可携带不同的值），后者是"后端静态能力声明"
        （在后端实例生命周期内不变）。
    """

    def resolve_path(self, requested: str) -> WorkspacePath:
        """将入参字符串规范化为 ``WorkspacePath``。

        纯函数式归一化，不触发任何 I/O；越界或含非法字符时抛
        ``WorkspaceConfinementViolation``。不接受 ``context`` 参数。

        Args:
            requested: 原始路径字符串，可能为相对或绝对形式。

        Returns:
            归一化后的合法 ``WorkspacePath``。
        """
        ...

    async def exists(
        self,
        path: WorkspacePath,
        *,
        context: Mapping[str, object] | None = None,
    ) -> bool:
        """判定 ``path`` 是否存在。

        Args:
            path: 要查询的逻辑路径。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。

        Returns:
            存在时返回 ``True``，否则 ``False``。
        """
        ...

    async def stat(
        self,
        path: WorkspacePath,
        *,
        context: Mapping[str, object] | None = None,
    ) -> WorkspaceStatEntry:
        """返回 ``path`` 的元数据。

        不存在时抛 ``WorkspaceNotFoundError``；其他 I/O 失败抛
        ``WorkspaceIoError``。

        Args:
            path: 要查询的逻辑路径。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。

        Returns:
            ``WorkspaceStatEntry`` 值对象。
        """
        ...

    async def read(
        self,
        path: WorkspacePath,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> bytes:
        """读取 ``path`` 的字节内容，可选按 UTF-8 行范围切片。

        行范围为闭区间（``start_line`` / ``end_line`` 均从 1 起）；
        若 ``path`` 为二进制文件且指定了行范围，后端应抛
        ``WorkspaceIoError(reason="decode_failed")``。

        Args:
            path: 要读取的逻辑路径。
            start_line: 起始行号（闭区间，从 1 起），``None`` 表示从头开始。
            end_line: 结束行号（闭区间），``None`` 表示读到末尾。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。

        Returns:
            读取到的字节串。
        """
        ...

    async def write(
        self,
        path: WorkspacePath,
        content: bytes,
        *,
        context: Mapping[str, object] | None = None,
    ) -> int:
        """将 ``content`` 写入 ``path``，返回写入字节数。

        自动创建父级逻辑目录；对 ``supports_atomic_write=True`` 的后端
        必须提供原子写保证（写失败不留下半写文件）。返回值与实际写入的
        字节数严格一致，调用方用于生成面向 LLM 的成功消息。

        Args:
            path: 目标逻辑路径。
            content: 待写入的字节串。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。

        Returns:
            实际写入的字节数。
        """
        ...

    async def edit(
        self,
        path: WorkspacePath,
        old_content: bytes,
        new_content: bytes,
        *,
        context: Mapping[str, object] | None = None,
    ) -> int:
        """对 ``path`` 做"首个匹配替换"。

        两阶段匹配：精确字节匹配 → 行级去空白模糊回退（仅在 UTF-8 可解码
        时启用）。未匹配时抛 ``WorkspaceIoError(reason="no_match")``，
        保留 ``common_tools.edit_file`` 的"未找到匹配文本"语义。

        Args:
            path: 目标逻辑路径。
            old_content: 要被替换的原始字节串。
            new_content: 替换后的字节串。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。

        Returns:
            替换后写入的字节数。
        """
        ...

    async def list_dir(
        self,
        path: WorkspacePath,
        *,
        recursive: bool = True,
        context: Mapping[str, object] | None = None,
    ) -> list[WorkspaceStatEntry]:
        """列出 ``path`` 下的条目。

        ``recursive=True`` 时深度优先递归列出；返回的每个
        ``WorkspaceStatEntry.path`` 均为相对于工作区根的 ``WorkspacePath``。

        Args:
            path: 要列出的逻辑目录路径。
            recursive: 是否递归列出子目录条目，默认 ``True``。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。

        Returns:
            条目元数据列表。
        """
        ...

    async def delete(
        self,
        path: WorkspacePath,
        *,
        context: Mapping[str, object] | None = None,
    ) -> None:
        """删除 ``path``。

        不存在时抛 ``WorkspaceNotFoundError``。本方法不对 LLM 直接暴露，
        仅供后端内部使用（例如 ``edit`` 的回滚路径）。

        Args:
            path: 要删除的逻辑路径。
            context: 观测上下文白名单字段（见类 docstring），默认 ``None``。
        """
        ...

    def capabilities(self) -> WorkspaceCapabilities:
        """返回本后端的能力声明。

        ``WorkspaceCapabilities`` 是"后端静态能力声明"，在后端实例
        生命周期内恒定。本方法是纯元数据查询、无 I/O 事件，因此
        **不**接受 ``context`` 参数。

        Returns:
            后端能力声明值对象。
        """
        ...

    def display_root_hint(self) -> str:
        """返回对 LLM 有意义的工作区定位字符串，供工具 description 动态拼接。

        本地后端返回 ``str(self._root)``（宿主绝对路径）；未来 OSS 后端
        可返回 ``oss://bucket/prefix/`` 等形式。本方法是纯元数据查询，
        不产生 I/O 事件，**不**接受 ``context`` 参数。

        注意：此返回值会被 LLM 上下文读取（已由用户在设计审批阶段明确
        决策放行），实现者应权衡信息价值与信息泄露边界，不得返回凭证、
        签名等敏感信息。

        Returns:
            可在 LLM 上下文中展示的工作区根标识字符串。
        """
        ...


@runtime_checkable
class LocallyMaterializable(Protocol):
    """本地物化能力子协议。

    仅由 ``capabilities.local_materialization=True`` 的后端实现，专供
    ``ShellExecTool`` / ``PythonExecTool`` 在启动子进程前取得宿主 ``cwd``。
    本协议**不接受** ``context`` 参数（同步方法、无 I/O 日志事件）。

    调用方（工具层）在调用前必须先通过 ``Workspace.capabilities()`` 或
    ``isinstance(workspace, LocallyMaterializable)`` 判断后端是否支持本地
    物化；未支持时须自行抛 ``WorkspaceUnsupportedOperationError`` 或
    ``ToolExecutionError``。
    """

    def materialize_cwd(self, path: WorkspacePath) -> str:
        """返回可直接作为子进程 ``cwd`` 的宿主目录绝对路径。

        本方法是本地后端对工具层暴露的唯一物理路径出口，其返回值
        **绝不**能被放回工具的对外参数或成功消息中（守住需求 4.4 / 8.6
        的路径泄露红线）。

        Args:
            path: 要物化的逻辑目录路径。

        Returns:
            宿主绝对路径字符串。
        """
        ...
