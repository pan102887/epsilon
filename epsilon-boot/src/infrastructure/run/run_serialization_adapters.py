"""Run 应用层序列化端口的基础设施实现。

各 adapter 逐一委托既有 serializer 自由函数（``segment_serialization`` /
``guardrail_serialization`` / ``workflow_serialization``），输出与原自由函数
逐字节等价。本模块把 ``application/run/*`` 对 serializer 的直接 import 反转为
组合根注入，序列化实现仍留基础设施层（ADR-0008）。
"""

from __future__ import annotations

from typing import Any

from domain.agent.guardrails import GuardrailObservation, GuardrailSummary
from domain.agent.segmented_execution import SegmentRunMetadata
from domain.run.workflow import (
    ChildRunOrchestrationState,
    WorkflowCapabilityDecision,
    WorkflowRunState,
)
from infrastructure.agent.guardrail_serialization import (
    guardrail_observation_to_event_payload,
    guardrail_summary_to_dict,
)
from infrastructure.agent.segment_serialization import (
    segment_run_metadata_to_http_dict,
)
from infrastructure.run.workflow_serialization import (
    child_run_orchestration_state_to_dict,
    workflow_capability_decision_to_dict,
    workflow_run_state_to_dict,
)


class WorkflowSerializerAdapter:
    """委托 workflow_serialization 自由函数的 WorkflowSerializerPort 实现。"""

    def workflow_run_state_to_dict(self, value: WorkflowRunState) -> dict[str, Any]:
        """委托 ``workflow_run_state_to_dict`` 自由函数。"""
        return workflow_run_state_to_dict(value)

    def workflow_capability_decision_to_dict(
        self, value: WorkflowCapabilityDecision
    ) -> dict[str, Any]:
        """委托 ``workflow_capability_decision_to_dict`` 自由函数。"""
        return workflow_capability_decision_to_dict(value)

    def child_run_orchestration_state_to_dict(
        self, value: ChildRunOrchestrationState
    ) -> dict[str, Any]:
        """委托 ``child_run_orchestration_state_to_dict`` 自由函数。"""
        return child_run_orchestration_state_to_dict(value)


class GuardrailSerializerAdapter:
    """委托 guardrail_serialization 自由函数的 GuardrailSerializerPort 实现。"""

    def guardrail_summary_to_dict(self, value: GuardrailSummary) -> dict[str, Any]:
        """委托 ``guardrail_summary_to_dict`` 自由函数。"""
        return guardrail_summary_to_dict(value)

    def guardrail_observation_to_event_payload(
        self, value: GuardrailObservation
    ) -> dict[str, Any]:
        """委托 ``guardrail_observation_to_event_payload`` 自由函数。"""
        return guardrail_observation_to_event_payload(value)


class SegmentSerializerAdapter:
    """委托 segment_serialization 自由函数的 SegmentSerializerPort 实现。"""

    def segment_run_metadata_to_http_dict(
        self, value: SegmentRunMetadata
    ) -> dict[str, object]:
        """委托 ``segment_run_metadata_to_http_dict`` 自由函数。"""
        return segment_run_metadata_to_http_dict(value)
