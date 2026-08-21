"""ReAct 最终轮流式输出协作者。

本模块封装 ReAct 最后一轮 ``model_access.stream(...)`` 到兼容分片与结构化
事件的映射逻辑。它只依赖领域端口和值对象，并通过构造函数接收上下文构建
能力与 usage 合并函数，避免导入或持有 ``ReActAgentAdapter``。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Protocol

from domain.agent.value_objects import AgentConfig, AgentStreamEvent
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ChatRequest, LLMResponse, StreamingChunk
from infrastructure.agent.round_stream_accumulator import (
    RoundStreamAccumulator as _RoundStreamAccumulator,
)

Usage = dict[str, int]
UsageMapping = Mapping[str, object] | None
UsageMerger = Callable[[Usage | None, Usage | None], Usage]


class FinalRoundContextBuilder(Protocol):
    """最终轮流式协作者所需的上下文构建能力。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        """构建一次模型流式调用所需的消息列表与构建阶段 usage。"""
        ...


def _coerce_usage(usage: UsageMapping) -> Usage:
    """把外部传入的 usage 映射收窄为 ``dict[str, int]``。

    Args:
        usage: 可选 usage 映射。值必须为非负整数。

    Returns:
        可交给 usage 合并函数处理的普通字典。

    Raises:
        ValueError: 当 usage 值不是非负整数时抛出。
    """
    if usage is None:
        return {}
    coerced: Usage = {}
    for key, value in usage.items():
        if not isinstance(value, int):
            raise ValueError(f"usage[{key!r}] 必须为 int")
        if value < 0:
            raise ValueError(f"usage[{key!r}] 必须为非负整数")
        coerced[key] = value
    return coerced


def _merge_usage(left: Usage | None, right: Usage | None) -> Usage:
    """合并两个 usage 字典，保持既有按 key 累加语义。"""
    merged: Usage = {}
    for usage in (left, right):
        if usage is None:
            continue
        for key, value in _coerce_usage(usage).items():
            merged[key] = merged.get(key, 0) + value
    return merged


class ReactFinalRoundStreamer:
    """ReAct 最终轮 stream chunk/event 映射协作者。"""

    def __init__(
        self,
        *,
        context_builder: FinalRoundContextBuilder,
        merge_usage: UsageMerger = _merge_usage,
    ) -> None:
        """初始化最终轮流式协作者。

        Args:
            context_builder: 构建模型请求消息的窄端口。
            merge_usage: usage 合并函数，默认保持既有按 key 累加语义。
        """
        self._context_builder = context_builder
        self._merge_usage = merge_usage

    async def stream_chunks(
        self,
        *,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        round_num: int,
        initial_usage: UsageMapping = None,
        response_capture: list[LLMResponse] | None = None,
    ) -> AsyncIterator[StreamingChunk]:
        """产出最终轮兼容 ``StreamingChunk`` 分片。

        Args:
            context: 当前对话上下文，本方法只读取消息快照。
            config: Agent 执行配置，用于模型名与工具 schema。
            model_access: 模型访问端口。
            round_num: 最终轮轮次号；兼容分片路径当前不写入该值。
            initial_usage: 进入最终轮前已累计的 usage。

        Yields:
            模型中间分片原样透出；``finished=True`` 分片会合并上下文构建
            usage、初始 usage 与模型末尾 usage。
        """
        del round_num
        chat_request, total_usage = await self._build_request_and_usage(
            context=context,
            config=config,
            model_access=model_access,
            initial_usage=initial_usage,
        )
        accumulator = (
            _RoundStreamAccumulator(model=config.model or "")
            if response_capture is not None
            else None
        )
        stream_start = time.monotonic()
        async for chunk in model_access.stream(chat_request):
            if accumulator is not None:
                accumulator.record_chunk(chunk)
            if chunk.finished:
                yield StreamingChunk(
                    delta_content=chunk.delta_content,
                    finished=chunk.finished,
                    usage=self._merge_usage(total_usage, chunk.usage or {}),
                    metadata=chunk.metadata,
                )
            else:
                yield chunk
        if accumulator is not None and response_capture is not None:
            response_capture.append(
                accumulator.build_response(latency_ms=(time.monotonic() - stream_start) * 1000.0)
            )

    async def stream_events(
        self,
        *,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        round_num: int,
        initial_usage: UsageMapping = None,
        response_capture: list[LLMResponse] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """产出最终轮结构化 ``AgentStreamEvent`` 事件。

        Args:
            context: 当前对话上下文，本方法只读取消息快照。
            config: Agent 执行配置，用于模型名与工具 schema。
            model_access: 模型访问端口。
            round_num: 最终轮轮次号，写入事件 metadata。
            initial_usage: 进入最终轮前已累计的 usage。

        Yields:
            ``assistant_delta``、``tool_arguments_delta`` 与
            ``assistant_done`` 事件，事件时序与既有 adapter 辅助方法一致。
        """
        chat_request, total_usage = await self._build_request_and_usage(
            context=context,
            config=config,
            model_access=model_access,
            initial_usage=initial_usage,
        )
        accumulator = (
            _RoundStreamAccumulator(model=config.model or "")
            if response_capture is not None
            else None
        )
        stream_start = time.monotonic()
        async for chunk in model_access.stream(chat_request):
            if accumulator is not None:
                accumulator.record_chunk(chunk)
            if chunk.delta_content:
                yield AgentStreamEvent(
                    kind="assistant_delta",
                    content=chunk.delta_content,
                )
            if not chunk.finished and chunk.tool_calls:
                for delta in chunk.tool_calls:
                    yield AgentStreamEvent(
                        kind="tool_arguments_delta",
                        content="",
                        tool_name=delta.name,
                        tool_call_id=delta.id,
                        arguments=delta.arguments_delta or "",
                        usage=None,
                        metadata={"round": round_num},
                    )
            if chunk.finished:
                yield AgentStreamEvent(
                    kind="assistant_done",
                    usage=self._merge_usage(total_usage, chunk.usage or {}),
                    metadata={"round": round_num},
                )
        if accumulator is not None and response_capture is not None:
            response_capture.append(
                accumulator.build_response(latency_ms=(time.monotonic() - stream_start) * 1000.0)
            )

    async def _build_request_and_usage(
        self,
        *,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        initial_usage: UsageMapping,
    ) -> tuple[ChatRequest, Usage]:
        """构建模型请求并合并进入流式调用前的 usage。"""
        builder_result = await self._context_builder.build(
            context.get_messages(),
            model_access=model_access,
            model=config.model,
        )
        chat_request = ChatRequest(
            messages=builder_result.messages,
            model=config.model,
            tools=config.tool_schemas,
        )
        total_usage = self._merge_usage(_coerce_usage(initial_usage), builder_result.usage)
        return chat_request, total_usage
