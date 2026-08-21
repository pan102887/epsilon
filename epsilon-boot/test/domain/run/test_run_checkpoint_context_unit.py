"""Run checkpoint ContextVar 单元测试模块。"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    get_run_checkpoint_context,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.ports import RunCheckpointSinkPort


class _FakeCheckpointSink:
    """测试用 sink 占位对象。"""


def _context(
    run_id: str,
    *,
    owner_id: str = "worker-1",
    segment_index: int = 0,
    recovery_mode: bool = False,
) -> RunCheckpointExecutionContext:
    return RunCheckpointExecutionContext(
        run_id=run_id,
        owner_id=owner_id,
        segment_index=segment_index,
        recovery_mode=recovery_mode,
        sink=cast(RunCheckpointSinkPort, _FakeCheckpointSink()),
    )


def test_checkpoint_context_defaults_to_none() -> None:
    """未设置时同步 Chat/Task 入口应保持无 checkpoint 行为。"""
    assert get_run_checkpoint_context() is None


def test_checkpoint_context_set_and_reset_restores_none() -> None:
    """set 返回的 token 可把上下文恢复到未设置状态。"""
    ctx = _context("run-1")

    token = set_run_checkpoint_context(ctx)
    try:
        assert get_run_checkpoint_context() is ctx
    finally:
        reset_run_checkpoint_context(token)

    assert get_run_checkpoint_context() is None


def test_checkpoint_context_nested_tokens_restore_previous_value() -> None:
    """嵌套 set/reset 必须按 token 恢复到上一层上下文。"""
    outer = _context("run-outer")
    inner = _context("run-inner", owner_id="worker-2", segment_index=3, recovery_mode=True)

    outer_token = set_run_checkpoint_context(outer)
    try:
        inner_token = set_run_checkpoint_context(inner)
        try:
            assert get_run_checkpoint_context() is inner
        finally:
            reset_run_checkpoint_context(inner_token)

        assert get_run_checkpoint_context() is outer
    finally:
        reset_run_checkpoint_context(outer_token)

    assert get_run_checkpoint_context() is None


@pytest.mark.asyncio
async def test_checkpoint_context_is_isolated_between_async_tasks() -> None:
    """并发 async task 分别设置上下文时不得互相覆盖。"""

    async def worker(ctx: RunCheckpointExecutionContext) -> tuple[str, str | None]:
        token = set_run_checkpoint_context(ctx)
        try:
            await asyncio.sleep(0)
            current = get_run_checkpoint_context()
            return ctx.run_id, current.run_id if current is not None else None
        finally:
            reset_run_checkpoint_context(token)

    first = _context("run-a")
    second = _context("run-b")

    results = await asyncio.gather(worker(first), worker(second))

    assert sorted(results) == [("run-a", "run-a"), ("run-b", "run-b")]
    assert get_run_checkpoint_context() is None


def test_checkpoint_execution_context_is_frozen() -> None:
    """执行上下文是不可变值对象，避免运行期被工具链意外改写。"""
    ctx = _context("run-1")

    with pytest.raises(FrozenInstanceError):
        ctx.run_id = "changed"  # type: ignore[misc]
