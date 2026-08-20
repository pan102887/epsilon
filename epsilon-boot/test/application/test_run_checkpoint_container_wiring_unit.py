"""后台 Run checkpoint 容器装配单元测试。"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _load_container_config_module():
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_run_checkpoint_container_wiring_module", str(config_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@pytest.fixture(autouse=True)
def _isolate_container():
    from common.container import container

    original_registry = container._registry.copy()
    original_singletons = container._singletons.copy()
    original_resources = container._async_resources[:]
    original_initialized = container._initialized_resources[:]
    original_run_store = _config_module._run_store_adapter
    original_checkpoint_store = _config_module._run_checkpoint_store_adapter
    original_run_manager = _config_module._run_worker_manager
    yield
    container._registry = original_registry
    container._singletons = original_singletons
    container._async_resources = original_resources
    container._initialized_resources = original_initialized
    _config_module._run_store_adapter = original_run_store
    _config_module._run_checkpoint_store_adapter = original_checkpoint_store
    _config_module._run_worker_manager = original_run_manager


def _set_backend(monkeypatch: pytest.MonkeyPatch, backend_value: str) -> None:
    fake_cfg = MagicMock()
    fake_cfg.backend = (
        _config_module.SessionStoreBackendKind.REDIS
        if backend_value == "redis"
        else _config_module.SessionStoreBackendKind.FILE
    )
    monkeypatch.setattr(_config_module, "session_store_config", fake_cfg)


def _set_run_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    worker_enabled: bool = True,
    checkpoint_enabled: bool = True,
    checkpoint_auto_recovery_enabled: bool = True,
):
    class _RunConfig:
        worker_count = 1
        lease_seconds = 60
        heartbeat_interval_seconds = 10
        max_queued_runs = 100
        max_running_runs = 2
        event_max_count = 1000
        event_ttl_seconds = 86400
        event_stream_wait_seconds = 1.0
        lost_sweep_interval_seconds = 30
        checkpoint_max_recovery_attempts = 3
        checkpoint_max_count = 200
        checkpoint_ttl_seconds = 604800
        checkpoint_max_payload_bytes = 262144
        checkpoint_tool_ledger_max_count = 1000

        def to_capacity_policy(self):
            from domain.run.value_objects import RunCapacityPolicy

            return RunCapacityPolicy(
                max_queued_runs=self.max_queued_runs,
                max_running_runs=self.max_running_runs,
            )

        def to_event_retention_policy(self):
            from domain.run.value_objects import EventRetentionPolicy

            return EventRetentionPolicy(
                max_event_count=self.event_max_count,
                ttl_seconds=self.event_ttl_seconds,
            )

        def to_checkpoint_retention_policy(self):
            from domain.run.value_objects import CheckpointRetentionPolicy

            return CheckpointRetentionPolicy(
                max_checkpoint_count=self.checkpoint_max_count,
                ttl_seconds=self.checkpoint_ttl_seconds,
                max_payload_bytes=self.checkpoint_max_payload_bytes,
                max_tool_ledger_count=self.checkpoint_tool_ledger_max_count,
            )

    cfg = _RunConfig()
    cfg.worker_enabled = worker_enabled
    cfg.checkpoint_enabled = checkpoint_enabled
    cfg.checkpoint_auto_recovery_enabled = checkpoint_auto_recovery_enabled
    monkeypatch.setattr(_config_module, "run_runtime_config", cfg)
    return cfg


def _prepare_local_persistence(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(_config_module, "_local_persistence_root", tmp_path)
    monkeypatch.setattr(
        _config_module,
        "_lock_factory",
        _config_module.LockFactory(acquire_timeout_ms=1000),
    )
    monkeypatch.setattr(_config_module, "_path_policy", _config_module.CrossPlatformPathPolicy())
    monkeypatch.setattr(
        _config_module,
        "_atomic_writer",
        _config_module.TempFileAtomicWriter(fsync_on_write=False),
    )


async def test_file_backend_resolves_local_checkpoint_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from common.container import container
    from domain.run.ports import RunCheckpointStorePort
    from infrastructure.run.local_file_run_checkpoint_store_adapter import (
        LocalFileRunCheckpointStoreAdapter,
    )

    _set_backend(monkeypatch, "file")
    _set_run_config(monkeypatch)
    _config_module.configure_container()
    _prepare_local_persistence(monkeypatch, tmp_path)

    checkpoint_store = await container.resolve(RunCheckpointStorePort)

    assert isinstance(checkpoint_store, LocalFileRunCheckpointStoreAdapter)


async def test_redis_backend_resolves_redis_checkpoint_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from domain.run.ports import RunCheckpointStorePort
    from infrastructure.run.redis_run_checkpoint_store_adapter import (
        RedisRunCheckpointStoreAdapter,
    )

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    checkpoint_store = await container.resolve(RunCheckpointStorePort)

    assert isinstance(checkpoint_store, RedisRunCheckpointStoreAdapter)


async def test_coordinator_receives_checkpoint_dependencies_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from common.container import container
    from common.container_models import Scope
    from domain.chat.ports import ChatServicePort
    from domain.run.ports import RunObservationStorePort
    from domain.task.ports import TaskAgentPort

    class _FakeCoordinator:
        kwargs: dict | None = None

        def __init__(self, **kwargs):
            self.__class__.kwargs = kwargs

    _set_backend(monkeypatch, "file")
    _set_run_config(monkeypatch, checkpoint_enabled=True)
    monkeypatch.setattr(_config_module, "RunExecutionCoordinator", _FakeCoordinator)
    _config_module.configure_container()
    _prepare_local_persistence(monkeypatch, tmp_path)
    container.register(ChatServicePort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: MagicMock(), Scope.SINGLETON)

    registered_coordinator_type = _config_module.register_run_components.__globals__[
        "RunExecutionCoordinator"
    ]
    coordinator = await container.resolve(registered_coordinator_type)
    observation_store = await container.resolve(RunObservationStorePort)

    assert isinstance(coordinator, _FakeCoordinator)
    assert _FakeCoordinator.kwargs is not None
    assert _FakeCoordinator.kwargs["checkpoint_enabled"] is True
    assert _FakeCoordinator.kwargs["checkpoint_store"] is not None
    assert _FakeCoordinator.kwargs["event_store"] is not None
    assert _FakeCoordinator.kwargs["retention_policy"] is not None
    assert observation_store is not None


async def test_checkpoint_disabled_does_not_create_recovery_sweep_for_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from common.container_models import Scope
    from domain.chat.ports import ChatServicePort
    from domain.task.ports import TaskAgentPort

    class _FakeCoordinator:
        def __init__(self, **kwargs):
            pass

    class _FakeManager:
        kwargs: dict | None = None

        def __init__(self, **kwargs):
            self.__class__.kwargs = kwargs

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch, checkpoint_enabled=False)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    monkeypatch.setattr(_config_module, "RunExecutionCoordinator", _FakeCoordinator)
    monkeypatch.setattr(_config_module, "RunWorkerManager", _FakeManager)
    _config_module.configure_container()
    container.register(ChatServicePort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(_FakeCoordinator, lambda: _FakeCoordinator(), Scope.SINGLETON)

    await container.resolve(_FakeManager)

    assert _FakeManager.kwargs is not None
    assert "executor" in _FakeManager.kwargs
    assert "coordinator" not in _FakeManager.kwargs
    assert _FakeManager.kwargs["recovery_sweep"] is None
    assert "recovery_service" not in _FakeManager.kwargs
