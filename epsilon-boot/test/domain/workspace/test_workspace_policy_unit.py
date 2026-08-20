"""``WorkspacePolicy.resolve`` 的 example-based 单元测试。

覆盖需求 2.1 / 2.2 / 2.3 / 2.4 / 2.5 / 2.6：

- 相对路径锚定到工作区根；
- 绝对形式以 ``/`` 起始解释为工作区绝对路径；
- ``.`` / ``..`` / 重复斜杠的归一化；
- 越根拒绝；
- 非法字符（NUL / 反斜杠 / Windows 盘符 / UNC）拒绝；
- 空串 / ``"."`` / ``"/"`` 统一映射到工作区根；
- 返回值为 ``WorkspacePath`` 且幂等。
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)
from domain.workspace.policy import WorkspacePolicy
from domain.workspace.value_objects import WorkspacePath


@pytest.fixture()
def policy() -> WorkspacePolicy:
    """复用的 ``WorkspacePolicy`` 实例。"""
    return WorkspacePolicy()


def _wp(posix: str) -> WorkspacePath:
    """测试夹具：直接用 ``PurePosixPath`` 构造 ``WorkspacePath``。"""
    return WorkspacePath(_posix=PurePosixPath(posix))


class TestHappyPath:
    """``resolve`` 对合法输入的归一化。"""

    def test_relative_path_anchored_to_root(self, policy: WorkspacePolicy) -> None:
        """相对路径 ``notes.md`` 应锚定到 ``/notes.md``。"""
        assert policy.resolve("notes.md") == _wp("/notes.md")

    def test_absolute_path_preserved(self, policy: WorkspacePolicy) -> None:
        """以 ``/`` 起始的绝对形式被直接解释为工作区绝对路径。"""
        assert policy.resolve("/a/b") == _wp("/a/b")

    def test_leading_dot_slash_normalized(self, policy: WorkspacePolicy) -> None:
        """``./a`` 中的 ``.`` 被归一化掉，结果为 ``/a``。"""
        assert policy.resolve("./a") == _wp("/a")

    def test_middle_dot_segment_normalized(self, policy: WorkspacePolicy) -> None:
        """中间的 ``.`` 段被归一化掉：``a/./b`` → ``/a/b``。"""
        assert policy.resolve("a/./b") == _wp("/a/b")

    def test_double_dot_folded_within_bounds(self, policy: WorkspacePolicy) -> None:
        """``..`` 在边界内被折叠：``a/../b`` → ``/b``。"""
        assert policy.resolve("a/../b") == _wp("/b")

    def test_nested_mixed_normalization(self, policy: WorkspacePolicy) -> None:
        """混合 ``/.`` / ``..`` 的路径归一化。"""
        assert policy.resolve("/a/./b/../c") == _wp("/a/c")

    def test_triple_slash_not_unc(self, policy: WorkspacePolicy) -> None:
        """``///a`` 的第三字符为 ``/``，**不**视为 UNC，归一化为 ``/a``。"""
        assert policy.resolve("///a") == _wp("/a")

    def test_deep_relative_path(self, policy: WorkspacePolicy) -> None:
        """多层相对路径段按 POSIX 拼接。"""
        assert policy.resolve("sub/dir/file.txt") == _wp("/sub/dir/file.txt")


class TestRootMapping:
    """空串 / ``"."`` / ``"/"`` 统一映射到工作区根（需求 2.4 / 6.4 / 7.2）。"""

    def test_empty_string_maps_to_root(self, policy: WorkspacePolicy) -> None:
        """空串返回工作区根。"""
        assert policy.resolve("") == _wp("/")

    def test_single_dot_maps_to_root(self, policy: WorkspacePolicy) -> None:
        """``"."`` 返回工作区根。"""
        assert policy.resolve(".") == _wp("/")

    def test_single_slash_maps_to_root(self, policy: WorkspacePolicy) -> None:
        """``"/"`` 返回工作区根。"""
        assert policy.resolve("/") == _wp("/")


class TestAbsoluteOutside:
    """``..`` 归一化后越根的越界拒绝（需求 2.2）。"""

    def test_simple_parent_escape_raises(self, policy: WorkspacePolicy) -> None:
        """``../etc/passwd`` 越出工作区根。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("../etc/passwd")
        assert ei.value.reason == ConfinementViolationReason.ABSOLUTE_OUTSIDE
        assert ei.value.requested_path == "../etc/passwd"

    def test_double_parent_escape_raises(self, policy: WorkspacePolicy) -> None:
        """``../../foo`` 越根两层。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("../../foo")
        assert ei.value.reason == ConfinementViolationReason.ABSOLUTE_OUTSIDE

    def test_absolute_parent_escape_raises(self, policy: WorkspacePolicy) -> None:
        """``/..`` 自根回退越界。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("/..")
        assert ei.value.reason == ConfinementViolationReason.ABSOLUTE_OUTSIDE


class TestIllegalCharacters:
    """非法字符前置拒绝（需求 2.5 / 2.6）。"""

    def test_nul_byte_rejected(self, policy: WorkspacePolicy) -> None:
        """NUL 字符触发 ``NUL_BYTE``。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("a\x00b")
        assert ei.value.reason == ConfinementViolationReason.NUL_BYTE

    def test_backslash_rejected(self, policy: WorkspacePolicy) -> None:
        """单反斜杠触发 ``BACKSLASH``。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("a\\b")
        assert ei.value.reason == ConfinementViolationReason.BACKSLASH

    def test_windows_drive_rejected(self, policy: WorkspacePolicy) -> None:
        """Windows 盘符 ``C:\\Windows`` 触发 ``WINDOWS_DRIVE``（优先于 BACKSLASH）。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("C:\\Windows")
        assert ei.value.reason == ConfinementViolationReason.WINDOWS_DRIVE

    def test_windows_drive_lowercase_rejected(self, policy: WorkspacePolicy) -> None:
        """小写盘符 ``c:/foo`` 同样被拒绝。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("c:/foo")
        assert ei.value.reason == ConfinementViolationReason.WINDOWS_DRIVE

    def test_unc_path_rejected(self, policy: WorkspacePolicy) -> None:
        """``\\\\server\\share`` 字面量即 POSIX 形式的 ``//server/share``。

        注意 tasks.md 中 ``\\\\server\\share`` 是 Windows 风格转义，Python
        源码字面量含反斜杠会先命中 ``BACKSLASH``；此处使用更典型的 POSIX
        UNC 形式 ``//server/share`` 以精确覆盖 ``UNC_PATH`` 分支。
        """
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("//server/share")
        assert ei.value.reason == ConfinementViolationReason.UNC_PATH


class TestResolveContract:
    """``resolve`` 返回值与异常契约。"""

    def test_returns_workspace_path_instance(self, policy: WorkspacePolicy) -> None:
        """成功时返回 ``WorkspacePath`` 实例。"""
        result = policy.resolve("x")
        assert isinstance(result, WorkspacePath)

    def test_violation_carries_requested_path(self, policy: WorkspacePolicy) -> None:
        """越界违规保留原始请求字符串，便于日志溯源。"""
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve("../x")
        assert ei.value.requested_path == "../x"

    def test_policy_is_frozen(self) -> None:
        """``WorkspacePolicy`` 为 frozen dataclass，相等性基于类型本身。"""
        p1 = WorkspacePolicy()
        p2 = WorkspacePolicy()
        assert p1 == p2
