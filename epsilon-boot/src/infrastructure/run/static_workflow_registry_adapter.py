"""静态 Run 工作流注册表适配器。"""

from __future__ import annotations

from dataclasses import replace

from domain.run.exceptions import RunUnknownWorkflowError, RunWorkflowDefinitionError
from domain.run.ports import WorkflowRegistryPort
from domain.run.workflow import (
    AgentRoleCapability,
    StandardWorkflowName,
    WorkflowApplicableCondition,
    WorkflowDefinition,
    WorkflowPhase,
    WorkflowPhaseDefinition,
)
from infrastructure.run.workflow_config import RunWorkflowConfig


class StaticWorkflowRegistryAdapter(WorkflowRegistryPort):
    """从静态配置和内置定义创建工作流注册表。"""

    def __init__(
        self,
        config: RunWorkflowConfig,
        definitions: tuple[WorkflowDefinition, ...] | None = None,
    ) -> None:
        """初始化静态工作流注册表并执行 fail-fast 校验。"""

        self._config = config
        configured_names = frozenset(config.enabled_workflow_names())
        raw_definitions = definitions or _builtin_definitions(config)
        known_names = frozenset(definition.name for definition in raw_definitions)
        unknown_configured = configured_names - known_names
        if unknown_configured:
            raise RunWorkflowDefinitionError(
                "RUN_WORKFLOW_ENABLED_WORKFLOWS 包含未知 workflow: "
                + ", ".join(sorted(unknown_configured))
            )

        definitions_by_name: dict[str, WorkflowDefinition] = {}
        for definition in raw_definitions:
            if definition.name in definitions_by_name:
                raise RunWorkflowDefinitionError(f"工作流名称重复: {definition.name}")
            try:
                definition.validate()
            except ValueError as exc:
                raise RunWorkflowDefinitionError(str(exc)) from exc
            enabled = config.enabled and definition.name in configured_names
            definitions_by_name[definition.name] = replace(definition, enabled=enabled)

        builtin_names = {item.value for item in StandardWorkflowName}
        missing_builtin = builtin_names - set(definitions_by_name)
        if missing_builtin:
            raise RunWorkflowDefinitionError(
                "缺少阶段六内置 workflow: " + ", ".join(sorted(missing_builtin))
            )

        self._definitions_by_name = definitions_by_name

    def list_definitions(self) -> list[WorkflowDefinition]:
        """返回所有启用或可诊断的工作流定义。"""

        return list(self._definitions_by_name.values())

    def get_definition(self, name: str) -> WorkflowDefinition | None:
        """按稳定名称查询工作流定义。"""

        return self._definitions_by_name.get(name)

    def require_definition(self, name: str) -> WorkflowDefinition:
        """按名称查询工作流定义，不存在时抛业务错误。"""

        definition = self.get_definition(name)
        if definition is None:
            raise RunUnknownWorkflowError(name)
        return definition


def _builtin_definitions(config: RunWorkflowConfig) -> tuple[WorkflowDefinition, ...]:
    """构造阶段六 v1 内置工作流定义。"""

    limit = config.to_collaboration_limit()
    execution_policy = config.to_execution_policy()
    return (
        WorkflowDefinition(
            name=StandardWorkflowName.RESEARCH.value,
            description="资料调研、搜索和信息整理工作流。",
            applicable=WorkflowApplicableCondition(
                run_kinds=frozenset({"chat", "task"}),
                task_classes=frozenset({"tool_task", "long_task"}),
                payload_keywords=frozenset({"research", "search", "调研", "搜索"}),
            ),
            phases=(
                WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
                WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="researcher"),
                WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
                WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="reporter"),
            ),
            roles=(
                AgentRoleCapability("planner", can_delegate=True),
                AgentRoleCapability("researcher", can_delegate=True),
                AgentRoleCapability("reviewer"),
                AgentRoleCapability("reporter"),
            ),
            collaboration_limit=limit,
            execution_policy=execution_policy,
            default_strategy_summary="先规划调研范围，再执行检索和整理，评估后收尾输出。",
        ),
        WorkflowDefinition(
            name=StandardWorkflowName.CODE_CHANGE.value,
            description="代码修改、测试修复和文件编辑工作流。",
            applicable=WorkflowApplicableCondition(
                run_kinds=frozenset({"task"}),
                task_classes=frozenset({"tool_task", "long_task"}),
                payload_keywords=frozenset({"code", "test", "fix", "代码", "测试", "修复"}),
            ),
            phases=(
                WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
                WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="executor"),
                WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
                WorkflowPhaseDefinition(WorkflowPhase.REVISE, role="executor"),
                WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="executor"),
            ),
            roles=(
                AgentRoleCapability("planner"),
                AgentRoleCapability("executor", can_delegate=True),
                AgentRoleCapability("reviewer", can_handoff=True),
            ),
            collaboration_limit=limit,
            execution_policy=execution_policy,
            default_strategy_summary="先规划变更，再执行、评估、必要时修正并收尾。",
        ),
        WorkflowDefinition(
            name=StandardWorkflowName.REPORT.value,
            description="报告、总结和文档生成工作流。",
            applicable=WorkflowApplicableCondition(
                run_kinds=frozenset({"chat", "task"}),
                task_classes=frozenset({"long_task"}),
                payload_keywords=frozenset({"report", "summary", "报告", "总结", "文档"}),
            ),
            phases=(
                WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
                WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="writer"),
                WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
                WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="writer"),
            ),
            roles=(
                AgentRoleCapability("planner"),
                AgentRoleCapability("writer", can_delegate=True),
                AgentRoleCapability("reviewer"),
            ),
            collaboration_limit=limit,
            execution_policy=execution_policy,
            default_strategy_summary="先规划报告结构，再撰写、评估和定稿。",
        ),
        WorkflowDefinition(
            name=StandardWorkflowName.BATCH_PROCESSING.value,
            description="批量处理、多文件或多素材任务工作流。",
            applicable=WorkflowApplicableCondition(
                run_kinds=frozenset({"task"}),
                task_classes=frozenset({"tool_task", "long_task"}),
                payload_keywords=frozenset({"batch", "bulk", "批量", "多文件", "多素材"}),
            ),
            phases=(
                WorkflowPhaseDefinition(WorkflowPhase.PLAN, role="planner"),
                WorkflowPhaseDefinition(WorkflowPhase.EXECUTE, role="worker"),
                WorkflowPhaseDefinition(WorkflowPhase.EVALUATE, role="reviewer"),
                WorkflowPhaseDefinition(WorkflowPhase.REVISE, role="worker"),
                WorkflowPhaseDefinition(WorkflowPhase.FINALIZE, role="worker"),
            ),
            roles=(
                AgentRoleCapability("planner", can_delegate=True),
                AgentRoleCapability("worker", can_delegate=True),
                AgentRoleCapability("reviewer"),
            ),
            collaboration_limit=limit,
            execution_policy=execution_policy,
            default_strategy_summary="先规划批次，再执行、评估、必要时修正并收尾。",
        ),
    )
