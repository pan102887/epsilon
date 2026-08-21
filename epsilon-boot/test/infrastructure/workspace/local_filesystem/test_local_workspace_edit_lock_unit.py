"""``LocalFilesystemWorkspace.edit`` 并发互斥与 Windows 降级单元测试。

覆盖范围（对应 tasks 7.11）：

1. POSIX 并发：两个 ``edit`` Task 同时修改同一文件时，``fcntl.flock`` 应
   把它们串行化，最终文件内容为**两次 edit 的串行叠加**，而不是"后者
   覆盖前者"。使用 ``threading.Barrier`` 保证两侧**同时**冲进临界区以
   最大化观察到锁生效的概率。
2. Windows 降级：``monkeypatch.setattr("platform.system", lambda: "Windows")``
   模拟 Windows 环境时，``edit`` 应正常完成（不抛异常），并触发**恰好一次**
   ``warning`` 级别日志；再次 ``edit`` 不应再次触发（一次性哨兵）。
3. ``flock`` ``EAGAIN``：``monkeypatch`` 把 ``fcntl.flock`` 替换为抛
   ``BlockingIOError(EAGAIN)`` 的函数，断言 ``edit`` 抛
   ``WorkspaceIoError(reason="lock_failed")``。

**本测试文件仅在 POSIX 运行**（Windows 下 ``fcntl`` 模块缺失，相关机制
只能在 POSIX CI 验证；Windows 上以 ``skipif`` 跳过）。
"""

from __future__ import annotations

import asyncio
import errno
import logging
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest

from domain.workspace.exceptions import WorkspaceIoError
from domain.workspace.policy import WorkspacePolicy
from infrastructure.workspace.local_filesystem import LocalFilesystemWorkspace
from infrastructure.workspace.local_filesystem import local_workspace as _lw

pytestmark = pytest.mark.asyncio


_SKIP_IF_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows 下无 fcntl 模块，edit 锁相关用例仅在 POSIX 运行",
)


@pytest.fixture
def ws(tmp_path: Path) -> LocalFilesystemWorkspace:
    return LocalFilesystemWorkspace(
        root=tmp_path.resolve(),
        follow_symlinks=False,
        policy=WorkspacePolicy(),
    )


@pytest.fixture(autouse=True)
def reset_windows_warning_sentinel_fixture() -> Iterator[None]:
    """每个用例前后把 Windows 一次性 warning 哨兵重置为 ``False``。"""
    _lw.reset_windows_warning_sentinel()
    yield
    _lw.reset_windows_warning_sentinel()


class TestEditConcurrencyMutex:
    """POSIX fcntl.flock 并发互斥断言。"""

    @_SKIP_IF_WINDOWS
    async def test_two_concurrent_edits_serialize(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """两次并发 edit 同一文件，最终内容应为两次 edit 串行叠加。

        初始内容 ``"A B"``；第一次 edit 把 ``"A"`` → ``"X"``，第二次把
        ``"B"`` → ``"Y"``；任意串行次序都会得到 ``"X Y"``，而不是 ``"X B"``
        或 ``"A Y"``（即一方被另一方覆盖）。用 ``threading.Barrier`` 在
        两个 executor 线程里同时冲进锁。
        """
        (tmp_path / "shared.txt").write_bytes(b"A B")
        wp = ws.resolve_path("shared.txt")
        barrier = threading.Barrier(2)

        def call_edit_sync(old: bytes, new: bytes) -> int:
            # Barrier 在外层线程池中同步等待，确保两侧同时进入 os.open
            barrier.wait()
            # 在线程内部运行一个独立事件循环跑 coroutine
            return asyncio.new_event_loop().run_until_complete(ws.edit(wp, old, new))

        errors: list[BaseException] = []

        def run_edit(old: bytes, new: bytes) -> None:
            try:
                call_edit_sync(old, new)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run_edit, args=(b"A", b"X")),
            threading.Thread(target=run_edit, args=(b"B", b"Y")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        if errors:
            raise errors[0]

        final = (tmp_path / "shared.txt").read_bytes()
        assert final == b"X Y", f"got {final!r}"


class TestWindowsDegradation:
    """Windows 下 ``edit`` 跳过 ``fcntl.flock`` 并 warning 一次。"""

    async def test_windows_edit_completes_and_warns_once(
        self,
        ws: LocalFilesystemWorkspace,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``platform.system() == "Windows"`` 时 edit 仍可完成且记录一次 warning。"""
        (tmp_path / "w.txt").write_bytes(b"foo")
        wp = ws.resolve_path("w.txt")
        monkeypatch.setattr(_lw.platform, "system", lambda: "Windows")
        caplog.set_level(logging.WARNING, logger=_lw.__name__)

        # 首次 edit：应完成写入，并触发一次 warning。
        n = await ws.edit(wp, b"foo", b"bar")
        assert n > 0
        assert (tmp_path / "w.txt").read_bytes() == b"bar"

        windows_warnings = [
            rec
            for rec in caplog.records
            if "Windows" in rec.getMessage() and rec.levelno == logging.WARNING
        ]
        assert len(windows_warnings) == 1

        # 第二次 edit：warning 不应再次触发（一次性哨兵生效）。
        caplog.clear()
        n2 = await ws.edit(wp, b"bar", b"baz")
        assert n2 > 0
        assert (tmp_path / "w.txt").read_bytes() == b"baz"
        second_warnings = [
            rec
            for rec in caplog.records
            if "Windows" in rec.getMessage() and rec.levelno == logging.WARNING
        ]
        assert second_warnings == []


class TestFlockEagainTranslatesToLockFailed:
    """``fcntl.flock`` 抛 ``BlockingIOError(EAGAIN)`` → ``WorkspaceIoError(lock_failed)``。"""

    @_SKIP_IF_WINDOWS
    async def test_flock_eagain_raises_lock_failed(
        self,
        ws: LocalFilesystemWorkspace,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``fcntl.flock`` mock 为抛 ``BlockingIOError(EAGAIN)``，断言对应错误。"""
        (tmp_path / "l.txt").write_bytes(b"x")
        wp = ws.resolve_path("l.txt")

        import fcntl as _fcntl  # POSIX 专用，跳过了 Windows 用例

        def _raise_eagain(*_args: object, **_kwargs: object) -> NoReturn:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")

        monkeypatch.setattr(_fcntl, "flock", _raise_eagain)

        with pytest.raises(WorkspaceIoError) as ei:
            await ws.edit(wp, b"x", b"y")
        assert ei.value.reason == "lock_failed"
        assert ei.value.operation == "edit"
