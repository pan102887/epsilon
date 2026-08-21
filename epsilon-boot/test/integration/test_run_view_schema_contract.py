"""Run 视图快照 schema 契约测试。

本模块只验证 HTTP Run adapter 暴露给 CLI/TUI/Web 的快照字段形状，
不启动数据库、缓存或后台 worker。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from application.api.routers.runs import RunSnapshotBody, snapshot_body
from domain.run.value_objects import RunKind, RunPayload, RunSnapshot, RunStatus

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _snapshot(
    *,
    collaboration_summary: dict[str, Any] | None,
    guardrail_summary: dict[str, Any] | None,
    workflow_run_state: dict[str, Any] | None,
) -> RunSnapshot:
    """构造用于 Run 视图 schema 契约断言的快照。"""

    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="schema-session",
        task={"goal": "verify run view schema"},
        model="test-model",
    )
    return RunSnapshot(
        run_id="run-schema-1",
        kind=RunKind.TASK,
        status=RunStatus.RUNNING,
        payload=payload,
        client_request_id="client-schema-1",
        payload_hash=payload.stable_hash(),
        result=None,
        error=None,
        approval_id=None,
        segment_metadata={"segment_index": 1},
        latest_event_cursor=12,
        can_continue=False,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        guardrail_summary=guardrail_summary,
        workflow_name="code_change",
        workflow_run_state=workflow_run_state,
        collaboration_summary=collaboration_summary,
    )


def _schema_property(schema: dict[str, Any], property_name: str) -> dict[str, Any]:
    """解析 Pydantic JSON schema 中可为空字段的真实属性 schema。"""

    field_schema = schema["properties"][property_name]
    for candidate in field_schema.get("anyOf", [field_schema]):
        reference = candidate.get("$ref")
        if isinstance(reference, str):
            definition_name = reference.rsplit("/", 1)[-1]
            return schema["$defs"][definition_name]
        if candidate.get("type") != "null":
            return candidate
    return field_schema


def test_run_snapshot_body_declares_run_view_summary_fields() -> None:
    """RunSnapshotBody 应稳定声明 Run 视图读取的摘要字段。"""

    fields = RunSnapshotBody.model_fields

    assert "collaboration_summary" in fields
    assert "guardrail_summary" in fields
    assert "workflow_run_state" in fields


def test_run_snapshot_body_static_schema_declares_canonical_collaboration_summary() -> None:
    """RunSnapshotBody 静态 schema 应声明 canonical 协作摘要字段。"""

    schema = RunSnapshotBody.model_json_schema()
    collaboration_schema = _schema_property(schema, "collaboration_summary")
    properties = collaboration_schema["properties"]

    assert "latest_steps" in properties
    assert properties["latest_steps"]["type"] == "array"
    assert "child_links" in properties
    assert "delegation_count" in properties
    assert "handoff_count" in properties
    assert "max_depth_seen" in properties
    assert "limit_hit_reason" in properties
    assert "recent_steps" not in properties


def test_snapshot_body_exposes_latest_steps_guardrail_and_workflow_state() -> None:
    """HTTP 快照 body 应透传 guardrail/workflow 并输出 canonical latest_steps。"""

    guardrail_summary = {
        "mode": "observe",
        "action": "observe",
        "reason": "tool_risk_gate_required",
        "runtime_stats": {"total_tokens": 128, "cost_available": False},
    }
    workflow_run_state = {
        "workflow_name": "code_change",
        "current_phase": "review",
        "phase_history": [{"phase": "execute", "status": "succeeded"}],
    }
    collaboration_summary = {
        "delegation_count": 1,
        "recent_steps": [
            {
                "action": "delegation",
                "target_agent": "reviewer",
                "result_summary": "legacy step mapped",
            }
        ],
    }

    body = snapshot_body(
        _snapshot(
            collaboration_summary=collaboration_summary,
            guardrail_summary=guardrail_summary,
            workflow_run_state=workflow_run_state,
        )
    ).model_dump(mode="json")

    assert body["guardrail_summary"] == guardrail_summary
    assert body["workflow_run_state"] == workflow_run_state
    assert body["collaboration_summary"]["latest_steps"] == [
        {
            "action": "delegation",
            "target_agent": "reviewer",
            "result_summary": "legacy step mapped",
        }
    ]
    assert "recent_steps" not in body["collaboration_summary"]


def test_snapshot_body_prefers_latest_steps_over_historical_recent_steps() -> None:
    """新旧协作步骤同时存在时，Run 视图契约必须以 latest_steps 为准。"""

    body = snapshot_body(
        _snapshot(
            collaboration_summary={
                "latest_steps": [
                    {
                        "action": "handoff",
                        "target_agent": "canonical-reviewer",
                        "result_summary": "latest step wins",
                    }
                ],
                "recent_steps": [
                    {
                        "action": "delegation",
                        "target_agent": "legacy-reviewer",
                        "result_summary": "legacy step ignored",
                    }
                ],
                "handoff_count": 1,
            },
            guardrail_summary={"action": "observe"},
            workflow_run_state={"current_phase": "review"},
        )
    ).model_dump(mode="json")

    assert body["collaboration_summary"]["latest_steps"] == [
        {
            "action": "handoff",
            "target_agent": "canonical-reviewer",
            "result_summary": "latest step wins",
        }
    ]
    assert body["collaboration_summary"]["handoff_count"] == 1
    assert "recent_steps" not in body["collaboration_summary"]
