"""``TempFileAtomicWriter`` 单元测试。

覆盖写入正确性、崩溃清理、``fsync`` 开关。
"""

import os
from pathlib import Path

import pytest

from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter


def test_write_bytes_atomic_roundtrip(tmp_path: Path):
    """写入后 ``read_bytes`` 必须等于 payload。"""
    writer = TempFileAtomicWriter(fsync_on_write=True)
    target = tmp_path / "a" / "b.json"
    payload = "你好，世界".encode()
    writer.write_bytes_atomic(target, payload)
    assert target.read_bytes() == payload


def test_write_bytes_atomic_creates_parent_dirs(tmp_path: Path):
    """``target`` 的父目录不存在时会被自动创建。"""
    writer = TempFileAtomicWriter(fsync_on_write=False)
    target = tmp_path / "x" / "y" / "z.json"
    writer.write_bytes_atomic(target, b"payload")
    assert target.parent.is_dir()
    assert target.read_bytes() == b"payload"


def test_write_bytes_atomic_cleans_tmp_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """当 ``os.fsync`` 抛 ``OSError`` 时，tmp 清理且 target 不存在。"""
    writer = TempFileAtomicWriter(fsync_on_write=True)
    target = tmp_path / "err.json"

    def fake_fsync(_fd: int) -> None:
        raise OSError("fake fsync failure")

    monkeypatch.setattr(os, "fsync", fake_fsync)

    with pytest.raises(OSError, match="fake fsync failure"):
        writer.write_bytes_atomic(target, b"payload")

    # target 不存在（os.replace 前已经失败）
    assert not target.exists()
    # tmp 已清理
    residual = [p for p in tmp_path.iterdir() if ".tmp-" in p.name]
    assert residual == []


def test_write_bytes_atomic_no_fsync_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``fsync_on_write=False`` 时不应调用 ``os.fsync``。"""
    writer = TempFileAtomicWriter(fsync_on_write=False)
    target = tmp_path / "nosync.json"

    called = {"count": 0}
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        called["count"] += 1
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    writer.write_bytes_atomic(target, b"p")
    assert called["count"] == 0
    assert target.read_bytes() == b"p"


def test_write_bytes_atomic_overwrites_existing(tmp_path: Path):
    """对已存在的 target 做 ``write_bytes_atomic`` 应完全覆盖旧内容。"""
    writer = TempFileAtomicWriter(fsync_on_write=False)
    target = tmp_path / "existing.json"
    target.write_bytes(b"OLD")
    writer.write_bytes_atomic(target, b"NEW")
    assert target.read_bytes() == b"NEW"
