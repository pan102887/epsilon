"""chat-default prompt 单一来源 helper 单元测试。

守护 ``resolve_chat_default_system_prompt`` 的行为等价契约：
``system_prompt`` 为 ``append_workspace_path_guidance(content)``、``prompt_id``
取自 ``get("chat-default")`` 且不受 workspace guidance 影响、``get`` 恰调用一次。
"""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.chat.chat_default_prompt import (
    ChatDefaultSystemPrompt,
    resolve_chat_default_system_prompt,
)
from infrastructure.prompt.workspace_guidance import append_workspace_path_guidance


@dataclass(frozen=True)
class _StubPrompt:
    """模拟 PromptRegistryPort.get 返回的 Prompt 值对象。"""

    content: str
    prompt_id: str


class _StubPromptRegistry:
    """记录 get 调用次数与入参的 PromptRegistryPort 测试替身。"""

    def __init__(self, prompt: _StubPrompt) -> None:
        self._prompt = prompt
        self.calls: list[str] = []

    def get(self, name: str) -> _StubPrompt:
        """返回固定 Prompt 并记录调用。"""

        self.calls.append(name)
        return self._prompt


def test_resolve_appends_workspace_guidance_and_preserves_prompt_id() -> None:
    """system_prompt 追加 workspace 引导，prompt_id 原样保留。"""

    prompt = _StubPrompt(content="你是助手。", prompt_id="chat-default@v3")
    registry = _StubPromptRegistry(prompt)

    resolved = resolve_chat_default_system_prompt(registry)  # type: ignore[arg-type]

    assert isinstance(resolved, ChatDefaultSystemPrompt)
    assert resolved.system_prompt == append_workspace_path_guidance(prompt.content)
    assert resolved.prompt_id == "chat-default@v3"


def test_resolve_loads_chat_default_exactly_once() -> None:
    """恰以 "chat-default" 调用一次注册表 get。"""

    registry = _StubPromptRegistry(_StubPrompt(content="内容", prompt_id="id-1"))

    resolve_chat_default_system_prompt(registry)  # type: ignore[arg-type]

    assert registry.calls == ["chat-default"]


def test_prompt_id_not_affected_by_workspace_guidance() -> None:
    """workspace guidance 只改内容，不改 prompt_id。"""

    prompt = _StubPrompt(content="原始内容", prompt_id="stable-id")
    registry = _StubPromptRegistry(prompt)

    resolved = resolve_chat_default_system_prompt(registry)  # type: ignore[arg-type]

    assert resolved.prompt_id == prompt.prompt_id
    assert resolved.system_prompt != prompt.content
