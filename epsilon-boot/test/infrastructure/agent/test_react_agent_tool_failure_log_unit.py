"""ReActAgentAdapter 工具失败 warning 日志单元测试模块。

验证 ReActAgentAdapter 在工具调用失败时按 ``Tool_Failure_Log`` 规范输出
warning 级日志，覆盖以下两种触发路径：

- 工具内部抛出运行期异常（``except Exception``）：
  ``reason="execution_error"``、``exc_type=ValueError`` 等；
- ``_ensure_tool_authorized`` 抛出 ``ToolPermissionDeniedError``：
  ``reason="permission_denied"``。

同时断言日志**不含** ``tool_call.arguments`` 完整文本，避免泄露密钥或大文本。

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest

from domain.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import AgentConfig
from domain.chat.context import BaseMessage, ConversationContext, ToolMessage, UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

_SECRET_ARGUMENTS = '{"path": "/etc/secret.txt", "api_key": "sk-DO-NOT-LEAK"}'


class _BoomTool(Tool):
    """测试用工具，永远抛出 ``ValueError("boom")``。"""

    @property
    def name(self) -> str:
        return "boom_tool"

    @property
    def description(self) -> str:
        return "always fails"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        }

    async def execute(self, **kwargs: object) -> ToolExecutionResult:
        raise ValueError("boom")


class _FakeContextBuilder:
    """测试用上下文构建器，原样回传单条 user 消息。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del messages, model_access, model
        return ContextBuilderResult(
            messages=[UserMessage(content="go")],
            usage={},
        )


class _FakeModel:
    """顺序返回 LLMResponse 的模型 fake，仅用于本测试。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)

    async def chat(self, request: ChatRequest) -> LLMResponse:
        return self.responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        if self.responses:
            response = self.responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        yield StreamingChunk(delta_content="done", finished=True)


def _config(allowed: frozenset[str]) -> AgentConfig:
    """构造允许工具集合受控的 ``AgentConfig``。"""
    return AgentConfig(
        system_prompt="你是测试助手",
        tool_schemas=[{"type": "function", "function": {"name": "boom_tool"}}],
        model="gpt-test",
        max_rounds=4,
        prompt_id="chat-default@v1",
        allowed_tool_names=allowed,
    )


@pytest.mark.asyncio
async def test_tool_internal_exception_emits_warning_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """工具内部异常 → 至少一条 WARNING 日志，含 tool_name / tool_call_id /
    异常类名（``ToolExecutionError``，由 ``ToolRegistry`` 将 ``ValueError``
    包装而来），不含 arguments 完整文本。

    覆盖 5.1 / 5.2 / 5.3 / 5.5 / 5.6。
    """
    registry = ToolRegistry()
    registry.register(_BoomTool())
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )

    tool_call = ToolCallRequest(
        id="call-boom-1",
        name="boom_tool",
        arguments=_SECRET_ARGUMENTS,
    )
    responses = [
        LLMResponse(
            content="",
            model="gpt-test",
            usage={"total_tokens": 1},
            tool_calls=[tool_call],
        ),
        LLMResponse(
            content="工具失败，抱歉",
            model="gpt-test",
            usage={"total_tokens": 2},
            tool_calls=[],
        ),
    ]

    context = ConversationContext()
    context.add_user_message("go")

    with caplog.at_level(
        logging.WARNING,
        logger="infrastructure.agent.react_agent_adapter",
    ):
        result = await adapter.run(
            context,
            _config(frozenset({"boom_tool"})),
            _FakeModel(responses),  # type: ignore[arg-type]
        )

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "infrastructure.agent.react_agent_adapter"
    ]
    assert warnings, "工具内部异常应至少触发一条 WARNING 日志"

    msg = warnings[0].getMessage()
    assert "boom_tool" in msg, f"日志应包含 tool_name=boom_tool: {msg}"
    assert "call-boom-1" in msg, f"日志应包含 tool_call_id=call-boom-1: {msg}"
    # ``ToolRegistry`` 将 ``ValueError`` 统一包装为 ``ToolExecutionError``
    # 后再抛给适配器；本测试断言 _log_tool_failure 记录了底层异常类名。
    assert "ToolExecutionError" in msg, f"日志应包含 exc_type=ToolExecutionError: {msg}"
    # 失败语义标签应为 execution_error
    assert "execution_error" in msg, f"日志应携带 reason=execution_error: {msg}"
    # 关键：不得记录 arguments 完整文本（含密钥片段）
    assert "sk-DO-NOT-LEAK" not in msg, f"日志泄露了 arguments 中的密钥片段: {msg}"
    assert "/etc/secret.txt" not in msg, f"日志泄露了 arguments 中的路径片段: {msg}"

    # 同时验证现有"将 str(exc) 作为 ToolMessage 回灌"的语义不变（5.5）
    assert result.status == "completed"
    tool_messages = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    assert any(m.content == "boom" for m in tool_messages), (
        "ToolMessage 内容应仍然是 str(exc)='boom'"
    )


@pytest.mark.asyncio
async def test_tool_permission_denied_emits_permission_denied_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``ToolPermissionDeniedError`` → WARNING 且 ``reason="permission_denied"``，
    包含 tool_name / tool_call_id / exc_type=ToolPermissionDeniedError。

    覆盖 5.4 / 5.6。
    """
    registry = ToolRegistry()
    # 注册 boom_tool 仅是为了让 registry 非空；权限拒绝发生在 _ensure_tool_authorized
    # 阶段，所以工具实际不会被执行。
    registry.register(_BoomTool())
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )

    # 模型尝试调用 unauthorized_tool，但 allowed_tool_names 仅含 boom_tool
    unauthorized_call = ToolCallRequest(
        id="call-denied-1",
        name="unauthorized_tool",
        arguments=_SECRET_ARGUMENTS,
    )
    responses = [
        LLMResponse(
            content="",
            model="gpt-test",
            usage={"total_tokens": 1},
            tool_calls=[unauthorized_call],
        ),
        LLMResponse(
            content="该工具不可用",
            model="gpt-test",
            usage={"total_tokens": 2},
            tool_calls=[],
        ),
    ]

    context = ConversationContext()
    context.add_user_message("go")

    with caplog.at_level(
        logging.WARNING,
        logger="infrastructure.agent.react_agent_adapter",
    ):
        await adapter.run(
            context,
            _config(frozenset({"boom_tool"})),
            _FakeModel(responses),  # type: ignore[arg-type]
        )

    # _execute_tool_call 路径下的 _log_tool_failure 调用应至少出现一次。
    # _collect_pending_actions 还会单独输出一条"工具调用被拒绝"warning，
    # 但本断言关注的是 _log_tool_failure 输出（含结构化字段）。
    failure_logs = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "permission_denied" in r.getMessage()
    ]
    assert failure_logs, "权限拒绝应触发 reason=permission_denied 的 warning 日志"

    msg = failure_logs[0].getMessage()
    assert "unauthorized_tool" in msg, f"日志应包含 tool_name=unauthorized_tool: {msg}"
    assert "call-denied-1" in msg, f"日志应包含 tool_call_id=call-denied-1: {msg}"
    assert "ToolPermissionDeniedError" in msg, (
        f"日志应包含 exc_type=ToolPermissionDeniedError: {msg}"
    )
    # 仍然不得泄露 arguments 完整文本
    assert "sk-DO-NOT-LEAK" not in msg
    assert "/etc/secret.txt" not in msg
