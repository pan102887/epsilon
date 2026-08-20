"""Run 工作流领域模型单元测试模块。"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from domain.run.value_objects import RunEventType
from domain.run.workflow import (
    AgentRoleCapability,
    CollaborationAction,
    CollaborationLimit,
    CollaborationStepTraceLink,
    CollaborationSummary,
    ParentChildRunLink,
    StandardWorkflowName,
    WorkflowApplicableCondition,
    WorkflowDefinition,
    WorkflowExecutionPolicy,
    WorkflowPhase,
    WorkflowPhaseDefinition,
    WorkflowPhaseRecord,
    WorkflowRunState,
)
from infrastructure.run.workflow_serialization import (
    collaboration_summary_to_dict,
    workflow_execution_policy_to_dict,
    workflow_run_state_to_dict,
)


def _valid_definition(
    *,
    name: str = "code_change",
    phases: tuple[WorkflowPhaseDefinition, ...] | None = None,
    roles: tuple[AgentRoleCapability, ...] | None = None,
    limit: CollaborationLimit | None = None,
) -> WorkflowDefinition:
    """构造默认合法工作流定义。"""

    effective_roles = roles or (
        AgentRoleCapability("planner"),
        AgentRoleCapability("executor", agent_names=("code_agent",), can_delegate=True),
        AgentRoleCapability("reviewer", can_handoff=True),
    )
    return WorkflowDefinition(
        name=name,
        description="代码修改工作流",
        applicable=WorkflowApplicableCondition(
            run_kinds=frozenset({"task"}),
            task_classes=frozenset({"long_task"}),
            payload_keywords=frozenset({"code"}),
        ),
        phases=phases
        or (
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.REVISE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
        ),
        roles=effective_roles,
        collaboration_limit=limit or CollaborationLimit(),
        default_strategy_summary="先规划，再执行、评估和收尾。",
    )


def test_standard_workflow_names_are_phase6_builtins() -> None:
    """阶段六 v1 必须保留四类标准工作流名称。"""
    assert {item.value for item in StandardWorkflowName} == {
        "research",
        "code_change",
        "report",
        "batch_processing",
    }


def test_workflow_definition_validate_accepts_complete_definition() -> None:
    """完整定义应通过校验并保留 JSON-safe 名称。"""
    definition = _valid_definition()

    definition.validate()

    assert definition.name == StandardWorkflowName.CODE_CHANGE.value


def test_agent_role_capability_defaults_to_minimum_permissions() -> None:
    """未声明的 role capability 必须默认拒绝工具、委派、交接和 child run。"""

    capability = AgentRoleCapability("executor")

    assert capability.allowed_tool_names == frozenset()
    assert capability.can_delegate is False
    assert capability.allowed_delegate_agents == frozenset()
    assert capability.can_handoff is False
    assert capability.allowed_handoff_agents == frozenset()
    assert capability.can_create_child_run is False


def test_workflow_execution_policy_defaults_to_compatible_disabled_state() -> None:
    """执行策略默认关闭 capability 与 child run，保持旧 workflow 兼容语义。"""

    policy = WorkflowExecutionPolicy()

    assert policy.role_capability_enabled is False
    assert policy.child_run_enabled is False
    assert workflow_execution_policy_to_dict(policy) == {
        "role_capability_enabled": False,
        "phase_handoff_required": {},
        "review_required_phases": [],
        "revise_target_phase": {},
        "child_run_enabled": False,
    }


def test_child_run_event_types_are_declared() -> None:
    """child run 编排事件类型应可被事件流稳定引用。"""

    assert RunEventType.CHILD_RUN_LINKED.value == "child_run_linked"
    assert RunEventType.CHILD_RUN_WAITING.value == "child_run_waiting"
    assert RunEventType.CHILD_RUN_RECONCILED.value == "child_run_reconciled"


def test_workflow_definition_validates_role_capability_fields() -> None:
    """角色能力集合必须可稳定序列化，非法代理名应 fail-fast。"""

    definition = _valid_definition(
        roles=(
            AgentRoleCapability("planner"),
            AgentRoleCapability(
                "executor",
                allowed_tool_names=frozenset({"shell.exec"}),
                can_delegate=True,
                allowed_delegate_agents=frozenset({"code_agent"}),
                can_handoff=True,
                allowed_handoff_agents=frozenset({"review_agent"}),
                can_create_child_run=True,
            ),
            AgentRoleCapability("reviewer"),
        )
    )

    definition.validate()
    executor = definition.roles[1]
    data = executor.__dict__
    assert data["allowed_tool_names"] == frozenset({"shell.exec"})
    assert data["allowed_delegate_agents"] == frozenset({"code_agent"})
    assert data["allowed_handoff_agents"] == frozenset({"review_agent"})
    assert data["can_create_child_run"] is True

    with pytest.raises(ValueError, match="allowed_delegate_agents"):
        _valid_definition(
            roles=(
                AgentRoleCapability("planner"),
                AgentRoleCapability(
                    "executor",
                    can_delegate=True,
                    allowed_delegate_agents=frozenset({"CodeAgent"}),
                ),
                AgentRoleCapability("reviewer"),
            )
        ).validate()


def test_workflow_execution_policy_validation_and_serialization() -> None:
    """workflow 执行策略应序列化 handoff/review/revise 约束。"""

    policy = WorkflowExecutionPolicy(
        role_capability_enabled=True,
        phase_handoff_required={"execute": "reviewer"},
        review_required_phases=frozenset({"evaluate"}),
        revise_target_phase={"evaluate": "revise"},
        child_run_enabled=True,
    )

    policy.validate()

    assert workflow_execution_policy_to_dict(policy) == {
        "role_capability_enabled": True,
        "phase_handoff_required": {"execute": "reviewer"},
        "review_required_phases": ["evaluate"],
        "revise_target_phase": {"evaluate": "revise"},
        "child_run_enabled": True,
    }
    with pytest.raises(ValueError, match="phase_handoff_required"):
        WorkflowExecutionPolicy(phase_handoff_required={"Execute": "reviewer"}).validate()


def test_workflow_definition_carries_execution_policy() -> None:
    """WorkflowDefinition 应持有执行策略供应用层 orchestrator 后续强制。"""

    policy = WorkflowExecutionPolicy(role_capability_enabled=True)
    definition = _valid_definition()
    definition = WorkflowDefinition(
        name=definition.name,
        description=definition.description,
        applicable=definition.applicable,
        phases=definition.phases,
        roles=definition.roles,
        collaboration_limit=definition.collaboration_limit,
        default_strategy_summary=definition.default_strategy_summary,
        execution_policy=policy,
    )

    definition.validate()

    assert definition.execution_policy is policy


def test_workflow_definition_requires_mandatory_phases() -> None:
    """缺少 plan/execute/evaluate/finalize 任一必需阶段都应失败。"""
    definition = _valid_definition(
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
        )
    )

    with pytest.raises(ValueError, match="finalize"):
        definition.validate()


def test_workflow_definition_rejects_invalid_names_and_duplicate_roles() -> None:
    """workflow、role 与 agent 名称必须稳定且不能重复。"""
    with pytest.raises(ValueError, match="snake_case"):
        _valid_definition(name="CodeChange").validate()

    with pytest.raises(ValueError, match="重复角色"):
        _valid_definition(
            roles=(
                AgentRoleCapability("planner"),
                AgentRoleCapability("planner"),
                AgentRoleCapability("executor"),
                AgentRoleCapability("reviewer"),
            )
        ).validate()

    with pytest.raises(ValueError, match="snake_case"):
        _valid_definition(
            roles=(
                AgentRoleCapability("planner", agent_names=("CodeAgent",)),
                AgentRoleCapability("executor"),
                AgentRoleCapability("reviewer"),
            )
        ).validate()


def test_workflow_definition_rejects_unknown_phase_role() -> None:
    """阶段引用未声明 role 时必须 fail-fast。"""
    definition = _valid_definition(
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="missing_role"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
        )
    )

    with pytest.raises(ValueError, match="未知 role"):
        definition.validate()


def test_workflow_definition_rejects_invalid_collaboration_limit() -> None:
    """协作限制必须符合非负或正整数边界。"""
    with pytest.raises(ValueError, match="max_recursion_depth"):
        _valid_definition(limit=CollaborationLimit(max_recursion_depth=-1)).validate()

    with pytest.raises(ValueError, match="max_parallel_delegations"):
        _valid_definition(limit=CollaborationLimit(max_parallel_delegations=0)).validate()

    with pytest.raises(ValueError, match="max_handoff_count"):
        _valid_definition(limit=CollaborationLimit(max_handoff_count=-1)).validate()


def test_workflow_run_state_to_dict_is_json_safe() -> None:
    """WorkflowRunState 序列化应输出 enum value、ISO 时间和 array。"""
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    state = WorkflowRunState(
        workflow_name="code_change",
        current_phase=WorkflowPhase.EXECUTE,
        phase_started_at=now,
        phase_history=(
            WorkflowPhaseRecord(
                phase=WorkflowPhase.PLAN,
                status="completed",
                started_at=now,
                completed_at=now,
                summary={"terminal_reason": "workflow_phase_completed"},
            ),
        ),
        phase_result_summary={"latest_terminal_reason": "workflow_phase_completed"},
        revise_counts={"revise": 0},
        active_role="executor",
        handoff_state={"status": "pending", "target_role": "reviewer"},
    )

    data = workflow_run_state_to_dict(state)

    assert data["current_phase"] == "execute"
    assert data["phase_started_at"] == now.isoformat()
    assert data["phase_history"][0]["phase"] == "plan"
    assert data["phase_history"][0]["completed_at"] == now.isoformat()
    assert data["revise_counts"] == {"revise": 0}
    assert data["active_role"] == "executor"
    assert data["handoff_state"] == {"status": "pending", "target_role": "reviewer"}


def test_collaboration_summary_to_dict_is_json_safe() -> None:
    """CollaborationSummary 应稳定序列化步骤和父子 Run 关系。"""
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    summary = CollaborationSummary(
        latest_steps=(
            CollaborationStepTraceLink(
                link_id="step-1",
                run_id="run-1",
                phase=WorkflowPhase.EXECUTE,
                source_role="executor",
                target_role="reviewer",
                target_agent="reviewer",
                action=CollaborationAction.DELEGATION,
                task_summary="review change",
                result_summary="ok",
                depth=1,
                created_at=now,
            ),
        ),
        child_links=(
            ParentChildRunLink(
                parent_run_id="run-1",
                child_run_id="run-2",
                role="worker",
                phase=WorkflowPhase.EXECUTE,
                reason="batch item",
                created_at=now,
            ),
        ),
        delegation_count=1,
        handoff_count=0,
        max_depth_seen=1,
    )

    data = collaboration_summary_to_dict(summary)

    assert data["latest_steps"][0]["action"] == "delegation"
    assert data["latest_steps"][0]["phase"] == "execute"
    assert data["latest_steps"][0]["created_at"] == now.isoformat()
    assert data["child_links"][0]["phase"] == "execute"
    assert data["delegation_count"] == 1
    assert "recent_steps" not in data


def test_workflow_module_keeps_domain_import_boundary() -> None:
    """workflow.py 不得导入 application、infrastructure、Web 或外部 runtime。"""
    source = Path("src/domain/run/workflow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_fragments = (
        "application.",
        "infrastructure.",
        "fastapi",
        "redis",
        "temporal",
        "langgraph",
        "dapr",
        "celery",
    )
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module.lower())

    for fragment in forbidden_fragments:
        assert all(fragment not in module for module in imported_modules)
