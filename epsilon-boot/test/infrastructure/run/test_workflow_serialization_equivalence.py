"""workflow 序列化映射器字面快照等价性测试。

对 8 个映射函数各写一条字面快照断言，锁定 ``frozenset`` 排序、``StrEnum``
``.value``、``datetime.isoformat()``、int 键 stringify 等边界。字面右值即
序列化契约主锁；领域旧 ``to_dict`` 已被本波移除，故不再交叉断言。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from domain.run.workflow import (
    ChildRunOrchestrationState,
    CollaborationAction,
    CollaborationStepTraceLink,
    CollaborationSummary,
    ParentChildRunLink,
    WorkflowCapabilityAction,
    WorkflowCapabilityDecision,
    WorkflowExecutionPolicy,
    WorkflowPhase,
    WorkflowPhaseRecord,
    WorkflowRunState,
)
from infrastructure.run.workflow_serialization import (
    child_run_orchestration_state_to_dict,
    collaboration_step_trace_link_to_dict,
    collaboration_summary_to_dict,
    parent_child_run_link_to_dict,
    workflow_capability_decision_to_dict,
    workflow_execution_policy_to_dict,
    workflow_phase_record_to_dict,
    workflow_run_state_to_dict,
)


def test_workflow_capability_decision_to_dict_snapshot() -> None:
    """能力判定结果：StrEnum action 应展开为 .value。"""

    obj = WorkflowCapabilityDecision(
        allowed=True,
        action=WorkflowCapabilityAction.DELEGATION,
        role="planner",
        target="worker",
        reason="allowed",
    )
    assert workflow_capability_decision_to_dict(obj) == {
        "allowed": True,
        "action": "delegation",
        "role": "planner",
        "target": "worker",
        "reason": "allowed",
    }


def test_workflow_execution_policy_to_dict_snapshot() -> None:
    """执行策略：frozenset 排序为 list，dict 映射原样保留。"""

    obj = WorkflowExecutionPolicy(
        role_capability_enabled=True,
        phase_handoff_required={"execute": "evaluate"},
        review_required_phases=frozenset({"evaluate", "plan"}),
        revise_target_phase={"evaluate": "execute"},
        child_run_enabled=False,
    )
    assert workflow_execution_policy_to_dict(obj) == {
        "role_capability_enabled": True,
        "phase_handoff_required": {"execute": "evaluate"},
        "review_required_phases": ["evaluate", "plan"],
        "revise_target_phase": {"evaluate": "execute"},
        "child_run_enabled": False,
    }


def test_workflow_phase_record_to_dict_snapshot() -> None:
    """阶段历史记录：datetime.isoformat()、可选字段与 int 键 stringify。"""

    obj = WorkflowPhaseRecord(
        phase=WorkflowPhase.EXECUTE,
        status="completed",
        started_at=datetime(2026, 7, 6, 12, 0, 0),
        completed_at=datetime(2026, 7, 6, 12, 5, 0),
        summary=cast(dict[str, Any], {1: "one", "note": "ok"}),
        error=None,
        revise_count=2,
    )
    assert workflow_phase_record_to_dict(obj) == {
        "phase": "execute",
        "status": "completed",
        "started_at": "2026-07-06T12:00:00",
        "completed_at": "2026-07-06T12:05:00",
        "summary": {"1": "one", "note": "ok"},
        "error": None,
        "revise_count": 2,
    }


def test_collaboration_step_trace_link_to_dict_snapshot() -> None:
    """协作步骤追踪关系：可选 phase 与 datetime 序列化。"""

    obj = CollaborationStepTraceLink(
        link_id="link-1",
        run_id="run-1",
        phase=WorkflowPhase.PLAN,
        source_role="planner",
        target_role="worker",
        target_agent="agent-x",
        action=CollaborationAction.DELEGATION,
        task_summary="do plan",
        result_summary=None,
        depth=1,
        created_at=datetime(2026, 7, 6, 9, 30, 0),
    )
    assert collaboration_step_trace_link_to_dict(obj) == {
        "link_id": "link-1",
        "run_id": "run-1",
        "phase": "plan",
        "source_role": "planner",
        "target_role": "worker",
        "target_agent": "agent-x",
        "action": "delegation",
        "task_summary": "do plan",
        "result_summary": None,
        "depth": 1,
        "created_at": "2026-07-06T09:30:00",
    }


def test_parent_child_run_link_to_dict_snapshot() -> None:
    """父子 Run 关系：phase 展开与 datetime 序列化。"""

    obj = ParentChildRunLink(
        parent_run_id="p-1",
        child_run_id="c-1",
        role="worker",
        phase=WorkflowPhase.FINALIZE,
        reason="spawn",
        created_at=datetime(2026, 7, 6, 8, 0, 0),
    )
    assert parent_child_run_link_to_dict(obj) == {
        "parent_run_id": "p-1",
        "child_run_id": "c-1",
        "role": "worker",
        "phase": "finalize",
        "reason": "spawn",
        "created_at": "2026-07-06T08:00:00",
    }


def test_child_run_orchestration_state_to_dict_snapshot() -> None:
    """child run 编排状态：可选 role 与 datetime 序列化。"""

    obj = ChildRunOrchestrationState(
        parent_run_id="p-1",
        child_run_id="c-1",
        phase=WorkflowPhase.EVALUATE,
        role=None,
        ownership_status="owned",
        reconciliation_status="pending",
        reason="await",
        updated_at=datetime(2026, 7, 6, 10, 15, 30),
    )
    assert child_run_orchestration_state_to_dict(obj) == {
        "parent_run_id": "p-1",
        "child_run_id": "c-1",
        "phase": "evaluate",
        "role": None,
        "ownership_status": "owned",
        "reconciliation_status": "pending",
        "reason": "await",
        "updated_at": "2026-07-06T10:15:30",
    }


def test_collaboration_summary_to_dict_snapshot() -> None:
    """协作摘要：嵌套 tuple[dataclass] 递归展开为 list[dict]。"""

    step = CollaborationStepTraceLink(
        link_id="link-1",
        run_id="run-1",
        phase=WorkflowPhase.PLAN,
        source_role="planner",
        target_role="worker",
        target_agent="agent-x",
        action=CollaborationAction.HANDOFF,
        task_summary="handoff",
        result_summary="done",
        depth=0,
        created_at=datetime(2026, 7, 6, 9, 0, 0),
    )
    child = ParentChildRunLink(
        parent_run_id="p-1",
        child_run_id="c-1",
        role="worker",
        phase=WorkflowPhase.EXECUTE,
        reason="spawn",
        created_at=datetime(2026, 7, 6, 9, 5, 0),
    )
    obj = CollaborationSummary(
        latest_steps=(step,),
        child_links=(child,),
        delegation_count=2,
        handoff_count=1,
        max_depth_seen=3,
        limit_hit_reason=None,
    )
    assert collaboration_summary_to_dict(obj) == {
        "latest_steps": [
            {
                "link_id": "link-1",
                "run_id": "run-1",
                "phase": "plan",
                "source_role": "planner",
                "target_role": "worker",
                "target_agent": "agent-x",
                "action": "handoff",
                "task_summary": "handoff",
                "result_summary": "done",
                "depth": 0,
                "created_at": "2026-07-06T09:00:00",
            }
        ],
        "child_links": [
            {
                "parent_run_id": "p-1",
                "child_run_id": "c-1",
                "role": "worker",
                "phase": "execute",
                "reason": "spawn",
                "created_at": "2026-07-06T09:05:00",
            }
        ],
        "delegation_count": 2,
        "handoff_count": 1,
        "max_depth_seen": 3,
        "limit_hit_reason": None,
    }


def test_workflow_run_state_to_dict_snapshot() -> None:
    """工作流运行状态：可选 datetime、嵌套 tuple 记录与 revise_counts 映射。"""

    record = WorkflowPhaseRecord(
        phase=WorkflowPhase.PLAN,
        status="completed",
        started_at=datetime(2026, 7, 6, 7, 0, 0),
        completed_at=None,
        summary={},
        error=None,
        revise_count=0,
    )
    obj = WorkflowRunState(
        workflow_name="research",
        current_phase=WorkflowPhase.EXECUTE,
        phase_started_at=datetime(2026, 7, 6, 7, 10, 0),
        phase_history=(record,),
        phase_result_summary={"ok": True},
        phase_error_summary=None,
        revise_counts={"execute": 1},
        active_role="worker",
        handoff_state=None,
    )
    assert workflow_run_state_to_dict(obj) == {
        "workflow_name": "research",
        "current_phase": "execute",
        "phase_started_at": "2026-07-06T07:10:00",
        "phase_history": [
            {
                "phase": "plan",
                "status": "completed",
                "started_at": "2026-07-06T07:00:00",
                "completed_at": None,
                "summary": {},
                "error": None,
                "revise_count": 0,
            }
        ],
        "phase_result_summary": {"ok": True},
        "phase_error_summary": None,
        "revise_counts": {"execute": 1},
        "active_role": "worker",
        "handoff_state": None,
    }
