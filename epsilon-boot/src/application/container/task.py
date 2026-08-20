"""Task 相关组合根注册。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from common.container_models import Scope
from domain.task.ports import TaskAgentPort


def register_task_components(
    container: Any,
    *,
    create_task_agent: Callable[[], Awaitable[TaskAgentPort]],
) -> None:
    """注册 Task Agent 组件。"""
    container.register(TaskAgentPort, create_task_agent, Scope.SINGLETON)

