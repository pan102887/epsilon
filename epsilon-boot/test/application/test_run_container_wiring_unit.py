"""后台 Run runtime 容器装配单元测试。"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Iterator
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _load_container_config_module() -> Any:
    """直接加载 ``container_config``，绕过应用包导出副作用。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_run_container_wiring_module", str(config_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@pytest.fixture(autouse=True)
def _isolate_container() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """每个测试恢复全局容器和 Run 模块级单例。"""
    from common.container import container

    test_container = cast(Any, container)
    original_registry = test_container._registry.copy()
    original_singletons = test_container._singletons.copy()
    original_resources = test_container._async_resources[:]
    original_initialized = test_container._initialized_resources[:]
    original_run_store = _config_module._run_store_adapter
    original_checkpoint_store = _config_module._run_checkpoint_store_adapter
    original_run_manager = _config_module._run_worker_manager
    yield
    test_container._registry = original_registry
    test_container._singletons = original_singletons
    test_container._async_resources = original_resources
    test_container._initialized_resources = original_initialized
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

        def to_capacity_policy(self) -> Any:
            from domain.run.value_objects import RunCapacityPolicy

            return RunCapacityPolicy(
                max_queued_runs=self.max_queued_runs,
                max_running_runs=self.max_running_runs,
            )

        def to_event_retention_policy(self) -> Any:
            from domain.run.value_objects import EventRetentionPolicy

            return EventRetentionPolicy(
                max_event_count=self.event_max_count,
                ttl_seconds=self.event_ttl_seconds,
            )

        def to_checkpoint_retention_policy(self) -> Any:
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


async def test_file_backend_registers_and_resolves_shared_local_run_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    from common.container import container
    from domain.run.ports import RunEventStorePort, RunObservationStorePort, RunStorePort
    from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter

    _set_backend(monkeypatch, "file")
    _set_run_config(monkeypatch)
    _config_module.configure_container()
    _prepare_local_persistence(monkeypatch, tmp_path)

    run_store = await container.resolve(RunStorePort)
    event_store = await container.resolve(RunEventStorePort)
    observation_store = await container.resolve(RunObservationStorePort)

    assert isinstance(run_store, LocalFileRunStoreAdapter)
    assert event_store is run_store
    assert observation_store is run_store


async def test_redis_backend_registers_and_resolves_shared_redis_run_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from domain.run.ports import RunEventStorePort, RunObservationStorePort, RunStorePort
    from infrastructure.run.redis_run_store_adapter import RedisRunStoreAdapter

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    run_store = await container.resolve(RunStorePort)
    event_store = await container.resolve(RunEventStorePort)
    observation_store = await container.resolve(RunObservationStorePort)

    assert isinstance(run_store, RedisRunStoreAdapter)
    assert event_store is run_store
    assert observation_store is run_store


async def test_run_worker_disabled_does_not_register_worker_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from common.container_models import make_registry_key
    from domain.run.ports import RunEventStorePort, RunObservationStorePort, RunStorePort

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch, worker_enabled=False)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    registry = cast(Any, container)._registry
    assert make_registry_key(RunStorePort) in registry
    assert make_registry_key(RunEventStorePort) in registry
    assert make_registry_key(RunObservationStorePort) in registry
    assert make_registry_key(_config_module.RunApplicationService) in registry
    assert make_registry_key(_config_module.RunExecutionCoordinator) in registry
    assert make_registry_key(_config_module.RunWorkerManager) in registry
    assert container.has_async_resource("run_worker_manager") is False


async def test_run_worker_resource_start_and_stop_calls_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common.container import container
    from common.container_models import Scope
    from domain.chat.ports import ChatServicePort
    from domain.task.ports import TaskAgentPort

    class _FakeManager:
        instances: ClassVar[list[_FakeManager]] = []

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.wake_count = 0
            self.__class__.instances.append(self)

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

        def wake_up(self) -> None:
            self.wake_count += 1

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch, worker_enabled=True)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    monkeypatch.setattr(_config_module, "RunWorkerManager", _FakeManager)
    _config_module.configure_container()
    container.register(ChatServicePort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: MagicMock(), Scope.SINGLETON)

    resource = next(
        entry
        for entry in cast(Any, container)._async_resources
        if entry.name == "run_worker_manager"
    )
    await resource.initializer()
    await resource.cleanup()

    manager = _FakeManager.instances[0]
    assert manager.started is True
    assert manager.stopped is True
    assert "executor" in manager.kwargs
    assert "coordinator" not in manager.kwargs
    assert manager.kwargs["recovery_sweep"] is not None
    assert "recovery_service" not in manager.kwargs


