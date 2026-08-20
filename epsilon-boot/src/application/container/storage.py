"""Storage 相关组合根注册。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from common.container_models import Scope
from domain.agent.ports import ApprovalStateStorePort, ArtifactStorePort, TraceStorePort
from domain.chat.ports import SessionContextStorePort, SessionIndexPort
from domain.health.aggregator import ReadinessAggregator


def register_storage_components(
    container: Any,
    *,
    create_session_store: Callable[[], SessionContextStorePort],
    create_session_index: Callable[[], SessionIndexPort],
    create_approval_state_store: Callable[[], ApprovalStateStorePort],
    create_trace_store: Callable[[], TraceStorePort | None],
    create_artifact_store: Callable[[], ArtifactStorePort | None],
    create_readiness_aggregator: Callable[[], ReadinessAggregator],
) -> None:
    """注册会话、审批、trace、artifact 与 readiness 组件。"""
    container.register(SessionContextStorePort, create_session_store, Scope.SINGLETON)
    container.register(SessionIndexPort, create_session_index, Scope.SINGLETON)
    container.register(ApprovalStateStorePort, create_approval_state_store, Scope.SINGLETON)
    container.register(TraceStorePort, create_trace_store, Scope.SINGLETON)
    container.register(ArtifactStorePort, create_artifact_store, Scope.SINGLETON)
    container.register(ReadinessAggregator, create_readiness_aggregator, Scope.SINGLETON)
