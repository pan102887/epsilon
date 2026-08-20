"""静态 Run workflow 注册表单元测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from domain.run.exceptions import RunUnknownWorkflowError, RunWorkflowDefinitionError
from domain.run.workflow import (
    AgentRoleCapability,
    StandardWorkflowName,
    WorkflowDefinition,
    WorkflowPhase,
    WorkflowPhaseDefinition,
)
from infrastructure.run.static_workflow_registry_adapter import (
    StaticWorkflowRegistryAdapter,
)
from infrastructure.run.workflow_config import RunWorkflowConfig


def _definitions() -> tuple[WorkflowDefinition, ...]:
    """返回默认内置定义副本。"""

    return tuple(StaticWorkflowRegistryAdapter(RunWorkflowConfig()).list_definitions())


def _replace_definition(
    definitions: tuple[WorkflowDefinition, ...],
    name: str,
    replacement: WorkflowDefinition,
) -> tuple[WorkflowDefinition, ...]:
    """按名称替换一条定义。"""

    return tuple(replacement if item.name == name else item for item in definitions)


def test_registry_contains_four_builtin_workflows() -> None:
    """注册表必须包含阶段六四类标准 workflow。"""
    registry = StaticWorkflowRegistryAdapter(RunWorkflowConfig())

    definitions = registry.list_definitions()

    assert [item.name for item in definitions] == [
        StandardWorkflowName.RESEARCH.value,
        StandardWorkflowName.CODE_CHANGE.value,
        StandardWorkflowName.REPORT.value,
        StandardWorkflowName.BATCH_PROCESSING.value,
    ]
    assert all(item.enabled for item in definitions)
    assert registry.get_definition("code_change") is not None
    assert registry.require_definition("code_change").name == "code_change"


def test_registry_applies_role_capability_config_to_builtin_policy() -> None:
    """内置 workflow 定义应携带配置转换出的执行策略开关。"""

    registry = StaticWorkflowRegistryAdapter(RunWorkflowConfig(role_capability_enabled=True))

    definitions = registry.list_definitions()

    assert all(item.execution_policy.role_capability_enabled is True for item in definitions)
    assert all(item.execution_policy.child_run_enabled is False for item in definitions)


def test_registry_applies_child_run_config_to_builtin_policy() -> None:
    """内置 workflow 定义应携带 child run 默认关闭/显式开启策略。"""

    registry = StaticWorkflowRegistryAdapter(RunWorkflowConfig(child_run_enabled=True))

    definitions = registry.list_definitions()

    assert all(item.execution_policy.child_run_enabled is True for item in definitions)


def test_registry_marks_disabled_workflows_as_diagnostic_definitions() -> None:
    """enabled_workflows 应控制 definition.enabled，但仍保留可诊断定义。"""
    registry = StaticWorkflowRegistryAdapter(RunWorkflowConfig(enabled_workflows="research,report"))

    definitions = {item.name: item for item in registry.list_definitions()}

    assert definitions["research"].enabled is True
    assert definitions["report"].enabled is True
    assert definitions["code_change"].enabled is False
    assert definitions["batch_processing"].enabled is False


def test_registry_global_disabled_marks_all_definitions_disabled() -> None:
    """RUN_WORKFLOW_ENABLED=false 时所有定义都应可诊断但不启用。"""
    registry = StaticWorkflowRegistryAdapter(RunWorkflowConfig(enabled=False))

    assert all(not item.enabled for item in registry.list_definitions())


def test_registry_rejects_unknown_enabled_workflow_name() -> None:
    """配置引用未知 workflow 时应启动期 fail-fast。"""
    with pytest.raises(RunWorkflowDefinitionError, match="unknown"):
        StaticWorkflowRegistryAdapter(RunWorkflowConfig(enabled_workflows="research,unknown"))


def test_registry_rejects_duplicate_definition_names() -> None:
    """定义集合出现重复名称时应 fail-fast。"""
    definitions = _definitions()

    with pytest.raises(RunWorkflowDefinitionError, match="重复"):
        StaticWorkflowRegistryAdapter(
            RunWorkflowConfig(),
            definitions=(*definitions, definitions[0]),
        )


def test_registry_rejects_missing_required_phase() -> None:
    """任一定义缺少必需 phase 时应 fail-fast。"""
    definitions = _definitions()
    code_change = next(item for item in definitions if item.name == "code_change")
    broken = replace(
        code_change,
        phases=tuple(
            phase for phase in code_change.phases if phase.phase is not WorkflowPhase.FINALIZE
        ),
    )

    with pytest.raises(RunWorkflowDefinitionError, match="finalize"):
        StaticWorkflowRegistryAdapter(
            RunWorkflowConfig(),
            definitions=_replace_definition(definitions, "code_change", broken),
        )


def test_registry_rejects_unknown_role_reference() -> None:
    """阶段引用未知 role 时应 fail-fast。"""
    definitions = _definitions()
    report = next(item for item in definitions if item.name == "report")
    broken = replace(
        report,
        phases=(
            WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
            WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="writer"),
            WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="missing_role"),
            WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="writer"),
        ),
    )

    with pytest.raises(RunWorkflowDefinitionError, match="未知 role"):
        StaticWorkflowRegistryAdapter(
            RunWorkflowConfig(),
            definitions=_replace_definition(definitions, "report", broken),
        )


def test_registry_rejects_invalid_definition_name() -> None:
    """非法 workflow 名称应 fail-fast。"""
    definitions = _definitions()
    research = next(item for item in definitions if item.name == "research")
    broken = replace(research, name="Research")

    with pytest.raises(RunWorkflowDefinitionError, match="snake_case"):
        StaticWorkflowRegistryAdapter(
            RunWorkflowConfig(enabled_workflows="code_change,report,batch_processing"),
            definitions=_replace_definition(definitions, "research", broken),
        )


def test_registry_rejects_invalid_role_capability_name() -> None:
    """非法 role 名称应 fail-fast。"""
    definitions = _definitions()
    batch = next(item for item in definitions if item.name == "batch_processing")
    broken = replace(
        batch,
        roles=(
            AgentRoleCapability("planner"),
            AgentRoleCapability("Worker"),
            AgentRoleCapability("reviewer"),
        ),
    )

    with pytest.raises(RunWorkflowDefinitionError, match="snake_case"):
        StaticWorkflowRegistryAdapter(
            RunWorkflowConfig(),
            definitions=_replace_definition(definitions, "batch_processing", broken),
        )


def test_registry_require_definition_raises_unknown_workflow() -> None:
    """require_definition 未命中时应抛业务错误。"""
    registry = StaticWorkflowRegistryAdapter(RunWorkflowConfig())

    with pytest.raises(RunUnknownWorkflowError):
        registry.require_definition("missing")
