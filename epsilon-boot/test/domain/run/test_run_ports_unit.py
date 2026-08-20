"""Run 领域 Port 静态签名测试模块。"""

from __future__ import annotations

import inspect
from dataclasses import fields
from typing import get_args, get_type_hints

from domain.run.ports import (
    ApprovalResumeStoreResult,
    RunEventStorePort,
    RunObservationStorePort,
    RunProgressSink,
    RunStorePort,
)
from domain.task.value_objects import TaskApprovalResumeRequest, TaskResult


def _parameter_names(method) -> list[str]:
    """返回方法签名中的参数名列表。"""
    return list(inspect.signature(method).parameters)


def test_run_store_port_method_names() -> None:
    """RunStorePort 必须暴露设计要求的全部方法。"""
    expected = {
        "create_run",
        "get_run",
        "get_by_client_request_id",
        "count_by_status",
        "claim_next",
        "refresh_lease",
        "acquire_approval_resume_lease",
        "release_approval_resume_lease",
        "request_cancel",
        "mark_succeeded",
        "mark_failed",
        "mark_paused",
        "mark_awaiting_approval",
        "mark_cancelled",
        "resolve_approval_resume",
        "enqueue_continue",
        "list_expired_leased_runs",
        "enqueue_recovery",
        "mark_lost_expired_run",
        "mark_lost_expired_leases",
    }

    for method_name in expected:
        assert method_name in RunStorePort.__dict__


