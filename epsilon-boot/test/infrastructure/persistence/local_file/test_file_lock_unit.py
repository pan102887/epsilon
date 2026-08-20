"""``CrossPlatformFileLock`` 与 ``LockFactory`` 单元测试。

覆盖 EX/SH 语义、超时语义与中文错误前缀。跨进程并发验证由
``test_multiprocess_concurrency.py``（阶段 3.4）负责。
"""

from pathlib import Path

import pytest

from infrastructure.persistence.local_file.file_lock import (
    CrossPlatformFileLock,
    LockFactory,
    LockMode,
    LockTimeout,
)


def test_exclusive_lock_blocks_second_exclusive(tmp_path: Path):
    """同一 lock_path 的第二次 EXCLUSIVE acquire 必须超时。"""
    lock_path = tmp_path / "demo.lock"
    lock_a = CrossPlatformFileLock(lock_path, acquire_timeout_ms=100, poll_interval_ms=10)
    lock_b = CrossPlatformFileLock(lock_path, acquire_timeout_ms=100, poll_interval_ms=10)
    handle_a = lock_a.acquire(LockMode.EXCLUSIVE)
    try:
        with pytest.raises(LockTimeout, match="获取本地持久化锁超时"):
            lock_b.acquire(LockMode.EXCLUSIVE)
    finally:
        handle_a.__exit__(None, None, None)


def test_shared_lock_permits_concurrent_shared(tmp_path: Path):
    """两个 SHARED acquire 可以同时持有同一 lock_path。"""
    lock_path = tmp_path / "shared.lock"
    lock_a = CrossPlatformFileLock(lock_path, acquire_timeout_ms=100)
    lock_b = CrossPlatformFileLock(lock_path, acquire_timeout_ms=100)
    handle_a = lock_a.acquire(LockMode.SHARED)
    try:
        handle_b = lock_b.acquire(LockMode.SHARED)
        handle_b.__exit__(None, None, None)
    finally:
        handle_a.__exit__(None, None, None)


def test_exit_closes_fd_and_releases(tmp_path: Path):
    """``LockHandle.__exit__`` 关闭后锁可再次被获取。"""
    lock_path = tmp_path / "reacquire.lock"
    lock = CrossPlatformFileLock(lock_path, acquire_timeout_ms=100)
    handle = lock.acquire(LockMode.EXCLUSIVE)
    handle.__exit__(None, None, None)
    # 再次获取应立即成功
    handle2 = lock.acquire(LockMode.EXCLUSIVE)
    handle2.__exit__(None, None, None)


def test_context_manager_usage(tmp_path: Path):
    """``LockHandle`` 作为上下文管理器使用时自动释放。"""
    lock_path = tmp_path / "ctx.lock"
    lock = CrossPlatformFileLock(lock_path, acquire_timeout_ms=100)
    with lock.acquire(LockMode.EXCLUSIVE):
        pass
    # 退出后应可立即再次获取
    with lock.acquire(LockMode.EXCLUSIVE):
        pass


def test_lock_timeout_message_contains_chinese_prefix(tmp_path: Path):
    """超时错误消息前缀必须包含中文"获取本地持久化锁超时"。"""
    lock_path = tmp_path / "timeout.lock"
    lock_a = CrossPlatformFileLock(lock_path, acquire_timeout_ms=50)
    lock_b = CrossPlatformFileLock(lock_path, acquire_timeout_ms=50)
    handle_a = lock_a.acquire(LockMode.EXCLUSIVE)
    try:
        with pytest.raises(LockTimeout) as excinfo:
            lock_b.acquire(LockMode.EXCLUSIVE)
        assert "获取本地持久化锁超时" in str(excinfo.value)
    finally:
        handle_a.__exit__(None, None, None)


def test_lock_factory_produces_new_instances(tmp_path: Path):
    """``LockFactory`` 每次调用返回新的 ``CrossPlatformFileLock`` 实例。"""
    factory = LockFactory(acquire_timeout_ms=100)
    path_a = tmp_path / "a.lock"
    path_b = tmp_path / "b.lock"
    lock_a = factory(path_a)
    lock_b = factory(path_b)
    assert isinstance(lock_a, CrossPlatformFileLock)
    assert isinstance(lock_b, CrossPlatformFileLock)
    assert lock_a is not lock_b


def test_lock_factory_creates_parent_dir_lazily(tmp_path: Path):
    """``acquire`` 会在需要时自动创建锁文件的父目录。"""
    factory = LockFactory(acquire_timeout_ms=100)
    lock_path = tmp_path / "nested" / "parent" / "lock.file"
    lock = factory(lock_path)
    handle = lock.acquire(LockMode.EXCLUSIVE)
    try:
        assert lock_path.parent.is_dir()
        assert lock_path.exists()
    finally:
        handle.__exit__(None, None, None)
