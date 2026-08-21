"""``prompt_id`` 在日志 / OTel span 中的传播单元测试。

# Validates: Requirement 7.1, 7.2, 7.5, 7.6 / Property 5

覆盖 Chat（同步路径）与 Task（SUCCESS / FAILED 双分支）三条路径，断言：

1. 日志 ``record.extra``（直接挂在 record 上的属性）中包含 ``prompt_id``
   字段，值与 ``LoadedPrompt.prompt_id`` 一致；
2. 通过 ``InMemorySpanExporter`` 捕获的 OTel span 含属性
   ``prompt.id``，值与 ``LoadedPrompt.prompt_id`` 一致；
3. 负向断言：``caplog.text`` 与所有 span 属性值的 ``str`` 形态都不包含
   ``LoadedPrompt.content`` 的前 5 个字符（保证 prompt 内容不污染日志 /
   trace，满足需求 7.5）。

本文件不引入任何新增第三方依赖：
``InMemorySpanExporter`` 来自既有 ``opentelemetry-sdk``。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from domain.agent.ports import AgentPort
from domain.agent.value_objects import AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatRequestVO, ContextBuilderResult
from domain.prompt.ports import PromptRegistryPort
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.task.task_agent_adapter import TaskAgentAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies

_CHAT_PROMPT = LoadedPrompt(
    prompt_id="chat-default@v3",
    name="chat-default",
    version="v3",
    content="你是一个用于 prompt-id 传播测试的助手。",
)
_TASK_PROMPT = LoadedPrompt(
    prompt_id="task-template@v1",
    name="task-template",
    version="v1",
    content="任务骨架内容（不应出现在日志或 trace 属性中）。",
)


@pytest.fixture
def in_memory_span_exporter() -> Iterator[InMemorySpanExporter]:
    """安装 InMemorySpanExporter 到全局 TracerProvider，返回 exporter 实例。

    OTel 全局 TracerProvider 一旦被设置就不允许再覆盖（首次设置后再
    ``set_tracer_provider`` 会被静默拒绝）。因此本 fixture 采取以下策略：

    - 若全局 provider 尚未配置，先安装一个 SDK ``TracerProvider``；
    - 之后向当前 provider 追加一个挂载在新建 ``InMemorySpanExporter`` 上
      的 ``SimpleSpanProcessor``，确保用例可以独立观测自己的 span；
    - 用例结束后清空 exporter 并关闭对应 processor，避免污染其他用例。
    """
    current_provider = trace.get_tracer_provider()
    if not isinstance(current_provider, TracerProvider):
        current_provider = TracerProvider()
        trace.set_tracer_provider(current_provider)

    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    current_provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        exporter.clear()
        processor.shutdown()


def _assert_prompt_id_in_logs_and_spans(
    caplog: pytest.LogCaptureFixture,
    exporter: InMemorySpanExporter,
    expected_prompt_id: str,
    forbidden_content_prefix: str,
) -> None:
    """复用断言：日志与 span 都含 ``prompt_id``，但都不含 prompt 内容片段。"""
    assert any(
        getattr(record, "prompt_id", None) == expected_prompt_id for record in caplog.records
    ), "至少一条日志记录的 extra.prompt_id 应等于期望值"

    spans = exporter.get_finished_spans()
    assert spans, "应至少捕获一条 span"
    assert any(
        span.attributes is not None
        and span.attributes.get("prompt.id") == expected_prompt_id
        for span in spans
    ), (
        "至少一条 span 的 prompt.id 属性应等于期望值"
    )

    # 负向断言：日志正文与 span 属性值都不应包含 prompt 内容前缀
    assert forbidden_content_prefix not in caplog.text, (
        "日志正文不得泄露 LoadedPrompt.content 的内容前缀"
    )
    for span in spans:
        assert span.attributes is not None
        for attr_value in span.attributes.values():
            assert forbidden_content_prefix not in str(attr_value), (
                "span 属性值不得泄露 LoadedPrompt.content 的内容前缀"
            )


@pytest.mark.asyncio
async def test_chat_service_adapter_propagates_prompt_id_to_logs_and_span(
    caplog: pytest.LogCaptureFixture,
    in_memory_span_exporter: InMemorySpanExporter,
) -> None:
    """ChatServiceAdapter.chat 必须把 ``prompt_id`` 写入 log extra 与 OTel span。"""
    tracer = trace.get_tracer(__name__)

    prompt_registry = MagicMock()
    prompt_registry.get = MagicMock(return_value=_CHAT_PROMPT)

    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()

    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()

    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="hi")],
            usage={},
        )
    )

    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="ok",
            model="test-model",
            usage={},
            latency_ms=1.0,
        )
    )

    tool_schemas: list[dict[str, Any]] = [
        {"type": "function", "function": {"name": "noop", "parameters": {}}}
    ]
    adapter = ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=context_builder,
        agent=agent,
        tool_calling_enabled=True,
        max_tool_rounds=5,
        tool_schemas=tool_schemas,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=_CHAT_PROMPT,
            agent=agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=5,
        ),
    )

    caplog.set_level(logging.INFO, logger="infrastructure.chat.chat_service_adapter")
    with tracer.start_as_current_span("chat-test"):
        await adapter.chat(ChatRequestVO(session_id="s1", message="hi", stream=False))

    _assert_prompt_id_in_logs_and_spans(
        caplog,
        in_memory_span_exporter,
        expected_prompt_id="chat-default@v3",
        forbidden_content_prefix=_CHAT_PROMPT.content[:5],
    )


def _build_task_adapter(
    prompt_registry: PromptRegistryPort,
    agent: AgentPort,
) -> TaskAgentAdapter:
    """构造仅供本测试模块使用的 TaskAgentAdapter 测试 dummy。"""
    tool_registry = MagicMock()
    tool_schemas: list[dict[str, Any]] = [
        {"type": "function", "function": {"name": "noop", "parameters": {}}}
    ]
    tool_registry.get_schemas = MagicMock(return_value=tool_schemas)

    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()

    compaction = MagicMock()
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()

    return TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=compaction,
        session_store=session_store,
        prompt_registry=prompt_registry,
        max_rounds=3,
    )


@pytest.mark.asyncio
async def test_task_agent_adapter_propagates_prompt_id_on_success(
    caplog: pytest.LogCaptureFixture,
    in_memory_span_exporter: InMemorySpanExporter,
) -> None:
    """SUCCESS 分支：execute 必须把 ``prompt_id`` 写入 log extra 与 OTel span。"""
    tracer = trace.get_tracer(__name__)

    prompt_registry = MagicMock()
    prompt_registry.get = MagicMock(return_value=_TASK_PROMPT)
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="完成",
            model="test-model",
            usage={},
            latency_ms=1.0,
        )
    )
    adapter = _build_task_adapter(prompt_registry, agent)

    caplog.set_level(logging.INFO, logger="infrastructure.task.task_agent_adapter")
    with tracer.start_as_current_span("task-success"):
        result = await adapter.execute(Task(goal="计算 1+1"))

    assert result.prompt_id == "task-template@v1"
    _assert_prompt_id_in_logs_and_spans(
        caplog,
        in_memory_span_exporter,
        expected_prompt_id="task-template@v1",
        forbidden_content_prefix=_TASK_PROMPT.content[:5],
    )


@pytest.mark.asyncio
async def test_task_agent_adapter_propagates_prompt_id_on_failure(
    caplog: pytest.LogCaptureFixture,
    in_memory_span_exporter: InMemorySpanExporter,
) -> None:
    """FAILED 分支：agent.run 抛异常后 ``prompt_id`` 仍透传到 log 与 span。"""
    tracer = trace.get_tracer(__name__)

    prompt_registry = MagicMock()
    prompt_registry.get = MagicMock(return_value=_TASK_PROMPT)
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=RuntimeError("boom"))
    adapter = _build_task_adapter(prompt_registry, agent)

    caplog.set_level(logging.INFO, logger="infrastructure.task.task_agent_adapter")
    with tracer.start_as_current_span("task-failure"):
        result = await adapter.execute(Task(goal="触发失败"))

    assert result.prompt_id == "task-template@v1"
    _assert_prompt_id_in_logs_and_spans(
        caplog,
        in_memory_span_exporter,
        expected_prompt_id="task-template@v1",
        forbidden_content_prefix=_TASK_PROMPT.content[:5],
    )
