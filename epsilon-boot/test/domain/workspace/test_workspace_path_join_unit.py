"""``WorkspacePath.join`` 的 happy-path 与越根/非法字符拒绝单元测试。

覆盖需求 2.2 / 2.5 / 2.6 中 ``join`` 拼接段的归一化行为与拒绝策略。
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)
from domain.workspace.value_objects import WorkspacePath


def _wp(posix: str) -> WorkspacePath:
    """测试夹具：直接用 PurePosixPath 构造 WorkspacePath（跳过 Policy）。"""
    return WorkspacePath(_posix=PurePosixPath(posix))


class TestJoinHappyPath:
    """``join`` 合法段的基础归一化行为。"""

    def test_append_simple_file_segment(self) -> None:
        """简单文件段 ``a.md`` 追加在根之后。"""
        result = _wp("/").join("a.md")
        assert result == _wp("/a.md")

    def test_append_multi_segment(self) -> None:
        """包含 ``/`` 的多段 ``sub/x`` 按 POSIX 规则拼接。"""
        result = _wp("/").join("sub/x")
        assert result == _wp("/sub/x")

    def test_leading_dot_slash_is_stripped(self) -> None:
        """段起始的 ``./`` 被折叠为空，不产生额外层级。"""
        result = _wp("/").join("./x")
        assert result == _wp("/x")

    def test_embedded_dotdot_collapses(self) -> None:
        """段中的 ``..`` 在不越过根的前提下被就地折叠。"""
        result = _wp("/").join("a/../b")
        assert result == _wp("/b")

    def test_dotdot_with_nested_parent_path(self) -> None:
        """连续 ``..`` 结合已嵌套父级的 happy-path：``/a/b/c`` + ``../../x`` → ``/a/x``。"""
        result = _wp("/a/b/c").join("../../x")
        assert result == _wp("/a/x")

    def test_join_preserves_frozen_dataclass_equality(self) -> None:
        """``join`` 返回的是新实例，且语义上与直接构造的路径相等。"""
        wp = _wp("/root")
        joined = wp.join("child/leaf")
        assert joined == _wp("/root/child/leaf")
        # 原实例未被修改。
        assert wp == _wp("/root")


class TestJoinAbsoluteOutside:
    """``..`` 回退越过根的越界拒绝。"""

    def test_dotdot_to_outside_from_root(self) -> None:
        """从根路径使用 ``..`` 直接越界。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/").join("../../etc")
        assert excinfo.value.reason is ConfinementViolationReason.ABSOLUTE_OUTSIDE

    def test_dotdot_stream_exceeds_nesting(self) -> None:
        """``..`` 数量超出已有层级深度 → ABSOLUTE_OUTSIDE。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/a").join("../../../")
        assert excinfo.value.reason is ConfinementViolationReason.ABSOLUTE_OUTSIDE

    def test_dotdot_just_at_root_is_still_outside(self) -> None:
        """两层退出 ``/a`` 会越过根，判定 ABSOLUTE_OUTSIDE（而非 ``/``）。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/a").join("../..")
        assert excinfo.value.reason is ConfinementViolationReason.ABSOLUTE_OUTSIDE


class TestJoinIllegalCharacters:
    """段中非法字符的拒绝。"""

    def test_nul_byte_rejected(self) -> None:
        """段含 NUL 字符 → NUL_BYTE。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/").join("a\x00b")
        assert excinfo.value.reason is ConfinementViolationReason.NUL_BYTE

    def test_backslash_rejected(self) -> None:
        """段含反斜杠 → BACKSLASH。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/").join("a\\b")
        assert excinfo.value.reason is ConfinementViolationReason.BACKSLASH

    def test_windows_drive_prefix_rejected(self) -> None:
        """段以 Windows 盘符前缀起始 → WINDOWS_DRIVE。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/").join("C:/a")
        assert excinfo.value.reason is ConfinementViolationReason.WINDOWS_DRIVE

    def test_lowercase_windows_drive_prefix_rejected(self) -> None:
        """小写 Windows 盘符前缀同样被拒。"""
        with pytest.raises(WorkspaceConfinementViolation) as excinfo:
            _wp("/").join("d:/x")
        assert excinfo.value.reason is ConfinementViolationReason.WINDOWS_DRIVE


class TestJoinTypeValidation:
    """类型校验：非 ``str`` 抛 ``TypeError``。"""

    def test_non_string_segment_raises_type_error(self) -> None:
        """段为 int / None / bytes 时抛 ``TypeError``。"""
        wp = _wp("/")
        with pytest.raises(TypeError):
            wp.join(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            wp.join(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            wp.join(b"bytes")  # type: ignore[arg-type]
