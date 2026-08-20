"""Run 状态机单元测试模块。"""

from __future__ import annotations

import pytest

from domain.run.exceptions import (
    RunIdempotencyConflictError,
    RunInvalidTransitionError,
)
from domain.run.state_machine import RunStateMachine
from domain.run.value_objects import RunStatus


def test_all_legal_transitions_are_accepted() -> None:
    """覆盖设计中列出的全部合法状态迁移。"""
    machine = RunStateMachine()
    legal_transitions = {
        RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
        RunStatus.RUNNING: {
            RunStatus.PAUSED,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.CANCEL_REQUESTED,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.LOST,
        },
        RunStatus.PAUSED: {RunStatus.QUEUED, RunStatus.CANCEL_REQUESTED},
        RunStatus.AWAITING_APPROVAL: {RunStatus.QUEUED, RunStatus.CANCEL_REQUESTED},
        RunStatus.CANCEL_REQUESTED: {RunStatus.CANCELLED, RunStatus.LOST},
    }

    for current, targets in legal_transitions.items():
        for target in targets:
            machine.assert_transition(current, target)


def test_terminal_statuses_reject_cancel_continue_and_claim() -> None:
    """终态状态不允许取消、继续或领取。"""
    machine = RunStateMachine()

    for status in (
        RunStatus.CANCELLED,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.LOST,
    ):
        assert machine.is_terminal(status)
        assert not machine.can_cancel(status)
        assert not machine.can_continue(status)
        assert not machine.can_claim(status)
        with pytest.raises(RunInvalidTransitionError):
            machine.assert_transition(status, RunStatus.QUEUED)


def test_invalid_transition_raises_run_invalid_transition_error() -> None:
    """非法迁移必须抛 RunInvalidTransitionError。"""
    machine = RunStateMachine()

    with pytest.raises(RunInvalidTransitionError) as caught:
        machine.assert_transition(RunStatus.QUEUED, RunStatus.SUCCEEDED)

    assert caught.value.code == 61003


def test_only_queued_can_be_claimed_and_lost_cannot_be_claimed() -> None:
    """状态机前置条件：只有 queued 可被 worker 领取。"""
    machine = RunStateMachine()

    assert machine.can_claim(RunStatus.QUEUED)
    for status in set(RunStatus) - {RunStatus.QUEUED}:
        assert not machine.can_claim(status)
    assert not machine.can_claim(RunStatus.LOST)


def test_cancel_targets_match_phase_three_rules() -> None:
    """queued 取消直接 cancelled，活跃/暂停/审批等待进入 cancel_requested。"""
    machine = RunStateMachine()

    assert machine.cancellation_target(RunStatus.QUEUED) is RunStatus.CANCELLED
    assert machine.cancellation_target(RunStatus.RUNNING) is RunStatus.CANCEL_REQUESTED
    assert machine.cancellation_target(RunStatus.PAUSED) is RunStatus.CANCEL_REQUESTED
    assert machine.cancellation_target(RunStatus.AWAITING_APPROVAL) is RunStatus.CANCEL_REQUESTED


def test_can_continue_only_allows_paused() -> None:
    """继续执行只允许 paused 状态。"""
    machine = RunStateMachine()

    assert machine.can_continue(RunStatus.PAUSED)
    for status in set(RunStatus) - {RunStatus.PAUSED}:
        assert not machine.can_continue(status)


def test_idempotency_payload_conflict_uses_dedicated_exception() -> None:
    """幂等 payload 冲突使用 RunIdempotencyConflictError，而非状态迁移异常。"""
    exc = RunIdempotencyConflictError("client-1")

    assert exc.code == 61010
    assert not isinstance(exc, RunInvalidTransitionError)
