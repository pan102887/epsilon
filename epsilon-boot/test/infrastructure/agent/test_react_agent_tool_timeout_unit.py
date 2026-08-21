"""``_execute_tool_call`` 工具超时单元测试模块。

覆盖 PR-3 任务 3.9：

(a) ``tool_timeout_seconds=None`` + ``tool.timeout_seconds=None``：不引入
    ``wait_for``，慢工具正常完成；
(b) 全局 ``0.1`` + 慢工具：触发 ``TimeoutError`` → ``is_error=True``
    + ``ToolMessage.metadata["error"] == True`` + 内容为
    ``"工具执行超时（0.1s)"`` + ``_log_tool_failure`` warning ``reason="timeout"``；
(c) per-tool override：全局 ``5.0`` / 工具 ``0.1`` / sleep ``1.0``：用工具级值；
(d) per-tool override：全局 ``0.1`` / 工具 ``5.0`` / sleep ``1.0``：不超时；
(e) 超时**不**触发 ``ApprovalInterrupt``；
(f) 超时日志不记录 ``tool_call.arguments`` 完整文本（NFR-4）；
(g) ``run_events`` 中间轮次工具超时产出 ``kind="tool_error"`` 事件。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from domain.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import AgentConfig, AgentStreamEvent
from domain.chat.context import BaseMessage, ConversationContext, ToolMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    LLMResponse,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

# ── Fakes ──


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


class _SlowTool(Tool):
    """睡眠固定时间的工具；可选覆盖 ``timeout_seconds``。"""

    def __init__(self, name: str, sleep_seconds: float, timeout: float | None) -> None:
        self._name = name
        self._sleep = sleep_seconds
        self._timeout = timeout
        self.executed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "slow tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def timeout_seconds(self) -> float | None:
        return self._timeout

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        await asyncio.sleep(self._sleep)
        self.executed = True
        return ToolExecutionResult(content="ok")


def _config(
    *,
    tool_timeout_seconds: float | None = None,
    allowed: set[str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[
            {"type": "function", "function": {"name": "slow", "parameters": {}}},
        ],
        model="m",
        max_rounds=2,
        prompt_id="chat-default@v1",
        allowed_tool_names=frozenset(allowed or {"slow"}),
        tool_timeout_seconds=tool_timeout_seconds,
    )


def _adapter_with(tool: Tool) -> ReActAgentAdapter:
    registry = ToolRegistry()
    registry.register(tool)
    return ReActAgentAdapter(
        tool_registry=registry,
        context_builder=_FakeContextBuilder(),
    )


def _tool_call() -> ToolCallRequest:
    return ToolCallRequest(id="call-1", name="slow", arguments='{"k":"v","secret":"sk-LEAK"}')


# ── (a) 不超时 ──


@pytest.mark.asyncio
async def test_no_timeout_when_both_unset() -> None:
    tool = _SlowTool("slow", sleep_seconds=0.05, timeout=None)
    adapter = _adapter_with(tool)
    cfg = _config(tool_timeout_seconds=None)
    ctx = ConversationContext()

    result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

    assert is_error is False
    assert result.content == "ok"
    assert tool.executed is True


# ── (b) 全局超时触发 ──


@pytest.mark.asyncio
async def test_global_timeout_triggers_timeout_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tool = _SlowTool("slow", sleep_seconds=1.0, timeout=None)
    adapter = _adapter_with(tool)
    cfg = _config(tool_timeout_seconds=0.1)
    ctx = ConversationContext()

    with caplog.at_level(logging.WARNING, logger="infrastructure.agent.react_agent_adapter"):
        result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

    assert is_error is True
    assert result.content == "工具执行超时（0.1s)"

    # ToolMessage 失败标记持久化
    msgs = ctx.get_messages()
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].metadata.get("error") is True

    # warning 日志
    timeout_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "reason=timeout" in r.getMessage()
    ]
    assert len(timeout_warnings) >= 1
    msg = timeout_warnings[0].getMessage()
    assert "tool_name=slow" in msg
    assert "tool_call_id=call-1" in msg
    assert "TimeoutError" in msg
    # NFR-4：不得记录 arguments 完整文本
    assert "sk-LEAK" not in msg


# ── (c) per-tool override（覆盖全局更长值）──


@pytest.mark.asyncio
async def test_per_tool_override_triggers_timeout() -> None:
    tool = _SlowTool("slow", sleep_seconds=1.0, timeout=0.1)
    adapter = _adapter_with(tool)
    # 全局 5s，但工具级 0.1s → 用工具级值
    cfg = _config(tool_timeout_seconds=5.0)
    ctx = ConversationContext()

    result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

    assert is_error is True
    # 内容携带工具级超时值（0.1s），而非全局 5.0s
    assert result.content == "工具执行超时（0.1s)"


# ── (d) per-tool override 优先于全局更短值 ──


@pytest.mark.asyncio
async def test_per_tool_override_disables_short_global() -> None:
    tool = _SlowTool("slow", sleep_seconds=0.2, timeout=5.0)
    adapter = _adapter_with(tool)
    # 全局 0.1s，但工具级 5.0s → 用工具级值，不超时
    cfg = _config(tool_timeout_seconds=0.1)
    ctx = ConversationContext()

    result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

    assert is_error is False
    assert result.content == "ok"


# ── (e) 超时不触发 ApprovalInterrupt ──


@pytest.mark.asyncio
async def test_timeout_does_not_trigger_approval_interrupt() -> None:
    """工具执行 timeout 不应触发 ``ApprovalInterrupt``；通过 run 路径验证。"""
    tool = _SlowTool("slow", sleep_seconds=1.0, timeout=None)
    adapter = _adapter_with(tool)
    cfg = _config(tool_timeout_seconds=0.1)

    # ReAct 流程：第 1 轮 stream 返回 tool_calls → _execute_tool_call 触发超时
    # → ToolMessage 回灌；第 2 轮 stream 返回 text 终止。
    from unittest.mock import AsyncMock

    from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

    model_access = AsyncMock()
    install_stream_mock(
        model_access,
        [
            LLMResponse(
                content="",
                model="m",
                usage={},
                tool_calls=[_tool_call()],
            ),
            LLMResponse(content="done", model="m", usage={}, tool_calls=[]),
        ],
    )

    context = ConversationContext()
    context.add_user_message("go")

    result = await adapter.run(context, cfg, model_access)

    # status 仍为 completed，不进入 approval_required
    assert result.status == "completed"


# ── (f) 已包含在 (b) 中 ──


# ── (g) run_events 中间轮次超时产出 tool_error ──


@pytest.mark.asyncio
async def test_run_events_intermediate_round_timeout_emits_tool_error() -> None:
    tool = _SlowTool("slow", sleep_seconds=1.0, timeout=None)
    adapter = _adapter_with(tool)
    cfg = _config(tool_timeout_seconds=0.1)

    from unittest.mock import AsyncMock

    from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

    model_access = AsyncMock()
    install_stream_mock(
        model_access,
        [
            LLMResponse(
                content="",
                model="m",
                usage={},
                tool_calls=[_tool_call()],
            ),
            LLMResponse(content="done", model="m", usage={}, tool_calls=[]),
        ],
    )

    context = ConversationContext()
    context.add_user_message("go")

    events: list[AgentStreamEvent] = []
    async for ev in adapter.run_events(context, cfg, model_access):
        events.append(ev)

    tool_error_events = [e for e in events if e.kind == "tool_error"]
    assert len(tool_error_events) == 1
    assert "工具执行超时" in tool_error_events[0].content
