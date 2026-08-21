"""本地文件系统后端的越界守卫工具模块（基础设施私有）。

本模块提供 ``SymlinkGuard`` 与 ``IdentityGuard`` 两个守卫类，作为
``LocalFilesystemWorkspace`` 在把 ``WorkspacePath`` 映射为宿主 ``Path``
之后的"二次越界防御"。它们的职责是：在 ``WorkspacePolicy`` 完成字符串级
归一化之后，再从**文件系统层面**确认目标路径没有通过符号链接或跨设备
大小写折叠等方式逃出工作区根。

两个守卫失败时统一抛出 ``WorkspaceConfinementViolation``，``reason`` 使用
``SYMLINK_ESCAPE`` / ``CROSS_DEVICE`` 枚举，供结构化日志聚合。

**使用约束**：

- 本模块以 ``_`` 前缀命名，属于 ``infrastructure/workspace/local_filesystem``
  的私有实现细节，禁止被领域层或工具层直接导入。
- 守卫的 ``check(...)`` 方法应在 ``LocalFilesystemWorkspace`` 的每个
  I/O 入口内部被调用，越早越好（在真正打开 fd / 调用 ``os.stat`` 之前）。
"""

from __future__ import annotations

import os
from pathlib import Path

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)


class SymlinkGuard:
    """符号链接越界守卫。

    根据 ``follow_symlinks`` 开关采用两种策略：

    - ``follow_symlinks=False``（默认，更严格）：从 ``root`` 开始**逐段
      ``os.lstat``**，一旦某段是符号链接立即抛
      ``WorkspaceConfinementViolation(reason=SYMLINK_ESCAPE)``，不做任何
      ``realpath`` 解引用。
    - ``follow_symlinks=True``：对目标调用 ``Path.resolve(strict=False)``
      做一次 realpath 解引用，再用 ``os.path.commonpath([resolved, root])
      == str(root)`` 判断仍落在工作区之内；若越界抛
      ``WorkspaceConfinementViolation(reason=SYMLINK_ESCAPE)``。

    Attributes:
        _root: 规范化后的工作区宿主绝对目录（已由 ``Path.resolve()`` 处理）。
        _follow_symlinks: 是否允许跟随符号链接。
    """

    def __init__(self, *, root: Path, follow_symlinks: bool) -> None:
        """初始化符号链接守卫。

        Args:
            root: 工作区宿主绝对目录，调用方必须保证已做过 ``resolve()``
                规范化且存在。
            follow_symlinks: 是否允许跟随符号链接。
        """
        self._root: Path = root
        self._follow_symlinks: bool = follow_symlinks

    def check(self, host_path: Path) -> None:
        """对给定宿主路径做符号链接守卫。

        本方法允许 ``host_path`` 指向尚不存在的子路径（典型场景是 ``write``
        尚未创建的目标文件）：在 ``follow_symlinks=False`` 模式下只检查
        从 ``root`` 到 ``host_path`` 路径中**已存在**的各级祖先是否为符号
        链接；在 ``follow_symlinks=True`` 模式下直接对 ``host_path`` 调用
        ``resolve(strict=False)`` 后做前缀判断。

        Args:
            host_path: 待检查的宿主路径，可以不存在。

        Raises:
            WorkspaceConfinementViolation: 命中符号链接逃逸（严格模式）
                或解引用后越出工作区根（宽松模式）。
        """
        if self._follow_symlinks:
            self._check_with_follow(host_path)
        else:
            self._check_strict(host_path)

    def _check_strict(self, host_path: Path) -> None:
        """严格模式：逐段 ``os.lstat``，禁止任意一级是符号链接。

        从 ``root`` 开始，按相对段推进。对每一级已存在的祖先使用
        ``os.lstat``（不解引用）判断是否为符号链接。已经不存在的层级
        直接短路返回（尾段尚未创建是允许的）。

        Args:
            host_path: 待检查的宿主路径。

        Raises:
            WorkspaceConfinementViolation: 路径中任一存在的层级为符号链接。
        """
        # 尝试计算相对 root 的段列表；若 host_path 不在 root 之下，抛越界。
        try:
            rel = host_path.relative_to(self._root)
        except ValueError:
            # host_path 不以 root 为前缀：直接判定越界。
            raise WorkspaceConfinementViolation(
                requested_path=str(host_path),
                reason=ConfinementViolationReason.SYMLINK_ESCAPE,
            ) from None

        current = self._root
        # 先检查 root 自身是否为符号链接（极少见，但不能放过）。
        if current.is_symlink():
            raise WorkspaceConfinementViolation(
                requested_path=str(host_path),
                reason=ConfinementViolationReason.SYMLINK_ESCAPE,
            )

        for segment in rel.parts:
            current = current / segment
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                # 到达尚未创建的层级，后续段也不可能是链接，直接返回。
                return
            # 判断是否为符号链接（``stat.S_ISLNK``）。
            import stat as _stat  # 延迟导入 stat 常量表，避免模块顶层污染。

            if _stat.S_ISLNK(st.st_mode):
                raise WorkspaceConfinementViolation(
                    requested_path=str(host_path),
                    reason=ConfinementViolationReason.SYMLINK_ESCAPE,
                )

    def _check_with_follow(self, host_path: Path) -> None:
        """宽松模式：``resolve(strict=False)`` 后判断仍落在 root 之下。

        Args:
            host_path: 待检查的宿主路径。

        Raises:
            WorkspaceConfinementViolation: 解引用后的路径越出 root。
        """
        resolved = host_path.resolve(strict=False)
        try:
            common = os.path.commonpath([str(resolved), str(self._root)])
        except ValueError:
            # 跨驱动器（Windows）或空路径等异常情况，一律判定越界。
            raise WorkspaceConfinementViolation(
                requested_path=str(host_path),
                reason=ConfinementViolationReason.SYMLINK_ESCAPE,
            ) from None
        if common != str(self._root):
            raise WorkspaceConfinementViolation(
                requested_path=str(host_path),
                reason=ConfinementViolationReason.SYMLINK_ESCAPE,
            )


