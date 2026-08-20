"""Run workflow 配置单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.configuration import ConfigurationError, PropertiesFileSettingsSource
from infrastructure.run.workflow_config import RunWorkflowConfig


def test_workflow_config_defaults_convert_to_collaboration_limit() -> None:
    """默认配置应启用四类标准工作流并可转换为协作限制。"""
    config = RunWorkflowConfig()

    assert config.enabled is True
    assert config.default_workflow == ""
    assert config.role_capability_enabled is False
    assert config.child_run_enabled is False
    assert config.enabled_workflow_names() == (
        "research",
        "code_change",
        "report",
        "batch_processing",
    )

    limit = config.to_collaboration_limit()

    assert limit.max_recursion_depth == 3
    assert limit.max_parallel_delegations == 3
    assert limit.max_handoff_count == 1
    assert limit.max_revise_per_phase == 1
    assert limit.max_child_runs == 0
    policy = config.to_execution_policy()
    assert policy.role_capability_enabled is False
    assert policy.child_run_enabled is False


def test_workflow_config_loads_from_config_properties(tmp_path: Path) -> None:
    """临时 config.properties 中的 RUN_WORKFLOW_* 键应覆盖默认值。"""
    props_file = tmp_path / "config.properties"
    props_file.write_text(
        "\n".join(
            [
                "RUN_WORKFLOW_ENABLED=false",
                "RUN_WORKFLOW_DEFAULT_WORKFLOW=code_change",
                "RUN_WORKFLOW_ENABLED_WORKFLOWS=research,code_change",
                "RUN_WORKFLOW_MAX_RECURSION_DEPTH=2",
                "RUN_WORKFLOW_MAX_PARALLEL_DELEGATIONS=4",
                "RUN_WORKFLOW_MAX_HANDOFF_COUNT=0",
                "RUN_WORKFLOW_MAX_REVISE_PER_PHASE=2",
                "RUN_WORKFLOW_MAX_CHILD_RUNS=1",
                "RUN_WORKFLOW_RECENT_COLLABORATION_SUMMARY_LIMIT=8",
                "RUN_WORKFLOW_ROLE_CAPABILITY_ENABLED=true",
                "RUN_WORKFLOW_CHILD_RUN_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    class _ConfigFromProperties(RunWorkflowConfig):
        """仅使用临时 properties 源的 workflow 配置。"""

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        ):
            """仅从测试提供的 config.properties 加载配置。"""

            return (
                PropertiesFileSettingsSource(
                    settings_cls,
                    properties_path=props_file,
                ),
            )

    config = _ConfigFromProperties()
    limit = config.to_collaboration_limit()

    assert config.enabled is False
    assert config.default_workflow == "code_change"
    assert config.enabled_workflow_names() == ("research", "code_change")
    assert limit.max_recursion_depth == 2
    assert limit.max_parallel_delegations == 4
    assert limit.max_handoff_count == 0
    assert limit.max_revise_per_phase == 2
    assert limit.max_child_runs == 1
    assert config.recent_collaboration_summary_limit == 8
    assert config.role_capability_enabled is True
    assert config.child_run_enabled is True
    assert config.to_execution_policy().role_capability_enabled is True
    assert config.to_execution_policy().child_run_enabled is True


def test_workflow_config_loads_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量应可覆盖默认 RUN_WORKFLOW_* 配置。"""
    monkeypatch.setenv("RUN_WORKFLOW_DEFAULT_WORKFLOW", "report")
    monkeypatch.setenv("RUN_WORKFLOW_ENABLED_WORKFLOWS", "research,report")
    monkeypatch.setenv("RUN_WORKFLOW_MAX_PARALLEL_DELEGATIONS", "7")

    config = RunWorkflowConfig()

    assert config.default_workflow == "report"
    assert config.enabled_workflow_names() == ("research", "report")
    assert config.max_parallel_delegations == 7


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("enabled_workflows", "", "RUN_WORKFLOW_ENABLED_WORKFLOWS"),
        ("enabled_workflows", "research,,code_change", "RUN_WORKFLOW_ENABLED_WORKFLOWS"),
        ("enabled_workflows", "Research", "RUN_WORKFLOW_ENABLED_WORKFLOWS"),
        ("default_workflow", "CodeChange", "RUN_WORKFLOW_DEFAULT_WORKFLOW"),
        ("max_recursion_depth", -1, "RUN_WORKFLOW_MAX_RECURSION_DEPTH"),
        ("max_parallel_delegations", 0, "RUN_WORKFLOW_MAX_PARALLEL_DELEGATIONS"),
        ("max_handoff_count", -1, "RUN_WORKFLOW_MAX_HANDOFF_COUNT"),
        ("max_revise_per_phase", -1, "RUN_WORKFLOW_MAX_REVISE_PER_PHASE"),
        ("max_child_runs", -1, "RUN_WORKFLOW_MAX_CHILD_RUNS"),
        (
            "recent_collaboration_summary_limit",
            0,
            "RUN_WORKFLOW_RECENT_COLLABORATION_SUMMARY_LIMIT",
        ),
    ],
)
def test_workflow_config_rejects_invalid_values(
    field_name: str,
    value: object,
    message: str,
) -> None:
    """非法 workflow 配置应 fail-fast。"""
    with pytest.raises(ConfigurationError, match=message):
        RunWorkflowConfig(**{field_name: value})


def test_workflow_config_rejects_default_not_in_enabled_workflows() -> None:
    """默认 workflow 必须包含在 enabled_workflows 中。"""
    with pytest.raises(ConfigurationError, match="RUN_WORKFLOW_DEFAULT_WORKFLOW"):
        RunWorkflowConfig(
            default_workflow="batch_processing",
            enabled_workflows="research,code_change",
        )
