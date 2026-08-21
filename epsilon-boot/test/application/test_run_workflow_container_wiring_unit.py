"""Run workflow 容器装配单元测试。"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest


def _load_container_config_module() -> Any:
    """直接加载 ``container_config``，绕过应用包导出副作用。"""

    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_run_workflow_container_wiring_module", str(config_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@pytest.fixture(autouse=True)
def isolate_container() -> Iterator[None]:
    """每个测试恢复全局容器和 Run 模块级单例。"""

    from common.container import container

    original_state = container.capture_state()
    original_run_store = _config_module._run_store_adapter
    original_checkpoint_store = _config_module._run_checkpoint_store_adapter
    original_run_manager = _config_module._run_worker_manager
    yield
    container.restore_state(original_state)
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
) -> Any:
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

        def __init__(self) -> None:
            self.worker_enabled = worker_enabled
            self.checkpoint_enabled = checkpoint_enabled
            self.checkpoint_auto_recovery_enabled = checkpoint_auto_recovery_enabled

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
    monkeypatch.setattr(_config_module, "run_runtime_config", cfg)
    return cfg


@pytest.mark.asyncio
async def test_workflow_components_are_registered_and_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from domain.run.ports import WorkflowRegistryPort, WorkflowSelectorPort
    from infrastructure.run.static_workflow_registry_adapter import (
        StaticWorkflowRegistryAdapter,
    )
    from infrastructure.run.static_workflow_selector import StaticWorkflowSelector
    from infrastructure.run.workflow_config import RunWorkflowConfig

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    workflow_config = await container.resolve(RunWorkflowConfig)
    registry = await container.resolve(WorkflowRegistryPort)
    selector = await container.resolve(WorkflowSelectorPort)
    orchestrator = await container.resolve(_config_module.WorkflowRunOrchestrator)

    assert workflow_config is _config_module.run_workflow_config
    assert workflow_config.to_execution_policy().role_capability_enabled is False
    assert isinstance(registry, StaticWorkflowRegistryAdapter)
    assert isinstance(selector, StaticWorkflowSelector)
    assert orchestrator._workflow_registry is registry


@pytest.mark.asyncio
async def test_run_service_and_coordinator_receive_workflow_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from common.container_models import Scope
    from domain.agent.ports import AgentGuardrailPolicyPort
    from domain.chat.ports import ChatServicePort
    from domain.run.ports import WorkflowRegistryPort, WorkflowSelectorPort
    from domain.task.ports import TaskAgentPort

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()
    container.register(ChatServicePort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(AgentGuardrailPolicyPort, lambda: MagicMock(), Scope.SINGLETON)

    service = await container.resolve(_config_module.RunApplicationService)
    coordinator = await container.resolve(_config_module.RunExecutionCoordinator)
    selector = await container.resolve(WorkflowSelectorPort)
    registry = await container.resolve(WorkflowRegistryPort)
    orchestrator = await container.resolve(_config_module.WorkflowRunOrchestrator)

    assert service._workflow_selector is selector
    assert coordinator._workflow_registry is registry
    assert coordinator._workflow_orchestrator is orchestrator


@pytest.mark.asyncio
async def test_disabled_workflow_config_still_resolves_old_runtime_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from common.container_models import Scope
    from domain.agent.ports import AgentGuardrailPolicyPort
    from domain.chat.ports import ChatServicePort
    from domain.run.ports import WorkflowSelectorPort
    from domain.run.value_objects import RunCreateRequest, RunKind, RunPayload
    from domain.task.ports import TaskAgentPort
    from infrastructure.run.workflow_config import RunWorkflowConfig

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    monkeypatch.setattr(
        _config_module,
        "run_workflow_config",
        RunWorkflowConfig(enabled=False),
    )
    _config_module.configure_container()
    container.register(ChatServicePort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(AgentGuardrailPolicyPort, lambda: MagicMock(), Scope.SINGLETON)

    service = await container.resolve(_config_module.RunApplicationService)
    coordinator = await container.resolve(_config_module.RunExecutionCoordinator)
    selector = await container.resolve(WorkflowSelectorPort)

    selection = selector.select(
        RunCreateRequest(
            payload=RunPayload(
                kind=RunKind.TASK,
                session_id="session-1",
                task={"goal": "fix code", "input_data": {}},
            ),
            client_request_id=None,
        )
    )

    assert service._workflow_selector is selector
    assert coordinator._workflow_orchestrator is not None
    assert selection.workflow is None
    assert selection.reason == "disabled"


@pytest.mark.asyncio
async def test_invalid_workflow_definition_config_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from common.container_errors import ProviderError
    from domain.run.exceptions import RunWorkflowDefinitionError
    from domain.run.ports import WorkflowRegistryPort
    from infrastructure.run.workflow_config import RunWorkflowConfig

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    monkeypatch.setattr(
        _config_module,
        "run_workflow_config",
        RunWorkflowConfig(enabled_workflows="research,unknown_workflow"),
    )
    _config_module.configure_container()

    with pytest.raises(ProviderError) as exc_info:
        await container.resolve(WorkflowRegistryPort)

    assert isinstance(exc_info.value.cause, RunWorkflowDefinitionError)


def test_domain_run_does_not_depend_on_infrastructure_or_application() -> None:
    """领域层不得反向依赖装配或基础设施实现。"""

    domain_root = pathlib.Path(__file__).resolve().parents[2] / "src" / "domain" / "run"
    forbidden = (
        "application.",
        "infrastructure.",
        "fastapi",
        "redis",
    )
    offenders: list[str] = []
    for path in domain_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in forbidden):
            offenders.append(str(path.relative_to(domain_root)))

    assert offenders == []
