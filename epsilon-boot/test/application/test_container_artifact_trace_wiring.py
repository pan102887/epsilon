"""artifact / trace store 与 tier resolver 的容器装配单元测试。

验证任务 13（DI 装配：tier resolver + artifact store + 改 trace store 工厂）：

- ``configure_container()`` 后 ``TraceStorePort`` 与 ``ArtifactStorePort`` 各解析为
  共享单例（两次 resolve 同一实例，写读共享）。
- ``ARTIFACT_ENABLED=false`` / ``TRACE_ENABLED=false`` 时对应 Port 解析为 None
  （Property 6：可选注入零行为变化）。
- ``_create_tier_resolver()`` 返回缓存单例。

沿用既有容器装配测试的加载与隔离范式：通过 importlib 直接加载
``container_config`` 模块，绕过 ``application/__init__.py`` 初始化副作用，并在每个
测试前后恢复全局容器状态与模块级单例。
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _load_container_config_module() -> Any:
    """直接加载 ``container_config``，绕过应用包导出副作用。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_container_artifact_trace_wiring_module", str(config_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@pytest.fixture(autouse=True)
def _isolate_container() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """每个测试恢复全局容器状态与 tier resolver 模块级单例。"""
    from common.container import container

    test_container = cast(Any, container)
    original_registry = test_container._registry.copy()
    original_singletons = test_container._singletons.copy()
    original_resources = test_container._async_resources[:]
    original_tier_resolver = _config_module._tier_resolver
    yield
    test_container._registry = original_registry
    test_container._singletons = original_singletons
    test_container._async_resources = original_resources
    _config_module._tier_resolver = original_tier_resolver


def _set_backend_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """将会话后端设为 redis，避免装配触发本地持久化异步资源初始化。"""
    fake_cfg = MagicMock()
    fake_cfg.backend = _config_module.SessionStoreBackendKind.REDIS
    monkeypatch.setattr(_config_module, "session_store_config", fake_cfg)


async def test_trace_and_artifact_ports_resolve_as_shared_singletons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TraceStorePort 与 ArtifactStorePort 均以共享单例装配（Property 6、需求 3.6/8.2）。"""
    from common.container import container
    from domain.agent.ports import ArtifactStorePort, TraceStorePort

    _set_backend_redis(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    trace_first = await container.resolve(TraceStorePort)
    trace_second = await container.resolve(TraceStorePort)
    artifact_first = await container.resolve(ArtifactStorePort)
    artifact_second = await container.resolve(ArtifactStorePort)

    # 默认启用（TRACE_ENABLED / ARTIFACT_ENABLED 默认 true），两次解析同一实例。
    assert trace_first is not None
    assert trace_first is trace_second
    assert artifact_first is not None
    assert artifact_first is artifact_second


async def test_artifact_port_resolves_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARTIFACT_ENABLED=false 时 ArtifactStorePort 解析为 None（Property 6）。"""
    import importlib

    from common.container import container
    from domain.agent.ports import ArtifactStorePort

    _set_backend_redis(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    # ArtifactConfig frozen 且非热更新，改环境变量不会重载既有单例；
    # 直接以 enabled=False 的 fake 配置替换模块级对象（工厂内惰性 import 该模块）。
    # 注意：包 __init__ 把 artifact_config 实例同名重导出，遮蔽了同名子模块，
    # 故经 importlib.import_module 拿到真正的子模块对象再 patch。
    artifact_config_module = importlib.import_module("infrastructure.artifact.artifact_config")
    fake_artifact_config = MagicMock()
    fake_artifact_config.enabled = False
    monkeypatch.setattr(artifact_config_module, "artifact_config", fake_artifact_config)
    _config_module.configure_container()

    artifact_store = await container.resolve(ArtifactStorePort)

    assert artifact_store is None


async def test_trace_port_resolves_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TRACE_ENABLED=false 时 TraceStorePort 解析为 None（Property 6）。"""
    import infrastructure.trace.trace_config as trace_config_module
    from common.container import container
    from domain.agent.ports import TraceStorePort

    _set_backend_redis(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    # TraceConfig frozen 且非热更新，改环境变量不会重载既有单例；
    # 直接以 enabled=False 的 fake 配置替换模块级对象（工厂内惰性 import）。
    fake_trace_config = MagicMock()
    fake_trace_config.enabled = False
    monkeypatch.setattr(trace_config_module, "trace_config", fake_trace_config)
    _config_module.configure_container()

    trace_store = await container.resolve(TraceStorePort)

    assert trace_store is None


async def test_create_tier_resolver_returns_cached_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_create_tier_resolver() 惰性缓存，重复调用返回同一实例。"""
    from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver

    monkeypatch.setattr(_config_module, "_tier_resolver", None)

    first = _config_module._create_tier_resolver()
    second = _config_module._create_tier_resolver()

    assert isinstance(first, LocalFileTierResolver)
    assert first is second
