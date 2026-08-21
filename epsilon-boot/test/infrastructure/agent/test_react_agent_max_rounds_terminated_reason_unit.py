"""``max_rounds`` 命中四入口 ``terminated_reason`` 透传单元测试模块。

覆盖需求 8.1-8.11, NFR-1, NFR-2, NFR-7, Property 4：

- (a) ``run``：``max_rounds=2`` 且第 2 轮模型仍返回 tool_calls + 工具被执行：
  断言 ``chat.call_count == 2``、``stream.call_count == 0``、
  ``AgentResult.terminated_reason == "max_rounds"``、``AgentResult.content == ""``、
  ``AgentResult.status == "completed"``。
- (b) caplog 验证 1 条 ``Max_Rounds_Termination_Warning``。
- (c) ``run_streaming``：``max_rounds=2`` 中间 1 轮 tool_calls 命中循环耗尽：
  断言 ``chat.call_count == 1``、``stream.call_count == 0``、最后一个
  ``StreamingChunk.finished == True`` 且 ``metadata["terminated_reason"] == "max_rounds"``。
- (d) ``run_events``：同 (c)。
- (e) 边界：最后一轮 ``kind == "text"`` → ``terminated_reason == "completed"``。
- (f) 边界：最后一轮 ``kind == "approval"`` →
  ``AgentResult.status == "approval_required"``、``terminated_reason == "completed"``。
- (g) ``resume``：从 ``interrupt.round_num + 1`` 起跑且循环耗尽 →
  ``AgentResult.terminated_reason == "max_rounds"``。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    AgentStreamEvent,
    ApprovalInterrupt,
    ApprovalPolicy,
)
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

# ── 测试辅助 ──


class _FakeContextBuilder:
    """测试 fake: 原样透传领域消息列表、空 usage。"""

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


def _response_to_chunks(response: LLMResponse) -> list[StreamingChunk]:
    """把 ``LLMResponse`` 等价转换为 v3 ``stream(...)`` 分片序列。

    用于把 v2 ``chat()`` mock 的响应语义等价改写为 v3 ``stream()`` mock，
    使 ``_RoundStreamAccumulator.consume + build_response()`` 重组结果与
    原 ``chat()`` 返回值一致（NFR-3）。
    """
    from domain.model_access.value_objects import StreamingToolCallDelta

    chunks: list[StreamingChunk] = []
    if response.content:
        chunks.append(StreamingChunk(delta_content=response.content, finished=False))
    if response.tool_calls:
        full = [
            StreamingToolCallDelta(
                index=i,
                id=tc.id,
                name=tc.name,
                arguments_delta=tc.arguments,
            )
            for i, tc in enumerate(response.tool_calls)
        ]
        chunks.append(
            StreamingChunk(
                delta_content="",
                finished=True,
                usage=response.usage,
                tool_calls=full,
            )
        )
    else:
        chunks.append(StreamingChunk(delta_content="", finished=True, usage=response.usage))
    return chunks


class _FakeModel:
    """v3：ReAct 内部全程 ``stream``，按 ``chat_responses`` 队列顺序产出
    等价的分片序列。``chat()`` 在 v3 ReAct 路径上不再被调用；保留方法是为
    了与 ``ModelAccessPort`` 契约对齐（非 ReAct 业务仍可能调用）。

    断言适配（NFR-3）：原 v2 断言中的 ``chat_call_count == N`` 改为
    ``stream_call_count == N``，因为 ReAct 路径的所有模型调用现在都走
    ``stream``。"""

    def __init__(
        self,
        chat_responses: list[LLMResponse],
        stream_chunks: list[StreamingChunk] | None = None,
    ) -> None:
        self._chat_responses = list(chat_responses)
        self._explicit_stream_chunks = list(stream_chunks) if stream_chunks is not None else None
        self.chat_call_count = 0
        self.stream_call_count = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:
        self.chat_call_count += 1
        return self._chat_responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        self.stream_call_count += 1
        if self._explicit_stream_chunks is not None:
            for chunk in self._explicit_stream_chunks:
                yield chunk
            return
        if not self._chat_responses:
            yield StreamingChunk(delta_content="", finished=True, usage={})
            return
        response = self._chat_responses.pop(0)
        for chunk in _response_to_chunks(response):
            yield chunk

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return 0


class _TerminationLogRecord(logging.LogRecord):
    round_num: int
    tool_call_count: int


def _config(max_rounds: int = 2) -> AgentConfig:
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "search", "parameters": {}}},
        ],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _adapter(approval_interrupt: bool = False) -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result ok"))

    if approval_interrupt:
        policy_port = MagicMock()
        policy_port.policy_for = MagicMock(
            return_value=ApprovalPolicy(
                tool_name="search",
                interrupt=True,
                allowed_decisions=frozenset({"approve", "reject"}),
                risk_label="测试风险",
            )
        )
        store = MagicMock()
        store.save = AsyncMock(return_value=None)
        return ReActAgentAdapter(
            tool_registry=tool_registry,
            context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
            approval_policy=policy_port,
            approval_store=store,
        )

    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


def _tool_calls_response(round_idx: int = 0) -> LLMResponse:
    """构造含 tool_calls 的 LLM 响应。"""
    return LLMResponse(
        content="",
        model="test-model",
        tool_calls=[
            ToolCallRequest(id=f"call-{round_idx}", name="search", arguments='{"q":"x"}'),
        ],
        usage={"total_tokens": 1},
    )


def _text_response(content: str = "done") -> LLMResponse:
    """构造纯文本 LLM 响应。"""
    return LLMResponse(
        content=content,
        model="test-model",
        tool_calls=[],
        usage={"total_tokens": 2},
    )


# ── (a) run 入口 ──


@pytest.mark.asyncio
async def test_run_max_rounds_hit_terminated_reason() -> None:
    """``run``: max_rounds=2 且两轮都返回 tool_calls → terminated_reason="max_rounds"。"""
    adapter = _adapter()
    model = _FakeModel(chat_responses=[_tool_calls_response(0), _tool_calls_response(1)])
    context = ConversationContext()

    result = await adapter.run(context, _config(max_rounds=2), model)

    # v3：ReAct 内部全程 stream。run 入口 max_rounds=2 命中时累计 2 次 stream
    # 调用（中间 2 轮均为 tool_calls，无最后一轮 _stream_final_round）。
    assert model.chat_call_count == 0
    assert model.stream_call_count == 2
    assert result.terminated_reason == "max_rounds"
    assert result.content == ""
    assert result.status == "completed"


# ── (b) caplog warning ──


@pytest.mark.asyncio
async def test_run_max_rounds_hit_emits_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``run``: max_rounds 命中时记录 Max_Rounds_Termination_Warning。"""
    adapter = _adapter()
    model = _FakeModel(chat_responses=[_tool_calls_response(0), _tool_calls_response(1)])
    context = ConversationContext()

    with caplog.at_level(logging.WARNING):
        await adapter.run(context, _config(max_rounds=2), model)

    warnings = [
        cast(_TerminationLogRecord, record)
        for record in caplog.records
        if "达到 max_rounds 仍存在未消费 tool_calls" in record.message
    ]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.round_num == 2
    assert record.tool_call_count == 1
    # NFR-7: 不记录 tool_call.arguments 完整文本
    assert '{"q":"x"}' not in record.message
    assert '{"q":"x"}' not in str(getattr(record, "msg", ""))


