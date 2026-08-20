"""Run checkpoint ContextVar 模块。

提供后台 Run 执行期的 checkpoint sink 传递通道。同步 Chat/Task 入口默认
不设置该上下文，因此读取结果为 ``None``，保持无 checkpoint 行为。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain.run.ports import RunCheckpointSinkPort


@dataclass(frozen=True)
class RunCheckpointExecutionContext:
    """当前后台 Run 的 checkpoint 执行上下文。"""

    run_id: str
    owner_id: str
    segment_index: int
    recovery_mode: bool
    sink: RunCheckpointSinkPort
    checkpoint_id: str | None = None
    context_snapshot: dict[str, Any] | None = None
    round_num: int | None = None
    usage: dict[str, int] | None = None
    segment_metadata: dict[str, Any] | None = None


_current_run_checkpoint_context: ContextVar[RunCheckpointExecutionContext | None] = ContextVar(
    "run_checkpoint_context",
    default=None,
)
"""当前协程链上的 Run checkpoint 执行上下文。"""


def set_run_checkpoint_context(
    value: RunCheckpointExecutionContext,
) -> Token[RunCheckpointExecutionContext | None]:
    """设置当前协程链的 checkpoint 上下文，并返回 reset token。"""
    return _current_run_checkpoint_context.set(value)


def reset_run_checkpoint_context(
    token: Token[RunCheckpointExecutionContext | None],
) -> None:
    """通过 token 还原 checkpoint 上下文至 ``set`` 前状态。"""
    _current_run_checkpoint_context.reset(token)


def get_run_checkpoint_context() -> RunCheckpointExecutionContext | None:
    """读取当前协程链上的 checkpoint 上下文，未设置时返回 ``None``。"""
    return _current_run_checkpoint_context.get()
