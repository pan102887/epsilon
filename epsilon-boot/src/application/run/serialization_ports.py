"""Run 应用层序列化能力抽象端口。

应用层通过本模块声明其所需的值对象→JSON-safe dict 序列化能力，具体实现
由 ``infrastructure`` 提供并经组合根注入，使 ``application/run/*`` 生产代码
不再直接 import ``infrastructure`` serializer（遵循
``docs/steering/ddd-architecture.md`` 的默认依赖方向）。序列化实现仍留
基础设施层（ADR-0008）。
"""

from __future__ import annotations

from typing import Any, Protocol

from domain.agent.guardrails import GuardrailObservation, GuardrailSummary
from domain.agent.segmented_execution import SegmentRunMetadata
from domain.run.workflow import (
    ChildRunOrchestrationState,
    WorkflowCapabilityDecision,
    WorkflowRunState,
)


class WorkflowSerializerPort(Protocol):
    """Run 工作流值对象的 JSON-safe 序列化能力。"""

    def workflow_run_state_to_dict(self, value: WorkflowRunState) -> dict[str, Any]:
        """返回 JSON-safe 工作流运行状态。"""
        ...

    def workflow_capability_decision_to_dict(
        self, value: WorkflowCapabilityDecision
    ) -> dict[str, Any]:
        """返回 JSON-safe 能力判定结果。"""
        ...

    def child_run_orchestration_state_to_dict(
        self, value: ChildRunOrchestrationState
    ) -> dict[str, Any]:
        """返回 JSON-safe child run 编排状态。"""
        ...


class GuardrailSerializerPort(Protocol):
    """Guardrail 值对象的线格式序列化能力。"""

    def guardrail_summary_to_dict(self, value: GuardrailSummary) -> dict[str, Any]:
        """返回 JSON-safe 护栏摘要。"""
        ...

    def guardrail_observation_to_event_payload(
        self, value: GuardrailObservation
    ) -> dict[str, Any]:
        """返回 JSON-safe 护栏观测事件 payload。"""
        ...


class SegmentSerializerPort(Protocol):
    """分段执行元数据的 HTTP 线格式序列化能力。"""

    def segment_run_metadata_to_http_dict(
        self, value: SegmentRunMetadata
    ) -> dict[str, object]:
        """返回 HTTP 响应友好的分段元数据字典。"""
        ...