# ── (c) run_streaming ──


@pytest.mark.asyncio
async def test_run_streaming_max_rounds_hit_skips_stream() -> None:
    """``run_streaming``: max_rounds=2 命中时 stream 被跳过。"""
    adapter = _adapter()
    # terminal_round = max_rounds - 1 = 1, 1 轮 chat 返回 tool_calls
    model = _FakeModel(chat_responses=[_tool_calls_response(0)])
    context = ConversationContext()

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=2), model):
        chunks.append(chunk)

    # v3：max_rounds=2 时 terminal_round=1，仅 1 轮 _iter_rounds stream，
    # 命中 max_rounds 时跳过 _stream_final_round → 累计 stream 调用 1 次。
    assert model.chat_call_count == 0
    assert model.stream_call_count == 1
    finished = [c for c in chunks if c.finished]
    assert len(finished) == 1
    assert finished[0].metadata.get("terminated_reason") == "max_rounds"
    assert finished[0].delta_content == ""


# ── (d) run_events ──


@pytest.mark.asyncio
async def test_run_events_max_rounds_hit_skips_stream() -> None:
    """``run_events``: max_rounds=2 命中时 stream 被跳过。"""
    adapter = _adapter()
    model = _FakeModel(chat_responses=[_tool_calls_response(0)])
    context = ConversationContext()

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, _config(max_rounds=2), model):
        events.append(ev)

    # v3：同 run_streaming，max_rounds=2 命中跳过最后一轮 stream。
    assert model.chat_call_count == 0
    assert model.stream_call_count == 1
    done_events = [e for e in events if e.kind == "assistant_done"]
    assert len(done_events) == 1
    assert done_events[0].metadata.get("terminated_reason") == "max_rounds"


