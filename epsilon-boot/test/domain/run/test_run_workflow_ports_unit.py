"""Run workflow Port 静态签名测试模块。"""

from __future__ import annotations

import inspect
from typing import get_type_hints

from domain.run.ports import (
    RunStorePort,
    WorkflowRegistryPort,
    WorkflowSelection,
    WorkflowSelectorPort,
)
from domain.run.workflow import WorkflowDefinition


def _signature(method) -> inspect.Signature:
    """返回方法签名对象。"""

    return inspect.signature(method)


def _parameter_names(method) -> list[str]:
    """返回方法签名中的参数名列表。"""

    return list(_signature(method).parameters)


def test_workflow_selection_fields_and_types() -> None:
    """WorkflowSelection 必须表达命中定义、显式标记和原因。"""
    hints = get_type_hints(WorkflowSelection)

    assert hints["workflow"] == WorkflowDefinition | None
    assert hints["explicit"] is bool
    assert hints["reason"] is str
    assert WorkflowSelection(workflow=None, explicit=False, reason="no_match").reason


def test_workflow_registry_port_signatures() -> None:
    """WorkflowRegistryPort 方法签名必须匹配设计。"""
    assert _parameter_names(WorkflowRegistryPort.list_definitions) == ["self"]
    assert _parameter_names(WorkflowRegistryPort.get_definition) == ["self", "name"]
    assert _parameter_names(WorkflowRegistryPort.require_definition) == ["self", "name"]

    hints = get_type_hints(WorkflowRegistryPort.list_definitions)
    assert hints["return"] == list[WorkflowDefinition]
    assert get_type_hints(WorkflowRegistryPort.get_definition)["return"] == (
        WorkflowDefinition | None
    )
    assert get_type_hints(WorkflowRegistryPort.require_definition)["return"] is (WorkflowDefinition)


def test_workflow_selector_port_signature() -> None:
    """WorkflowSelectorPort.select 必须接收 RunCreateRequest 并返回选择结果。"""
    assert _parameter_names(WorkflowSelectorPort.select) == ["self", "request"]
    assert get_type_hints(WorkflowSelectorPort.select)["return"] is WorkflowSelection


def test_run_store_workflow_optional_parameters_have_defaults() -> None:
    """RunStorePort 写入方法新增 workflow 参数必须保持默认 None。"""
    methods = (
        RunStorePort.mark_succeeded,
        RunStorePort.mark_failed,
        RunStorePort.mark_paused,
        RunStorePort.mark_awaiting_approval,
        RunStorePort.mark_cancelled,
        RunStorePort.resolve_approval_resume,
        RunStorePort.enqueue_recovery,
    )

    for method in methods:
        params = _signature(method).parameters
        for name in ("workflow_run_state", "collaboration_summary"):
            assert name in params
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is None
