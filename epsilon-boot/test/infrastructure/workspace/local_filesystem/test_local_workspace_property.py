"""``LocalFilesystemWorkspace._to_host_path`` 的 Hypothesis 属性测试。

覆盖 design.md §正确性属性 1：

    对任意 ``Workspace`` 实例 ``ws`` 和任意字符串 ``s``：若 ``ws.resolve_path(s)``
    返回 ``wp: WorkspacePath``，则对 ``LocalFilesystemWorkspace`` 而言，
    ``_to_host_path(wp)`` 的规范化结果始终满足
    ``os.path.commonpath([str(host), str(root)]) == str(root)``。

**依赖**：本测试依赖 ``hypothesis`` 库；若运行环境未安装 ``hypothesis``，
用例将在模块级通过 ``pytest.importorskip`` 自动跳过而非失败。
"""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.workspace.exceptions import WorkspaceConfinementViolation
from domain.workspace.policy import WorkspacePolicy
from infrastructure.workspace.local_filesystem import LocalFilesystemWorkspace

hypothesis = pytest.importorskip("hypothesis")

# 随机字符串策略：允许任意 Unicode 字符以覆盖 resolve 成功 / 失败的全部分支。
_ANY_TEXT = st.text(min_size=0, max_size=64)


class TestToHostPathStaysUnderRoot:
    """Property 1：``_to_host_path(wp)`` 始终落在 ``root`` 之下。"""

    @settings(max_examples=200, deadline=None)
    @given(s=_ANY_TEXT)
    def test_host_path_commonpath_equals_root(
        self, s: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """对任意字符串 ``s``：若 ``ws.resolve_path(s)`` 成功得 ``wp``，则
        ``_to_host_path(wp)`` 与 ``root`` 的 ``commonpath`` 必等于 ``root``。

        失败（``WorkspaceConfinementViolation``）路径不在本属性范围内，跳过
        该样本；``os.path.commonpath`` 在参数跨驱动器等异常情况下会抛
        ``ValueError``，此时也视为本属性不适用（但本测试使用 tmp_path 做
        root，理论上不会跨驱动器）。
        """
        # 每次用例动态建一个 tmp root，避免 hypothesis 多次调用互相污染
        root = tmp_path_factory.mktemp("ws_prop").resolve()
        ws = LocalFilesystemWorkspace(
            root=root,
            follow_symlinks=False,
            policy=WorkspacePolicy(),
        )
        try:
            wp = ws.resolve_path(s)
        except WorkspaceConfinementViolation:
            # Policy 拒绝：本样本跳过
            return

        host = ws.to_host_path(wp)
        # 不得触发 I/O：_to_host_path 是纯字符串拼接
        common = os.path.commonpath([str(host), str(root)])
        assert common == str(root), (
            f"_to_host_path escaped root: s={s!r} wp={wp.to_posix()!r} "
            f"host={host!r} root={root!r} common={common!r}"
        )
