"""Run 执行上下文单元测试模块。"""

from __future__ import annotations

import asyncio

from domain.run.runtime_context import (
    RunExecutionContext,
    get_run_execution_context,
    reset_run_execution_context,
    set_run_execution_context,
)


def _context(run_id: str, segment_index: int = 1) -> RunExecutionContext:
    """构造测试用 Run 执行上下文。"""

    return RunExecutionContext(
        run_id=run_id,
        owner_id=f"owner-{run_id}",
        segment_index=segment_index,
        recovery_mode=segment_index > 1,
    )


def test_run_execution_context_defaults_to_none() -> None:
    """未设置上下文时应返回 None。"""

    assert get_run_execution_context() is None


def test_run_execution_context_set_get_reset() -> None:
    """set/get/reset 应在当前执行上下文内成对工作。"""

    context = _context("run-1")

    token = set_run_execution_context(context)
    try:
        assert get_run_execution_context() is context
    finally:
        reset_run_execution_context(token)

    assert get_run_execution_context() is None


def test_run_execution_context_nested_reset_restores_previous() -> None:
    """嵌套设置后 reset 内层 token 应恢复外层上下文。"""

    outer = _context("run-outer", segment_index=1)
    inner = _context("run-inner", segment_index=2)

    outer_token = set_run_execution_context(outer)
    try:
        inner_token = set_run_execution_context(inner)
        try:
            assert get_run_execution_context() is inner
        finally:
            reset_run_execution_context(inner_token)
        assert get_run_execution_context() is outer
    finally:
        reset_run_execution_context(outer_token)

    assert get_run_execution_context() is None


def test_run_execution_context_is_isolated_between_async_tasks() -> None:
    """ContextVar 值应在并发 asyncio task 之间隔离。"""

    async def worker(run_id: str, segment_index: int) -> tuple[str, str | None, int | None]:
        context = _context(run_id, segment_index=segment_index)
        token = set_run_execution_context(context)
        try:
            await asyncio.sleep(0)
            current = get_run_execution_context()
            return (
                run_id,
                current.run_id if current else None,
                current.segment_index if current else None,
            )
        finally:
            reset_run_execution_context(token)

    async def main() -> list[tuple[str, str | None, int | None]]:
        return list(await asyncio.gather(worker("run-a", 1), worker("run-b", 2)))

    assert sorted(asyncio.run(main())) == [
        ("run-a", "run-a", 1),
        ("run-b", "run-b", 2),
    ]
    assert get_run_execution_context() is None
