"""本地文件会话上下文适配器（``Local_File_Session_Context_Adapter``）。

基于本地文件系统实现 ``SessionContextStorePort``，作为
``RedisSessionContextAdapter`` 的对等替代；默认后端。

**本期无 TTL / 无过期回收**（需求 2.补）：

- 会话文件仅在调用方显式 ``delete(session_id)`` 时被删除；
- ``load`` 路径**不**读取 ``stat().st_mtime``、不做任何"过期判断"；
- 构造函数**不**接收 ``ttl_seconds`` / ``reaper`` 参数——Adapter 与 TTL
  语义严格解耦。若未来需要 TTL 请提交新 feature。

写入路径：``EXCLUSIVE`` 锁持有 → ``TempFileAtomicWriter.write_bytes_atomic``
→ 释放锁。读取路径：``SHARED`` 锁持有 → ``path.read_bytes()`` → 释放锁。
删除路径：``path.unlink(missing_ok=True)``，幂等。

需求：1.1、1.2、1.3、1.4、1.5、1.6、1.7、2.补.1-2.补.3、9.1、9.2、9.4、12.1。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from domain.chat.ports import SessionContextStorePort
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import (
    CrossPlatformFileLock,
    LockMode,
)
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy

if TYPE_CHECKING:
    from domain.chat.context import ConversationContext

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LocalFileSessionContextAdapter(SessionContextStorePort):
    """``SessionContextStorePort`` 的本地文件实现。

    构造参数不接收 ``ttl_seconds`` / ``reaper`` 等 TTL 相关配置；与
    ``RedisSessionContextAdapter`` 在字段层面的差异仅限"Redis 客户端
    vs 本地 I/O 工具链"。

    文件布局：

    - ``<root>/sessions/<bucket>/<stem>.json``：会话数据文件；
    - ``<root>/sessions/<bucket>/<stem>.json.lock``：每会话独立锁文件；
    - ``<root>/sessions/<bucket>/<stem>.json.tmp-<pid>-<uuid>``：
      ``save`` 过程中的临时文件，由 ``TempFileAtomicWriter`` 管理。
    """

    def __init__(
        self,
        root: Path,
        lock_factory: Callable[[Path], CrossPlatformFileLock],
        path_policy: CrossPlatformPathPolicy,
        atomic_writer: TempFileAtomicWriter,
    ) -> None:
        """初始化适配器。

        Args:
            root: 本地持久化根目录（通常为 ``LOCAL_PERSISTENCE_ROOT``
                规范化后的绝对路径）。
            lock_factory: 跨平台文件锁工厂（由 DI 容器注入）。
            path_policy: 路径策略，用于哈希 ``session_id`` 与 Windows
                长路径校验。
            atomic_writer: 原子写入工具（``Temp_File_Atomic_Rename``）。
        """
        self._sessions_root = root / "sessions"
        self._lock_factory = lock_factory
        self._policy = path_policy
        self._writer = atomic_writer

    def _resolve_path(self, session_id: str) -> Path:
        """根据 ``session_id`` 计算目标 JSON 路径。

        使用不可逆哈希 + 2 位分桶，天然规避 Windows 保留名 / 非法字符
        / 大小写敏感冲突（需求 4.2、4.3）。同时对最终绝对路径做 Windows
        260 字符上限校验（需求 4.4）。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            目标 JSON 文件的绝对路径。
        """
        bucket, stem = self._policy.hash_session_id(session_id)
        path = self._sessions_root / bucket / f"{stem}.json"
        self._policy.check_absolute_path_length(path)
        return path

    async def save(self, session_id: str, context: "ConversationContext") -> None:
        """保存会话上下文（``EXCLUSIVE`` 锁 + 原子替换）。

        Args:
            session_id: 会话唯一标识符。
            context: 对话上下文对象；序列化为 UTF-8 JSON
                （``ensure_ascii=False``）。

        Raises:
            OSError: 底层 I/O 失败（``PermissionError`` / ``ENOSPC`` 等）
                记录结构化 ``logger.error`` 后原样抛出，语义与
                ``RedisSessionContextAdapter`` 对 ``RedisError`` 的处理
                一致。
        """
        path = self._resolve_path(session_id)
        lock_path = path.with_suffix(".json.lock")
        data = json.dumps(context.to_dict(), ensure_ascii=False).encode("utf-8")
        try:
            lock = self._lock_factory(lock_path)
            with lock.acquire(LockMode.EXCLUSIVE):
                self._writer.write_bytes_atomic(path, data)
        except OSError as exc:
            logger.error(
                "save 会话上下文失败 session_id=%s operation=save error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise

    async def load(self, session_id: str) -> "ConversationContext":
        """加载会话上下文（``SHARED`` 锁下读字节 + 反序列化）。

        - 文件不存在 → 返回空 ``ConversationContext``（需求 1.3）；
        - JSON 反序列化失败 → ``logger.error`` + 返回空 ``ConversationContext``
          （需求 1.4）；
        - 底层 I/O 失败 → ``logger.error`` + 返回空 ``ConversationContext``
          （需求 9.1，避免级联阻塞 Agent Loop）。
        - **不**读取 ``stat().st_mtime``、不做任何过期判断（需求 2.补.2）。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            对应的对话上下文；若不存在或反序列化失败则返回空实例。
        """
        from domain.chat.context import ConversationContext

        path = self._resolve_path(session_id)
        try:
            if not path.exists():
                return ConversationContext()
            lock = self._lock_factory(path.with_suffix(".json.lock"))
            with lock.acquire(LockMode.SHARED):
                raw = path.read_bytes()
        except OSError as exc:
            logger.error(
                "load 会话上下文失败 session_id=%s operation=load error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            return ConversationContext()

        try:
            return ConversationContext.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(
                "反序列化会话上下文失败 session_id=%s operation=load error_class=%s",
                session_id,
                type(exc).__name__,
            )
            return ConversationContext()

    async def delete(self, session_id: str) -> None:
        """删除会话上下文（幂等）。

        ``unlink(missing_ok=True)`` 语义保证目标文件不存在时静默返回成功
        （需求 1.5）。底层 I/O 失败仍抛出。

        Args:
            session_id: 会话唯一标识符。

        Raises:
            OSError: 底层删除失败（``PermissionError`` 等）时抛出；日志
                已记录结构化字段。
        """
        path = self._resolve_path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "delete 会话上下文失败 session_id=%s operation=delete error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise

    async def exists(self, session_id: str) -> bool:
        """判断会话上下文文件是否真实存在。

        本方法只检查目标 JSON 文件存在性，不读取正文、不解析 JSON，也不基于
        ``mtime`` 做 TTL 判断；本地文件会话仍然只由显式 ``delete`` 删除。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            目标会话 JSON 文件存在时返回 ``True``，否则返回 ``False``。
        """
        path = self._resolve_path(session_id)
        try:
            return path.exists()
        except OSError as exc:
            logger.error(
                "exists 会话上下文失败 session_id=%s operation=exists error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise

    async def compare_and_swap(
        self,
        session_id: str,
        mutator: Callable[["ConversationContext"], Awaitable[T]],
    ) -> T:
        """基于 EXCLUSIVE 文件锁的 CAS 等价实现。

        文件锁为悲观锁，对外语义与 Redis CAS 等价——锁持有期间执行
        read → mutator → atomic write 三步。不抛出 SessionConflictError。

        Args:
            session_id: 会话唯一标识符。
            mutator: 异步修改回调；锁持有期内串行执行。

        Returns:
            mutator 的返回值。

        Raises:
            OSError: 底层 I/O 失败。
        """
        from domain.chat.context import ConversationContext

        path = self._resolve_path(session_id)
        lock_path = path.with_suffix(".json.lock")
        try:
            lock = self._lock_factory(lock_path)
            with lock.acquire(LockMode.EXCLUSIVE):
                if path.exists():
                    raw = path.read_bytes()
                    try:
                        ctx = ConversationContext.from_dict(json.loads(raw))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        ctx = ConversationContext()
                else:
                    ctx = ConversationContext()

                result = await mutator(ctx)
                data = json.dumps(ctx.to_dict(), ensure_ascii=False).encode("utf-8")
                self._writer.write_bytes_atomic(path, data)
            return result
        except OSError as exc:
            logger.error(
                "compare_and_swap 会话上下文失败 "
                "session_id=%s operation=cas error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise
