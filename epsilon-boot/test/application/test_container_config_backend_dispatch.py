"""``configure_container()`` 按 ``SESSION_STORE_BACKEND`` 动态组装集成测试。

覆盖需求 6.1、6.3.1-6.3.7、7.1：Redis / File 两种组合下各自返回的
Adapter 类型与 ``ReadinessAggregator.checks`` 类型集合必须精确对齐，
杜绝"某后端关闭后健康检查仍被误注册"的回归。
"""

import importlib.util
import pathlib
from types import ModuleType
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from common.container_models import make_registry_key
from domain.health.aggregator import ReadinessAggregator


def _load_container_config_module() -> ModuleType:
    """直接加载 ``container_config``，绕过 ``application`` 包的 ``__init__``。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location("test_backend_dispatch_module", str(config_path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module: Any = _load_container_config_module()


@pytest.fixture(autouse=True)
def isolate_container():
    """每个测试用独立的全局容器状态，避免污染。"""
    from common.container import container

    original_state = container.capture_state()
    yield
    container.restore_state(original_state)


def _set_backend(monkeypatch: pytest.MonkeyPatch, backend_value: str) -> None:
    """用 monkeypatch 替换模块级 ``session_store_config``。"""
    fake_cfg = MagicMock()
    if backend_value == "redis":
        fake_cfg.backend = _config_module.SessionStoreBackendKind.REDIS
    else:
        fake_cfg.backend = _config_module.SessionStoreBackendKind.FILE
    monkeypatch.setattr(_config_module, "session_store_config", fake_cfg)


# ── 用例 (a)：SESSION_STORE_BACKEND=redis ──


def test_redis_backend_registers_redis_resource_only(
    monkeypatch: pytest.MonkeyPatch,
):
    """REDIS 后端 → 只注册 redis 异步资源，不注册 local_persistence。"""
    from common.container import container

    _set_backend(monkeypatch, "redis")

    _config_module.configure_container()

    assert container.has_async_resource("redis") is True
    assert container.has_async_resource("local_persistence") is False
    assert container.has_async_resource("database") is False


def test_redis_backend_readiness_contains_only_redis_check(
    monkeypatch: pytest.MonkeyPatch,
):
    """REDIS 后端 → ReadinessAggregator.checks 仅含 ``RedisHealthCheckAdapter``。

    **不**含 ``MysqlHealthCheckAdapter``（本期默认不注册 database），
    **不**含 ``LocalPersistenceHealthCheckAdapter``。
    """
    from infrastructure.health.redis_health_check_adapter import (
        RedisHealthCheckAdapter,
    )

    _set_backend(monkeypatch, "redis")
    # 构造一个假的 _redis_client 以便 RedisHealthCheckAdapter 能被实例化
    fake_redis_client = MagicMock()
    monkeypatch.setattr(_config_module, "_redis_client", fake_redis_client)

    _config_module.configure_container()

    aggregator = cast(ReadinessAggregator, _config_module._create_readiness_aggregator())
    check_types: set[type[object]] = {type(c) for c in aggregator.checks}
    assert check_types == {RedisHealthCheckAdapter}


# ── 用例 (b)：SESSION_STORE_BACKEND=file（默认） ──


def test_file_backend_registers_local_persistence_resource_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    """FILE 后端 → 只注册 local_persistence 异步资源，不注册 redis。"""
    from common.container import container

    _set_backend(monkeypatch, "file")
    # 避免 _init_workspace 触发实际 Workspace 构造（只需注册不触发初始化）
    monkeypatch.setenv("LOCAL_PERSISTENCE_ROOT", str(tmp_path))

    _config_module.configure_container()

    assert container.has_async_resource("local_persistence") is True
    assert container.has_async_resource("redis") is False
    assert container.has_async_resource("database") is False


def test_file_backend_readiness_contains_only_local_persistence_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    """FILE 后端 → ReadinessAggregator.checks 仅含
    ``LocalPersistenceHealthCheckAdapter``。

    **不**含 Redis / MySQL 健康检查。
    """
    from infrastructure.health.local_persistence_health_check_adapter import (
        LocalPersistenceHealthCheckAdapter,
    )

    _set_backend(monkeypatch, "file")
    # 直接设置模块级变量，模拟 _init_local_persistence 已执行的状态
    monkeypatch.setattr(_config_module, "_local_persistence_root", tmp_path)

    _config_module.configure_container()

    aggregator = cast(ReadinessAggregator, _config_module._create_readiness_aggregator())
    check_types: set[type[object]] = {type(c) for c in aggregator.checks}
    assert check_types == {LocalPersistenceHealthCheckAdapter}


def test_session_store_port_registered_as_singleton_under_both_backends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    """两种后端下 SessionContextStorePort / SessionIndexPort 均以 SINGLETON 注册。"""
    from common.container import container
    from common.container_models import Scope
    from domain.chat.ports import SessionContextStorePort, SessionIndexPort

    for backend in ("redis", "file"):
        # 重新隔离：清空容器状态
        from common.container import Container

        container.restore_state(Container().capture_state())
        _set_backend(monkeypatch, backend)
        if backend == "file":
            monkeypatch.setattr(_config_module, "_local_persistence_root", tmp_path)
        _config_module.configure_container()
        session_store_key = make_registry_key(SessionContextStorePort)
        session_index_key = make_registry_key(SessionIndexPort)
        registry = container.capture_state().registry
        assert session_store_key in registry
        assert registry[session_store_key].scope == Scope.SINGLETON
        assert session_index_key in registry
        assert registry[session_index_key].scope == Scope.SINGLETON


def test_file_backend_creates_local_session_index_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
):
    """FILE 后端创建本地文件会话索引 Adapter。"""
    from infrastructure.session.local_file_session_index_adapter import (
        LocalFileSessionIndexAdapter,
    )

    _set_backend(monkeypatch, "file")
    monkeypatch.setattr(_config_module, "_local_persistence_root", tmp_path)
    monkeypatch.setattr(
        _config_module,
        "_lock_factory",
        _config_module.LockFactory(acquire_timeout_ms=50),
    )
    monkeypatch.setattr(
        _config_module,
        "_path_policy",
        _config_module.CrossPlatformPathPolicy(),
    )
    monkeypatch.setattr(
        _config_module,
        "_atomic_writer",
        _config_module.TempFileAtomicWriter(fsync_on_write=False),
    )

    adapter = _config_module._create_session_index()

    assert isinstance(adapter, LocalFileSessionIndexAdapter)


def test_redis_backend_session_context_and_index_share_ttl(
    monkeypatch: pytest.MonkeyPatch,
):
    """REDIS 后端的 context store 与 session index 使用同一 TTL 配置。"""
    import infrastructure.session.session_ttl_config as ttl_module
    from infrastructure.session.redis_session_context_adapter import (
        RedisSessionContextAdapter,
    )
    from infrastructure.session.redis_session_index_adapter import RedisSessionIndexAdapter

    _set_backend(monkeypatch, "redis")
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    fake_ttl = MagicMock()
    fake_ttl.ttl_seconds = 123
    monkeypatch.setattr(ttl_module, "session_redis_ttl_config", fake_ttl)

    context_adapter = _config_module._create_session_store()
    index_adapter = _config_module._create_session_index()

    assert isinstance(context_adapter, RedisSessionContextAdapter)
    assert isinstance(index_adapter, RedisSessionIndexAdapter)
    assert context_adapter.ttl_seconds == 123
    assert index_adapter.ttl_seconds == 123