async def test_observation_store_binding_keeps_shared_adapter_when_convergence_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛开关开启时，观察写端口应与 Run store 共享同一 adapter。"""
    from common.container import container
    from domain.run.ports import RunObservationStorePort, RunStorePort

    _set_backend(monkeypatch, "redis")
    cfg = _set_run_config(monkeypatch)
    cfg.guardrail_runtime_convergence_enabled = True
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    run_store = await container.resolve(RunStorePort)
    observation_store = await container.resolve(RunObservationStorePort)

    assert observation_store is run_store


async def test_observation_store_binding_keeps_shared_adapter_when_convergence_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛开关关闭时，先保留观察写端口绑定但不改变当前运行时行为。"""
    from common.container import container
    from domain.run.ports import RunObservationStorePort, RunStorePort

    _set_backend(monkeypatch, "redis")
    cfg = _set_run_config(monkeypatch)
    cfg.guardrail_runtime_convergence_enabled = False
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    run_store = await container.resolve(RunStorePort)
    observation_store = await container.resolve(RunObservationStorePort)

    assert observation_store is run_store

    from common.container import container
    from common.container_models import Scope, make_registry_key
    from domain.chat.ports import ChatServicePort
    from domain.task.ports import TaskAgentPort

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    registry = cast(Any, container)._registry
    chat_service_key = make_registry_key(ChatServicePort)
    task_agent_key = make_registry_key(TaskAgentPort)
    assert chat_service_key in registry
    assert task_agent_key in registry
    assert registry[chat_service_key].scope == Scope.SINGLETON
    assert registry[task_agent_key].scope == Scope.SINGLETON


async def test_run_guardrail_recorder_resolves_when_convergence_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛开关开启时应解析真实 RunGuardrailRecorder。"""

    from common.container import container
    from domain.agent.ports import RunGuardrailRecorderPort

    _set_backend(monkeypatch, "redis")
    cfg = _set_run_config(monkeypatch)
    cfg.guardrail_runtime_convergence_enabled = True
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    recorder = await container.resolve(RunGuardrailRecorderPort)
    run_store = await container.resolve(_config_module.RunStorePort)
    observation_store = await container.resolve(_config_module.RunObservationStorePort)

    assert isinstance(recorder, _config_module.RunGuardrailRecorder)
    assert recorder._run_store is run_store
    assert recorder._observation_store is observation_store


async def test_run_guardrail_recorder_resolves_none_when_convergence_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛开关关闭时容器应返回 None，供后续接线保持兼容。"""

    from common.container import container
    from domain.agent.ports import RunGuardrailRecorderPort

    _set_backend(monkeypatch, "redis")
    cfg = _set_run_config(monkeypatch)
    cfg.guardrail_runtime_convergence_enabled = False
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()

    recorder = await container.resolve(RunGuardrailRecorderPort)

    assert recorder is None


async def test_create_agent_injects_recorder_when_convergence_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛开关开启时，Agent 工厂应向 ReActAdapter 注入 recorder。"""

    mock_tool_registry = MagicMock()
    mock_context_builder = MagicMock()
    mock_approval_policy = MagicMock()
    mock_approval_store = MagicMock()
    mock_guardrail_policy = MagicMock()
    mock_recorder = MagicMock()

    resolve_map: dict[Any, Any] = {
        _config_module.ToolRegistry: mock_tool_registry,
        _config_module.ContextBuilderPort: mock_context_builder,
        _config_module.ApprovalPolicyPort: mock_approval_policy,
        _config_module.ApprovalStateStorePort: mock_approval_store,
        _config_module.AgentGuardrailPolicyPort: mock_guardrail_policy,
        _config_module.RunGuardrailRecorderPort: mock_recorder,
        _config_module.TraceStorePort: None,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        return resolve_map[abstract_type]

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch.object(
            _config_module,
            "_run_guardrail_runtime_convergence_enabled",
            return_value=True,
        ),
        patch("infrastructure.agent.react_agent_adapter.ReActAgentAdapter") as mock_adapter,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)

        await _config_module._create_agent()

    resolved_types = [call.args[0] for call in mock_container.resolve.call_args_list]
    assert _config_module.RunGuardrailRecorderPort in resolved_types
    assert mock_adapter.call_args.kwargs["run_guardrail_recorder"] is mock_recorder


async def test_create_agent_skips_recorder_resolution_when_convergence_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收敛开关关闭时，Agent 工厂不应解析 recorder 且保持旧注入路径。"""

    mock_tool_registry = MagicMock()
    mock_context_builder = MagicMock()
    mock_approval_policy = MagicMock()
    mock_approval_store = MagicMock()
    mock_guardrail_policy = MagicMock()

    resolve_map: dict[Any, Any] = {
        _config_module.ToolRegistry: mock_tool_registry,
        _config_module.ContextBuilderPort: mock_context_builder,
        _config_module.ApprovalPolicyPort: mock_approval_policy,
        _config_module.ApprovalStateStorePort: mock_approval_store,
        _config_module.AgentGuardrailPolicyPort: mock_guardrail_policy,
        _config_module.TraceStorePort: None,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        return resolve_map[abstract_type]

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch.object(
            _config_module,
            "_run_guardrail_runtime_convergence_enabled",
            return_value=False,
        ),
        patch("infrastructure.agent.react_agent_adapter.ReActAgentAdapter") as mock_adapter,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)

        await _config_module._create_agent()

    resolved_types = [call.args[0] for call in mock_container.resolve.call_args_list]
    assert _config_module.RunGuardrailRecorderPort not in resolved_types
    assert mock_adapter.call_args.kwargs["run_guardrail_recorder"] is None


