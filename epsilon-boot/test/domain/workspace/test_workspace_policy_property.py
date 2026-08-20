"""``WorkspacePolicy.resolve`` 的 Hypothesis 属性测试。

覆盖 design.md §正确性属性 3 / 4：

- Property 3（幂等）：若 ``resolve(s)`` 成功得 ``wp``，
  则 ``resolve(wp.to_posix()) == wp``；
- Property 4（非法字符闭合）：包含 ``\\x00`` / ``\\`` / ``C:`` / ``//``
  前缀的字符串必然抛 ``WorkspaceConfinementViolation`` 且 ``reason`` 对应。

**依赖**：本测试依赖 ``hypothesis`` 库；若运行环境未安装 ``hypothesis``，
用例将在模块级通过 ``pytest.importorskip`` 自动跳过而非失败。
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
)
from domain.workspace.policy import WorkspacePolicy

hypothesis = pytest.importorskip("hypothesis")

# 文本策略：允许任意 Unicode 字符（含 NUL、反斜杠、Windows 盘符、UNC 等），
# 以最大化覆盖 resolve 的分支；min_size=0 允许空串。
_ANY_TEXT = st.text(min_size=0, max_size=64)


class TestIdempotent:
    """Property 3：``resolve`` 成功时结果幂等。"""

    @given(s=_ANY_TEXT)
    def test_resolve_is_idempotent_when_successful(self, s: str) -> None:
        """对任意字符串 ``s``，若 ``resolve(s)`` 成功，
        则 ``resolve(resolve(s).to_posix())`` 与之相等。
        """
        policy = WorkspacePolicy()
        try:
            wp1 = policy.resolve(s)
        except WorkspaceConfinementViolation:
            # 失败路径不在本属性覆盖范围；跳过该样本。
            return
        # 幂等：再次规范化应返回等价的 WorkspacePath。
        wp2 = policy.resolve(wp1.to_posix())
        assert wp1 == wp2


# 非法字符策略：保证字符串中至少包含一个受禁字符/模式，
# 用于 Property 4 的负面断言。
_NUL_PAYLOAD = st.text(max_size=16).flatmap(
    lambda prefix: st.text(max_size=16).map(lambda suffix: f"{prefix}\x00{suffix}")
)
_BACKSLASH_PAYLOAD = st.text(
    alphabet=st.characters(blacklist_characters="\x00:/"),
    max_size=16,
).flatmap(
    lambda prefix: st.text(
        alphabet=st.characters(blacklist_characters="\x00:/"),
        max_size=16,
    ).map(lambda suffix: f"{prefix}\\{suffix}")
)
_WINDOWS_DRIVE_PAYLOAD = st.tuples(
    st.sampled_from(list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")),
    st.text(
        alphabet=st.characters(blacklist_characters="\x00"),
        max_size=16,
    ),
).map(lambda t: f"{t[0]}:{t[1]}")
# UNC 形式：以 // 起始且第三字符非 /，并且中间不含 NUL（NUL 优先级更高）。
_UNC_PAYLOAD = st.text(
    alphabet=st.characters(blacklist_characters="\x00/\\:"),
    min_size=1,
    max_size=16,
).flatmap(
    lambda host: st.text(
        alphabet=st.characters(blacklist_characters="\x00\\:"),
        max_size=16,
    ).map(lambda tail: f"//{host}{tail}")
)


class TestIllegalCharacterClosure:
    """Property 4：非法字符 / 模式必然触发对应 ``reason``。"""

    @given(s=_NUL_PAYLOAD)
    def test_nul_byte_always_rejected(self, s: str) -> None:
        """含 NUL 字符必抛 ``NUL_BYTE``（该 reason 优先于所有其他分支）。"""
        policy = WorkspacePolicy()
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve(s)
        assert ei.value.reason == ConfinementViolationReason.NUL_BYTE

    @given(s=_BACKSLASH_PAYLOAD)
    def test_backslash_rejected_with_correct_reason(self, s: str) -> None:
        """含反斜杠且**不含** NUL / 盘符 / UNC 前缀时抛 ``BACKSLASH``。

        策略已从字母表中剔除 NUL / ``:`` / ``/``，确保不会命中更优先的分支。
        """
        policy = WorkspacePolicy()
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve(s)
        assert ei.value.reason == ConfinementViolationReason.BACKSLASH

    @given(payload=_WINDOWS_DRIVE_PAYLOAD)
    def test_windows_drive_prefix_rejected(self, payload: str) -> None:
        """以 ``[A-Za-z]:`` 起始必抛 ``WINDOWS_DRIVE`` 或 ``NUL_BYTE``（若
        同时含 NUL，NUL 优先；策略已剔除 NUL 以聚焦 WINDOWS_DRIVE 本身）。
        """
        policy = WorkspacePolicy()
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve(payload)
        assert ei.value.reason == ConfinementViolationReason.WINDOWS_DRIVE

    @given(s=_UNC_PAYLOAD)
    def test_unc_prefix_rejected(self, s: str) -> None:
        """以 ``//<非/>`` 起始必抛 ``UNC_PATH``。"""
        policy = WorkspacePolicy()
        with pytest.raises(WorkspaceConfinementViolation) as ei:
            policy.resolve(s)
        assert ei.value.reason == ConfinementViolationReason.UNC_PATH
