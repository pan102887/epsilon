"""``Single_System_Prompt_Injection_Site`` 单源注入单元测试模块。

验证 v2 重构中 ``run_streaming`` / ``run_events`` 入口处的
``_ensure_agent_system_prompt`` 调用被删除，注入收口为以下两类位置：

1. ``_iter_rounds`` 入口（首轮前唯一注入点，``run`` / ``run_streaming`` /
   ``run_events`` / ``resume`` 在 ``max_rounds > 1`` 时统一经过此处）;
2. ``max_rounds == 1`` 分支（不进 ``_iter_rounds``，需独立显式注入）。

具体覆盖：

- (a) ``run_streaming`` 在 ``max_rounds == 1`` 分支下注入仅发生一次（无重复）；
- (b) ``run_streaming`` 在 ``max_rounds > 1`` 路径下由 ``_iter_rounds`` 内单一
  注入完成，入口处不再调用；
- (c) ``run_events`` 行为与 ``run_streaming`` 一致；
- (d) 多次连续调用共享 context 时 SystemMessage 数量不增加（幂等保证）。

覆盖需求 1.1, 1.2, 1.3, 1.4, 1.5 与 Property 1。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

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
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


class _FakeModel:
    """返回纯文本回复的模型 fake。"""

    def __init__(self, content: str = "ok") -> None:
        self._content = content

    async def chat(self, request: ChatRequest) -> LLMResponse:
        return LLMResponse(content=self._content, model="test-model", usage={}, tool_calls=[])

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        yield StreamingChunk(delta_content=self._content, finished=True, usage={})

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return 0


def _adapter() -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


def _config(system_prompt: str = "你是助手", max_rounds: int = 3) -> AgentConfig:
    return AgentConfig(
        system_prompt=system_prompt,
        tool_schemas=[],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


class TestRunStreamingInjectionSingleSite:
    """验证 ``run_streaming`` 入口移除调用且注入仅发生一次。"""

    @pytest.mark.asyncio
    async def test_run_streaming_max_rounds_one_injects_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``max_rounds == 1`` 分支显式注入,SystemMessage 应仅出现 1 次。"""
        adapter = _adapter()
        ctx = ConversationContext()
        ctx.add_user_message("hi")

        # 计数 _ensure_agent_system_prompt 调用次数
        call_count = {"n": 0}
        original = ReActAgentAdapter.ensure_agent_system_prompt

        def _wrapped(
            context: ConversationContext,
            config: AgentConfig,
        ) -> None:
            call_count["n"] += 1
            original(context, config)

        monkeypatch.setattr(
            ReActAgentAdapter, "ensure_agent_system_prompt", staticmethod(_wrapped)
        )

        async for _ in adapter.run_streaming(ctx, _config(max_rounds=1), _FakeModel()):
            pass

        # max_rounds == 1 分支显式注入一次
        assert call_count["n"] == 1
        # SystemMessage 仅一条
        sys_msgs = [m for m in ctx.get_messages() if m.role == "system"]
        assert len(sys_msgs) == 1
        assert sys_msgs[0].content == "你是助手"

    @pytest.mark.asyncio
    async def test_run_streaming_max_rounds_gt_one_injects_inside_iter_rounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``max_rounds > 1`` 时入口不调用 _ensure_agent_system_prompt，
        由 _iter_rounds 内一次注入。
        """
        adapter = _adapter()
        ctx = ConversationContext()
        ctx.add_user_message("hi")

        # 模型直接返回纯文本 → 第一轮自然终止
        call_count = {"n": 0}
        original = ReActAgentAdapter.ensure_agent_system_prompt

        def _wrapped(
            context: ConversationContext,
            config: AgentConfig,
        ) -> None:
            call_count["n"] += 1
            original(context, config)

        monkeypatch.setattr(
            ReActAgentAdapter, "ensure_agent_system_prompt", staticmethod(_wrapped)
        )

        async for _ in adapter.run_streaming(ctx, _config(max_rounds=3), _FakeModel("text")):
            pass

        # _iter_rounds 进入时注入一次,入口处无调用
        assert call_count["n"] == 1
        sys_msgs = [m for m in ctx.get_messages() if m.role == "system"]
        assert len(sys_msgs) == 1


class TestRunEventsInjectionSingleSite:
    """验证 ``run_events`` 入口移除调用且注入仅发生一次。"""

    @pytest.mark.asyncio
    async def test_run_events_max_rounds_one_injects_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``max_rounds == 1`` 分支显式注入,SystemMessage 应仅出现 1 次。"""
        adapter = _adapter()
        ctx = ConversationContext()
        ctx.add_user_message("hi")

        call_count = {"n": 0}
        original = ReActAgentAdapter.ensure_agent_system_prompt

        def _wrapped(
            context: ConversationContext,
            config: AgentConfig,
        ) -> None:
            call_count["n"] += 1
            original(context, config)

        monkeypatch.setattr(
            ReActAgentAdapter, "ensure_agent_system_prompt", staticmethod(_wrapped)
        )

        async for _ in adapter.run_events(ctx, _config(max_rounds=1), _FakeModel()):
            pass

        assert call_count["n"] == 1
        sys_msgs = [m for m in ctx.get_messages() if m.role == "system"]
        assert len(sys_msgs) == 1

    @pytest.mark.asyncio
    async def test_run_events_max_rounds_gt_one_injects_inside_iter_rounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _adapter()
        ctx = ConversationContext()
        ctx.add_user_message("hi")

        call_count = {"n": 0}
        original = ReActAgentAdapter.ensure_agent_system_prompt

        def _wrapped(
            context: ConversationContext,
            config: AgentConfig,
        ) -> None:
            call_count["n"] += 1
            original(context, config)

        monkeypatch.setattr(
            ReActAgentAdapter, "ensure_agent_system_prompt", staticmethod(_wrapped)
        )

        async for _ in adapter.run_events(ctx, _config(max_rounds=3), _FakeModel("text")):
            pass

        assert call_count["n"] == 1
        sys_msgs = [m for m in ctx.get_messages() if m.role == "system"]
        assert len(sys_msgs) == 1


class TestRepeatedInvocationKeepsSystemMessageCountOne:
    """连续调用共享 context 时 SystemMessage 数量不增加(幂等保证)。"""

    @pytest.mark.asyncio
    async def test_repeated_run_streaming_does_not_duplicate(self) -> None:
        adapter = _adapter()
        ctx = ConversationContext()
        ctx.add_user_message("hi")

        async for _ in adapter.run_streaming(ctx, _config(max_rounds=3), _FakeModel("text")):
            pass
        ctx.add_user_message("again")
        async for _ in adapter.run_streaming(ctx, _config(max_rounds=3), _FakeModel("text")):
            pass
        ctx.add_user_message("third")
        async for _ in adapter.run_streaming(ctx, _config(max_rounds=1), _FakeModel("text")):
            pass

        sys_msgs = [m for m in ctx.get_messages() if m.role == "system"]
        assert len(sys_msgs) == 1

    @pytest.mark.asyncio
    async def test_repeated_run_events_does_not_duplicate(self) -> None:
        adapter = _adapter()
        ctx = ConversationContext()
        ctx.add_user_message("hi")

        async for _ in adapter.run_events(ctx, _config(max_rounds=3), _FakeModel("text")):
            pass
        ctx.add_user_message("again")
        async for _ in adapter.run_events(ctx, _config(max_rounds=1), _FakeModel("text")):
            pass

        sys_msgs = [m for m in ctx.get_messages() if m.role == "system"]
        assert len(sys_msgs) == 1
