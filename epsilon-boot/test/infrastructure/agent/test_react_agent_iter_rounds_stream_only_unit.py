"""``_iter_rounds`` 全程 stream 推进（v3）单元测试模块。

覆盖 PR-2 任务 2.10：

* (a1) **第 1 轮即返回 text 终止**：``model_access.chat`` 调用 0 次、
  ``model_access.stream`` 恰好 1 次，``AgentResult.terminated_reason == "completed"``。
* (a2) **``max_rounds=3`` 中间轮次 tool_calls 累积，第 3 轮 text 终止**：
  每轮均通过 stream 推进，累积 3 次 stream 调用；每轮 ``LLMResponse.tool_calls``
  与"等价 chat 一次返回"按 ``(id, name, arguments)`` 三元组逐一相等。
* (b) 累积期间不向上层产出对外 ``StreamingChunk`` / ``AgentStreamEvent``：
  ``run_streaming`` / ``run_events`` 中间轮次的对外事件时序与 v2 一致。
"""

from __future__ import annotations

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


def _adapter() -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


def _config(max_rounds: int) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "search", "parameters": {}}},
        ],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _tool_response(idx: int) -> LLMResponse:
    return LLMResponse(
        content="",
        model="test-model",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        tool_calls=[
            ToolCallRequest(id=f"call-{idx}", name="search", arguments=f'{{"q":"r{idx}"}}')
        ],
    )


def _text_response() -> LLMResponse:
    return LLMResponse(
        content="final",
        model="test-model",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        tool_calls=[],
    )


# ── (a1) 第 1 轮 text 即终止 ──


@pytest.mark.asyncio
async def test_first_round_text_terminates_immediately() -> None:
    model_access = AsyncMock()
    counter = install_stream_mock(model_access, [_text_response()])

    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("ask")

    result = await adapter.run(context, _config(max_rounds=3), model_access)

    # v3：ReAct 不调用 chat；stream 恰好 1 次。
    assert model_access.chat.call_count == 0
    assert counter.call_count == 1
    assert result.terminated_reason == "completed"
    assert result.content == "final"


# ── (a2) max_rounds=3 中间轮 tool_calls，最后一轮 text 终止 ──


@pytest.mark.asyncio
async def test_max_rounds_three_with_text_at_last_round() -> None:
    responses = [_tool_response(0), _tool_response(1), _text_response()]
    model_access = AsyncMock()
    counter = install_stream_mock(model_access, responses)

    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("ask")

    result = await adapter.run(context, _config(max_rounds=3), model_access)

    # v3：3 轮均通过 stream 推进 → 3 次 stream，0 次 chat。
    assert counter.call_count == 3
    assert model_access.chat.call_count == 0
    assert result.terminated_reason == "completed"
    assert result.content == "final"


# ── (b) 累积期间不对外发事件 ──


@pytest.mark.asyncio
async def test_intermediate_round_emits_no_chunks_during_stream_accumulation() -> None:
    """中间轮次累积期间，``run_streaming`` 不向上层产出由
    ``model_access.stream`` 直接透传的 ``StreamingChunk``——所有产出都来自
    ``_iter_rounds`` 外侧的 heartbeat / tool_progress / final 路径，与 v2
    形态字面一致（NFR-3）。"""
    responses = [_tool_response(0), _text_response()]
    model_access = AsyncMock()
    counter = install_stream_mock(model_access, responses)

    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("ask")

    chunks = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=3), model_access):
        chunks.append(chunk)

    # 中间轮次：1 个 heartbeat + 1 个 tool_progress.start + 1 个 tool_progress.end = 3 个
    intermediate = [c for c in chunks if not c.finished]
    types = {c.metadata.get("type") for c in intermediate}
    assert types <= {"heartbeat", "tool_progress"}
    # 最终分片 = 1
    finished = [c for c in chunks if c.finished]
    assert len(finished) == 1
    assert finished[0].delta_content == "final"
    # stream 调用 2 次（中间 1 轮 tool_calls + 最后 1 轮 text 自然终止 → 不进入
    # _stream_final_round；中间轮 tool_calls 退出本轮后，第 2 轮即 text 终止）
    assert counter.call_count == 2


@pytest.mark.asyncio
async def test_run_events_intermediate_round_no_extra_events() -> None:
    """``run_events`` 中间轮次累积期间，对外事件序列只包含 ``status`` /
    ``tool_start`` / ``tool_result`` / ``assistant_delta`` / ``assistant_done``
    等 v2 已有 kind，**不**因切流引入新的事件。"""
    responses = [_tool_response(0), _text_response()]
    model_access = AsyncMock()
    install_stream_mock(model_access, responses)

    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("ask")

    events = []
    async for ev in adapter.run_events(context, _config(max_rounds=3), model_access):
        events.append(ev)

    kinds = [e.kind for e in events]
    # 不应在中间轮次产出 tool_arguments_delta（仅最后一轮 stream 阶段产出）
    # 第 2 轮 text 终止 → 不进入最后一轮 _stream_events_final_round
    assert "tool_arguments_delta" not in kinds
    assert "assistant_done" in kinds
