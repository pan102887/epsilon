"""本地文件持久化共享工具包。

对外导出本地文件后端需要的核心工具：路径策略、跨平台文件锁、原子写、
临时残留清理。具体适配器（如 ``LocalFileSessionContextAdapter``）位于
``infrastructure/session/``；配置类位于本包 ``config/`` 子包。

本包不导入任何领域层模块；所有错误消息均为中文可读文案。

注意：file_lock 依赖 portalocker，后者在 import 时会尝试导入 redis.client。
在 redis 包损坏（如 Windows + 特定版本组合）时会导致 AttributeError。
因此 file_lock 的导入采用延迟方式，仅在实际访问时触发。
"""

from typing import TYPE_CHECKING, Any

from .atomic_writer import TempFileAtomicWriter
from .path_policy import CrossPlatformPathPolicy, PathPolicyViolation
from .tmp_file_sweeper import TmpFileSweeper

if TYPE_CHECKING:
    from .file_lock import CrossPlatformFileLock, LockFactory, LockHandle, LockMode, LockTimeout


def __getattr__(name: str) -> Any:
    """延迟导入 file_lock 模块的导出，避免包初始化时拉入 portalocker→redis 链。"""
    _file_lock_names = {
        "CrossPlatformFileLock",
        "LockFactory",
        "LockHandle",
        "LockMode",
        "LockTimeout",
    }
    if name in _file_lock_names:
        from . import file_lock

        return getattr(file_lock, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CrossPlatformFileLock",
    "CrossPlatformPathPolicy",
    "LockFactory",
    "LockHandle",
    "LockMode",
    "LockTimeout",
    "PathPolicyViolation",
    "TempFileAtomicWriter",
    "TmpFileSweeper",
]