class IdentityGuard:
    """跨设备身份守卫。

    在启动期记录 ``root`` 的 ``st_dev``（文件系统设备号），在每次 I/O
    前对目标路径或其最近存在的祖先调用 ``os.stat``，若 ``st_dev`` 与
    ``root`` 的不同则抛 ``WorkspaceConfinementViolation(reason=CROSS_DEVICE)``。

    主要防御场景：macOS/HFS+ 的大小写折叠在 ``PurePosixPath`` 字符串比较
    下难以识别，而 inode+dev 对比在跨文件系统时天然可靠；本守卫作为
    ``SymlinkGuard`` 的补充，防止通过 mount 点或大小写变体逃出 root。

    Attributes:
        _root: 工作区宿主绝对目录。
        _root_dev: root 所在文件系统的 ``st_dev``，在构造时缓存。
    """

    def __init__(self, *, root: Path) -> None:
        """初始化身份守卫，立即读取并缓存 root 的 ``st_dev``。

        Args:
            root: 工作区宿主绝对目录，必须已存在且为目录；否则 ``os.stat``
                抛出的异常会原样穿透（启动期由 ``configure_container``
                捕获并转为 ``ConfigurationError``）。
        """
        self._root: Path = root
        self._root_dev: int = os.stat(root).st_dev

    @property
    def root_dev(self) -> int:
        return self._root_dev

    def check(self, host_path: Path) -> None:
        """对目标路径或其最近存在的祖先做跨设备身份校验。

        若 ``host_path`` 尚未创建（典型 ``write`` 场景），回溯到最近一个
        存在的祖先做 ``st_dev`` 对比。如果直到工作区根都不存在（理论上
        不可能，因为启动期已校验 root 存在），则默认放行（由上层 I/O
        报 ``FileNotFoundError`` → ``WorkspaceNotFoundError``）。

        Args:
            host_path: 待检查的宿主路径。

        Raises:
            WorkspaceConfinementViolation: 目标（或最近存在祖先）的
                ``st_dev`` 与 root 不同，判定 ``CROSS_DEVICE``。
        """
        current = host_path
        # 向上回溯到最近存在的祖先。
        while True:
            try:
                st = os.stat(current)
                break
            except FileNotFoundError:
                parent = current.parent
                if parent == current:
                    # 到达文件系统根仍不存在：放行，交给上层 I/O。
                    return
                current = parent
            except OSError:
                # 权限问题等异常；守卫不吞此类错误，原样抛回让上层处理。
                raise
        if st.st_dev != self._root_dev:
            raise WorkspaceConfinementViolation(
                requested_path=str(host_path),
                reason=ConfinementViolationReason.CROSS_DEVICE,
            )
