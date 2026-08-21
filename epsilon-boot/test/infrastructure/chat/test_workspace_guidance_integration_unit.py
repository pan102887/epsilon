"""ChatServiceAdapter 与 ``append_workspace_path_guidance`` 的集成单元测试。

# Validates: Requirements 4.3, 4.4, 6.1, 6.2

构造期断言：经过 ``ChatServiceAdapter`` 构造后，``self._system_prompt`` 必然
以 ``LoadedPrompt.content`` 起始，并以 ``_WORKSPACE_PATH_GUIDANCE`` 收尾，
保证路径规范文案在 Prompt 装配链路上**只在构造期幂等追加一次**。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from domain.chat.context import UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.prompt.workspace_guidance import WORKSPACE_PATH_GUIDANCE


def _build_adapter(content: str) -> ChatServiceAdapter:
    """构造仅用于本模块断言的 ``ChatServiceAdapter`` 实例。

    所有依赖均为 ``MagicMock`` / ``AsyncMock``，仅 ``prompt_registry.get``
    返回真实的 ``LoadedPrompt(content=content)``，以便观察构造期路径规范
    追加的副作用。

    Args:
        content: ``LoadedPrompt.content`` 的初始值。

    Returns:
        构造完成的 ``ChatServiceAdapter`` 实例。
    """
    loaded = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content=content,
    )
    prompt_registry = MagicMock()
    prompt_registry.get = MagicMock(return_value=loaded)
    return ChatServiceAdapter(
        session_store=AsyncMock(),
        model_registry=MagicMock(),
        prompt_registry=prompt_registry,
        context_builder=MagicMock(
            build=AsyncMock(
                return_value=ContextBuilderResult(
                    messages=[UserMessage(content="builder message")],
                    environment_injected=True,
                )
            )
        ),
        agent=AsyncMock(),
        tool_calling_enabled=False,
        max_tool_rounds=5,
        tool_schemas=[],
    )


def test_system_prompt_starts_with_loaded_content() -> None:
    """构造完成后 ``_system_prompt`` 应以 ``LoadedPrompt.content`` 起始。"""
    adapter = _build_adapter("自定义助手内容")
    assert adapter.system_prompt.startswith("自定义助手内容")


def test_system_prompt_ends_with_workspace_guidance() -> None:
    """构造完成后 ``_system_prompt`` 应以工作区路径规范收尾。"""
    adapter = _build_adapter("自定义助手内容")
    assert adapter.system_prompt.rstrip().endswith(WORKSPACE_PATH_GUIDANCE.strip())


def test_system_prompt_does_not_double_append_when_content_already_has_guidance() -> None:
    """``LoadedPrompt.content`` 末尾已含规范文案时构造期不再二次追加。"""
    content = "自定义助手内容" + WORKSPACE_PATH_GUIDANCE
    adapter = _build_adapter(content)
    assert adapter.system_prompt == content


def test_prompt_id_attribute_matches_loaded_prompt() -> None:
    """构造完成后 ``_prompt_id`` 应等于 ``LoadedPrompt.prompt_id``。"""
    adapter = _build_adapter("任意内容")
    assert adapter.prompt_id == "chat-default@v1"
