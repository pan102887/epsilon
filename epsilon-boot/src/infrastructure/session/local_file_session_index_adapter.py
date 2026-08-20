"""本地文件会话索引适配器。

本模块实现 ``SessionIndexPort``，用一会话一个 JSON 索引文件的方式保存
`SessionMetadata`，供 TUI `/sessions` 和 `/resume` 使用。索引是会话发现
辅助数据，不承载完整对话正文；聊天主数据仍由 ``SessionContextStorePort``
负责保存。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from domain.chat.ports import SessionIndexPort
from domain.chat.value_objects import SessionMetadata
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import (
    CrossPlatformFileLock,
    LockMode,
)
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy

logger = logging.getLogger(__name__)


class LocalFileSessionIndexAdapter(SessionIndexPort):
    """会话索引的本地文件实现。

    文件布局：

    - ``<root>/session_index/<bucket>/<stem>.json``：会话元数据 JSON；
    - ``<root>/session_index/<bucket>/<stem>.json.lock``：每会话独立锁文件。
    """

    def __init__(
        self,
        root: Path,
        lock_factory: Callable[[Path], CrossPlatformFileLock],
        path_policy: CrossPlatformPathPolicy,
        atomic_writer: TempFileAtomicWriter,
    ) -> None:
        """初始化本地文件会话索引适配器。"""
        self._index_root = root / "session_index"
        self._lock_factory = lock_factory
        self._policy = path_policy
        self._writer = atomic_writer

    def _resolve_path(self, session_id: str) -> Path:
        """根据 ``session_id`` 计算索引 JSON 文件路径。"""
        bucket, stem = self._policy.hash_session_id(session_id)
        path = self._index_root / bucket / f"{stem}.json"
        self._policy.check_absolute_path_length(path)
        return path

    async def upsert(self, metadata: SessionMetadata) -> None:
        """新增或更新指定会话索引。"""
        path = self._resolve_path(metadata.session_id)
        lock = self._lock_factory(path.with_suffix(".json.lock"))
        payload = json.dumps(_metadata_to_dict(metadata), ensure_ascii=False).encode("utf-8")
        try:
            with lock.acquire(LockMode.EXCLUSIVE):
                self._writer.write_bytes_atomic(path, payload)
        except OSError as exc:
            logger.error(
                "upsert 会话索引失败 session_id=%s operation=upsert error_class=%s errno=%s",
                metadata.session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise

    async def get(self, session_id: str) -> SessionMetadata | None:
        """按会话 ID 读取索引元数据。"""
        path = self._resolve_path(session_id)
        lock = self._lock_factory(path.with_suffix(".json.lock"))
        try:
            with lock.acquire(LockMode.SHARED):
                raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.error(
                "get 会话索引失败 session_id=%s operation=get error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise

        try:
            return _metadata_from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(
                "反序列化会话索引失败 session_id=%s operation=get error_class=%s",
                session_id,
                type(exc).__name__,
            )
            return None

    async def list_recent(self, limit: int = 20) -> list[SessionMetadata]:
        """按更新时间倒序列出最近会话索引。"""
        if limit <= 0 or not self._index_root.exists():
            return []

        results: list[SessionMetadata] = []
        for path in self._index_root.glob("*/*.json"):
            try:
                metadata = _metadata_from_dict(json.loads(path.read_bytes()))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.error(
                    "读取会话索引列表项失败 file=%s operation=list_recent error_class=%s",
                    path,
                    type(exc).__name__,
                )
                continue
            results.append(metadata)

        results.sort(key=lambda item: item.updated_at_epoch_ms, reverse=True)
        return results[:limit]

    async def delete(self, session_id: str) -> None:
        """幂等删除指定会话索引项。"""
        path = self._resolve_path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "delete 会话索引失败 session_id=%s operation=delete error_class=%s errno=%s",
                session_id,
                type(exc).__name__,
                getattr(exc, "errno", None),
            )
            raise


def _metadata_to_dict(metadata: SessionMetadata) -> dict[str, object]:
    """把会话元数据转为 JSON 友好的字典。"""
    data: dict[str, object] = {
        "session_id": metadata.session_id,
        "updated_at_epoch_ms": metadata.updated_at_epoch_ms,
        "message_count": metadata.message_count,
        "preview": metadata.preview,
    }
    if metadata.created_at_epoch_ms is not None:
        data["created_at_epoch_ms"] = metadata.created_at_epoch_ms
    if metadata.model is not None:
        data["model"] = metadata.model
    return data


def _metadata_from_dict(data: dict[str, object]) -> SessionMetadata:
    """从字典恢复会话元数据值对象。"""
    return SessionMetadata(
        session_id=str(data["session_id"]),
        updated_at_epoch_ms=int(data["updated_at_epoch_ms"]),
        message_count=int(data["message_count"]),
        preview=str(data["preview"]),
        created_at_epoch_ms=(
            int(data["created_at_epoch_ms"])
            if data.get("created_at_epoch_ms") is not None
            else None
        ),
        model=str(data["model"]) if data.get("model") is not None else None,
    )
