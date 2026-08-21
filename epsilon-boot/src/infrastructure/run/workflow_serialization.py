"""Run 工作流领域值对象的基础设施层序列化 helper。

本模块把 ``domain/run/workflow.py`` 中 8 个工作流值对象的 ``to_dict``
序列化职责外移到基础设施层，遵循 `docs/steering/ddd-architecture.md` 的
依赖方向：``infrastructure → domain``，即本模块可导入领域值对象类型，但
不向 ``domain/`` 反向暴露任何符号。私有 helper ``_dataclass_to_json_safe_dict``
/ ``_json_safe`` 逐字符对齐领域层原实现，保证行为等价的纯重构。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from domain.run.workflow import (
    ChildRunOrchestrationState,
    CollaborationStepTraceLink,
    CollaborationSummary,
    ParentChildRunLink,
    WorkflowCapabilityDecision,
    WorkflowExecutionPolicy,
    WorkflowPhaseRecord,
    WorkflowRunState,
)


def workflow_capability_decision_to_dict(value: WorkflowCapabilityDecision) -> dict[str, Any]:
    """返回 JSON-safe 能力判定结果。"""

    return _dataclass_to_json_safe_dict(value)


def workflow_execution_policy_to_dict(value: WorkflowExecutionPolicy) -> dict[str, Any]:
    """返回 JSON-safe 工作流执行策略。"""

    return _dataclass_to_json_safe_dict(value)


def workflow_phase_record_to_dict(value: WorkflowPhaseRecord) -> dict[str, Any]:
    """返回 JSON-safe 阶段历史记录。"""

    return _dataclass_to_json_safe_dict(value)


def collaboration_step_trace_link_to_dict(value: CollaborationStepTraceLink) -> dict[str, Any]:
    """返回 JSON-safe 协作步骤追踪关系。"""

    return _dataclass_to_json_safe_dict(value)


def parent_child_run_link_to_dict(value: ParentChildRunLink) -> dict[str, Any]:
    """返回 JSON-safe 父子 Run 关系。"""

    return _dataclass_to_json_safe_dict(value)


def child_run_orchestration_state_to_dict(value: ChildRunOrchestrationState) -> dict[str, Any]:
    """返回 JSON-safe child run 编排状态。"""

    return _dataclass_to_json_safe_dict(value)


def collaboration_summary_to_dict(value: CollaborationSummary) -> dict[str, Any]:
    """返回 JSON-safe 协作摘要。"""

    return _dataclass_to_json_safe_dict(value)


def workflow_run_state_to_dict(value: WorkflowRunState) -> dict[str, Any]:
    """返回 JSON-safe 工作流运行状态。"""

    return _dataclass_to_json_safe_dict(value)


def _dataclass_to_json_safe_dict(value: Any) -> dict[str, Any]:
    """把 dataclass 值对象转换为 JSON-safe 字典。"""

    if not is_dataclass(value):
        raise TypeError("value 必须为 dataclass 实例")
    return {item.name: _json_safe(getattr(value, item.name)) for item in fields(value)}


def _json_safe(value: Any) -> Any:
    """把 enum、datetime、tuple、frozenset 和 dataclass 转换为 JSON-safe 值。"""

    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _dataclass_to_json_safe_dict(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in cast(tuple[object, ...], value)]
    if isinstance(value, frozenset):
        values = cast(frozenset[str], value)
        return [_json_safe(item) for item in sorted(values)]
    if isinstance(value, list):
        return [_json_safe(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    return value
