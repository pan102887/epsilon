"""``max_total_tokens`` 预算与 ``terminated_reason="token_budget_exceeded"``
单元测试模块。

覆盖 PR-4 任务 4.11 / Property 5：

(a) ``run``：第 1 轮 tool_calls + usage 已超出 → 工具被执行 +
    ``terminated_reason == "token_budget_exceeded"`` + 仅 1 条
    ``Token_Budget_Exceeded_Warning`` + ``stream`` 调用 1 次（无第 2 轮）；
(b) ``run_streaming``：超限分支跳过 ``_stream_final_round``，最后一片
    ``StreamingChunk.metadata["terminated_reason"] == "token_budget_exceeded"``；
(c) ``run_events``：超限分支最后一个事件为 ``kind="assistant_done"`` 且
    ``metadata["terminated_reason"] == "token_budget_exceeded"``；
(d) text 路径下即使最后一轮 usage 超预算，仍 ``terminated_reason == "completed"``；
(e) approval 路径下不改写为 ``token_budget_exceeded``；
(f) ``max_total_tokens=None`` 行为与 v2 一致；
(g) ``max_total_tokens`` 与 ``max_rounds`` 共存：命中预算优先，两类 warning 互斥；
(h) ``Token_Budget_Computation_Rule``：``total_tokens`` 缺失时回退到
    ``prompt_tokens + completion_tokens``。
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.ports import ApprovalPolicyPort
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    AgentStreamEvent,
    ApprovalPolicy,
)
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import LLMResponse, StreamingChunk, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


class _FakeContextBuilder:
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


def _config(*, max_rounds: int = 3, max_total_tokens: int | None = None) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "search", "parameters": {}}},
        ],
        model="m",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
        max_total_tokens=max_total_tokens,
    )


def _adapter(*, approval: bool = False) -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    if approval:
        policy_port = MagicMock(spec=ApprovalPolicyPort)
        policy_port.policy_for = MagicMock(
            return_value=ApprovalPolicy(
                tool_name="search",
                interrupt=True,
                allowed_decisions=frozenset({"approve", "reject"}),
            )
        )
        store = MagicMock()
        store.save = AsyncMock(return_value=None)
        return ReActAgentAdapter(
            tool_registry=tool_registry,
            context_builder=_FakeContextBuilder(),
            approval_policy=policy_port,
            approval_store=store,
        )

    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),
    )


def _tool_response(tokens_total: int = 100) -> LLMResponse:
    return LLMResponse(
        content="",
        model="m",
        usage={"total_tokens": tokens_total},
        tool_calls=[ToolCallRequest(id="c1", name="search", arguments='{"q":"x"}')],
    )


def _text_response(tokens_total: int = 1) -> LLMResponse:
    return LLMResponse(
        content="done",
        model="m",
        usage={"total_tokens": tokens_total},
        tool_calls=[],
    )


# ── (a) run 入口超限 ──


@pytest.mark.asyncio
async def test_run_token_budget_exceeded_terminates_after_tools(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _adapter()
    model = AsyncMock()
    counter = install_stream_mock(model, [_tool_response(tokens_total=100)])

    context = ConversationContext()
    with caplog.at_level(logging.WARNING, logger="infrastructure.agent.react_agent_adapter"):
        result = await adapter.run(context, _config(max_rounds=3, max_total_tokens=10), model)

    # 第 1 轮 tool_calls 命中预算 → 工具被执行 + 终止
    assert result.terminated_reason == "token_budget_exceeded"
    assert result.status == "completed"
    # 仅 1 次 stream（无第 2 轮）
    assert counter.call_count == 1
    # 仅 1 条 Token_Budget_Exceeded_Warning
    budget_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "max_total_tokens 预算" in r.getMessage()
    ]
    assert len(budget_warnings) == 1
    # max_rounds 互斥（不应有 max_rounds warning）
    max_rounds_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "达到 max_rounds" in r.getMessage()
    ]
    assert len(max_rounds_warnings) == 0


# ── (b) run_streaming 超限分支 ──


@pytest.mark.asyncio
async def test_run_streaming_token_budget_exceeded_skips_final_stream() -> None:
    adapter = _adapter()
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(tokens_total=100)])

    context = ConversationContext()
    chunks: list[StreamingChunk] = []
    async for c in adapter.run_streaming(
        context, _config(max_rounds=3, max_total_tokens=10), model
    ):
        chunks.append(c)

    finished = [c for c in chunks if c.finished]
    assert len(finished) == 1
    assert finished[0].metadata.get("terminated_reason") == "token_budget_exceeded"
    assert finished[0].delta_content == ""


# ── (c) run_events 超限分支 ──


@pytest.mark.asyncio
async def test_run_events_token_budget_exceeded_emits_assistant_done() -> None:
    adapter = _adapter()
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(tokens_total=100)])

    context = ConversationContext()
    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, _config(max_rounds=3, max_total_tokens=10), model):
        events.append(ev)

    done = [e for e in events if e.kind == "assistant_done"]
    assert len(done) == 1
    assert done[0].metadata.get("terminated_reason") == "token_budget_exceeded"


# ── (d) text 路径不改写 ──


@pytest.mark.asyncio
async def test_text_path_keeps_completed_even_when_usage_exceeds_budget() -> None:
    adapter = _adapter()
    model = AsyncMock()
    # 第 1 轮 text + usage 已超预算 → 仍 completed（决策 9）
    install_stream_mock(model, [_text_response(tokens_total=999)])

    context = ConversationContext()
    result = await adapter.run(context, _config(max_rounds=3, max_total_tokens=10), model)

    assert result.terminated_reason == "completed"
    assert result.content == "done"


# ── (e) approval 路径不改写 ──


@pytest.mark.asyncio
async def test_approval_path_keeps_completed_when_usage_exceeds_budget() -> None:
    adapter = _adapter(approval=True)
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(tokens_total=999)])

    context = ConversationContext()
    context.session_id = "s1"
    result = await adapter.run(context, _config(max_rounds=3, max_total_tokens=10), model)

    assert result.status == "approval_required"
    # HITL 中断由 status 表达；terminated_reason 保持 completed（NFR-5）
    assert result.terminated_reason == "completed"


# ── (f) max_total_tokens=None 行为不变 ──


@pytest.mark.asyncio
async def test_no_budget_check_when_max_total_tokens_is_none() -> None:
    adapter = _adapter()
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(tokens_total=999), _text_response(tokens_total=1)])

    context = ConversationContext()
    result = await adapter.run(context, _config(max_rounds=3, max_total_tokens=None), model)

    assert result.terminated_reason == "completed"
    assert result.content == "done"


# ── (g) 共存：预算优先 ──


@pytest.mark.asyncio
async def test_budget_takes_priority_over_max_rounds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _adapter()
    model = AsyncMock()
    install_stream_mock(model, [_tool_response(tokens_total=100)])

    context = ConversationContext()
    with caplog.at_level(logging.WARNING, logger="infrastructure.agent.react_agent_adapter"):
        result = await adapter.run(context, _config(max_rounds=2, max_total_tokens=10), model)

    # 命中预算 → token_budget_exceeded
    assert result.terminated_reason == "token_budget_exceeded"
    # 互斥校验
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    has_budget = any("max_total_tokens 预算" in m for m in msgs)
    has_max_rounds = any("达到 max_rounds" in m for m in msgs)
    assert has_budget
    assert not has_max_rounds


# ── (h) Token_Budget_Computation_Rule 回退 ──


@pytest.mark.asyncio
async def test_token_budget_computation_falls_back_when_total_tokens_missing() -> None:
    adapter = _adapter()
    model = AsyncMock()
    response = LLMResponse(
        content="",
        model="m",
        usage={"prompt_tokens": 50, "completion_tokens": 60},  # 无 total_tokens
        tool_calls=[ToolCallRequest(id="c1", name="search", arguments='{"q":"x"}')],
    )
    install_stream_mock(model, [response])

    context = ConversationContext()
    result = await adapter.run(context, _config(max_rounds=3, max_total_tokens=10), model)

    # 50 + 60 = 110 > 10 → 命中预算
    assert result.terminated_reason == "token_budget_exceeded"


# ── (i) outcome_to_agent_result 自然透传 ──


@pytest.mark.asyncio
async def test_outcome_to_agent_result_natural_pass_through() -> None:
    """``_outcome_to_agent_result`` 直接透传 ``terminated_reason`` 字段。"""
    from domain.agent.agent_loop_policy import RoundOutcome, outcome_to_agent_result

    response = LLMResponse(content="x", model="m", usage={}, tool_calls=[])
    outcome = RoundOutcome(
        kind="final",
        round_num=2,
        response=response,
        total_usage={"total_tokens": 1},
        terminated_reason="token_budget_exceeded",
    )
    result = outcome_to_agent_result(outcome)
    assert result.terminated_reason == "token_budget_exceeded"
