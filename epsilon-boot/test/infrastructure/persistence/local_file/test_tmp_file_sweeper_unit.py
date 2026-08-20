"""``TmpFileSweeper`` 单元测试。

覆盖需求 2.补.1、2.补.2、2.补.8、3.2、9.5：仅清理 ``*.tmp-*`` 残留，
不触碰 ``.json`` 会话文件；mtime 阈值判据正确；返回摘要三字段完整；
**反向断言**类上不存在任何 ``is_expired`` / ``start`` / ``stop`` 方法。
"""

import os
import time
from pathlib import Path

from infrastructure.persistence.local_file.tmp_file_sweeper import TmpFileSweeper


def _backdate(path: Path, seconds_ago: int) -> None:
    """把 ``path`` 的 atime / mtime 回拨 ``seconds_ago`` 秒。"""
    past = time.time() - seconds_ago
    os.utime(path, (past, past))


def test_sweep_once_deletes_stale_tmp(tmp_path: Path):
    """mtime 超过阈值的 ``.tmp-*`` 文件必须被删除。"""
    sessions = tmp_path / "sessions"
    bucket = sessions / "ab"
    bucket.mkdir(parents=True)
    stale = bucket / "cd.json.tmp-123-abcd"
    stale.write_bytes(b"half-written")
    _backdate(stale, seconds_ago=7200)  # 2 小时前

    sweeper = TmpFileSweeper(sessions_root=sessions, max_age_seconds=3600)
    summary = sweeper.sweep_once()

    assert summary == {"scanned": 1, "deleted": 1, "errored": 0}
    assert not stale.exists()


def test_sweep_once_preserves_json_session_file(tmp_path: Path):
    """即使 ``.json`` 会话文件的 mtime 非常旧，也必须保留。"""
    sessions = tmp_path / "sessions"
    bucket = sessions / "ab"
    bucket.mkdir(parents=True)
    session_file = bucket / "cd.json"
    session_file.write_bytes(b"{}")
    _backdate(session_file, seconds_ago=7200)

    sweeper = TmpFileSweeper(sessions_root=sessions, max_age_seconds=3600)
    summary = sweeper.sweep_once()

    # .json 不计入 scanned，不会被删
    assert summary == {"scanned": 0, "deleted": 0, "errored": 0}
    assert session_file.exists()


def test_sweep_once_preserves_fresh_tmp(tmp_path: Path):
    """mtime 距今小于阈值的 ``.tmp-*`` 文件必须保留。"""
    sessions = tmp_path / "sessions"
    bucket = sessions / "ab"
    bucket.mkdir(parents=True)
    fresh = bucket / "cd.json.tmp-123-abcd"
    fresh.write_bytes(b"in-progress")
    # 距今 60s

    sweeper = TmpFileSweeper(sessions_root=sessions, max_age_seconds=3600)
    summary = sweeper.sweep_once()

    assert summary == {"scanned": 1, "deleted": 0, "errored": 0}
    assert fresh.exists()


def test_sweep_once_missing_root_returns_zero(tmp_path: Path):
    """``sessions_root`` 不存在时返回全零摘要。"""
    sessions = tmp_path / "absent"
    sweeper = TmpFileSweeper(sessions_root=sessions, max_age_seconds=3600)
    summary = sweeper.sweep_once()
    assert summary == {"scanned": 0, "deleted": 0, "errored": 0}


def test_sweep_once_ignores_lock_files(tmp_path: Path):
    """``.lock`` 文件即使 mtime 很旧也不被触碰。"""
    sessions = tmp_path / "sessions"
    bucket = sessions / "ab"
    bucket.mkdir(parents=True)
    lock = bucket / "cd.json.lock"
    lock.write_bytes(b"")
    _backdate(lock, seconds_ago=86400)

    sweeper = TmpFileSweeper(sessions_root=sessions, max_age_seconds=3600)
    summary = sweeper.sweep_once()

    assert summary == {"scanned": 0, "deleted": 0, "errored": 0}
    assert lock.exists()


def test_sweep_once_multiple_buckets(tmp_path: Path):
    """多 bucket 下并存的 ``.tmp-*`` 与 ``.json`` 被分别处理。"""
    sessions = tmp_path / "sessions"
    for bucket_name in ("ab", "cd"):
        bucket = sessions / bucket_name
        bucket.mkdir(parents=True)
        (bucket / "a.json").write_bytes(b"{}")
        stale = bucket / "a.json.tmp-1-x"
        stale.write_bytes(b"x")
        _backdate(stale, seconds_ago=7200)

    sweeper = TmpFileSweeper(sessions_root=sessions, max_age_seconds=3600)
    summary = sweeper.sweep_once()
    assert summary == {"scanned": 2, "deleted": 2, "errored": 0}
    # .json 依然存在
    assert (sessions / "ab" / "a.json").exists()
    assert (sessions / "cd" / "a.json").exists()


# ── 反向断言：本类不得存在 TTL 相关方法（需求 2.补.1、2.补.6） ──


def test_tmp_file_sweeper_has_no_ttl_methods():
    """``TmpFileSweeper`` 必须不具备任何 TTL / 后台任务语义的方法。"""
    assert not hasattr(TmpFileSweeper, "is_expired")
    assert not hasattr(TmpFileSweeper, "start")
    assert not hasattr(TmpFileSweeper, "stop")
