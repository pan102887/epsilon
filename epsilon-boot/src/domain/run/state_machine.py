"""Run 状态机定义模块。

本模块集中约束后台 Run 的合法状态迁移，避免应用服务、worker 或
存储适配器各自复制规则导致行为漂移。
"""

from __future__ import annotations

from typing import ClassVar

from domain.run.exceptions import RunInvalidTransitionError
from domain.run.value_objects import RunStatus


class RunStateMachine:
    """后台 Run 生命周期状态机。"""

    _TERMINAL_STATUSES = frozenset(
        {
            RunStatus.CANCELLED,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.LOST,
        }
    )
    _TRANSITIONS: ClassVar[dict[RunStatus, frozenset[RunStatus]]] = {
        RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
        RunStatus.RUNNING: frozenset(
            {
                RunStatus.PAUSED,
                RunStatus.AWAITING_APPROVAL,
                RunStatus.CANCEL_REQUESTED,
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.LOST,
            }
        ),
        RunStatus.PAUSED: frozenset({RunStatus.QUEUED, RunStatus.CANCEL_REQUESTED}),
        RunStatus.AWAITING_APPROVAL: frozenset({RunStatus.QUEUED, RunStatus.CANCEL_REQUESTED}),
        RunStatus.CANCEL_REQUESTED: frozenset({RunStatus.CANCELLED, RunStatus.LOST}),
        RunStatus.CANCELLED: frozenset(),
        RunStatus.SUCCEEDED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.LOST: frozenset(),
    }

    def assert_transition(self, current: RunStatus, target: RunStatus) -> None:
        """校验状态迁移是否合法。

        Args:
            current: 当前 Run 状态。
            target: 目标 Run 状态。

        Raises:
            RunInvalidTransitionError: 当迁移不在状态机允许集合内时抛出。
        """

        if target not in self._TRANSITIONS[current]:
            raise RunInvalidTransitionError(current.value, target.value)

    def is_terminal(self, status: RunStatus) -> bool:
        """判断状态是否为终态。"""

        return status in self._TERMINAL_STATUSES

    def can_cancel(self, status: RunStatus) -> bool:
        """判断当前状态是否接受取消请求。"""

        return status in {
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.CANCEL_REQUESTED,
        }

    def can_continue(self, status: RunStatus) -> bool:
        """判断当前状态是否接受继续执行请求。"""

        return status is RunStatus.PAUSED

    def can_claim(self, status: RunStatus) -> bool:
        """判断当前状态是否可被 worker 领取执行。"""

        return status is RunStatus.QUEUED

    def cancellation_target(self, status: RunStatus) -> RunStatus:
        """返回接受取消请求后应进入的目标状态。

        queued Run 在 worker 启动前可直接进入 cancelled；running、paused
        和 awaiting_approval 先进入 cancel_requested，等待 worker 或应用
        服务后续收敛。
        """

        if status is RunStatus.QUEUED:
            return RunStatus.CANCELLED
        if status in {
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.CANCEL_REQUESTED,
        }:
            return RunStatus.CANCEL_REQUESTED
        raise RunInvalidTransitionError(status.value, RunStatus.CANCEL_REQUESTED.value)
