"""Run runtime 相关组合根注册。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from application.run.run_application_service import RunApplicationService
from application.run.run_approval_resumer import RunApprovalResumer
from application.run.run_checkpoint_recovery_service import RunRecoveryService
from application.run.run_execution_coordinator import RunExecutionCoordinator
from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from common.container_models import Scope
from domain.agent.ports import RunGuardrailRecorderPort
from domain.run.ports import (
    RunCheckpointStorePort,
    RunEventStorePort,
    RunObservationStorePort,
    RunStorePort,
    WorkflowRegistryPort,
    WorkflowSelectorPort,
)
from infrastructure.run.run_worker_manager import RunWorkerManager
from infrastructure.run.workflow_config import RunWorkflowConfig


def register_run_components(
    container: Any,
    *,
    create_run_workflow_config: Callable[[], RunWorkflowConfig],
    create_run_workflow_registry: Callable[[], Awaitable[WorkflowRegistryPort]],
    create_run_workflow_selector: Callable[[], Awaitable[WorkflowSelectorPort]],
    create_workflow_run_orchestrator: Callable[[], Awaitable[WorkflowRunOrchestrator]],
    create_run_store_adapter: Callable[[], RunStorePort],
    create_run_guardrail_recorder: Callable[[], Awaitable[RunGuardrailRecorderPort | None]],
    create_run_approval_resumer: Callable[[], Awaitable[RunApprovalResumer]],
    create_run_checkpoint_store_adapter: Callable[[], RunCheckpointStorePort],
    create_run_execution_coordinator: Callable[[], Awaitable[RunExecutionCoordinator]],
    create_run_recovery_service: Callable[[], Awaitable[RunRecoveryService]],
    create_run_application_service: Callable[[], Awaitable[RunApplicationService]],
    create_run_worker_manager: Callable[[], Awaitable[RunWorkerManager]],
    run_worker_manager_type: type[RunWorkerManager] = RunWorkerManager,
) -> None:
    """注册 Run runtime 相关组件。"""
    container.register(RunWorkflowConfig, create_run_workflow_config, Scope.SINGLETON)
    container.register(WorkflowRegistryPort, create_run_workflow_registry, Scope.SINGLETON)
    container.register(WorkflowSelectorPort, create_run_workflow_selector, Scope.SINGLETON)
    container.register(WorkflowRunOrchestrator, create_workflow_run_orchestrator, Scope.SINGLETON)
    container.register(RunStorePort, create_run_store_adapter, Scope.SINGLETON)
    container.register(RunEventStorePort, create_run_store_adapter, Scope.SINGLETON)
    container.register(RunObservationStorePort, create_run_store_adapter, Scope.SINGLETON)
    container.register(RunGuardrailRecorderPort, create_run_guardrail_recorder, Scope.SINGLETON)
    container.register(RunApprovalResumer, create_run_approval_resumer, Scope.SINGLETON)
    container.register(
        RunCheckpointStorePort,
        create_run_checkpoint_store_adapter,
        Scope.SINGLETON,
    )
    container.register(RunExecutionCoordinator, create_run_execution_coordinator, Scope.SINGLETON)
    container.register(RunRecoveryService, create_run_recovery_service, Scope.SINGLETON)
    container.register(RunApplicationService, create_run_application_service, Scope.SINGLETON)
    container.register(run_worker_manager_type, create_run_worker_manager, Scope.SINGLETON)
