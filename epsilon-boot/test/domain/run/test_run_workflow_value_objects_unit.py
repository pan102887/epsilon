"""Run workflow 字段与异常单元测试模块。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime

from common.exceptions import BizException
from domain.run.exceptions import (
    RunCollaborationLimitExceededError,
    RunUnknownWorkflowError,
    RunWorkflowDefinitionError,
)
from domain.run.value_objects import (
    RunCreateRequest,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)


def test_run_event_type_includes_workflow_events() -> None:
    """RunEventType 必须包含阶段六 workflow 与 collaboration 事件。"""
    assert RunEventType.WORKFLOW_SELECTED.value == "workflow_selected"
    assert RunEventType.WORKFLOW_SELECTION_SKIPPED.value == "workflow_selection_skipped"
    assert RunEventType.WORKFLOW_PHASE_STARTED.value == "workflow_phase_started"
    assert RunEventType.WORKFLOW_PHASE_COMPLETED.value == "workflow_phase_completed"
    assert RunEventType.WORKFLOW_PHASE_FAILED.value == "workflow_phase_failed"
    assert RunEventType.COLLABORATION_STEP_RECORDED.value == "collaboration_step_recorded"
    assert RunEventType.COLLABORATION_LIMIT_HIT.value == "collaboration_limit_hit"


def test_run_create_request_workflow_fields_default_to_none() -> None:
    """旧调用方不传 workflow 字段时应保持默认 None。"""
    payload = RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"})

    request = RunCreateRequest(payload=payload, client_request_id="client-1")

    assert request.workflow_name is None
    assert request.workflow_run_state is None
    assert request.collaboration_summary is None


def test_run_create_request_keeps_workflow_metadata_out_of_payload_hash() -> None:
    """workflow 元数据不得改变 RunPayload 的稳定摘要。"""
    payload = RunPayload(kind=RunKind.TASK, session_id="s1", task={"goal": "fix tests"})
    base = RunCreateRequest(payload=payload, client_request_id="client-1")
    with_workflow = replace(
        base,
        workflow_name="code_change",
        workflow_run_state={"current_phase": "plan"},
        collaboration_summary={"latest_steps": []},
    )

    assert base.effective_payload_hash() == with_workflow.effective_payload_hash()
    assert payload.stable_hash() == with_workflow.effective_payload_hash()


def test_run_snapshot_workflow_fields_default_to_none_for_legacy_snapshots() -> None:
    """旧 RunSnapshot 构造不传 workflow 字段时应兼容默认 None。"""
    now = datetime(2026, 6, 9, tzinfo=UTC)
    payload = RunPayload(kind=RunKind.CHAT, session_id="s1", chat={"message": "hi"})

    snapshot = RunSnapshot(
        run_id="run-1",
        kind=RunKind.CHAT,
        status=RunStatus.QUEUED,
        payload=payload,
        client_request_id="client-1",
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata=None,
        latest_event_cursor=None,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=now,
        updated_at=now,
        version=1,
    )

    data = asdict(snapshot)

    assert snapshot.workflow_name is None
    assert snapshot.workflow_run_state is None
    assert snapshot.collaboration_summary is None
    assert data["workflow_name"] is None


def test_run_workflow_exceptions_use_reserved_codes_and_biz_base() -> None:
    """阶段六 workflow 异常应使用 61017-61019 并继承 BizException。"""
    exceptions = [
        RunUnknownWorkflowError("missing_workflow"),
        RunWorkflowDefinitionError("缺少 finalize 阶段"),
        RunCollaborationLimitExceededError("run-1", "max_handoff_count"),
    ]

    assert [exc.code for exc in exceptions] == [61017, 61018, 61019]
    assert all(isinstance(exc, BizException) for exc in exceptions)
    assert "未知运行工作流" in exceptions[0].message
    assert "运行工作流定义无效" in exceptions[1].message
    assert "协作限制已命中" in exceptions[2].message


def test_run_workflow_exception_messages_hide_sensitive_payload() -> None:
    """workflow 异常消息不得泄露完整 payload、工具参数或敏感 token。"""
    sensitive = '{"messages":[{"role":"user","content":"secret prompt"}],"api_key":"k"}'
    exceptions = [
        RunUnknownWorkflowError(sensitive),
        RunWorkflowDefinitionError(sensitive),
        RunCollaborationLimitExceededError("run-1", sensitive),
    ]

    for exc in exceptions:
        assert "secret prompt" not in exc.message
        assert "api_key" not in exc.message
        assert "messages" not in exc.message
        assert "敏感详情已隐藏" in exc.message
