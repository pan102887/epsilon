"""Run 工作流配置模块。

基于项目统一的 ``PropertiesBaseSettings`` 与 ``create_config`` 读取
``RUN_WORKFLOW_`` 前缀配置项。配置主源为 ``config.properties``，环境变量
仅用于覆盖。
"""

from __future__ import annotations

import re

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config
from domain.run.workflow import CollaborationLimit, WorkflowExecutionPolicy

_WORKFLOW_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class RunWorkflowConfig(PropertiesBaseSettings):
    """Run 工作流配置。"""

    model_config = SettingsConfigDict(env_prefix="RUN_WORKFLOW_")

    enabled: bool = True
    default_workflow: str = ""
    enabled_workflows: str = "research,code_change,report,batch_processing"
    max_recursion_depth: int = 3
    max_parallel_delegations: int = 3
    max_handoff_count: int = 1
    max_revise_per_phase: int = 1
    max_child_runs: int = 0
    recent_collaboration_summary_limit: int = 5
    role_capability_enabled: bool = False
    child_run_enabled: bool = False

    @model_validator(mode="after")
    def _validate_run_workflow_config(self) -> RunWorkflowConfig:
        """校验 Run workflow 配置，非法时 fail-fast。"""

        raw_workflows = tuple(item.strip() for item in self.enabled_workflows.split(","))
        if any(not item for item in raw_workflows):
            raise ConfigurationError("RUN_WORKFLOW_ENABLED_WORKFLOWS 不能包含空名称")
        workflows = raw_workflows
        if not workflows:
            raise ConfigurationError("RUN_WORKFLOW_ENABLED_WORKFLOWS 不能为空")
        for name in workflows:
            if not _WORKFLOW_NAME_PATTERN.fullmatch(name):
                raise ConfigurationError(
                    "RUN_WORKFLOW_ENABLED_WORKFLOWS 必须为小写 snake_case 名称列表"
                )

        default_workflow = self.default_workflow.strip()
        if default_workflow:
            if not _WORKFLOW_NAME_PATTERN.fullmatch(default_workflow):
                raise ConfigurationError("RUN_WORKFLOW_DEFAULT_WORKFLOW 必须为小写 snake_case 名称")
            if default_workflow not in workflows:
                raise ConfigurationError(
                    "RUN_WORKFLOW_DEFAULT_WORKFLOW 必须包含在 RUN_WORKFLOW_ENABLED_WORKFLOWS 中"
                )

        non_negative_fields = {
            "RUN_WORKFLOW_MAX_RECURSION_DEPTH": self.max_recursion_depth,
            "RUN_WORKFLOW_MAX_HANDOFF_COUNT": self.max_handoff_count,
            "RUN_WORKFLOW_MAX_REVISE_PER_PHASE": self.max_revise_per_phase,
            "RUN_WORKFLOW_MAX_CHILD_RUNS": self.max_child_runs,
        }
        for key, value in non_negative_fields.items():
            if value < 0:
                raise ConfigurationError(f"{key} 必须大于等于 0")

        if self.max_parallel_delegations <= 0:
            raise ConfigurationError("RUN_WORKFLOW_MAX_PARALLEL_DELEGATIONS 必须为正整数")
        if self.recent_collaboration_summary_limit <= 0:
            raise ConfigurationError("RUN_WORKFLOW_RECENT_COLLABORATION_SUMMARY_LIMIT 必须为正整数")
        return self

    def enabled_workflow_names(self) -> tuple[str, ...]:
        """返回去空格后的启用 workflow 名称列表。"""

        return tuple(item.strip() for item in self.enabled_workflows.split(","))

    def to_collaboration_limit(self) -> CollaborationLimit:
        """将配置转换为领域层协作限制策略。"""

        return CollaborationLimit(
            max_recursion_depth=self.max_recursion_depth,
            max_parallel_delegations=self.max_parallel_delegations,
            max_handoff_count=self.max_handoff_count,
            max_revise_per_phase=self.max_revise_per_phase,
            max_child_runs=self.max_child_runs,
        )

    def to_execution_policy(self) -> WorkflowExecutionPolicy:
        """将配置转换为领域层 workflow 执行策略。"""

        return WorkflowExecutionPolicy(
            role_capability_enabled=self.role_capability_enabled,
            child_run_enabled=self.child_run_enabled,
        )


run_workflow_config = create_config(RunWorkflowConfig)
"""全局 Run 工作流配置实例，通过项目配置工厂创建。"""
