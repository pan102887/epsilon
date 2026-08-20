"""WorkspacePolicy：逻辑路径归一化与合法性校验（纯领域）。

本模块定义 ``WorkspacePolicy`` 不可变对象，把 LLM / 工具调用方提供的
``Requested_Path`` 规范化为 ``WorkspacePath``；所有失败通过
``WorkspaceConfinementViolation`` 以明确的 ``ConfinementViolationReason``
枚举值传递。

**关键约束**：

- 纯函数实现，不触发任何 I/O；符号链接守卫、跨设备守卫由基础设施层
  在 ``LocalFilesystemWorkspace`` 中单独实现。
- 本模块允许 ``from domain.workspace.value_objects import WorkspacePath``
  （单向依赖）；``value_objects.py`` 通过 ``join`` 的自洽实现打破循环依赖，
  不导入本模块。
- 失败时**绝不**返回被裁剪后的路径，必须以异常方式传递。

允许的 import：``re`` / ``dataclasses`` / ``pathlib.PurePosixPath`` /
``domain.workspace.value_objects`` / ``domain.workspace.exceptions``。
禁止引入 ``infrastructure/`` / FastAPI / pydantic-settings / 任何存储 SDK。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)
from domain.workspace.value_objects import WorkspacePath

# 非法字符常量；与 ``value_objects._reject_illegal_chars`` 并列维护，本期不共享常量模块。
_NUL_BYTE: str = "\x00"
_BACKSLASH: str = "\\"
_WINDOWS_DRIVE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class WorkspacePolicy:
    """逻辑路径规范化策略（纯函数式）。

    ``WorkspacePolicy`` 不依赖任何基础设施、不触发 I/O、不持有可变状态；
    所有失败通过 ``WorkspaceConfinementViolation`` 以明确 ``reason`` 枚举
    传递给调用方。

    典型调用：

        >>> policy = WorkspacePolicy()
        >>> wp = policy.resolve("notes.md")  # -> WorkspacePath("/notes.md")

    校验顺序与失败枚举对应关系，见 ``resolve`` 方法文档。
    """

    def resolve(self, requested: str) -> WorkspacePath:
        """将任意输入字符串规范化为 ``WorkspacePath``。

        校验顺序：

        1. 空串 / ``"."`` / ``"/"`` 统一映射到工作区根
           ``WorkspacePath(PurePosixPath("/"))``；
        2. 前置字符扫描（按"更专指的规则优先"的原则）：

           - 含 ``\\x00`` → ``NUL_BYTE``；
           - 匹配 ``^[A-Za-z]:`` → ``WINDOWS_DRIVE``
             （优先于 ``BACKSLASH``，以便形如 ``C:\\Windows`` 这类
             Windows 盘符 + 反斜杠的组合被归类为 ``WINDOWS_DRIVE``）；
           - ``//`` 开头且第三字符非 ``/`` → ``UNC_PATH``
             （优先于 ``BACKSLASH``）；
           - 含 ``\\``    → ``BACKSLASH``；

        3. 以 ``/`` 起始视为"工作区绝对路径"，否则锚定到 ``/``
           （在字符串前拼接 ``/``）；
        4. 使用 ``PurePosixPath`` 归一化（消除 ``.`` / ``..`` / 重复 ``/``）；
        5. 归一化后首段仍为 ``..`` 或路径脱离 ``/`` → ``ABSOLUTE_OUTSIDE``；
        6. 构造 ``WorkspacePath(PurePosixPath("/" + joined))`` 并返回。

        Args:
            requested: 原始路径字符串，可能是相对 / 绝对形式，
                可能含 ``.`` / ``..`` / 非法字符。

        Returns:
            归一化后的合法 ``WorkspacePath``，始终以 ``/`` 起始。

        Raises:
            WorkspaceConfinementViolation: 命中任一校验分支时抛出，
                ``reason`` 为对应的 ``ConfinementViolationReason`` 枚举。
        """
        # 1. 空串 / "." / "/" 统一视为工作区根。
        if requested == "" or requested == "." or requested == "/":
            return WorkspacePath(_posix=PurePosixPath("/"))

        # 2. 前置字符扫描（"更专指的规则优先"）。
        if _NUL_BYTE in requested:
            raise WorkspaceConfinementViolation(
                requested_path=requested,
                reason=ConfinementViolationReason.NUL_BYTE,
            )
        # 先判 WINDOWS_DRIVE：``^[A-Za-z]:`` 比单纯"含反斜杠"更专指，
        # 典型案例 ``C:\\Windows`` 应归类为 ``WINDOWS_DRIVE`` 而非 ``BACKSLASH``。
        if _WINDOWS_DRIVE_RE.match(requested):
            raise WorkspaceConfinementViolation(
                requested_path=requested,
                reason=ConfinementViolationReason.WINDOWS_DRIVE,
            )
        # UNC 前缀：严格遵循"以 // 开头且第三字符非 /"的判据。
        if (
            len(requested) >= 3
            and requested[0] == "/"
            and requested[1] == "/"
            and requested[2] != "/"
        ):
            raise WorkspaceConfinementViolation(
                requested_path=requested,
                reason=ConfinementViolationReason.UNC_PATH,
            )
        if _BACKSLASH in requested:
            raise WorkspaceConfinementViolation(
                requested_path=requested,
                reason=ConfinementViolationReason.BACKSLASH,
            )

        # 3. 锚定到工作区根：相对路径拼接 "/" 前缀。
        anchored = requested if requested.startswith("/") else "/" + requested

        # 4. PurePosixPath 归一化：消除 "." / ".." / 重复 "/"。
        combined = PurePosixPath(anchored)
        parts: list[str] = []
        for part in combined.parts:
            if part == "..":
                # 5. 归一化阶段 ".." 越根 → ABSOLUTE_OUTSIDE。
                if not parts or parts == ["/"]:
                    raise WorkspaceConfinementViolation(
                        requested_path=requested,
                        reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
                    )
                parts.pop()
            elif part in ("", "."):
                continue
            else:
                parts.append(part)

        # 5. 再次校验：归一化后若脱离 "/"，视为越界。
        if not parts or parts[0] != "/":
            # 理论不可达（anchored 已以 "/" 起始），防御性断言。
            raise WorkspaceConfinementViolation(
                requested_path=requested,
                reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
            )

        # 6. 重组为 "/"-起始的 POSIX 路径，返回 WorkspacePath。
        rebuilt = PurePosixPath(*parts)
        return WorkspacePath(_posix=rebuilt)
