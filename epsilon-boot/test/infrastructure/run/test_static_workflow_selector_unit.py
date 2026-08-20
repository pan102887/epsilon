"""静态 Run workflow 选择器单元测试。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain.run.exceptions import RunUnknownWorkflowError
from domain.run.value_objects import RunCreateRequest, RunKind, RunPayload
from infrastructure.run.static_workflow_registry_adapter import (
    StaticWorkflowRegistryAdapter,
)
from infrastructure.run.static_workflow_selector import StaticWorkflowSelector
from infrastructure.run.workflow_config import RunWorkflowConfig


def _selector(
    config: RunWorkflowConfig | None = None,
) -> StaticWorkflowSelector:
    """构造默认 selector。"""

    effective_config = config or RunWorkflowConfig()
    return StaticWorkflowSelector(
        registry=StaticWorkflowRegistryAdapter(effective_config),
        config=effective_config,
    )


def _request(
    goal: str,
    *,
    workflow_name: str | None = None,
    task_classification: str | None = None,
) -> RunCreateRequest:
    """构造任务 Run 创建请求。"""

    return RunCreateRequest(
        payload=RunPayload(kind=RunKind.TASK, session_id="s1", task={"goal": goal}),
        client_request_id="client-1",
        workflow_name=workflow_name,
        task_classification=task_classification,
    )


def test_selector_uses_explicit_workflow() -> None:
    """显式 workflow 优先于 payload 规则。"""
    selection = _selector().select(_request("fix code tests", workflow_name="report"))

    assert selection.workflow is not None
    assert selection.workflow.name == "report"
    assert selection.explicit is True
    assert selection.reason == "explicit_workflow"


def test_selector_rejects_explicit_unknown_workflow() -> None:
    """显式未知 workflow 必须抛业务错误。"""
    with pytest.raises(RunUnknownWorkflowError):
        _selector().select(_request("fix code tests", workflow_name="missing"))


def test_selector_rejects_explicit_disabled_workflow() -> None:
    """显式选择未启用 workflow 不得静默降级。"""
    selector = _selector(RunWorkflowConfig(enabled_workflows="research,report"))

    with pytest.raises(RunUnknownWorkflowError):
        selector.select(_request("fix code tests", workflow_name="code_change"))


def test_selector_uses_default_workflow_before_payload_rules() -> None:
    """配置 default_workflow 时应优先选择默认 workflow。"""
    selector = _selector(
        RunWorkflowConfig(
            default_workflow="report",
            enabled_workflows="research,code_change,report,batch_processing",
        )
    )

    selection = selector.select(_request("fix code tests"))

    assert selection.workflow is not None
    assert selection.workflow.name == "report"
    assert selection.explicit is False
    assert selection.reason == "default_workflow"


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("fix code tests and update files", "code_change"),
        ("batch process many files", "batch_processing"),
        ("research source material", "research"),
        ("write a summary report", "report"),
    ],
)
def test_selector_matches_payload_keywords(goal: str, expected: str) -> None:
    """payload 关键词应映射到对应 workflow。"""
    selection = _selector().select(_request(goal))

    assert selection.workflow is not None
    assert selection.workflow.name == expected
    assert selection.reason == "rule_match"


def test_selector_uses_task_classification_as_fallback() -> None:
    """无关键词时 task_classification 可作为确定性 fallback。"""
    selection = _selector().select(_request("do the thing", task_classification="long_task"))

    assert selection.workflow is not None
    assert selection.workflow.name == "code_change"


def test_selector_returns_no_match_for_compatible_default_path() -> None:
    """自动选择无匹配时不得阻断 Run 创建。"""
    selection = _selector().select(_request("hello"))

    assert selection.workflow is None
    assert selection.explicit is False
    assert selection.reason == "no_match"


def test_selector_returns_disabled_when_global_workflow_disabled() -> None:
    """全局禁用 workflow 时自动选择应跳过。"""
    selection = _selector(RunWorkflowConfig(enabled=False)).select(_request("fix code tests"))

    assert selection.workflow is None
    assert selection.explicit is False
    assert selection.reason == "disabled"


def test_selector_has_no_external_service_imports() -> None:
    """选择器不得导入模型、HTTP、Redis 或外部服务模块。"""
    source = Path("src/infrastructure/run/static_workflow_selector.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module.lower())

    forbidden_fragments = (
        "model_access",
        "openai",
        "httpx",
        "requests",
        "redis",
        "fastapi",
    )
    for fragment in forbidden_fragments:
        assert all(fragment not in module for module in imported_modules)
