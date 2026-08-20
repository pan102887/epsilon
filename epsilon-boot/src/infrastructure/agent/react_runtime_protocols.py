"""ReAct 基础设施协作者所需的窄运行时协议。

本模块只定义 ``infrastructure.agent`` 内部协作者回调门面的最小能力集合，
用于让工具执行与审批恢复协作者避免接收完整 ``ReActAgentAdapter`` 实例。
协议仅依赖领域值对象与模型接入值对象，不导入 application 层或具体 adapter。
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol

from domain.agent.value_objects import (
    AgentConfig,
    AgentStreamEvent,
    ApprovalDecision,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext
from domain.model_access.value_objects import StreamingChunk, ToolCallRequest


class ToolExecutionRuntime(Protocol):
    """ReAct 工具执行协作者所需的门面运行时能力。"""

    def execute_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
    ) -> Awaitable[None]:
        """执行单个工具调用并负责把工具结果写回对话上下文。"""
        ...

    def tool_progress_chunk(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
        phase: str,
    ) -> StreamingChunk:
        """构造既有 streaming 工具进度分片。"""
        ...

    def tool_start_event(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
    ) -> AgentStreamEvent:
        """构造工具开始执行事件。"""
        ...

    def tool_result_event(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
        content: str,
    ) -> AgentStreamEvent:
        """构造工具执行成功事件。"""
        ...

    def tool_error_event(
        self,
        round_num: int,
        tool_call: ToolCallRequest,
        content: str,
    ) -> AgentStreamEvent:
        """构造工具执行失败事件。"""
        ...


class ApprovalResumeRuntime(Protocol):
    """ReAct 审批恢复协作者所需的门面运行时能力。"""

    def execute_approved_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
        usage: dict[str, int],
    ) -> Awaitable[None]:
        """执行审批通过或人工编辑后的工具调用。"""
        ...

    def validate_edited_tool_call(self, tool_name: str, arguments: object) -> None:
        """复用注册工具的参数 cast/validate 规则校验人工编辑后的参数。"""
        ...

    def record_rejected_tool_call(
        self,
        context: ConversationContext,
        action: PendingActionRequest,
        decision: ApprovalDecision,
        *,
        round_num: int,
        usage: dict[str, int],
    ) -> Awaitable[None]:
        """记录审批拒绝的工具调用结果。"""
        ...
