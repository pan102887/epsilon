"""``append_workspace_path_guidance`` 幂等性属性测试。

对 :func:`infrastructure.prompt.workspace_guidance.append_workspace_path_guidance`
的核心不变量施加 Hypothesis 随机 Unicode 覆盖：

- Property 3（幂等）：对任意字符串 ``s``，``append_workspace_path_guidance``
  的二次调用等于一次调用；
- 返回值必然以 :data:`_WORKSPACE_PATH_GUIDANCE` 的 ``strip()`` 结尾
  （基于 ``rstrip().endswith(...)`` 比较，见需求 6.3）。

本测试与 design.md §7 / §正确性属性 §Property 3 对齐。
"""

# Validates: Property 3 / Requirements 6.1, 6.3

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from infrastructure.prompt.workspace_guidance import (
    _WORKSPACE_PATH_GUIDANCE,
    append_workspace_path_guidance,
)


@given(s=st.text())
def test_append_workspace_path_guidance_is_idempotent(s: str) -> None:
    """对任意 Unicode 字符串，二次追加等于一次追加（Property 3 / 需求 6.1）。"""
    once = append_workspace_path_guidance(s)
    twice = append_workspace_path_guidance(once)
    assert twice == once


@given(s=st.text())
def test_result_ends_with_workspace_path_guidance_stripped(s: str) -> None:
    """返回值的 ``rstrip()`` 必以 ``_WORKSPACE_PATH_GUIDANCE.strip()`` 结尾（需求 6.3）。"""
    result = append_workspace_path_guidance(s)
    assert result.rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip())
