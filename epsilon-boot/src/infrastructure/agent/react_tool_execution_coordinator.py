"""ReAct 工具执行协调器。

本模块承接 ``ReActAgentAdapter`` 中同轮工具调用的并发调度、流式进度分片
和结构化工具事件配对输出。协调器只依赖 ``ToolExecutionRuntime`` 窄协议，
不导入应用层或具体 adapter，使工具执行副作用仍由门面运行时负责。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextvars import Token
from dataclasses import dataclass

from domain.agent.value_objects import AgentConfig, AgentStreamEvent
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import StreamingChunk, ToolCallRequest
from infrastructure.agent.react_runtime_protocols import ToolExecutionRuntime


@dataclass(frozen=True)
class ToolExecutionBatchResult:
    """同轮工具执行批次结果。

    Attributes:
        executed_count: 本批次实际调度的工具调用数量。
    """

    executed_count: int


@dataclass(frozen=True)
class _ToolExecutionOutcome:
    """单个工具执行后的事件映射结果。"""

    tool_call: ToolCallRequest
    content: str
    is_error: bool


class ReactToolExecutionCoordinator:
    """ReAct 工具执行基础设施协作者。

    协调器只处理同轮工具调用的并发骨架和输出顺序：所有具体授权、审批、
    checkpoint、guardrail、trace 与上下文写入均由传入的 runtime 完成。
    """

    def __init__(self, runtime: ToolExecutionRuntime) -> None:
        """初始化工具执行协调器。

        Args:
            runtime: 实现 ``ToolExecutionRuntime`` 的门面运行时。
        """

        self._runtime = runtime

    async def dispatch(
        self,
        *,
        context: ConversationContext,
        tool_calls: Sequence[ToolCallRequest],
        config: AgentConfig,
        round_num: int = 0,
    ) -> ToolExecutionBatchResult:
        """并发执行同轮工具调用。

        Args:
            context: 当前对话上下文，工具结果由 runtime 原地写入。
            tool_calls: 同一模型轮次返回的工具调用列表。
            config: 当前 Agent 执行配置。

        Returns:
            包含执行数量的批次结果。
        """

        calls = tuple(tool_calls)
        if not calls:
            return ToolExecutionBatchResult(executed_count=0)

        token = self._set_parent_context(context)
        try:
            if len(calls) == 1:
                await self._runtime.execute_tool_call(
                    context, calls[0], config, round_num=round_num
                )
            else:
                await asyncio.gather(
                    *(
                        self._runtime.execute_tool_call(
                            context, call, config, round_num=round_num
                        )
                        for call in calls
                    )
                )
            return ToolExecutionBatchResult(executed_count=len(calls))
        finally:
            self._reset_parent_context(token)

    async def stream_progress(
        self,
        *,
        context: ConversationContext,
        tool_calls: Sequence[ToolCallRequest],
        config: AgentConfig,
        round_num: int,
    ) -> AsyncIterator[StreamingChunk]:
        """执行工具并产出兼容流式进度分片。

        Args:
            context: 当前对话上下文，工具结果由 runtime 原地写入。
            tool_calls: 同一模型轮次返回的工具调用列表。
            config: 当前 Agent 执行配置。
            round_num: 当前 ReAct 轮次编号。

        Yields:
            每个工具调用相邻的 ``start`` / ``end`` 进度分片。
        """

        calls = tuple(tool_calls)
        if not calls:
            return
        if len(calls) == 1:
            tool_call = calls[0]
            yield self._runtime.tool_progress_chunk(round_num, tool_call, "start")
            await self._runtime.execute_tool_call(
                context, tool_call, config, round_num=round_num
            )
            yield self._runtime.tool_progress_chunk(round_num, tool_call, "end")
            return

        outcomes = await self._execute_many(
            context=context,
            tool_calls=calls,
            config=config,
            round_num=round_num,
        )
        for outcome in outcomes:
            yield self._runtime.tool_progress_chunk(round_num, outcome.tool_call, "start")
            yield self._runtime.tool_progress_chunk(round_num, outcome.tool_call, "end")

    async def stream_events(
        self,
        *,
        context: ConversationContext,
        tool_calls: Sequence[ToolCallRequest],
        config: AgentConfig,
        round_num: int,
    ) -> AsyncIterator[AgentStreamEvent]:
        """执行工具并产出结构化工具事件。

        Args:
            context: 当前对话上下文，工具结果由 runtime 原地写入。
            tool_calls: 同一模型轮次返回的工具调用列表。
            config: 当前 Agent 执行配置。
            round_num: 当前 ReAct 轮次编号。

        Yields:
            每个工具调用相邻的 ``tool_start`` + ``tool_result`` 或
            ``tool_start`` + ``tool_error`` 事件。
        """

        calls = tuple(tool_calls)
        if not calls:
            return
        if len(calls) == 1:
            tool_call = calls[0]
            yield self._runtime.tool_start_event(round_num, tool_call)
            await self._runtime.execute_tool_call(
                context, tool_call, config, round_num=round_num
            )
            outcome = self._outcome_from_context(context, tool_call)
            if outcome.is_error:
                yield self._runtime.tool_error_event(round_num, tool_call, outcome.content)
            else:
                yield self._runtime.tool_result_event(round_num, tool_call, outcome.content)
            return

        outcomes = await self._execute_many(
            context=context,
            tool_calls=calls,
            config=config,
            round_num=round_num,
        )
        for outcome in outcomes:
            yield self._runtime.tool_start_event(round_num, outcome.tool_call)
            if outcome.is_error:
                yield self._runtime.tool_error_event(
                    round_num,
                    outcome.tool_call,
                    outcome.content,
                )
            else:
                yield self._runtime.tool_result_event(
                    round_num,
                    outcome.tool_call,
                    outcome.content,
                )

    async def _execute_many(
        self,
        *,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        round_num: int,
    ) -> tuple[_ToolExecutionOutcome, ...]:
        """并发执行工具调用并收集事件映射所需的结果。"""

        if not tool_calls:
            return ()

        token = self._set_parent_context(context)
        try:
            if len(tool_calls) == 1:
                return (
                    await self._execute_one(
                        context=context,
                        tool_call=tool_calls[0],
                        config=config,
                        round_num=round_num,
                    ),
                )

            completed = await asyncio.gather(
                *(
                    self._execute_one(
                        context=context,
                        tool_call=tool_call,
                        config=config,
                        round_num=round_num,
                    )
                    for tool_call in tool_calls
                )
            )
            return tuple(completed)
        finally:
            self._reset_parent_context(token)

    async def _execute_one(
        self,
        *,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        round_num: int,
    ) -> _ToolExecutionOutcome:
        """执行单个工具调用并从上下文中提取事件内容。"""

        await self._runtime.execute_tool_call(
            context,
            tool_call,
            config,
            round_num=round_num,
        )
        return self._outcome_from_context(context, tool_call)

    @staticmethod
    def _outcome_from_context(
        context: ConversationContext,
        tool_call: ToolCallRequest,
    ) -> _ToolExecutionOutcome:
        """从上下文中提取工具执行后的事件映射结果。"""
        message = ReactToolExecutionCoordinator._latest_tool_message(context, tool_call.id)
        if message is None:
            return _ToolExecutionOutcome(tool_call=tool_call, content="", is_error=False)
        return _ToolExecutionOutcome(
            tool_call=tool_call,
            content=message.content,
            is_error=bool(message.metadata.get("error")),
        )

    @staticmethod
    def _latest_tool_message(
        context: ConversationContext,
        tool_call_id: str,
    ) -> ToolMessage | None:
        """返回指定工具调用最近写入的工具消息。"""

        for message in reversed(context.get_messages()):
            if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
                return message
        return None

    @staticmethod
    def _set_parent_context(
        context: ConversationContext,
    ) -> Token[ConversationContext | None]:
        """设置 handoff 父上下文并返回重置 token。"""

        from infrastructure.agent.handoff_context import set_parent_context

        return set_parent_context(context)

    @staticmethod
    def _reset_parent_context(token: Token[ConversationContext | None]) -> None:
        """重置 handoff 父上下文。"""

        from infrastructure.agent.handoff_context import reset_parent_context

        reset_parent_context(token)
