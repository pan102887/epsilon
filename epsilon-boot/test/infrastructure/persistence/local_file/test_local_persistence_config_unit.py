"""``SessionStoreConfig`` / ``LocalPersistenceConfig`` 单元测试。

覆盖默认值、env 覆盖行为，以及"已废弃 TTL 键必须被拒绝"的反向断言
（锁死需求 2.补.5）。
"""

import pytest
from pydantic import ValidationError

from infrastructure.persistence.local_file.config.backend_config import (
    SessionStoreBackendKind,
    SessionStoreConfig,
)
from infrastructure.persistence.local_file.config.local_persistence_config import (
    LocalPersistenceConfig,
)


def test_local_persistence_config_defaults():
    """默认值应与 ``config.properties`` 模板一致。

    决策 1a（local-trace-artifacts）：``LOCAL_PERSISTENCE_ROOT`` 已在
    ``config.properties`` 中注释留空，``root`` 代码默认改为空串——空串语义为
    启用 USER tier 默认迁移（``~/.epsilon/persistence/<project-hash>/``），
    实际默认根在 ``_init_local_persistence`` 装配期由 tier resolver 解析。
    """
    cfg = LocalPersistenceConfig()
    assert cfg.root == ""
    assert cfg.create_if_missing is True
    assert cfg.fsync_on_write is True
    assert cfg.lock_acquire_timeout_ms == 5000
    assert cfg.tmp_sweep_max_age_seconds == 3600


def test_local_persistence_config_env_override(monkeypatch: pytest.MonkeyPatch):
    """环境变量覆盖：``LOCAL_PERSISTENCE_FSYNC_ON_WRITE=false``。"""
    monkeypatch.setenv("LOCAL_PERSISTENCE_FSYNC_ON_WRITE", "false")
    cfg = LocalPersistenceConfig()
    assert cfg.fsync_on_write is False


def test_local_persistence_config_root_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """``LOCAL_PERSISTENCE_ROOT`` 支持从环境变量注入绝对路径。"""
    monkeypatch.setenv("LOCAL_PERSISTENCE_ROOT", str(tmp_path))
    cfg = LocalPersistenceConfig()
    assert cfg.root == str(tmp_path)


# ── 反向断言：LocalPersistenceConfig 不得拥有已废弃的 TTL 字段 ──


def test_local_persistence_config_has_no_ttl_fields():
    """``session_ttl_seconds`` / ``reaper_interval_seconds`` 必须不存在。"""
    cfg = LocalPersistenceConfig()
    assert not hasattr(cfg, "session_ttl_seconds")
    assert not hasattr(cfg, "reaper_interval_seconds")


def test_local_persistence_config_rejects_legacy_ttl_env(
    monkeypatch: pytest.MonkeyPatch,
):
    """外部注入 ``LOCAL_PERSISTENCE_SESSION_TTL_SECONDS`` 必须触发
    ``ValidationError``（需求 2.补.5）。
    """
    monkeypatch.setenv("LOCAL_PERSISTENCE_SESSION_TTL_SECONDS", "3600")
    with pytest.raises(ValidationError):
        LocalPersistenceConfig()


def test_local_persistence_config_rejects_legacy_reaper_env(
    monkeypatch: pytest.MonkeyPatch,
):
    """外部注入 ``LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS`` 必须触发
    ``ValidationError``（需求 2.补.5）。
    """
    monkeypatch.setenv("LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS", "60")
    with pytest.raises(ValidationError):
        LocalPersistenceConfig()


# ── SessionStoreConfig ──


def test_session_store_config_defaults_to_file():
    """默认 backend 必须是 ``FILE``（本期零配置即 file 后端）。"""
    cfg = SessionStoreConfig()
    assert cfg.backend == SessionStoreBackendKind.FILE


def test_session_store_config_redis_override(monkeypatch: pytest.MonkeyPatch):
    """``SESSION_STORE_BACKEND=redis`` 应切换为 Redis 后端。"""
    monkeypatch.setenv("SESSION_STORE_BACKEND", "redis")
    cfg = SessionStoreConfig()
    assert cfg.backend == SessionStoreBackendKind.REDIS


def test_session_store_config_rejects_unknown_backend(
    monkeypatch: pytest.MonkeyPatch,
):
    """未知 backend 值（如 ``memory``）必须触发 ``ValidationError``。"""
    monkeypatch.setenv("SESSION_STORE_BACKEND", "memory")
    with pytest.raises(ValidationError):
        SessionStoreConfig()
