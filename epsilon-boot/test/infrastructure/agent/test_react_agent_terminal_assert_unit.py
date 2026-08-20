"""``Terminal_Round_Boundary_Assert`` 单元测试模块。

覆盖 PR-4 任务 4.12 / Property 6：

(a) ``terminal_round=0`` 边界（``run_streaming`` / ``run_events`` 设置
    ``terminal_round=config.max_rounds - 1`` 且 ``max_rounds=1`` 实际不进入
    ``_iter_rounds`` 主循环）→ ``last_response is None`` 分支直接 return。
(b) 正常 ``max_rounds`` 命中 → assert 通过 + ``terminated_reason="max_rounds"``
    + ``Max_Rounds_Termination_Warning`` 仅 1 条。
(c) 故意构造"最后一轮 tool_calls 但 caller 不执行工具回写"的人工测试场景：
    assert 抛 ``AssertionError``。
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


class _FakeContextBuilder:
    async def build(self, messages, **kwargs) -> ContextBuilderResult:
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


def _config(max_rounds: int) -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        model="m",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _adapter() -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


def _tool_response() -> LLMResponse:
    return LLMResponse(
        content="",
        model="m",
        usage={"total_tokens": 1},
        tool_calls=[ToolCallRequest(id="c1", name="search", arguments='{"q":"x"}')],
    )


# ── (a) terminal_round=0 边界 ──


@pytest.mark.asyncio
async def test_terminal_round_zero_returns_without_assert_error() -> None:
    """``terminal_round=0`` 时直接 return，不触发 assert。"""
    adapter = _adapter()
    context = ConversationContext()
    model = AsyncMock()
    install_stream_mock(model, [])

    # 直接消费 _iter_rounds，传 terminal_round=0
    outcomes = []
    async for outcome in adapter._iter_rounds(
        context, _config(max_rounds=1), model, terminal_round=0
    ):
        outcomes.append(outcome)

    assert outcomes == []  # 0 outcome 产出


# ── (b) 正常 max_rounds 命中 ──


@pytest.mark.asyncio
async def test_normal_max_rounds_hit_passes_assert(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _adapter()
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(), _tool_response()])

    context = ConversationContext()
    with caplog.at_level(logging.WARNING, logger="infrastructure.agent.react_agent_adapter"):
        result = await adapter.run(context, _config(max_rounds=2), model)

    assert result.terminated_reason == "max_rounds"
    # Max_Rounds_Termination_Warning 仅 1 条
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "达到 max_rounds" in r.getMessage()
    ]
    assert len(warnings) == 1


# ── (c) 故意破坏不变量：assert 抛 AssertionError ──


@pytest.mark.asyncio
async def test_assertion_error_when_caller_skips_tool_writeback() -> None:
    """直接驱动 ``_iter_rounds`` 但跳过工具回写：assert 应抛 AssertionError。"""
    adapter = _adapter()
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(), _tool_response()])

    context = ConversationContext()
    gen = adapter._iter_rounds(context, _config(max_rounds=2), model)

    outcomes = []
    with pytest.raises(AssertionError):
        async for outcome in gen:
            outcomes.append(outcome)
            # 故意不调用 ``context.add_tool_result``，直接继续推进；
            # 当循环耗尽分支被触发时 assert 期望 ``messages[-1]`` 为
            # ToolMessage，但本测试故意未追加，于是 assert 失败。
