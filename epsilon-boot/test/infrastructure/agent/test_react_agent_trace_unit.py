"""ReActAgentAdapter trace 集成单元测试。"""

from collections.abc import AsyncIterator, Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import ToolExecutionError
from domain.agent.tools import ToolExecutionResult
from domain.agent.trace_value_objects import ErrorTrace, ModelCallTrace, ToolCallTrace
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, UserMessage
from domain.model_access.value_objects import (
    ChatRequest,
    StreamingChunk,
    StreamingToolCallDelta,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


def _make_config(**overrides: Any) -> AgentConfig:
    defaults: dict[str, Any] = {
        "system_prompt": "you are helpful",
        "tool_schemas": [{"type": "function", "function": {"name": "echo", "parameters": {}}}],
        "model": "gpt-4",
        "max_rounds": 3,
        "prompt_id": "test@v1",
        "allowed_tool_names": frozenset(["echo"]),
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_context(session_id: str = "test-session") -> ConversationContext:
    ctx = ConversationContext()
    ctx.session_id = session_id
    ctx.add_user_message("hello")
    return ctx


def _make_tool_registry_mock() -> MagicMock:
    registry = MagicMock()
    registry.get_schemas.return_value = []

    async def _execute(request: ToolCallRequest) -> ToolExecutionResult:
        return ToolExecutionResult(content="tool result")

    registry.execute = AsyncMock(side_effect=_execute)
    return registry


def _make_tool_registry_with_metadata(metadata: Mapping[str, Any]) -> MagicMock:
    """工具注册表 mock：execute 返回携带指定 metadata 的 ToolExecutionResult。"""
    registry = MagicMock()
    registry.get_schemas.return_value = []

    async def _execute(request: ToolCallRequest) -> ToolExecutionResult:
        return ToolExecutionResult(content="tool ok", metadata=dict(metadata))

    registry.execute = AsyncMock(side_effect=_execute)
    return registry


def _make_tool_registry_raising(exc: Exception) -> MagicMock:
    """工具注册表 mock：execute 抛出指定异常（模拟工具执行失败）。"""
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.execute = AsyncMock(side_effect=exc)
    return registry


def _make_model_access_text_response() -> MagicMock:
    """模拟只返回文本的 model_access（不调用工具）。"""
    model_access = MagicMock()

    async def _stream(request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        yield StreamingChunk(
            delta_content="hello",
            finished=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    model_access.stream = _stream
    return model_access


def _make_model_access_tool_then_text() -> MagicMock:
    """模拟先返回 tool_calls，再返回文本。"""
    model_access = MagicMock()
    call_count = {"n": 0}

    async def _stream(request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 第一轮：返回 tool_calls（使用 StreamingToolCallDelta 格式）
            yield StreamingChunk(
                delta_content="",
                finished=True,
                usage={"prompt_tokens": 20, "completion_tokens": 10},
                tool_calls=[
                    StreamingToolCallDelta(
                        index=0, id="tc_1", name="echo", arguments_delta='{"msg":"hi"}'
                    )
                ],
            )
        else:
            # 第二轮：返回文本
            yield StreamingChunk(
                delta_content="done",
                finished=True,
                usage={"prompt_tokens": 30, "completion_tokens": 15},
            )

    model_access.stream = _stream
    return model_access


async def test_trace_records_model_call_after_llm_response():
    """验证 ModelCallTrace 被记录。"""
    trace_store = AsyncMock()
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(
            messages=[UserMessage(content="hi")],
            usage={},
        )
    )

    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_mock(),
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=1)
    model_access = _make_model_access_text_response()

    result = await adapter.run(ctx, config, model_access)
    assert result.content == "hello"

    # 验证 trace_store.append_step 被调用
    assert trace_store.append_step.call_count >= 1
    # 第一次调用应该是 ModelCallTrace
    first_call = trace_store.append_step.call_args_list[0]
    session_id, step = first_call[0]
    assert session_id == "test-session"
    assert isinstance(step, ModelCallTrace)
    assert step.kind == "model_call"


async def test_trace_records_tool_call_with_correct_fields():
    """验证 ToolCallTrace 包含正确的字段。"""
    trace_store = AsyncMock()
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(
            messages=[UserMessage(content="hi")],
            usage={},
        )
    )

    registry = _make_tool_registry_mock()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=3)
    model_access = _make_model_access_tool_then_text()

    result = await adapter.run(ctx, config, model_access)
    assert result.content == "done"

    # 找到 ToolCallTrace 调用
    tool_traces = [
        call[0][1]
        for call in trace_store.append_step.call_args_list
        if isinstance(call[0][1], ToolCallTrace)
    ]
    assert len(tool_traces) == 1
    tt = tool_traces[0]
    assert tt.tool_name == "echo"
    assert tt.tool_call_id == "tc_1"
    assert tt.success is True
    assert tt.latency_ms > 0


async def test_trace_store_none_no_error():
    """trace_store 为 None 时正常运行无异常。"""
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(
            messages=[UserMessage(content="hi")],
            usage={},
        )
    )

    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_mock(),
        context_builder=context_builder,
        trace_store=None,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=1)
    model_access = _make_model_access_text_response()

    result = await adapter.run(ctx, config, model_access)
    assert result.content == "hello"


