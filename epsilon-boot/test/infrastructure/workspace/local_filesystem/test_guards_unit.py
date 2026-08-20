"""``SymlinkGuard`` 与 ``IdentityGuard`` 单元测试。

覆盖范围（对应 tasks 6.2 用例清单）：

- ``SymlinkGuard`` 严格模式（``follow_symlinks=False``）：
  - 含符号链接的路径段立即抛 ``SYMLINK_ESCAPE``；
  - 无符号链接的常规路径通过；
  - 尾段不存在但前缀合法的路径通过（支持 ``write`` 场景）。
- ``SymlinkGuard`` 宽松模式（``follow_symlinks=True``）：
  - 符号链接指向 root 内部：通过；
  - 符号链接指向 root 外部：抛 ``SYMLINK_ESCAPE``。
- ``IdentityGuard``：
  - 通过 ``monkeypatch`` 模拟 ``st_dev`` 不同：抛 ``CROSS_DEVICE``；
  - ``st_dev`` 相同：通过；
  - ``host_path`` 不存在时回溯到最近存在的祖先做校验。

Windows 下符号链接创建受限，相关用例以 ``@pytest.mark.skipif`` 跳过；
``IdentityGuard`` 用例以 ``monkeypatch`` 构造 ``os.stat`` 返回值，不依赖
真实的跨设备文件系统。
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)
from infrastructure.workspace.local_filesystem._guards import (
    IdentityGuard,
    SymlinkGuard,
)

# 平台条件：Windows 下创建符号链接需要特殊权限，测试跳过。
_SKIP_IF_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows 下符号链接需特权，跳过 symlink 相关用例",
)


class TestSymlinkGuardStrict:
    """``follow_symlinks=False``：逐段 lstat，拒绝任意级别的符号链接。"""

    def test_plain_path_passes(self, tmp_path: Path) -> None:
        """无任何符号链接的常规路径应通过。"""
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.txt").write_text("hello", encoding="utf-8")
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=False)
        # 通过即不抛异常。
        guard.check(tmp_path / "sub" / "file.txt")

    def test_nonexistent_leaf_passes(self, tmp_path: Path) -> None:
        """尾段尚未创建、但前缀合法的路径应通过（write 场景）。"""
        (tmp_path / "sub").mkdir()
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=False)
        guard.check(tmp_path / "sub" / "not_yet.txt")

    @_SKIP_IF_WINDOWS
    def test_symlink_segment_rejected(self, tmp_path: Path) -> None:
        """路径中出现符号链接段时抛 ``SYMLINK_ESCAPE``。"""
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=False)
        with pytest.raises(WorkspaceConfinementViolation) as exc:
            guard.check(link / "file.txt")
        assert exc.value.reason == ConfinementViolationReason.SYMLINK_ESCAPE

    @_SKIP_IF_WINDOWS
    def test_symlink_to_external_rejected_strict(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """符号链接指向 root 之外，在严格模式下仍被拒绝（因为链接本身就是越界信号）。"""
        outside = tmp_path_factory.mktemp("outside")
        link = tmp_path / "escape"
        link.symlink_to(outside)
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=False)
        with pytest.raises(WorkspaceConfinementViolation) as exc:
            guard.check(link / "x.txt")
        assert exc.value.reason == ConfinementViolationReason.SYMLINK_ESCAPE

    def test_path_outside_root_rejected(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """``host_path`` 完全不在 root 下 → 判定 ``SYMLINK_ESCAPE``。"""
        outside = tmp_path_factory.mktemp("other") / "file.txt"
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=False)
        with pytest.raises(WorkspaceConfinementViolation) as exc:
            guard.check(outside)
        assert exc.value.reason == ConfinementViolationReason.SYMLINK_ESCAPE


class TestSymlinkGuardFollow:
    """``follow_symlinks=True``：允许跟随，但解引用后仍须在 root 之内。"""

    @_SKIP_IF_WINDOWS
    def test_symlink_pointing_inside_root_passes(self, tmp_path: Path) -> None:
        """指向 root 内部的符号链接应通过。"""
        real = tmp_path / "real"
        real.mkdir()
        (real / "file.txt").write_text("x", encoding="utf-8")
        link = tmp_path / "alias"
        link.symlink_to(real)
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=True)
        guard.check(link / "file.txt")

    @_SKIP_IF_WINDOWS
    def test_symlink_pointing_outside_root_rejected(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """指向 root 外部的符号链接解引用后应判定 ``SYMLINK_ESCAPE``。"""
        outside = tmp_path_factory.mktemp("outside")
        link = tmp_path / "escape"
        link.symlink_to(outside)
        guard = SymlinkGuard(root=tmp_path, follow_symlinks=True)
        with pytest.raises(WorkspaceConfinementViolation) as exc:
            guard.check(link / "x.txt")
        assert exc.value.reason == ConfinementViolationReason.SYMLINK_ESCAPE


class TestIdentityGuard:
    """``IdentityGuard`` 跨设备校验。"""

    def test_same_device_passes(self, tmp_path: Path) -> None:
        """与 root 同设备的路径应通过（同一 tmp_path 下的文件必然同设备）。"""
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")
        guard = IdentityGuard(root=tmp_path)
        guard.check(tmp_path / "file.txt")

    def test_different_device_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``st_dev`` 不同时抛 ``CROSS_DEVICE``。"""
        # 先创建守卫（此时 root_dev 已缓存为真实值）。
        guard = IdentityGuard(root=tmp_path)
        target = tmp_path / "file.txt"
        target.write_text("x", encoding="utf-8")

        # 用 monkeypatch 把 os.stat 替换为在 target 上返回不同 st_dev。
        real_stat = os.stat
        fake_dev = guard._root_dev + 999

        def _fake_stat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            st = real_stat(path, *args, **kwargs)
            if str(path) == str(target):
                return SimpleNamespace(
                    st_dev=fake_dev,
                    st_ino=st.st_ino,
                    st_mode=st.st_mode,
                    st_size=st.st_size,
                    st_mtime=st.st_mtime,
                )
            return st

        monkeypatch.setattr("infrastructure.workspace.local_filesystem._guards.os.stat", _fake_stat)

        with pytest.raises(WorkspaceConfinementViolation) as exc:
            guard.check(target)
        assert exc.value.reason == ConfinementViolationReason.CROSS_DEVICE

    def test_nonexistent_path_backtracks_to_ancestor(self, tmp_path: Path) -> None:
        """``host_path`` 不存在时回溯到存在的祖先（root）做校验，应通过。"""
        guard = IdentityGuard(root=tmp_path)
        # 目标路径深度嵌套且完全不存在。
        target = tmp_path / "deep" / "nested" / "not_yet.txt"
        guard.check(target)


class TestIdentityGuardRootCapture:
    """启动期捕获 ``st_dev`` 的行为。"""

    def test_root_dev_captured_on_init(self, tmp_path: Path) -> None:
        """``__init__`` 时立即读取 ``st_dev`` 并缓存为 ``_root_dev``。"""
        guard = IdentityGuard(root=tmp_path)
        assert guard._root_dev == os.stat(tmp_path).st_dev
