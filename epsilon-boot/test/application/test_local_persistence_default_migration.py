"""会话主状态 USER tier 默认迁移单元测试（任务 14）。

覆盖需求 2.2、2A.1、2A.3、8.5、8.6 与正确性属性 Property 8：

- 未显式配 ``LOCAL_PERSISTENCE_ROOT``（root 为空）且会话后端非 redis 时，
  ``_resolve_local_persistence_config`` 解析出的 root 落
  ``<user_base>/.epsilon/persistence/<project-hash>/``（临时 HOME 断言）。
- 显式 ``LOCAL_PERSISTENCE_ROOT=<abs>`` 时不迁移；``_validate_local_persistence_root``
  的安全校验（与 WORKSPACE_ROOT 相互包含）在迁移路径上仍 fail-fast、不弱化。
- ``SESSION_STORE_BACKEND=redis`` 时不走 USER tier 迁移（原样透传配置）。
- 首次启动一次性提示：旧默认目录非空且新默认目录为空时触发 ``logger.info``，
  且不自动搬运数据（新目录仍为空 / 未拷贝文件）。

沿用既有容器装配测试的加载与隔离范式：通过 importlib 直接加载
``container_config`` 模块，绕过 ``application/__init__.py`` 初始化副作用，并在每个
测试前后恢复模块级 tier resolver 单例。
"""

from __future__ import annotations

import importlib.util
import logging
import pathlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from common.configuration import ConfigurationError
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver


def _load_container_config_module() -> Any:
    """直接加载 ``container_config``，绕过应用包导出副作用。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_local_persistence_default_migration_module", str(config_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@pytest.fixture(autouse=True)
def _reset_tier_resolver() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """每个测试前后重置 tier resolver 模块级单例，避免跨用例串状态。"""
    original = _config_module._tier_resolver
    _config_module._tier_resolver = None
    yield
    _config_module._tier_resolver = original


def _install_resolver(monkeypatch: pytest.MonkeyPatch, project_base: Any, user_base: Any) -> None:
    """安装一个基点确定的 tier resolver 缓存实例，替代默认 CWD/HOME 解析。"""
    resolver = LocalFileTierResolver(project_base=project_base, user_base=user_base)
    monkeypatch.setattr(_config_module, "_tier_resolver", resolver)


def _fake_session_store_config(backend: Any) -> Any:
    """构造仅暴露 backend 的假 session store config。"""
    fake = MagicMock()
    fake.backend = backend
    return fake


def _fake_local_persistence_config(root: str, *, create_if_missing: bool = True) -> Any:
    """构造仅暴露 root / create_if_missing 的假 local persistence config。"""
    fake = MagicMock()
    fake.root = root
    fake.create_if_missing = create_if_missing
    return fake


def test_empty_root_migrates_to_user_tier_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """root 为空 + 后端非 redis → 迁移到 <user_base>/.epsilon/persistence/<hash>/（Property 8）。"""
    project_base = tmp_path / "proj"
    project_base.mkdir()
    user_base = tmp_path / "home"
    user_base.mkdir()
    _install_resolver(monkeypatch, project_base, user_base)

    monkeypatch.setattr(
        _config_module,
        "session_store_config",
        _fake_session_store_config(_config_module.SessionStoreBackendKind.FILE),
    )
    monkeypatch.setattr(
        _config_module,
        "local_persistence_config",
        _fake_local_persistence_config(""),
    )

    effective = _config_module._resolve_local_persistence_config()

    resolver = _config_module._create_tier_resolver()
    expected = user_base.resolve() / ".epsilon" / "persistence" / resolver.project_hash()
    assert pathlib.Path(effective.root) == expected


def test_explicit_root_is_respected_and_not_migrated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """显式 LOCAL_PERSISTENCE_ROOT 尊重原值、不迁移（Property 8）。"""
    explicit = tmp_path / "explicit_persist"
    explicit.mkdir()
    _install_resolver(monkeypatch, tmp_path / "proj", tmp_path / "home")

    monkeypatch.setattr(
        _config_module,
        "session_store_config",
        _fake_session_store_config(_config_module.SessionStoreBackendKind.FILE),
    )
    fake_cfg = _fake_local_persistence_config(str(explicit))
    monkeypatch.setattr(_config_module, "local_persistence_config", fake_cfg)

    effective = _config_module._resolve_local_persistence_config()

    # 显式配置原样透传（返回底层配置对象本身，root 不变）。
    assert effective is fake_cfg
    assert effective.root == str(explicit)


def test_explicit_root_still_fails_fast_on_workspace_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """显式 root 与 WORKSPACE_ROOT 相互包含时 _validate 仍 fail-fast（校验不弱化）。"""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    # 会话根落在 workspace 之下 → 相互包含冲突。
    lp_root = ws_root / "persist"

    fake_ws = MagicMock()
    fake_ws.root = str(ws_root)
    monkeypatch.setattr(_config_module, "workspace_config", fake_ws)

    cfg = _fake_local_persistence_config(str(lp_root))

    with pytest.raises(ConfigurationError, match="不得与 WORKSPACE_ROOT 共用或相互包含"):
        _config_module._validate_local_persistence_root(cfg)


def test_redis_backend_does_not_migrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """SESSION_STORE_BACKEND=redis 时不走 USER tier 迁移（原样透传）。"""
    _install_resolver(monkeypatch, tmp_path / "proj", tmp_path / "home")

    monkeypatch.setattr(
        _config_module,
        "session_store_config",
        _fake_session_store_config(_config_module.SessionStoreBackendKind.REDIS),
    )
    fake_cfg = _fake_local_persistence_config("")
    monkeypatch.setattr(_config_module, "local_persistence_config", fake_cfg)

    effective = _config_module._resolve_local_persistence_config()

    # redis 后端：原样返回底层配置对象，root 保持空、不迁移。
    assert effective is fake_cfg
    assert effective.root == ""


def test_first_start_migration_hint_emitted_without_moving_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """旧默认目录非空 + 新目录为空时触发一次性 logger.info 提示，且不搬运数据。"""
    # 构造旧默认目录 <CWD>/../.local_persistence/epsilon-boot（非空）。
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    legacy_root = (cwd.parent / ".local_persistence" / "epsilon-boot").resolve()
    legacy_root.mkdir(parents=True)
    (legacy_root / "sessions").mkdir()
    (legacy_root / "sessions" / "old.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(cwd)

    project_base = cwd
    user_base = tmp_path / "home"
    user_base.mkdir()
    _install_resolver(monkeypatch, project_base, user_base)

    monkeypatch.setattr(
        _config_module,
        "session_store_config",
        _fake_session_store_config(_config_module.SessionStoreBackendKind.FILE),
    )
    monkeypatch.setattr(
        _config_module,
        "local_persistence_config",
        _fake_local_persistence_config(""),
    )

    with caplog.at_level(logging.INFO, logger=_config_module.logger.name):
        effective = _config_module._resolve_local_persistence_config()

    # 触发一次性中文提示，含旧路径、新路径与两个选项关键字。
    hint_records = [r for r in caplog.records if "旧会话数据目录" in r.getMessage()]
    assert len(hint_records) == 1
    msg = hint_records[0].getMessage()
    assert str(legacy_root) in msg
    assert effective.root in msg
    assert "手动" in msg or "拷贝" in msg
    assert "LOCAL_PERSISTENCE_ROOT" in msg

    # 不自动搬运数据：新默认目录仍不存在（或为空），旧数据原样保留。
    new_root = pathlib.Path(effective.root)
    assert (not new_root.exists()) or not any(new_root.iterdir())
    assert (legacy_root / "sessions" / "old.json").exists()


def test_no_hint_when_legacy_dir_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """旧默认目录不存在时不输出迁移提示。"""
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    _install_resolver(monkeypatch, cwd, tmp_path / "home")
    monkeypatch.setattr(
        _config_module,
        "session_store_config",
        _fake_session_store_config(_config_module.SessionStoreBackendKind.FILE),
    )
    monkeypatch.setattr(
        _config_module,
        "local_persistence_config",
        _fake_local_persistence_config(""),
    )

    with caplog.at_level(logging.INFO, logger=_config_module.logger.name):
        _config_module._resolve_local_persistence_config()

    assert not any("旧会话数据目录" in r.getMessage() for r in caplog.records)
