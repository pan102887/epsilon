"""``LocalPersistenceHealthCheckAdapter`` 单元测试。

覆盖需求 6.3.5、9.1：路径校验、权限校验、touch 写验证、异常转 DOWN。
"""

import os
from pathlib import Path

import pytest

from domain.health.value_objects import HealthStatus
from infrastructure.health.local_persistence_health_check_adapter import (
    LocalPersistenceHealthCheckAdapter,
)


async def test_check_up_when_dir_is_writable(tmp_path: Path):
    """目录存在且可读写 → UP。"""
    adapter = LocalPersistenceHealthCheckAdapter(root=tmp_path)
    result = await adapter.check()
    assert result.name == "local_persistence"
    assert result.status == HealthStatus.UP
    assert result.reason is None


async def test_check_down_when_path_missing(tmp_path: Path):
    """路径不存在 → DOWN。"""
    missing = tmp_path / "nonexistent"
    adapter = LocalPersistenceHealthCheckAdapter(root=missing)
    result = await adapter.check()
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "不是目录" in result.reason


async def test_check_down_when_path_is_file(tmp_path: Path):
    """路径是文件而非目录 → DOWN。"""
    file_path = tmp_path / "afile.txt"
    file_path.write_bytes(b"x")
    adapter = LocalPersistenceHealthCheckAdapter(root=file_path)
    result = await adapter.check()
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "不是目录" in result.reason


async def test_check_down_when_access_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``os.access`` mock 返回 False → DOWN 且 reason 含权限位。"""
    import infrastructure.health.local_persistence_health_check_adapter as module

    # 模拟写权限缺失：读返回 True、写返回 False
    real_access = os.access

    def fake_access(path, mode):
        if mode & os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(module.os, "access", fake_access)
    adapter = LocalPersistenceHealthCheckAdapter(root=tmp_path)
    result = await adapter.check()
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "缺少" in result.reason
    assert "W" in result.reason


async def test_check_down_when_tempfile_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``tempfile.NamedTemporaryFile`` 抛 ``OSError`` → DOWN。"""
    import infrastructure.health.local_persistence_health_check_adapter as module

    def boom(*args, **kwargs):
        raise OSError("fake touch failure")

    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", boom)
    adapter = LocalPersistenceHealthCheckAdapter(root=tmp_path)
    result = await adapter.check()
    assert result.status == HealthStatus.DOWN
    assert result.reason is not None
    assert "fake touch failure" in result.reason


async def test_check_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """即使底层抛 ``OSError``，适配器也必须返回 DOWN 而非抛出。"""
    import infrastructure.health.local_persistence_health_check_adapter as module

    def boom(*args, **kwargs):
        raise OSError("broken")

    monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", boom)
    adapter = LocalPersistenceHealthCheckAdapter(root=tmp_path)
    # 不应抛异常
    result = await adapter.check()
    assert result.status == HealthStatus.DOWN
