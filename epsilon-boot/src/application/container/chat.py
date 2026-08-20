"""Chat 相关组合根注册。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from common.container_models import Scope
from domain.chat.ports import ChatServicePort, ContextBuilderPort, ContextCompactionPort


def register_chat_components(
    container: Any,
    *,
    create_compaction_adapter: Callable[[], Awaitable[ContextCompactionPort]],
    create_context_builder: Callable[[], Awaitable[ContextBuilderPort]],
    create_chat_service: Callable[[], Awaitable[ChatServicePort]],
) -> None:
    """注册 Chat 相关组件。"""
    container.register(ContextCompactionPort, create_compaction_adapter, Scope.SINGLETON)
    container.register(ContextBuilderPort, create_context_builder, Scope.SINGLETON)
    container.register(ChatServicePort, create_chat_service, Scope.SINGLETON)

