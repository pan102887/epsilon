"""Run checkpoint Port 静态签名测试模块。"""

from __future__ import annotations

import inspect
from collections.abc import Callable

from domain.run.ports import RunCheckpointSinkPort, RunCheckpointStorePort


def _parameter_names(method: Callable[..., object]) -> list[str]:
    """返回方法签名中的参数名列表。"""
    return list(inspect.signature(method).parameters)


def _return_annotation(method: Callable[..., object]) -> object:
    """返回方法签名中的返回类型标注。"""
    return inspect.signature(method).return_annotation


def test_run_checkpoint_store_port_method_names() -> None:
    """RunCheckpointStorePort 必须暴露设计要求的全部方法。"""
    expected = {
        "save_checkpoint",
        "latest_checkpoint",
        "list_checkpoints",
        "put_tool_pending",
        "complete_tool_result",
        "get_tool_result",
        "list_tool_ledger",
        "trim_checkpoints",
    }

    for method_name in expected:
        assert method_name in RunCheckpointStorePort.__dict__


def test_run_checkpoint_store_port_signatures() -> None:
    """RunCheckpointStorePort 关键方法参数必须与设计一致。"""
    assert _parameter_names(RunCheckpointStorePort.save_checkpoint) == [
        "self",
        "checkpoint",
    ]
    assert _parameter_names(RunCheckpointStorePort.latest_checkpoint) == [
        "self",
        "run_id",
    ]
    assert _parameter_names(RunCheckpointStorePort.list_checkpoints) == [
        "self",
        "run_id",
        "after_sequence",
        "limit",
    ]
    assert _parameter_names(RunCheckpointStorePort.put_tool_pending) == [
        "self",
        "entry",
    ]
    assert _parameter_names(RunCheckpointStorePort.complete_tool_result) == [
        "self",
        "run_id",
        "tool_execution_key",
        "result",
        "is_error",
        "metadata",
    ]
    assert _parameter_names(RunCheckpointStorePort.get_tool_result) == [
        "self",
        "run_id",
        "tool_execution_key",
    ]
    assert _parameter_names(RunCheckpointStorePort.list_tool_ledger) == [
        "self",
        "run_id",
    ]
    assert _parameter_names(RunCheckpointStorePort.trim_checkpoints) == [
        "self",
        "run_id",
        "policy",
    ]


def test_run_checkpoint_store_port_return_annotations() -> None:
    """RunCheckpointStorePort 返回类型必须保留领域值对象边界。"""
    assert _return_annotation(RunCheckpointStorePort.save_checkpoint) == "DurableCheckpoint"
    assert (
        _return_annotation(RunCheckpointStorePort.latest_checkpoint) == "DurableCheckpoint | None"
    )
    assert _return_annotation(RunCheckpointStorePort.list_checkpoints) == "list[DurableCheckpoint]"
    assert _return_annotation(RunCheckpointStorePort.put_tool_pending) == "ToolResultLedgerEntry"
    assert (
        _return_annotation(RunCheckpointStorePort.complete_tool_result) == "ToolResultLedgerEntry"
    )
    assert (
        _return_annotation(RunCheckpointStorePort.get_tool_result) == "ToolResultLedgerEntry | None"
    )
    assert (
        _return_annotation(RunCheckpointStorePort.list_tool_ledger) == "list[ToolResultLedgerEntry]"
    )
    assert _return_annotation(RunCheckpointStorePort.trim_checkpoints) == "None"


def test_run_checkpoint_sink_port_method_names() -> None:
    """RunCheckpointSinkPort 必须暴露 Agent 执行边界回调。"""
    expected = {
        "model_completed",
        "before_tool_call",
        "after_tool_call",
        "approval_interrupt",
        "segment_done",
    }

    for method_name in expected:
        assert method_name in RunCheckpointSinkPort.__dict__


def test_run_checkpoint_sink_port_signatures() -> None:
    """RunCheckpointSinkPort 方法参数必须与设计一致。"""
    assert _parameter_names(RunCheckpointSinkPort.model_completed) == [
        "self",
        "context",
        "round_num",
        "usage",
        "trace_summary",
        "segment_metadata",
    ]
    assert _parameter_names(RunCheckpointSinkPort.before_tool_call) == [
        "self",
        "tool_call",
        "round_num",
        "segment_index",
        "replay_policy",
        "side_effect_level",
        "idempotency_key",
    ]
    assert _parameter_names(RunCheckpointSinkPort.after_tool_call) == [
        "self",
        "context",
        "tool_execution_key",
        "result",
        "is_error",
        "metadata",
        "round_num",
        "usage",
    ]
    assert _parameter_names(RunCheckpointSinkPort.approval_interrupt) == [
        "self",
        "context",
        "round_num",
        "usage",
        "approval_id",
    ]
    assert _parameter_names(RunCheckpointSinkPort.segment_done) == [
        "self",
        "context",
        "segment_metadata",
        "usage",
    ]


def test_run_checkpoint_sink_port_return_annotations() -> None:
    """RunCheckpointSinkPort 返回类型必须表达 checkpoint 或 replay 结果。"""
    assert _return_annotation(RunCheckpointSinkPort.model_completed) == "DurableCheckpoint"
    assert (
        _return_annotation(RunCheckpointSinkPort.before_tool_call) == "ToolResultLedgerEntry | None"
    )
    assert _return_annotation(RunCheckpointSinkPort.after_tool_call) == "DurableCheckpoint"
    assert _return_annotation(RunCheckpointSinkPort.approval_interrupt) == "DurableCheckpoint"
    assert _return_annotation(RunCheckpointSinkPort.segment_done) == "DurableCheckpoint"
