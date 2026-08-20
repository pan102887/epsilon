"""Workspace 值对象单元测试模块。

覆盖 ``WorkspacePath`` / ``WorkspaceStatEntry`` / ``WorkspaceCapabilities`` /
``WorkspaceBackendKind`` 的冻结性、等价性与基础行为。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import PurePosixPath

import pytest

from domain.workspace.value_objects import (
    WorkspaceBackendKind,
    WorkspaceCapabilities,
    WorkspacePath,
    WorkspaceStatEntry,
)


class TestWorkspacePathBasics:
    """``WorkspacePath`` 的不可变性、等价性与基础方法。"""

    def test_is_frozen(self) -> None:
        """验证 ``WorkspacePath`` 是 frozen dataclass，赋值属性抛 FrozenInstanceError。"""
        wp = WorkspacePath(_posix=PurePosixPath("/a"))
        with pytest.raises(FrozenInstanceError):
            wp._posix = PurePosixPath("/b")  # type: ignore[misc]

    def test_equality_on_same_value(self) -> None:
        """同值 ``WorkspacePath`` 实例 ``==`` 判定为相等，可作 dict 键/集合元素。"""
        wp1 = WorkspacePath(_posix=PurePosixPath("/a/b"))
        wp2 = WorkspacePath(_posix=PurePosixPath("/a/b"))
        assert wp1 == wp2
        assert hash(wp1) == hash(wp2)
        assert {wp1, wp2} == {wp1}

    def test_to_posix_returns_slash_prefixed_string(self) -> None:
        """``to_posix()`` 返回以 "/" 起始的字符串。"""
        wp = WorkspacePath(_posix=PurePosixPath("/a/b/c.md"))
        assert wp.to_posix() == "/a/b/c.md"

    def test_to_posix_for_root(self) -> None:
        """工作区根路径对应的 ``to_posix()`` 为 "/"。"""
        wp_root = WorkspacePath(_posix=PurePosixPath("/"))
        assert wp_root.to_posix() == "/"

    def test_str_equals_to_posix(self) -> None:
        """``__str__`` 与 ``to_posix()`` 返回一致。"""
        wp = WorkspacePath(_posix=PurePosixPath("/x/y"))
        assert str(wp) == wp.to_posix()

    def test_parent_of_nested_path(self) -> None:
        """多级路径的 ``parent()`` 返回上一级。"""
        wp = WorkspacePath(_posix=PurePosixPath("/a/b/c"))
        assert wp.parent() == WorkspacePath(_posix=PurePosixPath("/a/b"))

    def test_parent_of_first_level(self) -> None:
        """一级路径 ``/a`` 的 ``parent()`` 为根 ``/``。"""
        wp = WorkspacePath(_posix=PurePosixPath("/a"))
        assert wp.parent() == WorkspacePath(_posix=PurePosixPath("/"))

    def test_parent_of_root_is_root(self) -> None:
        """根路径的 ``parent()`` 仍为根（与 PurePosixPath 行为一致）。"""
        wp_root = WorkspacePath(_posix=PurePosixPath("/"))
        assert wp_root.parent() == wp_root

    def test_name_returns_last_segment(self) -> None:
        """``name()`` 返回路径末段。"""
        assert WorkspacePath(_posix=PurePosixPath("/a/b/c.md")).name() == "c.md"
        assert WorkspacePath(_posix=PurePosixPath("/a")).name() == "a"

    def test_name_for_root_is_empty(self) -> None:
        """根路径的 ``name()`` 为空串。"""
        assert WorkspacePath(_posix=PurePosixPath("/")).name() == ""


class TestWorkspaceStatEntry:
    """``WorkspaceStatEntry`` 的冻结性与字段存储。"""

    def test_is_frozen(self) -> None:
        """``WorkspaceStatEntry`` 是 frozen dataclass。"""
        entry = WorkspaceStatEntry(
            path=WorkspacePath(_posix=PurePosixPath("/a")),
            is_file=True,
            is_dir=False,
            size=100,
            mtime=1234567890.0,
        )
        with pytest.raises(FrozenInstanceError):
            entry.size = 200  # type: ignore[misc]

    def test_nullable_size_and_mtime(self) -> None:
        """``size`` / ``mtime`` 可为 ``None``（对应不支持元数据的后端）。"""
        entry = WorkspaceStatEntry(
            path=WorkspacePath(_posix=PurePosixPath("/prefix/")),
            is_file=False,
            is_dir=True,
            size=None,
            mtime=None,
        )
        assert entry.size is None
        assert entry.mtime is None


class TestWorkspaceCapabilities:
    """``WorkspaceCapabilities`` 的默认值与冻结性。"""

    def test_default_fields_are_all_false(self) -> None:
        """``WorkspaceCapabilities`` 默认 6 个字段全为 ``False``。"""
        cap = WorkspaceCapabilities()
        assert cap.supports_symlinks is False
        assert cap.supports_atomic_write is False
        assert cap.supports_append is False
        assert cap.supports_streaming is False
        assert cap.supports_large_files is False
        assert cap.local_materialization is False

    def test_is_frozen(self) -> None:
        """``WorkspaceCapabilities`` 是 frozen dataclass。"""
        cap = WorkspaceCapabilities()
        with pytest.raises(FrozenInstanceError):
            cap.local_materialization = True  # type: ignore[misc]

    def test_custom_construction(self) -> None:
        """以关键字参数自定义能力字段。"""
        cap = WorkspaceCapabilities(
            supports_symlinks=True,
            supports_atomic_write=True,
            local_materialization=True,
        )
        assert cap.supports_symlinks is True
        assert cap.supports_atomic_write is True
        assert cap.local_materialization is True
        # 未显式赋值的字段仍为默认 False。
        assert cap.supports_append is False


class TestWorkspaceBackendKind:
    """``WorkspaceBackendKind`` 枚举。"""

    def test_local_filesystem_value(self) -> None:
        """``LOCAL_FILESYSTEM`` 的枚举值为字符串 ``local_filesystem``。"""
        assert WorkspaceBackendKind.LOCAL_FILESYSTEM.value == "local_filesystem"

    def test_is_str_enum(self) -> None:
        """``WorkspaceBackendKind`` 同时是 ``str`` 子类，便于配置文件透传。"""
        assert isinstance(WorkspaceBackendKind.LOCAL_FILESYSTEM, str)
        assert WorkspaceBackendKind.LOCAL_FILESYSTEM == "local_filesystem"
