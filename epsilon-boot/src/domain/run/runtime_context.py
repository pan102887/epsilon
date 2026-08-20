"""Run 执行上下文 ContextVar 模块。

本模块使用 ``ContextVar`` 保存当前线程或协程中的 Run 执行上下文，
供 Run 运行时链路中的领域与应用组件读取统一的 run_id、owner_id、
segment_index 与恢复模式标记，而不依赖基础设施或 checkpoint 开关。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunExecutionContext:
    """当前线程或协程中的 Run 执行上下文。"""

    run_id: str
    owner_id: str
    segment_index: int
    recovery_mode: bool = False
    guardrail_summary: dict[str, Any] | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: dict[str, Any] | None = None


_RUN_EXECUTION_CONTEXT: ContextVar[RunExecutionContext | None] = ContextVar(
    "run_execution_context",
    default=None,
)


def get_run_execution_context() -> RunExecutionContext | None:
    """返回当前 Run 执行上下文；非 Run 路径时返回 None。"""

    return _RUN_EXECUTION_CONTEXT.get()


def set_run_execution_context(
    context: RunExecutionContext,
) -> Token[RunExecutionContext | None]:
    """设置当前 Run 执行上下文并返回可用于恢复的 token。"""

    return _RUN_EXECUTION_CONTEXT.set(context)


def reset_run_execution_context(token: Token[RunExecutionContext | None]) -> None:
    """使用 token 恢复上一个 Run 执行上下文。"""

    _RUN_EXECUTION_CONTEXT.reset(token)
