"""ReActAgentAdapter system_prompt 幂等注入单元测试模块。

验证 ``_ensure_agent_system_prompt`` 的三种核心场景：
- (a) 空 context → 注入 ``config.system_prompt``
- (b) context 已含任一 SystemMessage → 不再追加
- (c) 多 Agent 委派双 context：父 ctx 注入父 prompt，子 ctx 注入子 prompt，互不污染

本测试通过 ``_iter_rounds`` 的消费路径间接触发注入（与生产路径一致），
确保幂等规则在真实调用链路中生效。

**Validates: Requirements 7.1, 7.2, 7.5, 7.6, 7.7, 7.8**
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class _FakeContextBuilder:
    """测试用上下文构建器。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del model_access, model
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


class _FakeModel:
    """返回纯文本回复的模型 fake。"""

    def __init__(self, content: str = "回复") -> None:
        self._content = content

    async def chat(self, request: ChatRequest) -> LLMResponse:
        return LLMResponse(
            content=self._content,
            model="test-model",
            usage={"total_tokens": 1},
            tool_calls=[],
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        yield StreamingChunk(delta_content=self._content, finished=True, usage={"total_tokens": 1})


def _make_adapter() -> ReActAgentAdapter:
    """构造使用 mock 依赖的 ReActAgentAdapter。"""
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


def _make_config(system_prompt: str, max_rounds: int = 3) -> AgentConfig:
    """构造指定 system_prompt 的 AgentConfig。"""
    return AgentConfig(
        system_prompt=system_prompt,
        tool_schemas=[],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


async def test_empty_context_injects_system_prompt() -> None:
    """(a) 空 context → 注入 config.system_prompt。

    验证当 ConversationContext 中不含任何 SystemMessage 时，
    _iter_rounds 在首轮模型调用前注入 config.system_prompt。

    **Validates: Requirement 7.1**
    """
    adapter = _make_adapter()
    context = ConversationContext()
    context.add_user_message("hello")
    config = _make_config("你是一个助手")

    await adapter.run(context, config, _FakeModel())  # type: ignore[arg-type]

    messages = context.get_messages()
    system_messages = [m for m in messages if m.role == "system"]
    assert len(system_messages) == 1, f"期望 1 条 SystemMessage，实际 {len(system_messages)} 条"
    assert system_messages[0].content == "你是一个助手"


async def test_context_with_existing_system_message_skips_injection() -> None:
    """(b) 已含 SystemMessage → 不再追加。

    验证当 ConversationContext 中已经存在 SystemMessage 时，
    _iter_rounds 跳过注入，不会重复追加。

    **Validates: Requirement 7.2**
    """
    adapter = _make_adapter()
    context = ConversationContext()
    context.add_system_message("已有的系统提示词")
    context.add_user_message("hello")
    config = _make_config("新的系统提示词")

    await adapter.run(context, config, _FakeModel())  # type: ignore[arg-type]

    messages = context.get_messages()
    system_messages = [m for m in messages if m.role == "system"]
    # 仍然只有 1 条（原有的），不会追加新的
    assert len(system_messages) == 1, (
        f"期望 1 条 SystemMessage（不追加），实际 {len(system_messages)} 条"
    )
    assert system_messages[0].content == "已有的系统提示词"


async def test_multi_agent_delegation_independent_contexts() -> None:
    """(c) 多 Agent 委派双 context：父 ctx 注入父 prompt，子 ctx 注入子 prompt，互不污染。

    验证不同 AgentConfig 实例分别操作不同的 ConversationContext 时，
    各自注入各自的 system_prompt，互不影响。

    **Validates: Requirements 7.5, 7.6, 7.8**
    """
    adapter = _make_adapter()

    # 父 Agent
    parent_context = ConversationContext()
    parent_context.add_user_message("parent question")
    parent_config = _make_config("父 Agent 系统提示词")

    await adapter.run(parent_context, parent_config, _FakeModel("父回复"))  # type: ignore[arg-type]

    # 子 Agent（独立 context）
    child_context = ConversationContext()
    child_context.add_user_message("child question")
    child_config = _make_config("子 Agent 系统提示词")

    await adapter.run(child_context, child_config, _FakeModel("子回复"))  # type: ignore[arg-type]

    # 验证父 context 的 system_prompt
    parent_sys = [m for m in parent_context.get_messages() if m.role == "system"]
    assert len(parent_sys) == 1
    assert parent_sys[0].content == "父 Agent 系统提示词"

    # 验证子 context 的 system_prompt
    child_sys = [m for m in child_context.get_messages() if m.role == "system"]
    assert len(child_sys) == 1
    assert child_sys[0].content == "子 Agent 系统提示词"

    # 互不污染：父 context 中不含子 prompt，反之亦然
    assert all(m.content != "子 Agent 系统提示词" for m in parent_context.get_messages())
    assert all(m.content != "父 Agent 系统提示词" for m in child_context.get_messages())


async def test_shared_context_idempotent_when_child_reuses_parent() -> None:
    """子 Agent 复用父 Agent 的 ConversationContext 时，幂等规则跳过注入。

    模拟场景：父 Agent 已注入 system_prompt 后，子 Agent 复用同一个
    ConversationContext（已含 system 消息），此时 _ensure_agent_system_prompt
    应跳过注入，避免父子提示词冲突。

    **Validates: Requirement 7.7**
    """
    adapter = _make_adapter()

    # 父 Agent 先注入
    shared_context = ConversationContext()
    shared_context.add_user_message("question")
    parent_config = _make_config("父 Agent 系统提示词")

    await adapter.run(shared_context, parent_config, _FakeModel("回复1"))  # type: ignore[arg-type]

    # 此时 context 已有 system message
    system_count_after_parent = sum(1 for m in shared_context.get_messages() if m.role == "system")
    assert system_count_after_parent == 1

    # 子 Agent 复用同一 context
    child_config = _make_config("子 Agent 系统提示词")
    shared_context.add_user_message("follow up")

    await adapter.run(shared_context, child_config, _FakeModel("回复2"))  # type: ignore[arg-type]

    # 系统消息数量不增加
    system_count_after_child = sum(1 for m in shared_context.get_messages() if m.role == "system")
    assert system_count_after_child == 1, (
        f"共享 context 中 SystemMessage 数量不应增加，期望 1 实际 {system_count_after_child}"
    )
    # 内容仍为父 Agent 的 prompt（先注入者保留）
    sys_msg = next(m for m in shared_context.get_messages() if m.role == "system")
    assert sys_msg.content == "父 Agent 系统提示词"


async def test_empty_system_prompt_skips_injection() -> None:
    """config.system_prompt 为空字符串时不注入。

    **Validates: Requirement 7.1（边界情况）**
    """
    adapter = _make_adapter()
    context = ConversationContext()
    context.add_user_message("hello")
    config = _make_config("")

    await adapter.run(context, config, _FakeModel())  # type: ignore[arg-type]

    messages = context.get_messages()
    system_messages = [m for m in messages if m.role == "system"]
    assert len(system_messages) == 0, "system_prompt 为空时不应注入任何 SystemMessage"
