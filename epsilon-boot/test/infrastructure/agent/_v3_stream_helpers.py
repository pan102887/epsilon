"""共享 v3 ``stream`` mock 辅助。

⚠️ 该模块以 ``_v3_`` 前缀命名，pytest 默认不收集；纯辅助、不含测试用例。


v3 起 ReAct 内部全程 ``model_access.stream``。本模块为大量 v2 测试中
``_FakeModel.chat`` 的等价改写提供一致的 ``LLMResponse → list[StreamingChunk]``
转换函数（NFR-3）。

使用方式：

    from test.infrastructure.agent._v3_stream_helpers import (
        response_to_chunks,
        FakeStreamModel,
    )
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from domain.chat.context import BaseMessage
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    StreamingToolCallDelta,
)


def response_to_chunks(response: LLMResponse) -> list[StreamingChunk]:
    """把 ``LLMResponse`` 等价转换为 v3 ``stream(...)`` 分片序列。

    - 文本：先一片 ``delta_content=content`` 中间分片；
    - 工具调用：``finished=True`` 分片携带按 ``index`` 升序的完整列表，每个
      :class:`StreamingToolCallDelta` 的 ``arguments_delta`` 设为完整 arguments；
    - 末尾分片携带 ``response.usage``。
    """
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


class FakeStreamModel:
    """v3 ``ModelAccessPort`` 测试 fake：``stream`` 按 ``responses`` 队列产出。

    Attributes:
        chat_call_count: 仅用于兼容 v2 既有断言；v3 ReAct 不调用 ``chat``。
        stream_call_count: 每次 ``stream`` 被调用时 +1（含 ReAct 内部轮次与
            最后一轮 ``_stream_*_final_round`` 调用）。
    """

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        *,
        default_final_chunks: list[StreamingChunk] | None = None,
    ) -> None:
        """初始化 fake。

        Args:
            responses: 顺序消费的 ``LLMResponse`` 队列；每次 ``stream``
                调用消费一个并按 :func:`response_to_chunks` 转换。
            default_final_chunks: 队列耗尽后的兜底分片序列；用于模拟最后一轮
                ``_stream_final_round`` 的真分片输出。默认为 ``"最终回答"`` 单分片。
        """
        self._responses = list(responses or [])
        self._default_final_chunks = (
            list(default_final_chunks)
            if default_final_chunks is not None
            else [
                StreamingChunk(
                    delta_content="最终回答",
                    finished=True,
                    usage={"total_tokens": 5},
                )
            ]
        )
        self.chat_call_count = 0
        self.stream_call_count = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:
        self.chat_call_count += 1
        return self._responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        self.stream_call_count += 1
        if self._responses:
            response = self._responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        for chunk in self._default_final_chunks:
            yield chunk

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return sum(len(message.content) for message in messages)


@dataclass
class StreamCallCounter:
    """Typed call history for dynamically installed stream mocks."""

    responses: list[LLMResponse]
    final_chunks: list[StreamingChunk] | None = None
    call_count: int = 0
    calls: list[ChatRequest] = field(default_factory=list[ChatRequest])


def install_stream_mock(
    model_access: Any,
    responses: list[LLMResponse],
    *,
    final_chunks: list[StreamingChunk] | None = None,
) -> StreamCallCounter:
    """把一个 ``MagicMock`` 改造为 v3 ``stream`` 等价 mock。

    用法（替代原 ``model_access.chat = AsyncMock(side_effect=responses)``）::

        responses = [LLMResponse(...), LLMResponse(...), ...]
        counter = install_stream_mock(model_access, responses)
        ...
        assert counter.call_count == len(responses)

    返回的对象提供 ``call_count`` / ``calls`` 属性，作为原 ``model_access.chat``
    的 spec 等价替代（NFR-3）。
    """

    counter = StreamCallCounter(
        responses=list(responses),
        final_chunks=list(final_chunks) if final_chunks is not None else None,
    )

    async def _stream(request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        counter.call_count += 1
        counter.calls.append(request)
        if counter.responses:
            response = counter.responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        if counter.final_chunks is not None:
            for chunk in counter.final_chunks:
                yield chunk
            return
        # 队列耗尽且未指定 final_chunks 兜底分片：产出最小空 finish 分片。
        yield StreamingChunk(delta_content="", finished=True, usage={})

    model_access.stream = _stream
    # 把 counter 暴露在 ``model_access._v3_stream_counter`` 上，方便测试代码
    # 通过 ``model_access._v3_stream_counter.calls`` 检视每轮请求（替代
    # v2 ``model_access.chat.call_args_list``）。
    model_access._v3_stream_counter = counter
    return counter