def test_run_store_port_key_signatures() -> None:
    """RunStorePort 关键方法参数必须与设计一致。"""
    assert _parameter_names(RunStorePort.create_run) == ["self", "request"]
    assert _parameter_names(RunStorePort.get_run) == ["self", "run_id"]
    assert _parameter_names(RunStorePort.get_by_client_request_id) == [
        "self",
        "client_request_id",
    ]
    assert _parameter_names(RunStorePort.count_by_status) == ["self", "statuses"]
    assert _parameter_names(RunStorePort.claim_next) == [
        "self",
        "owner_id",
        "lease_seconds",
    ]
    assert _parameter_names(RunStorePort.refresh_lease) == [
        "self",
        "run_id",
        "owner_id",
        "lease_seconds",
    ]
    assert _parameter_names(RunStorePort.acquire_approval_resume_lease) == [
        "self",
        "run_id",
        "owner_id",
        "lease_seconds",
    ]
    assert _parameter_names(RunStorePort.release_approval_resume_lease) == [
        "self",
        "run_id",
        "owner_id",
    ]
    assert _parameter_names(RunStorePort.request_cancel) == ["self", "run_id"]
    assert _parameter_names(RunStorePort.enqueue_continue) == [
        "self",
        "run_id",
        "model",
    ]
    assert _parameter_names(RunStorePort.resolve_approval_resume) == [
        "self",
        "run_id",
        "owner_id",
        "result",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert _parameter_names(RunStorePort.mark_lost_expired_leases) == ["self", "now"]
    assert _parameter_names(RunStorePort.list_expired_leased_runs) == ["self", "now"]
    assert _parameter_names(RunStorePort.enqueue_recovery) == [
        "self",
        "run_id",
        "latest_checkpoint_id",
        "recovery_attempt_count",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert _parameter_names(RunStorePort.mark_lost_expired_run) == [
        "self",
        "run_id",
        "reason",
        "recovery_error",
    ]


def test_run_store_port_mark_methods_signatures() -> None:
    """RunStorePort 标记状态方法参数必须与设计一致。"""
    assert _parameter_names(RunStorePort.mark_succeeded) == [
        "self",
        "run_id",
        "owner_id",
        "result",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert _parameter_names(RunStorePort.mark_failed) == [
        "self",
        "run_id",
        "owner_id",
        "error",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert _parameter_names(RunStorePort.mark_paused) == [
        "self",
        "run_id",
        "owner_id",
        "result",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert _parameter_names(RunStorePort.mark_awaiting_approval) == [
        "self",
        "run_id",
        "owner_id",
        "approval_id",
        "result",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert _parameter_names(RunStorePort.mark_cancelled) == [
        "self",
        "run_id",
        "owner_id",
        "reason",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]


def test_run_observation_store_port_signatures() -> None:
    """RunObservationStorePort 方法参数必须与设计一致。"""
    assert _parameter_names(RunObservationStorePort.record_runtime_observation) == [
        "self",
        "run_id",
        "owner_id",
        "event_type",
        "payload",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]

    assert _parameter_names(RunEventStorePort.append_event) == [
        "self",
        "run_id",
        "event_type",
        "payload",
    ]
    assert _parameter_names(RunEventStorePort.list_events) == [
        "self",
        "run_id",
        "after_cursor",
        "limit",
    ]
    assert _parameter_names(RunEventStorePort.wait_events) == [
        "self",
        "run_id",
        "after_cursor",
        "timeout_seconds",
    ]
    assert _parameter_names(RunEventStorePort.trim_events) == [
        "self",
        "run_id",
        "policy",
    ]
    assert _parameter_names(RunEventStorePort.first_cursor) == ["self", "run_id"]


def test_run_progress_sink_signatures() -> None:
    """RunProgressSink 进度回调签名必须与设计一致。"""
    assert _parameter_names(RunProgressSink.segment_started) == [
        "self",
        "run_id",
        "segment_index",
    ]
    assert _parameter_names(RunProgressSink.segment_done) == [
        "self",
        "run_id",
        "metadata",
    ]


def test_approval_resume_store_result_fields() -> None:
    """ApprovalResumeStoreResult 必须表达审批恢复的五类目标状态。"""

    result = ApprovalResumeStoreResult(
        status="awaiting_approval",
        approval_id="approval-2",
        result={"summary": "awaiting"},
        error={"message": "ignored"},
        terminal_reason="awaiting_approval",
        guardrail_summary={"action": "require_approval"},
        workflow_run_state={"phase": "review"},
        collaboration_summary={"latest_steps": []},
    )

    assert result.status == "awaiting_approval"
    assert result.approval_id == "approval-2"
    assert result.result == {"summary": "awaiting"}
    assert result.error == {"message": "ignored"}
    assert result.terminal_reason == "awaiting_approval"
    assert result.guardrail_summary == {"action": "require_approval"}
    assert result.workflow_run_state == {"phase": "review"}
    assert result.collaboration_summary == {"latest_steps": []}


def test_approval_resume_store_result_static_contract() -> None:
    """ApprovalResumeStoreResult 字段名与状态字面量必须稳定。"""

    field_names = [item.name for item in fields(ApprovalResumeStoreResult)]
    status_literal = get_args(get_type_hints(ApprovalResumeStoreResult)["status"])

    assert field_names == [
        "status",
        "approval_id",
        "result",
        "error",
        "terminal_reason",
        "guardrail_summary",
        "workflow_run_state",
        "collaboration_summary",
    ]
    assert status_literal == (
        "queued",
        "awaiting_approval",
        "succeeded",
        "failed",
        "cancelled",
    )


def test_task_approval_resume_request_static_contract() -> None:
    """TaskApprovalResumeRequest 必须暴露任务审批恢复所需字段。"""

    field_names = [item.name for item in fields(TaskApprovalResumeRequest)]

    assert field_names == ["session_id", "approval_id", "decisions", "model"]


def test_task_result_approval_id_field_is_optional_and_defaults_to_none() -> None:
    """TaskResult.approval_id 必须保持可选字段并默认不占位。"""

    field_map = {item.name: item for item in fields(TaskResult)}
    approval_id_type = get_type_hints(TaskResult)["approval_id"]

    assert "approval_id" in field_map
    assert get_args(approval_id_type) == (str, type(None))
    assert field_map["approval_id"].default is None