async def test_trace_store_exception_does_not_affect_agent_result():
    """trace store 异常不影响 Agent 正常返回。"""
    trace_store = AsyncMock()
    trace_store.append_step = AsyncMock(side_effect=RuntimeError("disk full"))

    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(
            messages=[UserMessage(content="hi")],
            usage={},
        )
    )

    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_mock(),
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=1)
    model_access = _make_model_access_text_response()

    # 不应抛异常
    result = await adapter.run(ctx, config, model_access)
    assert result.content == "hello"


# ═══════════════════════════════════════════════════════════════
# T5.3：metadata 透传 / error_class·error_message 填充 / ErrorTrace 补录
# structured-tool-result 需求 7.2 / 7.3 / 7.4，需求 8.1–8.5
# ═══════════════════════════════════════════════════════════════


def _tool_traces(trace_store: AsyncMock) -> list[ToolCallTrace]:
    """从 trace_store.append_step 调用记录中提取 ToolCallTrace 列表。"""
    return [
        call[0][1]
        for call in trace_store.append_step.call_args_list
        if isinstance(call[0][1], ToolCallTrace)
    ]


def _error_traces(trace_store: AsyncMock) -> list[ErrorTrace]:
    """从 trace_store.append_step 调用记录中提取 ErrorTrace 列表。"""
    return [
        call[0][1]
        for call in trace_store.append_step.call_args_list
        if isinstance(call[0][1], ErrorTrace)
    ]


async def test_tool_call_trace_metadata_populated_from_result():
    """成功路径：result.metadata 透传到 ToolCallTrace.metadata（需求 7.2）。"""
    trace_store = AsyncMock()
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(messages=[UserMessage(content="hi")], usage={})
    )

    metadata = {"exit_code": 0, "working_dir": "/", "truncated": False}
    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_with_metadata(metadata),
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=3)
    model_access = _make_model_access_tool_then_text()

    await adapter.run(ctx, config, model_access)

    traces = _tool_traces(trace_store)
    assert len(traces) == 1
    tt = traces[0]
    assert tt.metadata == metadata
    # 成功路径：error_class / error_message 保持 None（需求 7.4）
    assert tt.success is True
    assert tt.error_class is None
    assert tt.error_message is None


async def test_tool_call_trace_error_fields_filled_on_failure():
    """失败路径：error_class 取自 metadata，error_message 取截断 content（需求 7.3）。"""
    trace_store = AsyncMock()
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(messages=[UserMessage(content="hi")], usage={})
    )

    exc = ToolExecutionError(message="tool blew up", tool_name="echo")
    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_raising(exc),
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=3)
    model_access = _make_model_access_tool_then_text()

    await adapter.run(ctx, config, model_access)

    traces = _tool_traces(trace_store)
    assert len(traces) == 1
    tt = traces[0]
    assert tt.success is False
    # 一般异常分支 metadata 填 error_class=type(exc).__name__
    assert tt.error_class == "ToolExecutionError"
    assert tt.metadata.get("error_class") == "ToolExecutionError"
    # error_message 取回灌 content（含异常消息）
    assert tt.error_message is not None
    assert "tool blew up" in tt.error_message


async def test_error_trace_written_on_agent_loop_exception():
    """Agent Loop 级非工具异常（模型调用失败）→ ErrorTrace 被写入并向上传播（需求 8.1/8.2）。"""
    trace_store = AsyncMock()
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(messages=[UserMessage(content="hi")], usage={})
    )

    model_access = MagicMock()

    async def _stream(request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        raise RuntimeError("model provider down")
        yield  # pragma: no cover  # 使函数成为 async generator

    model_access.stream = _stream

    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_mock(),
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=1)

    # 原始异常向上传播，不被吞掉（需求 8.1）
    with pytest.raises(RuntimeError, match="model provider down"):
        await adapter.run(ctx, config, model_access)

    errors = _error_traces(trace_store)
    assert len(errors) >= 1
    et = errors[0]
    assert et.kind == "error"
    assert et.error_class == "RuntimeError"
    assert et.error_message is not None
    assert "model provider down" in et.error_message
    assert et.timestamp_epoch > 0


async def test_error_trace_not_written_for_tool_failure():
    """工具执行失败不走 ErrorTrace，仅通过 ToolCallTrace 记录（需求 8.4）。"""
    trace_store = AsyncMock()
    context_builder = AsyncMock()
    context_builder.build = AsyncMock(
        return_value=MagicMock(messages=[UserMessage(content="hi")], usage={})
    )

    exc = ToolExecutionError(message="oops", tool_name="echo")
    adapter = ReActAgentAdapter(
        tool_registry=_make_tool_registry_raising(exc),
        context_builder=context_builder,
        trace_store=trace_store,
    )

    ctx = _make_context()
    config = _make_config(max_rounds=3)
    model_access = _make_model_access_tool_then_text()

    await adapter.run(ctx, config, model_access)

    # 工具失败通过 ToolCallTrace 记录，不额外产生 ErrorTrace
    assert len(_tool_traces(trace_store)) == 1
    assert _error_traces(trace_store) == []
