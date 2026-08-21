"""Workspace 领域值对象模块。

本模块定义 Workspace 抽象所需的不可变值对象：

- ``WorkspaceBackendKind``：工作区后端种类枚举，本期仅支持 ``local_filesystem``。
- ``WorkspacePath``：逻辑路径值对象，以 POSIX 形式持有已归一化、"/"-起始的路径。
- ``WorkspaceStatEntry``：后端无关的条目元数据。
- ``WorkspaceCapabilities``：后端能力声明，所有字段带默认值，新增字段对旧调用方透明。

本模块刻意**不导入** ``domain.workspace.policy``（顶层或函数体内均不得 import），
以打破 ``value_objects.py`` ↔ ``policy.py`` 的潜在循环依赖；``WorkspacePath.join``
采用纯 ``PurePosixPath`` 拼接 + 手动 ``..`` 折叠实现自洽校验，不再依赖 Policy。

仅允许依赖：``pathlib.PurePosixPath`` / ``enum`` / ``dataclasses`` /
``domain.workspace.exceptions``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import cast

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)


class WorkspaceBackendKind(StrEnum):
    """工作区后端种类枚举。

    本期仅 ``LOCAL_FILESYSTEM`` 视为合法值。未来可追加 ``OSS = "oss"``，
    届时只需在 ``WorkspaceConfig`` 放开 ``_reject_unsupported_backend``
    校验并注册对应工厂即可，不需要改动枚举骨架。
    """

    LOCAL_FILESYSTEM = "local_filesystem"
    # 未来可追加：OSS = "oss"


# 非法字符常量；与 ``domain.workspace.policy`` 并列维护，本期不共享常量模块。
_NUL_BYTE: str = "\x00"
_BACKSLASH: str = "\\"
_WINDOWS_DRIVE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:")


def _reject_illegal_chars(segment: str) -> None:
    """校验 ``join`` 入参段是否包含非法字符。

    与 ``WorkspacePolicy`` 保持相同字符集闭合，但实现体放在 ``value_objects``
    内部，避免导入 ``policy`` 形成循环依赖。

    Args:
        segment: 待拼接的相对段字符串。

    Raises:
        WorkspaceConfinementViolation: 当 ``segment`` 含 NUL 字符、反斜杠或
            Windows 盘符前缀时抛出，并携带对应的 ``ConfinementViolationReason``。
    """
    if _NUL_BYTE in segment:
        raise WorkspaceConfinementViolation(
            requested_path=segment,
            reason=ConfinementViolationReason.NUL_BYTE,
        )
    if _BACKSLASH in segment:
        raise WorkspaceConfinementViolation(
            requested_path=segment,
            reason=ConfinementViolationReason.BACKSLASH,
        )
    if _WINDOWS_DRIVE_RE.match(segment):
        raise WorkspaceConfinementViolation(
            requested_path=segment,
            reason=ConfinementViolationReason.WINDOWS_DRIVE,
        )


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    """逻辑路径值对象。

    必须经 ``WorkspacePolicy.resolve()`` 构造，外部调用方不应直接实例化。
    内部持有已归一化的 ``PurePosixPath``：始终以 "/" 起始、不含 ".."、
    不含反斜杠、不含 NUL 字符、首段不越过工作区根。

    Attributes:
        _posix: 已归一化的 POSIX 路径，约束为 "/" 起始、绝对路径。
    """

    _posix: PurePosixPath

    def to_posix(self) -> str:
        """返回 "/"-起始的字符串形式，供日志与工具返回消息使用。

        Returns:
            以 "/" 起始的 POSIX 风格路径字符串。
        """
        return self._posix.as_posix()

    def join(self, segment: str) -> WorkspacePath:
        """在当前合法 ``WorkspacePath`` 后拼接一个相对段，返回新实例。

        **关键决策**：本方法**不调用** ``WorkspacePolicy``，而是采用纯
        ``PurePosixPath`` 拼接 + 手动 ``..`` 折叠实现自洽校验；原因详见
        ``docs/spec/workspace/design.md`` 数据模型章节。

        算法：

        1. 类型校验：``segment`` 必须为 ``str``，否则 ``TypeError``。
        2. 调用 ``_reject_illegal_chars(segment)``：拒绝 NUL、反斜杠、
           Windows 盘符前缀。
        3. ``combined = PurePosixPath(self._posix) / segment``；手动折叠
           ``combined.parts``：``".."`` 回退一段，若回退会越过根则抛
           ``WorkspaceConfinementViolation(reason=ABSOLUTE_OUTSIDE)``；
           ``"."`` 与空段跳过；其他段追加。
        4. 重组为 "/"-起始的 POSIX 路径，返回新 ``WorkspacePath``。

        Args:
            segment: 待拼接的相对段字符串，不得以 "/" 开头。

        Returns:
            拼接后的新 ``WorkspacePath``。

        Raises:
            TypeError: ``segment`` 非 ``str`` 类型。
            WorkspaceConfinementViolation: ``segment`` 含非法字符或拼接后
                越过工作区根。
        """
        segment_value = cast(object, segment)
        if not isinstance(segment_value, str):
            raise TypeError(
                f"segment 必须为 str，实际类型：{type(segment_value).__name__}"
            )
        _reject_illegal_chars(segment)

        combined = PurePosixPath(self._posix) / segment
        # combined.parts 形如 ("/", "a", "b", "..", "c")；根锚点用 "/" 表示。
        parts: list[str] = []
        for part in combined.parts:
            if part == "..":
                # parts 为空或仅余 "/" 时再退会越过根。
                if not parts or parts == ["/"]:
                    raise WorkspaceConfinementViolation(
                        requested_path=f"{self._posix.as_posix()}/{segment}",
                        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
                    )
                parts.pop()
            elif part in ("", "."):
                continue
            else:
                parts.append(part)

        if not parts or parts[0] != "/":
            # 防御性断言：self._posix 已以 "/" 起始，理论不可达。
            parts.insert(0, "/")
        rebuilt = PurePosixPath(*parts)
        return WorkspacePath(_posix=rebuilt)

    def parent(self) -> WorkspacePath:
        """返回当前路径的父路径。

        工作区根（"/"）的 ``parent`` 仍为工作区根（与 ``PurePosixPath``
        的语义保持一致）。

        Returns:
            父路径对应的新 ``WorkspacePath``。
        """
        return WorkspacePath(_posix=self._posix.parent)

    def name(self) -> str:
        """返回路径末段名称。

        工作区根（"/"）的 ``name`` 为空串，与 ``PurePosixPath`` 的语义一致。

        Returns:
            路径末段字符串；根路径时返回 ""。
        """
        return self._posix.name

    def __str__(self) -> str:
        """返回 "/"-起始的字符串形式，方便日志与格式化。"""
        return self.to_posix()


@dataclass(frozen=True, slots=True)
class WorkspaceStatEntry:
    """后端无关的条目元数据。

    用于 ``Workspace.stat`` 与 ``Workspace.list_dir`` 的返回值。对 OSS 后端，
    ``is_dir`` 表示"以 / 结尾的前缀存在"；``size`` / ``mtime`` 可为 ``None``。

    Attributes:
        path: 条目的逻辑路径，始终以工作区根为基准。
        is_file: 是否为普通文件。
        is_dir: 是否为目录（或 OSS 前缀）。
        size: 字节大小，后端不支持时为 ``None``。
        mtime: 最近修改时间的 Unix 时间戳秒，后端不支持时为 ``None``。
    """

    path: WorkspacePath
    is_file: bool
    is_dir: bool
    size: int | None
    mtime: float | None


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilities:
    """后端能力声明值对象。

    所有字段均带默认值 ``False``，保证未来新增字段对旧调用方透明。工具层
    对后端差异的所有分支都应通过 ``WorkspaceCapabilities`` 表达，**不得**
    通过 ``isinstance`` 判断后端类型。

    Attributes:
        supports_symlinks: 是否允许解引用符号链接（由 ``follow_symlinks`` 决定）。
        supports_atomic_write: 是否具备原子写入能力。
        supports_append: 是否支持追加写。
        supports_streaming: 是否支持流式读写。
        supports_large_files: 是否支持大文件（超出内存容量的读写）。
        local_materialization: 是否可把逻辑路径物化到宿主文件系统路径；
            ``ShellExecTool`` / ``PythonExecTool`` 据此决定是否拒绝执行。
    """

    supports_symlinks: bool = False
    supports_atomic_write: bool = False
    supports_append: bool = False
    supports_streaming: bool = False
    supports_large_files: bool = False
    local_materialization: bool = False
