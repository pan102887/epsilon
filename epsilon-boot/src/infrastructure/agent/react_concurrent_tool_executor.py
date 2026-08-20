"""ReAct 同轮多工具并发执行骨架（基础设施层内部协作者）。

从 ``ReActAgentAdapter`` 门面抽出的工具并发编排职责（SRP 拆分，
``ddd-followup-refinements`` 切片 C）。本模块承载同轮多工具的并发调度
（``asyncio.gather``）、父上下文 ContextVar 传参（``set_parent_context`` /
``reset_parent_context``）与流式 / 事件配对时序。

依 ADR-0013「工具并发骨架留基础设施、不开 P2 第三片」的结论，本骨架**明确保留
在基础设施层**——它依赖 Python 运行时并发原语、ContextVar 运行时上下文缝合与
流式事件时序，属技术并发编排，不上提领域层。本次拆分仅为门面内部按 SRP 重排
模块归属，逐字平移三方法体，不改变任何对外可观测行为，不重开 ADR-0013。

三方法通过 ``ToolExecutionRuntime`` 窄回调协议回调门面完成工具执行、trace 记账
与执行后 guardrail 观测；协议为基础设施层内部协作契约，不跨层、不入领域层。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from domain.agent.value_objects import AgentStreamEvent
from domain.model_access.value_objects import StreamingChunk

if TYPE_CHECKING:
    from domain.agent.tools import ToolExecutionResult
    from domain.agent.value_objects import AgentConfig
    from domain.chat.context import ConversationContext
    from domain.model_access.value_objects import ToolCallRequest


class ToolExecutionRuntime(Protocol):
    """并发骨架回调门面的工具执行运行时（基础设施层内部协议）。

    ``ConcurrentToolExecutor`` 通过本协议回调 ``ReActAgentAdapter`` 完成单个
    工具执行、工具调用 trace 记账与执行后 guardrail 观测。协议方法签名与门面
    对应方法逐一对应，语义不变。
    """

    async def _execute_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
        skip_guardrail_before: bool,
        record_guardrail_after: bool,
    ) -> tuple[ToolExecutionResult, bool]:
        """执行单个工具调用并返回 (结果, 是否错误)。"""
        ...

    async def _record_tool_call_trace(
        self,
        session_id: str | None,
        round_num: int,
        tool_call: ToolCallRequest,
        result: ToolExecutionResult,
        is_error: bool,
        elapsed_ms: float,
    ) -> None:
        """记录单个工具调用 trace。"""
        ...

    async def _record_tool_after_observation(
        self,
        *,
        tool_call: ToolCallRequest,
        usage: dict[str, int] | None,
        round_num: int,
        is_error: bool,
        elapsed_ms: float,
    ) -> None:
        """记录工具执行后的 guardrail 观测。"""
        ...


class ConcurrentToolExecutor:
    """同轮多工具并发执行 / 流式进度 / 事件骨架（ADR-0013 留基础设施）。

    单工具时直接 await（fast path），多工具时通过 ``asyncio.gather`` 并发，
    并保持流式 chunk / 事件的 start-end 配对相邻。进入工具执行前经
    ``set_parent_context`` 写入父上下文 ContextVar，``finally`` 中还原。
    """

    def __init__(self, runtime: ToolExecutionRuntime) -> None:
        """初始化并发执行器。

        Args:
            runtime: 工具执行运行时回调（通常为 ``ReActAgentAdapter`` 门面自身）。
        """
        self._runtime = runtime

    async def dispatch(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        session_id: str | None = None,
        round_num: int = 0,
    ) -> None:
        """同轮多个工具调用并发执行的统一入口。

        单工具时直接 await（fast path），多工具时通过 asyncio.gather 并发。

        Spec A R1.6：进入工具执行前 ``set_parent_context(context)``，让
        ``HandoffToAgentTool.execute`` 通过 ContextVar 拿到父 ``ConversationContext``
        消息快照；``finally`` 中 ``reset_parent_context(token)`` 还原。
        """
        from infrastructure.agent.handoff_context import (
            reset_parent_context,
            set_parent_context,
        )

        token = set_parent_context(context)
        try:
            tool_results: dict[str, tuple[ToolExecutionResult, bool, float]] = {}
            if len(tool_calls) == 1:
                tc = tool_calls[0]
                start_t = time.time()
                result, is_error = await self._runtime._execute_tool_call(
                    context,
                    tc,
                    config,
                    round_num=round_num,
                    skip_guardrail_before=True,
                    record_guardrail_after=False,
                )
                tool_results[tc.id] = (result, is_error, (time.time() - start_t) * 1000)
            else:

                async def _run_and_trace(
                    tc: ToolCallRequest,
                ) -> tuple[str, ToolExecutionResult, bool, float]:
                    start_t = time.time()
                    result, is_error = await self._runtime._execute_tool_call(
                        context,
                        tc,
                        config,
                        round_num=round_num,
                        skip_guardrail_before=True,
                        record_guardrail_after=False,
                    )
                    return tc.id, result, is_error, (time.time() - start_t) * 1000

                completed = await asyncio.gather(*(_run_and_trace(tc) for tc in tool_calls))
                for tool_call_id, result, is_error, elapsed in completed:
                    tool_results[tool_call_id] = (result, is_error, elapsed)

            for tc in tool_calls:
                result, is_error, elapsed = tool_results[tc.id]
                await self._runtime._record_tool_call_trace(
                    session_id, round_num, tc, result, is_error, elapsed
                )
                await self._runtime._record_tool_after_observation(
                    tool_call=tc,
                    usage=None,
                    round_num=round_num,
                    is_error=is_error,
                    elapsed_ms=elapsed,
                )
                if len(tool_calls) == 1:
                    return
        finally:
            reset_parent_context(token)

    async def stream_progress(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        round_num: int,
    ) -> AsyncIterator[StreamingChunk]:
        """run_streaming 同轮工具并发版本，保持事件配对相邻。

        每个 tool_call 包装为 task，按完成顺序整段 yield start + end。

        Spec A R1.6：进入工具执行前 ``set_parent_context(context)``。
        """
        from infrastructure.agent.handoff_context import (
            reset_parent_context,
            set_parent_context,
        )

        session_id = context.session_id
        token = set_parent_context(context)
        try:
            tool_results: dict[str, tuple[ToolExecutionResult, bool, float]] = {}
            if len(tool_calls) == 1:
                tc = tool_calls[0]
                yield self._tool_progress_chunk(round_num, tc, "start")
                start_t = time.time()
                result, is_error = await self._runtime._execute_tool_call(
                    context,
                    tc,
                    config,
                    round_num=round_num,
                    skip_guardrail_before=True,
                    record_guardrail_after=False,
                )
                tool_results[tc.id] = (result, is_error, (time.time() - start_t) * 1000)
            else:

                async def _run_one(
                    tc: ToolCallRequest,
                ) -> tuple[str, ToolExecutionResult, bool, float]:
                    start_t = time.time()
                    result, is_error = await self._runtime._execute_tool_call(
                        context,
                        tc,
                        config,
                        round_num=round_num,
                        skip_guardrail_before=True,
                        record_guardrail_after=False,
                    )
                    return tc.id, result, is_error, (time.time() - start_t) * 1000

                completed = await asyncio.gather(*(_run_one(tc) for tc in tool_calls))
                for tool_call_id, result, is_error, elapsed in completed:
                    tool_results[tool_call_id] = (result, is_error, elapsed)

            for tc in tool_calls:
                result, is_error, elapsed = tool_results[tc.id]
                if len(tool_calls) > 1:
                    yield self._tool_progress_chunk(round_num, tc, "start")
                await self._runtime._record_tool_call_trace(
                    session_id, round_num, tc, result, is_error, elapsed
                )
                await self._runtime._record_tool_after_observation(
                    tool_call=tc,
                    usage=None,
                    round_num=round_num,
                    is_error=is_error,
                    elapsed_ms=elapsed,
                )
                yield self._tool_progress_chunk(round_num, tc, "end")
        finally:
            reset_parent_context(token)

    async def events(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        round_num: int,
    ) -> AsyncIterator[AgentStreamEvent]:
        """run_events 同轮工具并发版本，保持事件配对相邻。

        每个 tool_call 包装为 task，按完成顺序整段 yield tool_start + tool_result/error。

        Spec A R1.6：进入工具执行前 ``set_parent_context(context)``。
        """
        from infrastructure.agent.handoff_context import (
            reset_parent_context,
            set_parent_context,
        )

        session_id = context.session_id
        token = set_parent_context(context)
        try:
            tool_results: dict[str, tuple[ToolExecutionResult, bool, float]] = {}
            if len(tool_calls) == 1:
                tc = tool_calls[0]
                yield AgentStreamEvent(
                    kind="tool_start",
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    arguments=tc.arguments,
                    metadata={"round": round_num},
                )
                start_t = time.time()
                result, is_error = await self._runtime._execute_tool_call(
                    context,
                    tc,
                    config,
                    round_num=round_num,
                    skip_guardrail_before=True,
                    record_guardrail_after=False,
                )
                tool_results[tc.id] = (result, is_error, (time.time() - start_t) * 1000)
            else:

                async def _run_one(
                    tc: ToolCallRequest,
                ) -> tuple[str, ToolExecutionResult, bool, float]:
                    start_t = time.time()
                    result, is_error = await self._runtime._execute_tool_call(
                        context,
                        tc,
                        config,
                        round_num=round_num,
                        skip_guardrail_before=True,
                        record_guardrail_after=False,
                    )
                    return tc.id, result, is_error, (time.time() - start_t) * 1000

                completed = await asyncio.gather(*(_run_one(tc) for tc in tool_calls))
                for tool_call_id, result, is_error, elapsed in completed:
                    tool_results[tool_call_id] = (result, is_error, elapsed)

            for tc in tool_calls:
                result, is_error, elapsed = tool_results[tc.id]
                if len(tool_calls) > 1:
                    yield AgentStreamEvent(
                        kind="tool_start",
                        tool_name=tc.name,
                        tool_call_id=tc.id,
                        arguments=tc.arguments,
                        metadata={"round": round_num},
                    )
                await self._runtime._record_tool_call_trace(
                    session_id, round_num, tc, result, is_error, elapsed
                )
                await self._runtime._record_tool_after_observation(
                    tool_call=tc,
                    usage=None,
                    round_num=round_num,
                    is_error=is_error,
                    elapsed_ms=elapsed,
                )
                yield AgentStreamEvent(
                    kind="tool_error" if is_error else "tool_result",
                    content=result.content,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    arguments=tc.arguments,
                    metadata={"round": round_num},
                )
        finally:
            reset_parent_context(token)

    @staticmethod
    def _tool_progress_chunk(
        round_num: int,
        tool_call: ToolCallRequest,
        phase: str,
    ) -> StreamingChunk:
        """构造工具进度分片，标记工具执行开始/结束。"""
        return StreamingChunk(
            delta_content="",
            finished=False,
            metadata={
                "type": "tool_progress",
                "round": round_num,
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "phase": phase,
            },
        )
