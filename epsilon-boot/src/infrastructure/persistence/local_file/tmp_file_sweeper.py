"""``Tmp_File_Sweeper``：启动期一次性清理 ``*.tmp-<pid>-<uuid>`` 残留。

设计约束（需求 2.补 / 3.2 / 9.5）：

- 仅在启动时调用一次；**不**创建 ``asyncio`` 任务或线程；
- 仅清理文件名含 ``.tmp-`` 的残留；**不**触碰 ``.json`` / ``.lock`` 等
  任何非残留文件；
- ``mtime`` 阈值默认 3600s；低于阈值的半写文件保留（可能是另一进程正在
  进行中的 ``save``）；
- 本组件替代原 ``TtlReaper``；本期会话无 TTL / 无后台回收。

职责边界：``TmpFileSweeper`` 仅识别写入过程崩溃遗留的 tmp 文件，
与会话 JSON 文件的寿命完全解耦。
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class TmpFileSweeper:
    """一次性扫描 ``sessions/<bucket>/`` 下的半写 tmp 残留并清理。

    本类是同步、一次性组件：调用方在启动期同步调用 ``sweep_once()`` 后
    即可丢弃实例。**不**提供 ``start`` / ``stop`` / ``is_expired`` 等方法，
    以严格贴合需求 2.补.1、2.补.6 的反向约束。
    """

    def __init__(self, sessions_root: Path, max_age_seconds: int) -> None:
        """初始化扫描器。

        Args:
            sessions_root: 会话文件根目录（``<LOCAL_PERSISTENCE_ROOT>/sessions``）。
            max_age_seconds: ``mtime`` 阈值秒数；距今超过该值的 ``.tmp-*``
                文件会被删除。
        """
        self._sessions_root = sessions_root
        self._max_age = max_age_seconds

    def sweep_once(self) -> dict[str, int]:
        """启动期一次性扫描；返回 ``{scanned, deleted, errored}`` 摘要。

        仅识别并删除文件名含 ``.tmp-`` 的残留；``.json`` 会话文件与
        ``.lock`` 文件在任何情况下都不会被触碰（本期会话无 TTL）。

        Returns:
            包含 ``scanned``、``deleted``、``errored`` 三个整数字段的摘要
            字典；扫描根目录不存在时返回全零摘要。
        """
        scanned = 0
        deleted = 0
        errored = 0
        if not self._sessions_root.exists():
            logger.info(
                "TmpFileSweeper 扫描完成 scanned=%d deleted=%d errored=%d",
                scanned,
                deleted,
                errored,
            )
            return {"scanned": scanned, "deleted": deleted, "errored": errored}
        now = time.time()
        try:
            bucket_iter = self._sessions_root.iterdir()
        except OSError:
            logger.info(
                "TmpFileSweeper 扫描完成 scanned=%d deleted=%d errored=%d",
                scanned,
                deleted,
                errored,
            )
            return {"scanned": scanned, "deleted": deleted, "errored": errored}

        for bucket in bucket_iter:
            if not bucket.is_dir():
                continue
            try:
                entries = list(bucket.iterdir())
            except OSError:
                errored += 1
                continue
            for entry in entries:
                # 严格限定：只看 .tmp- 前缀；跳过 .json 与 .lock
                if ".tmp-" not in entry.name:
                    continue
                scanned += 1
                try:
                    if now - entry.stat().st_mtime > self._max_age:
                        entry.unlink()
                        deleted += 1
                except OSError:
                    errored += 1
        logger.info(
            "TmpFileSweeper 扫描完成 scanned=%d deleted=%d errored=%d",
            scanned,
            deleted,
            errored,
        )
        return {"scanned": scanned, "deleted": deleted, "errored": errored}
