"""``LocalFileSessionContextAdapter`` 单元测试。

覆盖需求 1.1-1.7、2.补.2、9.1，以及正确性属性 Property 1、2、5、8。
"""

import json
import os
import time
from pathlib import Path

import pytest

from domain.chat.context import ConversationContext
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.session.local_file_session_context_adapter import (
    LocalFileSessionContextAdapter,
)


def _make_adapter(root: Path) -> LocalFileSessionContextAdapter:
    """构造一个落盘到 ``root`` 的适配器（辅助函数）。"""
    return LocalFileSessionContextAdapter(
        root=root,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _make_context() -> ConversationContext:
    """构造一个带若干消息的 ``ConversationContext``。"""
    ctx = ConversationContext()
    ctx.add_system_message("你好系统")
    ctx.add_user_message("你好用户")
    ctx.add_assistant_message("你好助手")
    return ctx


# ── 基本 save/load/delete ──


async def test_save_then_load_roundtrip(tmp_path: Path):
    """``save`` → ``load`` 往返等价（Property 1）。"""
    adapter = _make_adapter(tmp_path)
    ctx = _make_context()
    await adapter.save("session-1", ctx)
    loaded = await adapter.load("session-1")
    assert loaded.to_dict() == ctx.to_dict()


async def test_load_nonexistent_returns_empty_context(tmp_path: Path):
    """``load`` 不存在的 session 返回空 ``ConversationContext``（需求 1.3）。"""
    adapter = _make_adapter(tmp_path)
    loaded = await adapter.load("nonexistent")
    assert loaded.to_dict() == {"messages": []}


async def test_delete_nonexistent_is_idempotent(tmp_path: Path):
    """``delete`` 不存在的 session 不抛（Property 2）。"""
    adapter = _make_adapter(tmp_path)
    await adapter.delete("nonexistent")
    # 第二次依然无异常
    await adapter.delete("nonexistent")


async def test_save_then_delete_then_load(tmp_path: Path):
    """``save`` → ``delete`` → ``load`` 返回空。"""
    adapter = _make_adapter(tmp_path)
    ctx = _make_context()
    await adapter.save("s", ctx)
    await adapter.delete("s")
    loaded = await adapter.load("s")
    assert loaded.to_dict() == {"messages": []}


async def test_exists_tracks_context_file_lifecycle(tmp_path: Path):
    """``exists`` 只反映会话 JSON 文件是否存在。"""
    adapter = _make_adapter(tmp_path)

    assert await adapter.exists("s") is False

    await adapter.save("s", _make_context())
    assert await adapter.exists("s") is True

    await adapter.delete("s")
    assert await adapter.exists("s") is False


async def test_load_corrupted_json_returns_empty_and_logs_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """损坏 JSON 触发 ``logger.error`` + 返回空（需求 1.4）。"""
    adapter = _make_adapter(tmp_path)
    # 先写一个合法文件以拿到正确目录结构
    await adapter.save("s-bad", _make_context())
    # 然后把文件内容改成损坏 JSON
    policy = CrossPlatformPathPolicy()
    bucket, stem = policy.hash_session_id("s-bad")
    path = tmp_path / "sessions" / bucket / f"{stem}.json"
    path.write_bytes(b"{not-valid-json")
    caplog.clear()
    import logging

    with caplog.at_level(logging.ERROR):
        loaded = await adapter.load("s-bad")
    assert loaded.to_dict() == {"messages": []}
    assert any("反序列化会话上下文失败" in r.message for r in caplog.records)


# ── 需求 2.补.2 反向断言：会话无 TTL，mtime 回拨不会让 load 返回空 ──


async def test_load_returns_ctx_when_mtime_backdated_one_day(tmp_path: Path):
    """``save`` 后将 ``mtime`` 回拨到 1 天前，``load`` 仍返回原 ctx。"""
    adapter = _make_adapter(tmp_path)
    ctx = _make_context()
    await adapter.save("age-1d", ctx)
    # 找到文件并回拨 mtime
    policy = CrossPlatformPathPolicy()
    bucket, stem = policy.hash_session_id("age-1d")
    path = tmp_path / "sessions" / bucket / f"{stem}.json"
    past = time.time() - 86400
    os.utime(path, (past, past))
    loaded = await adapter.load("age-1d")
    assert loaded.to_dict() == ctx.to_dict()


async def test_load_returns_ctx_when_mtime_backdated_thirty_days(tmp_path: Path):
    """``save`` 后将 ``mtime`` 回拨到 30 天前，``load`` 仍返回原 ctx。"""
    adapter = _make_adapter(tmp_path)
    ctx = _make_context()
    await adapter.save("age-30d", ctx)
    policy = CrossPlatformPathPolicy()
    bucket, stem = policy.hash_session_id("age-30d")
    path = tmp_path / "sessions" / bucket / f"{stem}.json"
    past = time.time() - 30 * 86400
    os.utime(path, (past, past))
    loaded = await adapter.load("age-30d")
    assert loaded.to_dict() == ctx.to_dict()


# ── 需求 9.1：save 遇 OSError 抛出且日志含结构化字段 ──


async def test_save_permission_error_raises_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """``save`` 遇 ``PermissionError`` 抛出且日志含 ``error_class=PermissionError``。"""
    adapter = _make_adapter(tmp_path)

    # monkeypatch TempFileAtomicWriter.write_bytes_atomic to raise
    def boom(target: Path, payload: bytes) -> None:
        raise PermissionError(13, "permission denied", str(target))

    monkeypatch.setattr(
        adapter._writer,  # type: ignore[attr-defined]
        "write_bytes_atomic",
        boom,
    )

    import logging

    caplog.clear()
    with caplog.at_level(logging.ERROR), pytest.raises(PermissionError):
        await adapter.save("s-perm", _make_context())
    assert any(
        "operation=save" in r.message and "error_class=PermissionError" in r.message
        for r in caplog.records
    )


# ── 构造函数签名反向约束（需求 2.补.6） ──


def test_constructor_does_not_accept_ttl_or_reaper(tmp_path: Path):
    """构造函数必须拒绝 ``ttl_seconds`` / ``reaper`` 参数。"""
    with pytest.raises(TypeError):
        LocalFileSessionContextAdapter(
            root=tmp_path,
            lock_factory=LockFactory(acquire_timeout_ms=100),
            path_policy=CrossPlatformPathPolicy(),
            atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
            ttl_seconds=3600,  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        LocalFileSessionContextAdapter(
            root=tmp_path,
            lock_factory=LockFactory(acquire_timeout_ms=100),
            path_policy=CrossPlatformPathPolicy(),
            atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
            reaper=object(),  # type: ignore[call-arg]
        )


# ── 文件布局：save 会在 sessions/<bucket>/<stem>.json 下生成 ──


async def test_save_writes_to_expected_path(tmp_path: Path):
    """``save`` 产生的 JSON 路径与 ``hash_session_id`` 一致。"""
    adapter = _make_adapter(tmp_path)
    await adapter.save("layout-test", _make_context())
    policy = CrossPlatformPathPolicy()
    bucket, stem = policy.hash_session_id("layout-test")
    path = tmp_path / "sessions" / bucket / f"{stem}.json"
    assert path.exists()
    # 内容能被 json 解析
    payload = json.loads(path.read_bytes())
    assert "messages" in payload
