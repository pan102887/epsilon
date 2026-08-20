"""``StorageTier`` 领域枚举的单元测试与依赖方向静态断言。

覆盖：

1. 枚举取值：含 USER / PROJECT / TENANT，均为 ``str`` 子类，且取值文本正确
   （``StorageTier.PROJECT == "project"``）。
2. 依赖方向静态断言（复用仓库既有源码扫描风格）：``storage_tier.py`` 源码不含
   任何物理路径 / 后端字符串字面量（``.epsilon`` / ``~`` / ``WORKSPACE_ROOT`` /
   ``OSS`` / ``S3``），也不 import 任何 ``infrastructure`` 模块——保证领域层与
   基础设施解耦（需求 1.2、正确性属性 Property 3 部分）。
"""

from __future__ import annotations

import inspect

import pytest

from domain.storage import storage_tier as storage_tier_module
from domain.storage.storage_tier import StorageTier

_FORBIDDEN_LITERALS: tuple[str, ...] = (
    ".epsilon",
    "~",
    "WORKSPACE_ROOT",
    "OSS",
    "S3",
)
"""领域层禁止出现的物理路径 / 后端字符串字面量。"""


def test_storage_tier_contains_expected_members() -> None:
    """枚举含 USER / PROJECT / TENANT 三个成员。"""
    assert {member.name for member in StorageTier} == {"USER", "PROJECT", "TENANT"}


def test_storage_tier_members_are_str() -> None:
    """每个成员均为 ``str`` 子类（StrEnum 语义）。"""
    for member in StorageTier:
        assert isinstance(member, str)


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (StorageTier.USER, "user"),
        (StorageTier.PROJECT, "project"),
        (StorageTier.TENANT, "tenant"),
    ],
)
def test_storage_tier_values(member: StorageTier, expected_value: str) -> None:
    """各成员取值文本正确，且可直接与字符串比较。"""
    assert member == expected_value
    assert member.value == expected_value


def test_storage_tier_project_equals_project_string() -> None:
    """``StorageTier.PROJECT == "project"``（tasks.md 精确验收）。"""
    assert StorageTier.PROJECT == "project"


def test_storage_tier_source_has_no_physical_path_or_backend_literal() -> None:
    """依赖方向静态断言：源码不含物理路径 / 后端字符串字面量。"""
    source = inspect.getsource(storage_tier_module)
    for literal in _FORBIDDEN_LITERALS:
        assert literal not in source, (
            f"storage_tier.py 不应出现物理路径 / 后端字面量 {literal!r}（需求 1.2）"
        )


def test_storage_tier_source_does_not_import_infrastructure() -> None:
    """依赖方向静态断言：源码不 import 任何 ``infrastructure`` 模块。"""
    source = inspect.getsource(storage_tier_module)
    assert "infrastructure" not in source, (
        "storage_tier.py 不应 import 或引用 infrastructure（DDD 依赖方向）"
    )
