"""``LocalFileSessionIndexAdapter`` 单元测试。"""

import json
from pathlib import Path

from domain.chat.value_objects import SessionMetadata
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.session.local_file_session_index_adapter import (
    LocalFileSessionIndexAdapter,
)


def _make_adapter(root: Path) -> LocalFileSessionIndexAdapter:
    """构造落盘到 ``root`` 的本地会话索引适配器。"""
    return LocalFileSessionIndexAdapter(
        root=root,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _metadata(
    session_id: str,
    *,
    updated_at: int,
    message_count: int = 1,
    preview: str = "hello",
) -> SessionMetadata:
    """构造测试用会话元数据。"""
    return SessionMetadata(
        session_id=session_id,
        created_at_epoch_ms=updated_at - 10,
        updated_at_epoch_ms=updated_at,
        message_count=message_count,
        preview=preview,
        model="qwen3",
    )


async def test_upsert_then_get_roundtrip(tmp_path: Path) -> None:
    """验证 upsert 后可按 session_id 读取同等元数据。"""
    adapter = _make_adapter(tmp_path)
    metadata = _metadata("s1", updated_at=1000)

    await adapter.upsert(metadata)

    assert await adapter.get("s1") == metadata


async def test_get_missing_returns_none(tmp_path: Path) -> None:
    """验证缺失索引返回 None。"""
    adapter = _make_adapter(tmp_path)

    assert await adapter.get("missing") is None


async def test_upsert_overwrites_existing_metadata(tmp_path: Path) -> None:
    """验证同一 session 二次 upsert 覆盖索引字段。"""
    adapter = _make_adapter(tmp_path)
    await adapter.upsert(_metadata("s1", updated_at=1000, preview="old"))
    latest = _metadata("s1", updated_at=2000, message_count=3, preview="new")

    await adapter.upsert(latest)

    assert await adapter.get("s1") == latest


async def test_list_recent_orders_by_updated_at_and_applies_limit(
    tmp_path: Path,
) -> None:
    """验证 list_recent 按更新时间倒序并截断 limit。"""
    adapter = _make_adapter(tmp_path)
    await adapter.upsert(_metadata("old", updated_at=1000))
    await adapter.upsert(_metadata("new", updated_at=3000))
    await adapter.upsert(_metadata("mid", updated_at=2000))

    sessions = await adapter.list_recent(limit=2)

    assert [item.session_id for item in sessions] == ["new", "mid"]


async def test_list_recent_empty_when_root_missing_or_limit_non_positive(
    tmp_path: Path,
) -> None:
    """验证空索引和非正 limit 返回空列表。"""
    adapter = _make_adapter(tmp_path)

    assert await adapter.list_recent() == []

    await adapter.upsert(_metadata("s1", updated_at=1000))
    assert await adapter.list_recent(limit=0) == []


async def test_list_recent_skips_corrupted_json(tmp_path: Path) -> None:
    """验证损坏索引 JSON 被跳过，不阻塞列表展示。"""
    adapter = _make_adapter(tmp_path)
    valid = _metadata("valid", updated_at=1000)
    await adapter.upsert(valid)

    bucket, stem = CrossPlatformPathPolicy().hash_session_id("bad")
    bad_path = tmp_path / "session_index" / bucket / f"{stem}.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{not-json", encoding="utf-8")

    assert await adapter.list_recent() == [valid]


async def test_delete_is_idempotent(tmp_path: Path) -> None:
    """验证 delete 删除索引且可重复调用。"""
    adapter = _make_adapter(tmp_path)
    await adapter.upsert(_metadata("s1", updated_at=1000))

    await adapter.delete("s1")
    await adapter.delete("s1")

    assert await adapter.get("s1") is None


async def test_upsert_writes_expected_json_path(tmp_path: Path) -> None:
    """验证索引文件路径和 JSON 字段符合设计。"""
    adapter = _make_adapter(tmp_path)
    metadata = _metadata("layout", updated_at=1000)

    await adapter.upsert(metadata)

    bucket, stem = CrossPlatformPathPolicy().hash_session_id("layout")
    path = tmp_path / "session_index" / bucket / f"{stem}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["session_id"] == "layout"
    assert payload["updated_at_epoch_ms"] == 1000
    assert payload["message_count"] == 1
    assert payload["preview"] == "hello"