# ── (e) 边界: text kind → terminated_reason == "completed" ──


@pytest.mark.asyncio
async def test_run_text_kind_terminated_reason_completed() -> None:
    """最后一轮 text kind → terminated_reason == "completed"，无 warning。"""
    adapter = _adapter()
    model = _FakeModel(chat_responses=[_tool_calls_response(0), _text_response("ok")])
    context = ConversationContext()

    result = await adapter.run(context, _config(max_rounds=2), model)

    assert result.terminated_reason == "completed"
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_run_text_kind_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """最后一轮 text kind 不触发 Max_Rounds_Termination_Warning。"""
    adapter = _adapter()
    model = _FakeModel(chat_responses=[_tool_calls_response(0), _text_response("ok")])
    context = ConversationContext()

    with caplog.at_level(logging.WARNING):
        await adapter.run(context, _config(max_rounds=2), model)

    warnings = [r for r in caplog.records if "达到 max_rounds 仍存在未消费 tool_calls" in r.message]
    assert len(warnings) == 0


# ── (f) 边界: approval kind → status="approval_required", terminated_reason="completed" ──


@pytest.mark.asyncio
async def test_run_approval_kind_terminated_reason_completed() -> None:
    """approval kind → status="approval_required", terminated_reason="completed"。"""
    adapter = _adapter(approval_interrupt=True)
    model = _FakeModel(chat_responses=[_tool_calls_response(0)])
    context = ConversationContext()
    context.session_id = "sess-test"

    result = await adapter.run(context, _config(max_rounds=2), model)

    assert result.status == "approval_required"
    assert result.terminated_reason == "completed"


@pytest.mark.asyncio
async def test_run_approval_kind_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """approval kind 不触发 Max_Rounds_Termination_Warning。"""
    adapter = _adapter(approval_interrupt=True)
    model = _FakeModel(chat_responses=[_tool_calls_response(0)])
    context = ConversationContext()
    context.session_id = "sess-test"

    with caplog.at_level(logging.WARNING):
        await adapter.run(context, _config(max_rounds=2), model)

    warnings = [r for r in caplog.records if "达到 max_rounds 仍存在未消费 tool_calls" in r.message]
    assert len(warnings) == 0


# ── (g) resume 入口 ──


@pytest.mark.asyncio
async def test_resume_max_rounds_hit_terminated_reason() -> None:
    """``resume``: 循环耗尽时 → terminated_reason="max_rounds"。"""
    adapter = _adapter()
    # resume 从 round_num + 1 = 2 开始, max_rounds=2, 1 轮 chat 返回 tool_calls
    model = _FakeModel(chat_responses=[_tool_calls_response(1)])
    context = ConversationContext()
    # 模拟中断前有一条 assistant + tool 消息
    context.add_assistant_message_with_tool_calls(
        content="", tool_calls=[ToolCallRequest(id="call-0", name="search", arguments="{}")]
    )
    context.add_tool_result(tool_name="search", result="ok", tool_call_id="call-0")

    interrupt = ApprovalInterrupt(
        session_id="sess-1",
        approval_id="appr-1",
        actions=(),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="test-model",
        usage_so_far={"total_tokens": 1},
    )

    result = await adapter.resume(context, _config(max_rounds=2), model, interrupt, decisions=())

    assert result.terminated_reason == "max_rounds"
    assert result.status == "completed"
    # v3：resume 从 round 2 起跑，max_rounds=2 命中 → 1 次 stream（中间轮，
    # 跳过最后一轮 _stream_final_round）。
    assert model.chat_call_count == 0
    assert model.stream_call_count == 1
