"""Handoff 父上下文 ContextVar 模块。

提供基于 :mod:`contextvars` 的"父 ConversationContext 快照"传递通道，
使 ``HandoffToAgentTool`` 在 ``execute()`` 内部能拿到父 Agent 当前的消息
快照，而无需修改 ``Tool`` ABC 接口或对 HandoffTool 做 isinstance 特判。

设计要点：

- ``ContextVar`` 的 task-local 语义天然适配同轮多个工具并发执行的场景：
  ``ReActAgentAdapter._dispatch_concurrent_tool_calls`` /
  ``_stream_concurrent_tool_progress`` /
  ``_events_concurrent_tool_calls`` 在入口 ``set(...)`` 写入父上下文，
  ``finally`` 中 ``reset(token)`` 还原；同 ``Task`` 内派生的子协程
  （``asyncio.gather`` 等）共享该值，异步工具执行能正确读取。
- 不复制上下文消息：写入的是 ``ConversationContext`` 引用本身；调用方仅
  通过 ``ctx.get_messages()`` 获取消息列表的浅拷贝，避免修改父侧状态。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.chat.context import ConversationContext

_current_parent_context: ContextVar[ConversationContext | None] = ContextVar(
    "handoff_parent_context",
    default=None,
)
"""当前 Agent Loop 的父 ``ConversationContext`` 引用。"""


def get_parent_context() -> ConversationContext | None:
    """读取当前协程链上的父 ``ConversationContext`` 引用。

    供 ``HandoffToAgentTool.execute`` 在工具执行期内调用，获取消息快照后
    传给 ``DelegationPort.handoff(...)``。当处于非 Agent Loop 执行场景
    （如直接单元测试 Tool）时返回 ``None``。
    """
    return _current_parent_context.get()


def set_parent_context(
    ctx: ConversationContext,
) -> Token[ConversationContext | None]:
    """把父 ``ConversationContext`` 设置到当前协程链上，返回 reset token。

    Args:
        ctx: 待绑定的父 ``ConversationContext`` 实例。

    Returns:
        ``Token``，由调用方在 ``finally`` 块中传给
        :func:`reset_parent_context` 还原原值。
    """
    return _current_parent_context.set(ctx)


def reset_parent_context(token: Token[ConversationContext | None]) -> None:
    """通过 token 还原 ContextVar 至 ``set`` 前的值。"""
    _current_parent_context.reset(token)
