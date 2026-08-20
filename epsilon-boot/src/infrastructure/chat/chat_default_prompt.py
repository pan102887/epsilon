"""chat-default 系统 Prompt 加载的单一来源。

把 ``PromptRegistryPort.get("chat-default")`` + ``append_workspace_path_guidance``
+ ``prompt_id`` 提取收敛到唯一函数，供 ``ChatServiceAdapter`` 构造期与组合根
``_create_chat_service`` 共同调用，消除两处重复的 prompt 加载细节（行为等价，
``ddd-followup-refinements`` 切片 B）。

本模块处基础设施层，不引入领域运行时关注点；``prompt_id`` 不受 workspace guidance
影响，与拆分前的三行加载逻辑逐字节等价。
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.prompt.ports import PromptRegistryPort
from infrastructure.prompt.workspace_guidance import append_workspace_path_guidance


@dataclass(frozen=True)
class ChatDefaultSystemPrompt:
    """经 workspace guidance 处理后的 chat-default 系统 Prompt。

    Attributes:
        system_prompt: 追加 workspace 路径引导后的系统提示词内容。
        prompt_id: chat-default Prompt 的版本化标识（不受 guidance 影响）。
    """

    system_prompt: str
    prompt_id: str


def resolve_chat_default_system_prompt(
    prompt_registry: PromptRegistryPort,
) -> ChatDefaultSystemPrompt:
    """加载 chat-default Prompt 并追加 workspace 路径引导。

    与原 ``ChatServiceAdapter.__init__`` / ``_create_chat_service`` 中的三行加载
    逻辑逐字节等价：``get("chat-default")`` → ``append_workspace_path_guidance``
    → 取 ``prompt_id``；``prompt_id`` 不受 workspace guidance 影响。

    Args:
        prompt_registry: Prompt 注册表端口，用于加载 chat-default Prompt。

    Returns:
        承载 system_prompt 与 prompt_id 的 ``ChatDefaultSystemPrompt``。
    """
    loaded_prompt = prompt_registry.get("chat-default")
    return ChatDefaultSystemPrompt(
        system_prompt=append_workspace_path_guidance(loaded_prompt.content),
        prompt_id=loaded_prompt.prompt_id,
    )
