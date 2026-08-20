"""静态 Run 工作流选择器。"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from domain.run.exceptions import RunUnknownWorkflowError
from domain.run.ports import WorkflowRegistryPort, WorkflowSelection, WorkflowSelectorPort
from domain.run.value_objects import RunCreateRequest
from domain.run.workflow import StandardWorkflowName, WorkflowDefinition
from infrastructure.run.workflow_config import RunWorkflowConfig


class StaticWorkflowSelector(WorkflowSelectorPort):
    """基于显式参数、配置和 payload 规则的确定性 workflow 选择器。"""

    def __init__(
        self,
        *,
        registry: WorkflowRegistryPort,
        config: RunWorkflowConfig,
    ) -> None:
        """初始化静态 workflow 选择器。"""

        self._registry = registry
        self._config = config

    def select(self, request: RunCreateRequest) -> WorkflowSelection:
        """根据显式参数、task_classification 与 payload 选择工作流。"""

        explicit_name = (request.workflow_name or "").strip()
        if explicit_name:
            workflow = self._enabled_definition_or_raise(explicit_name)
            return WorkflowSelection(
                workflow=workflow,
                explicit=True,
                reason="explicit_workflow",
            )

        if not self._config.enabled:
            return WorkflowSelection(workflow=None, explicit=False, reason="disabled")

        default_name = self._config.default_workflow.strip()
        if default_name:
            workflow = self._enabled_definition_or_raise(default_name)
            return WorkflowSelection(
                workflow=workflow,
                explicit=False,
                reason="default_workflow",
            )

        payload_text = _payload_text(request)
        task_classification = (request.task_classification or "").strip()
        for name in _SELECTION_ORDER:
            workflow = self._registry.get_definition(name.value)
            if workflow is None or not workflow.enabled:
                continue
            if _matches(workflow, payload_text, task_classification):
                return WorkflowSelection(
                    workflow=workflow,
                    explicit=False,
                    reason="rule_match",
                )

        return WorkflowSelection(workflow=None, explicit=False, reason="no_match")

    def _enabled_definition_or_raise(self, name: str) -> WorkflowDefinition:
        """读取启用定义，不存在或未启用时抛显式选择错误。"""

        workflow = self._registry.get_definition(name)
        if workflow is None or not workflow.enabled:
            raise RunUnknownWorkflowError(name)
        return workflow


_SELECTION_ORDER = (
    StandardWorkflowName.CODE_CHANGE,
    StandardWorkflowName.BATCH_PROCESSING,
    StandardWorkflowName.RESEARCH,
    StandardWorkflowName.REPORT,
)


def _matches(
    workflow: WorkflowDefinition,
    payload_text: str,
    task_classification: str,
) -> bool:
    """判断 workflow 是否匹配请求特征。"""

    applicable = workflow.applicable
    if applicable.payload_keywords and any(
        keyword.lower() in payload_text for keyword in applicable.payload_keywords
    ):
        return True
    return bool(task_classification and task_classification in applicable.task_classes)


def _payload_text(request: RunCreateRequest) -> str:
    """把 RunCreateRequest payload 转换为用于关键词匹配的小写文本。"""

    payload_dict: dict[str, Any] = asdict(request.payload)
    encoded = json.dumps(
        payload_dict,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return encoded.lower()
