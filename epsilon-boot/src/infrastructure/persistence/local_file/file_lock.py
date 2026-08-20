"""跨平台文件锁抽象（``Cross_Platform_File_Lock``）。

内部基于第三方 ``portalocker`` 依赖把 Linux ``fcntl.flock`` 与 Windows
``LockFileEx`` 统一到同一语义：整文件级互斥、fd 关闭即释放、进程崩溃由
内核自动释放（需求 2.1、2.5）。

本模块仅提供"锁抽象"；具体如何使用（例如 ``save`` 前持 EX 锁、``load``
前持 SH 锁）由 ``LocalFileSessionContextAdapter`` 自行决定。

需求：2.1、2.2、2.4、2.5、2.6、12.2。
"""

# portalocker 3.x 在 __init__ 中执行 `from .redis import RedisLock`，
# 该子模块在类定义时引用 `redis.client.PubSubWorkerThread`。当 redis
# 包内部初始化异常（Windows + redis 7.x + Python 3.13 组合下
# `redis.exceptions` 模块缺失）时，`redis.client` 属性不存在，抛出
# `AttributeError` 而非 `ImportError`，portalocker 的 except 无法捕获。
# 解决方案：预先将 `portalocker.redis` 占位为 None，使 portalocker 跳过
# 对该子模块的真实导入。
import contextlib
import importlib.util as _ilu
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import IO

_portalocker_spec = _ilu.find_spec("portalocker")
if _portalocker_spec is not None:
    import types as _types

    _sentinel_key = "portalocker.redis"
    _had_key = _sentinel_key in sys.modules
    if not _had_key:
        sys.modules[_sentinel_key] = _types.ModuleType(_sentinel_key)  # type: ignore[assignment]
    try:
        import portalocker
        from portalocker import LockFlags
    finally:
        if not _had_key and _sentinel_key in sys.modules:
            del sys.modules[_sentinel_key]
else:
    raise ImportError("portalocker is required but not installed")


class LockMode(Enum):
    """锁模式：``EXCLUSIVE``（写入互斥）或 ``SHARED``（读取并行）。"""

    EXCLUSIVE = "EX"
    SHARED = "SH"


class LockTimeout(TimeoutError):
    """锁获取超时。

    错误消息前缀为中文"获取本地持久化锁超时"，便于运维在日志中 grep
    定位；继承 ``TimeoutError`` 便于上层统一捕获。
    """


@dataclass
class LockHandle:
    """持有中的锁句柄，实现上下文管理器。

    进入 ``with`` 时本对象自身作为句柄返回；退出时先调用
    ``portalocker.unlock`` 释放锁，再关闭 fd（关闭 fd 同样会释放
    ``fcntl.flock``，这里做了双保险）。
    """

    fd: IO[bytes]
    path: Path
    mode: LockMode

    def __enter__(self) -> "LockHandle":
        """上下文管理器入口，直接返回自身。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """释放锁并关闭 fd；无论是否有异常，均确保 fd 关闭。"""
        try:
            portalocker.unlock(self.fd)
        finally:
            # fd 已在别处关闭（例如进程中断）时忽略
            with contextlib.suppress(OSError):
                self.fd.close()


class CrossPlatformFileLock:
    """跨平台文件锁，支持 ``EXCLUSIVE`` / ``SHARED``、非阻塞轮询 + 超时。

    使用 ``portalocker.LockFlags.NON_BLOCKING`` 以非阻塞方式尝试加锁，
    失败时 sleep ``poll_interval_ms`` 后重试，直到成功或超过
    ``acquire_timeout_ms`` 时抛 ``LockTimeout``。
    """

    def __init__(
        self,
        lock_path: Path,
        acquire_timeout_ms: int,
        poll_interval_ms: int = 20,
    ) -> None:
        """初始化文件锁。

        Args:
            lock_path: 锁文件路径（``*.lock``）。若父目录不存在，
                ``acquire`` 调用时会自动创建。
            acquire_timeout_ms: 获取锁的最大等待时间（毫秒）。
            poll_interval_ms: 非阻塞轮询间隔（毫秒），默认 20ms。
        """
        self._lock_path = lock_path
        self._timeout_ms = acquire_timeout_ms
        self._poll_ms = poll_interval_ms
        self._ensure_backend_supported()

    @staticmethod
    def _ensure_backend_supported() -> None:
        """校验当前平台支持的锁后端（需求 2.6）。

        Linux / Windows / macOS 直接返回；其他 Unix 变体依赖 ``fcntl``
        回退，若 ``import fcntl`` 失败将直接冒泡由启动期捕获，触发
        ``Startup_Failure``。
        """
        if sys.platform in ("linux", "darwin", "win32"):
            return
        # FreeBSD 等其他 Unix：依赖 fcntl；不可用则 ImportError 冒泡。
        import fcntl  # noqa: F401

    def acquire(self, mode: LockMode) -> LockHandle:
        """以非阻塞方式轮询获取锁，直到成功或超时。

        Args:
            mode: 锁模式（``EXCLUSIVE`` 或 ``SHARED``）。

        Returns:
            ``LockHandle``，可作为上下文管理器；退出时自动释放。

        Raises:
            LockTimeout: 在 ``acquire_timeout_ms`` 内未能获取到锁。
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(self._lock_path, "a+b")  # noqa: SIM115  # fd 生命周期跨函数，由 LockHandle 持有并在释放时关闭
        flags = (
            LockFlags.EXCLUSIVE if mode is LockMode.EXCLUSIVE else LockFlags.SHARED
        ) | LockFlags.NON_BLOCKING
        deadline = time.monotonic() + self._timeout_ms / 1000.0
        while True:
            try:
                portalocker.lock(fd, flags)
                return LockHandle(fd=fd, path=self._lock_path, mode=mode)
            except portalocker.exceptions.LockException as exc:
                if time.monotonic() >= deadline:
                    fd.close()
                    raise LockTimeout(
                        f"获取本地持久化锁超时：{self._lock_path}，timeout={self._timeout_ms}ms"
                    ) from exc
                time.sleep(self._poll_ms / 1000.0)


class LockFactory:
    """锁工厂。

    由 DI 容器注入适配器，便于测试替身注入内存实现或自定义超时。
    工厂自身是不可变的：只持有 ``acquire_timeout_ms``；每次调用
    产出一个新的 ``CrossPlatformFileLock`` 实例。
    """

    def __init__(self, acquire_timeout_ms: int) -> None:
        """初始化工厂。

        Args:
            acquire_timeout_ms: 所有由本工厂创建的锁共用的超时（毫秒）。
        """
        self._timeout_ms = acquire_timeout_ms

    def __call__(self, lock_path: Path) -> CrossPlatformFileLock:
        """创建一个新的锁实例。

        Args:
            lock_path: 锁文件路径。

        Returns:
            新的 ``CrossPlatformFileLock`` 实例。
        """
        return CrossPlatformFileLock(lock_path, self._timeout_ms)