async def test_run_approval_resumer_resolves_with_chat_and_task_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """容器应把 Chat/Task 端口装配为按 RunKind 分派的审批恢复器。"""

    from common.container import container
    from common.container_models import Scope
    from domain.chat.ports import ChatServicePort
    from domain.task.ports import TaskAgentPort

    chat_service = MagicMock()
    task_agent = MagicMock()

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()
    container.register(ChatServicePort, lambda: chat_service, Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: task_agent, Scope.SINGLETON)

    resumer = await container.resolve(_config_module.RunApprovalResumer)

    assert isinstance(resumer, _config_module.RunApprovalResumer)
    assert resumer._chat_service is chat_service
    assert resumer._task_agent is task_agent


async def test_task_agent_port_resolves_with_callable_resume_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实容器解析出的 TaskAgentPort 应暴露可调用的 resume_approval。"""

    from common.container import container
    from common.container_models import Scope
    from domain.agent.ports import AgentPort, ApprovalStateStorePort
    from domain.chat.ports import ContextCompactionPort, SessionContextStorePort
    from domain.model_access.ports import ModelRegistryPort
    from domain.prompt.ports import PromptRegistryPort
    from domain.prompt.value_objects import LoadedPrompt
    from domain.task.ports import TaskAgentPort
    from domain.workspace import Workspace
    from infrastructure.task.task_agent_adapter import TaskAgentAdapter

    agent = MagicMock()
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    session_store = MagicMock()
    prompt_registry = MagicMock()
    prompt_registry.get.return_value = LoadedPrompt(
        prompt_id="task-template@v1",
        name="task-template",
        version="v1",
        content="template",
    )
    approval_store = MagicMock()

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()
    container.register(AgentPort, lambda: agent, Scope.SINGLETON)
    container.register(ModelRegistryPort, lambda: model_registry, Scope.SINGLETON)
    container.register(ContextCompactionPort, lambda: MagicMock(), Scope.SINGLETON)
    container.register(SessionContextStorePort, lambda: session_store, Scope.SINGLETON)
    container.register(PromptRegistryPort, lambda: prompt_registry, Scope.SINGLETON)
    container.register(ApprovalStateStorePort, lambda: approval_store, Scope.SINGLETON)
    workspace = MagicMock()
    workspace.display_root_hint.return_value = "/workspace"
    workspace.resolve_path.side_effect = lambda path: path
    container.register(Workspace, lambda: workspace, Scope.SINGLETON)

    task_agent = await container.resolve(TaskAgentPort)

    assert isinstance(task_agent, TaskAgentAdapter)
    assert callable(task_agent.resume_approval)


async def test_run_application_service_injects_run_approval_resumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RunApplicationService 应注入容器解析出的 RunApprovalResumer。"""

    from common.container import container
    from common.container_models import Scope

    RunApplicationService = _config_module.RunApplicationService
    RunApprovalResumer = _config_module.RunApprovalResumer
    from domain.agent.ports import AgentGuardrailPolicyPort
    from domain.chat.ports import ChatServicePort
    from domain.run.ports import WorkflowSelectorPort
    from domain.task.ports import TaskAgentPort

    chat_service = MagicMock()
    task_agent = MagicMock()
    guardrail_policy = MagicMock()
    workflow_selector = MagicMock()

    _set_backend(monkeypatch, "redis")
    _set_run_config(monkeypatch)
    monkeypatch.setattr(_config_module, "_redis_client", MagicMock())
    _config_module.configure_container()
    container.register(ChatServicePort, lambda: chat_service, Scope.SINGLETON)
    container.register(TaskAgentPort, lambda: task_agent, Scope.SINGLETON)
    container.register(
        AgentGuardrailPolicyPort,
        lambda: guardrail_policy,
        Scope.SINGLETON,
    )
    container.register(
        WorkflowSelectorPort,
        lambda: workflow_selector,
        Scope.SINGLETON,
    )

    service = await container.resolve(RunApplicationService)
    resumer = await container.resolve(RunApprovalResumer)

    assert isinstance(service, RunApplicationService)
    assert service._approval_resumer is resumer
