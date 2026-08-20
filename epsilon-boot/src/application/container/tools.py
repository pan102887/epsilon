"""Tool 相关组合根注册。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from common.container_models import Scope
from domain.agent.tools import ToolRegistry


def register_tool_components(
    container: Any,
    *,
    create_tool_registry: Callable[[], ToolRegistry | Awaitable[ToolRegistry]],
) -> None:
    """注册工具注册表。"""
    container.register(ToolRegistry, create_tool_registry, Scope.SINGLETON)
