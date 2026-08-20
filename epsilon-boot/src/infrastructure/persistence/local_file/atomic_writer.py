"""临时文件原子替换（``Temp_File_Atomic_Rename``）。

写入策略：先写入 ``<target>.tmp-<pid>-<uuid>``，``flush`` 后（可选）
``os.fsync`` 落盘，再用 ``os.replace`` 原子替换目标文件。

崩溃一致性保证：

- 临时文件写入中途崩溃 → 残留 ``.tmp-*`` 文件由 ``TmpFileSweeper`` 在下次
  启动时按 mtime 阈值清理（需求 3.2）；
- 临时文件写完但 ``os.replace`` 之前崩溃 → 目标文件保持崩溃前上一版本，
  未发生部分覆盖（需求 3.3）。

``sweep_stale_tmp`` 的职责已解耦到独立的 ``TmpFileSweeper`` 组件（任务 2.8）。

需求：3.1、3.3、3.4。
"""

import contextlib
import os
import uuid
from pathlib import Path


class TempFileAtomicWriter:
    """原子写入工具，可选是否 ``fsync``。"""

    def __init__(self, fsync_on_write: bool) -> None:
        """初始化原子写入器。

        Args:
            fsync_on_write: 是否在 ``os.replace`` 之前调用 ``os.fsync``
                把数据落盘。``False`` 将放弃断电一致性保证（仅开发 /
                测试场景建议关闭）。
        """
        self._fsync = fsync_on_write

    def write_bytes_atomic(self, target: Path, payload: bytes) -> None:
        """以原子方式把 ``payload`` 写入 ``target``。

        流程：父目录不存在则创建 → 打开 tmp 文件写入 → flush →
        （可选）``os.fsync`` → ``os.replace(tmp, target)``。

        异常处理：任一步骤失败都会尝试 ``tmp.unlink(missing_ok=True)``
        清理残留，然后重新 raise 原异常。``Windows`` 下 ``os.replace``
        要求 tmp 与 target 在同一卷，这里通过 ``target.with_name(...)``
        把 tmp 创建在与 target 完全相同的父目录下，自然满足跨平台。

        Args:
            target: 目标文件路径。
            payload: 要写入的字节流。

        Raises:
            OSError: 底层 I/O 错误（``PermissionError`` / ``ENOSPC`` 等）
                会在清理 tmp 后原样抛出，由调用方决定日志与降级策略。
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                if self._fsync:
                    os.fsync(f.fileno())
            os.replace(tmp, target)  # POSIX & Windows 原子替换
        except BaseException:
            # 写失败时清理 tmp，避免残留；不吞异常
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
