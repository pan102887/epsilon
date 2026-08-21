"""ReAct ``_iter_rounds`` 内部使用的单轮流式累积器。

v3 决策：``ReAct`` 内部全程 ``model_access.stream(...)``（决策 1=B，去除中间
轮次的 ``model_access.chat`` 路径），由本累积器对单轮流式分片做内部聚合，
对外仍以 ``LLMResponse`` 等价形态产出，使 ``_iter_rounds`` 的下游分支判定
（``response.tool_calls`` / ``response.content`` / ``response.usage``）保持
v2 行为不变。

本类是 ``_iter_rounds`` 的内部实现细节，**不**对外发任何 ``StreamingChunk``
或 ``AgentStreamEvent``：累积期间所有分片由 ``consume`` 静默吸收；中间轮次
的对外事件时序（heartbeat、tool_progress、status、tool_start、
tool_result/tool_error 等）由 ``run_streaming`` / ``run_events`` 自身按 v2
形态在外侧维持。

字段累积规则参见 :class:`StreamingChunk` / :class:`StreamingToolCallDelta`
的协议契约：

* ``delta_content``：所有分片按到达顺序拼接成 ``LLMResponse.content``。
* ``tool_calls``：按 :attr:`StreamingToolCallDelta.index` 跨分片合并为
  完整 ``list[ToolCallRequest]``；当 ``finished=True`` 分片携带"完整
  arguments"列表时优先覆盖增量拼接结果（决策 11），保证累积值与"等价
  chat 一次返回的 ``LLMResponse.tool_calls``"按 ``(id, name, arguments)``
  三元组完全相等且顺序一致。
* ``usage``：取 ``finished=True`` 分片的 ``usage``；缺失视为空 dict。
* ``model``：构造时由调用方注入（``_iter_rounds`` 在累积器构造时传
  ``config.model or ""``，与 v2 ``model_access.chat`` 返回的
  ``LLMResponse.model`` 字段对齐）。
* ``latency_ms``：``time.monotonic`` 毫秒差，覆盖 ``consume`` 全量耗时。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from domain.model_access.value_objects import (
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)

logger = logging.getLogger(__name__)


class RoundStreamAccumulator:
    """单轮流式分片累积器，对外产出与 ``model_access.chat`` 等价的 ``LLMResponse``。

    使用方式（仅由 ``_iter_rounds`` 内部使用）::

        accumulator = RoundStreamAccumulator(model=config.model or "")
        await accumulator.consume(model_access.stream(chat_request))
        response = accumulator.build_response()

    finished 违约判定从严说明（D3）：``finished=True`` 分片承诺携带
    "三字段非 ``None`` 且非空字符串"的完整列表；本累积器对 ``None`` 与
    ``""`` 同等视为违约并回退到增量累积结果，并通过 ``logger.warning``
    输出 WARN 日志（``extra`` 字段集对齐统一诊断字段集），让 ELK 聚合
    可按 ``source="stream_finished"`` 命中。增量分支保留 ``is not None``
    判定，空串照常累积进 ``slot["id"]``，与既有行为完全一致。
    """

    def __init__(self, model: str) -> None:
        """初始化累积器。

        Args:
            model: 当前轮次使用的模型名称，写入产出的 ``LLMResponse.model``。
                通常由 ``_iter_rounds`` 传入 ``config.model or ""``，与 v2
                ``model_access.chat`` 返回值的 model 字段对齐。
        """
        self._model = model
        self._content_parts: list[str] = []
        # 增量拼接累积态：键为 ``StreamingToolCallDelta.index``。
        self._acc_tool_calls: dict[int, dict[str, str | None]] = {}
        # 末尾分片携带的"完整列表"，存在则优先覆盖增量拼接结果。
        self._final_tool_calls: list[ToolCallRequest] | None = None
        self._usage: dict[str, int] = {}
        self._consumed = False
        self._start: float | None = None
        self._latency_ms: float = 0.0

    async def consume(self, stream: AsyncIterator[StreamingChunk]) -> None:
        """逐分片消费流式输入。

        累积期间不抛任何业务异常；底层 ``model_access.stream`` 抛出的异常透传
        给本协程的调用者（``_iter_rounds``），不在此处捕获。

        Args:
            stream: ``model_access.stream(...)`` 产出的 ``StreamingChunk``
                异步迭代器。

        Raises:
            RuntimeError: 重复调用 ``consume`` 时抛出（同一累积器仅支持一次
                消费，避免误用）。
        """
        if self._consumed:
            raise RuntimeError("_RoundStreamAccumulator.consume 仅支持调用一次")
        self._consumed = True
        self._start = time.monotonic()

        async for chunk in stream:
            self.record_chunk(chunk)

        self._latency_ms = (time.monotonic() - self._start) * 1000.0

    def record_chunk(self, chunk: StreamingChunk) -> None:
        """累积单个流式分片。

        供 ``consume`` 与"外侧边产出边累积"的调用方（如 ``run_streaming`` /
        ``run_events`` 的 ``max_rounds==1`` 快速路径，需在向客户端逐分片产出
        的同时捕获等价 ``LLMResponse``）共用同一累积逻辑，避免分片合并规则
        重复实现导致行为分裂。本方法不做计时——``consume`` 覆盖全量耗时，
        外侧调用方可在 ``build_response(latency_ms=...)`` 显式传入。

        Args:
            chunk: 单个 ``StreamingChunk`` 分片。
        """
        if chunk.delta_content:
            self._content_parts.append(chunk.delta_content)

        if chunk.tool_calls is not None:
            if chunk.finished:
                # ``finished=True`` 分片承诺携带"按 index 升序的完整列表"，
                # 每个元素 id/name/arguments_delta 全部非 ``None`` 且非空串。
                # 优先以此为准，覆盖增量拼接结果（决策 11）。
                final: list[ToolCallRequest] = []
                for delta in chunk.tool_calls:
                    if not delta.id or not delta.name or not delta.arguments_delta:
                        # 容错（D3）：契约要求 finished=True 分片三字段全非
                        # None 且非空串；若上游违约（None 或空串），回退到
                        # 增量累积结果（不覆盖），并通过 WARN 日志暴露违约。
                        logger.warning(
                            "流式 finished 分片违约，回退到增量累积结果",
                            extra={
                                "source": "stream_finished",
                                "provider": None,
                                "model": self._model,
                                "tool_name": delta.name or None,
                                "tool_call_index": delta.index,
                                "raw_id_value": delta.id,
                                "violation_field": (
                                    "id"
                                    if not delta.id
                                    else "name"
                                    if not delta.name
                                    else "arguments_delta"
                                ),
                            },
                        )
                        final = []
                        break
                    final.append(
                        ToolCallRequest(
                            id=delta.id,
                            name=delta.name,
                            arguments=delta.arguments_delta,
                        )
                    )
                if final:
                    self._final_tool_calls = final
            else:
                for delta in chunk.tool_calls:
                    slot = self._acc_tool_calls.setdefault(
                        delta.index,
                        {"id": None, "name": None, "arguments": ""},
                    )
                    if delta.id is not None:
                        slot["id"] = delta.id
                    if delta.name is not None:
                        slot["name"] = delta.name
                    if delta.arguments_delta is not None:
                        slot["arguments"] = (slot.get("arguments") or "") + delta.arguments_delta

        if chunk.finished and chunk.usage:
            self._usage = dict(chunk.usage)

    def build_response(self, latency_ms: float | None = None) -> LLMResponse:
        """把累积态展开为 ``LLMResponse``。

        语义等价于 v2 ``model_access.chat()`` 一次返回值——下游
        ``_iter_rounds`` 的所有 v2 分支判断（``response.tool_calls`` /
        ``response.content`` / ``response.usage``）行为不变。

        Args:
            latency_ms: 可选耗时覆盖值（毫秒）。``consume`` 路径不传，沿用其
                内部计时；"外侧边产出边累积"的调用方通过本参数显式注入本轮
                stream 全量耗时。

        Returns:
            等价于 ``chat()`` 的 ``LLMResponse``。
        """
        if self._final_tool_calls is not None:
            tool_calls = list(self._final_tool_calls)
        else:
            tool_calls: list[ToolCallRequest] = []
            for index in sorted(self._acc_tool_calls):
                slot = self._acc_tool_calls[index]
                tc_id = slot.get("id") or ""
                tc_name = slot.get("name") or ""
                tc_args = slot.get("arguments") or ""
                if not tc_id or not tc_name or not tc_args:
                    # 跳过协议不完整的占位（与 ToolCallRequest.__post_init__
                    # 校验对齐：三字段缺一会构造失败）。
                    continue
                tool_calls.append(ToolCallRequest(id=tc_id, name=tc_name, arguments=tc_args))

        return LLMResponse(
            content="".join(self._content_parts),
            model=self._model,
            usage=dict(self._usage),
            latency_ms=latency_ms if latency_ms is not None else self._latency_ms,
            tool_calls=tool_calls,
        )
