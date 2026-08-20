"""LocalFileTierResolver 单元测试。

覆盖 tier→目录映射确定性、子目录创建幂等、TENANT 抛错（Property 1），
PROJECT-traces 与既有 .epsilon/traces 等价（Property 2），以及 project-hash
单一生成点、确定性、16 位、不含路径明文与 USER tier / persistence 共享
分区键（Property 10）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from domain.storage.storage_tier import StorageTier
from infrastructure.storage.local_file_tier_resolver import (
    LocalFileTierResolver,
    ResolvedTierLayout,
)

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def test_resolve_project_is_deterministic(tmp_path: Path) -> None:
    """同一实例、同基点的 PROJECT 解析恒返回同一 home（Property 1）。"""
    resolver = LocalFileTierResolver(project_base=tmp_path)
    first = resolver.resolve(StorageTier.PROJECT)
    second = resolver.resolve(StorageTier.PROJECT)
    assert first.home == second.home
    assert first.home == tmp_path.resolve() / ".epsilon"


def test_resolve_user_is_deterministic(tmp_path: Path) -> None:
    """同一实例、同基点的 USER 解析恒返回同一 home（Property 1）。"""
    user_base = tmp_path / "home"
    resolver = LocalFileTierResolver(project_base=tmp_path / "proj", user_base=user_base)
    first = resolver.resolve(StorageTier.USER)
    second = resolver.resolve(StorageTier.USER)
    assert first.home == second.home
    assert first.home == user_base.resolve() / ".epsilon" / resolver.project_hash()


def test_resolve_tenant_raises_value_error(tmp_path: Path) -> None:
    """TENANT 无本地实现，恒抛 ValueError（Property 1）。"""
    resolver = LocalFileTierResolver(project_base=tmp_path)
    with pytest.raises(ValueError):
        resolver.resolve(StorageTier.TENANT)


@pytest.mark.parametrize(
    "subdir_getter",
    [
        lambda layout: layout.sessions_dir(),
        lambda layout: layout.traces_dir(),
        lambda layout: layout.artifacts_dir(),
        lambda layout: layout.logs_dir(),
    ],
)
def test_subdir_creation_is_idempotent(
    tmp_path: Path,
    subdir_getter: Callable[[ResolvedTierLayout], Path],
) -> None:
    """各子目录 create=True 时幂等创建，重复调用不报错（Property 1，需求 1.5）。"""
    resolver = LocalFileTierResolver(project_base=tmp_path)
    layout = resolver.resolve(StorageTier.PROJECT)
    first = subdir_getter(layout)
    second = subdir_getter(layout)
    assert first == second
    assert first.exists()
    assert first.is_dir()


def test_subdir_create_false_does_not_create(tmp_path: Path) -> None:
    """create=False 时仅返回路径而不落盘。"""
    resolver = LocalFileTierResolver(project_base=tmp_path)
    layout = resolver.resolve(StorageTier.PROJECT)
    path = layout.traces_dir(create=False)
    assert path == tmp_path.resolve() / ".epsilon" / "traces"
    assert not path.exists()


def test_project_traces_equivalent_to_cwd_dot_epsilon_traces(tmp_path: Path) -> None:
    """PROJECT 基点 == 进程 CWD 时，traces_dir 与 <CWD>/.epsilon/traces 等价（Property 2）。"""
    # 模拟本地默认场景：project_base 即进程 CWD。
    resolver = LocalFileTierResolver(project_base=tmp_path)
    resolved = resolver.resolve(StorageTier.PROJECT).traces_dir()
    expected = (tmp_path / ".epsilon" / "traces").resolve()
    assert resolved == expected


def test_user_logs_and_persistence_share_project_hash(tmp_path: Path) -> None:
    """USER tier logs 与 user_persistence_root 落对应位置且共享同一 hash（Property 10）。"""
    user_base = tmp_path / "home"
    project_base = tmp_path / "workspace"
    resolver = LocalFileTierResolver(project_base=project_base, user_base=user_base)
    project_hash = resolver.project_hash()

    logs_dir = resolver.resolve(StorageTier.USER).logs_dir()
    persistence_root = resolver.user_persistence_root()

    assert logs_dir == user_base.resolve() / ".epsilon" / project_hash / "logs"
    assert persistence_root == user_base.resolve() / ".epsilon" / "persistence" / project_hash
    # 二者共享同一 project-hash 分区键。
    assert project_hash in str(logs_dir)
    assert project_hash in str(persistence_root)


def test_project_hash_is_deterministic(tmp_path: Path) -> None:
    """对同一基点，project_hash() 恒定（Property 10）。"""
    resolver = LocalFileTierResolver(project_base=tmp_path)
    assert resolver.project_hash() == resolver.project_hash()


def test_project_hash_is_16_hex_chars(tmp_path: Path) -> None:
    """project_hash() 长度为 16 位十六进制（Property 10）。"""
    resolver = LocalFileTierResolver(project_base=tmp_path)
    digest = resolver.project_hash()
    assert len(digest) == 16
    assert _HEX16.match(digest) is not None


def test_project_hash_does_not_contain_raw_path(tmp_path: Path) -> None:
    """project_hash() 不包含原始路径子串，避免泄露宿主目录结构（Property 10）。"""
    project_base = tmp_path / "secret-dir-name"
    resolver = LocalFileTierResolver(project_base=project_base)
    digest = resolver.project_hash()
    # 原始路径各段不得出现在 hash 中。
    for segment in str(project_base.resolve()).split("/"):
        if segment:
            assert segment not in digest
    # 反向：hash 不等于任何简单可逆的路径表达。
    assert str(project_base) not in digest


@pytest.mark.parametrize(
    ("project_base_name", "user_base_name"),
    [
        ("proj-a", "home-a"),
        ("proj-b", "home-b"),
        ("deep/nested/proj", "home-c"),
    ],
)
def test_multi_base_hash_determinism_and_isolation(
    tmp_path: Path,
    project_base_name: str,
    user_base_name: str,
) -> None:
    """多基点组合下 hash 确定性且不同基点产生不同 hash（属性风格，Property 1/10）。"""
    project_base = tmp_path / project_base_name
    user_base = tmp_path / user_base_name
    resolver = LocalFileTierResolver(project_base=project_base, user_base=user_base)

    # 同一 resolver 多次调用确定性。
    assert resolver.project_hash() == resolver.project_hash()

    # 相同基点重建 resolver 结果一致。
    rebuilt = LocalFileTierResolver(project_base=project_base, user_base=user_base)
    assert resolver.project_hash() == rebuilt.project_hash()

    # 不同 project_base 产生不同 hash（碰撞概率忽略）。
    other = LocalFileTierResolver(
        project_base=tmp_path / (project_base_name + "-other"),
        user_base=user_base,
    )
    assert resolver.project_hash() != other.project_hash()


def test_resolved_layout_is_frozen(tmp_path: Path) -> None:
    """ResolvedTierLayout 为 frozen dataclass，home 不可变。"""
    layout = ResolvedTierLayout(home=tmp_path)
    with pytest.raises(FrozenInstanceError):
        layout.home = tmp_path / "other"  # type: ignore[misc]  # 验证 frozen 语义
