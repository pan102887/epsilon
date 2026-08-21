"""Run workflow 协作上下文单元测试模块。"""

from __future__ import annotations

import asyncio

from domain.run.workflow import CollaborationLimit, WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    get_workflow_collaboration_context,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)


def _context(run_id: str, depth: int = 0) -> WorkflowCollaborationContext:
    """构造测试用协作上下文。"""

    return WorkflowCollaborationContext(
        run_id=run_id,
        workflow_name="code_change",
        phase=WorkflowPhase.EXECUTE,
        source_role="executor",
        limit=CollaborationLimit(),
        depth=depth,
        handoff_count=0,
        delegation_count=0,
    )


def test_workflow_collaboration_context_defaults_to_none() -> None:
    """未设置上下文时应返回 None。"""
    assert get_workflow_collaboration_context() is None


def test_workflow_collaboration_context_set_get_reset() -> None:
    """set/get/reset 应在当前执行上下文内成对工作。"""
    ctx = _context("run-1")

    token = set_workflow_collaboration_context(ctx)
    try:
        assert get_workflow_collaboration_context() is ctx
    finally:
        reset_workflow_collaboration_context(token)

    assert get_workflow_collaboration_context() is None


def test_workflow_collaboration_context_nested_reset_restores_previous() -> None:
    """嵌套设置后 reset 内层 token 应恢复外层上下文。"""
    outer = _context("run-outer", depth=0)
    inner = _context("run-inner", depth=1)

    outer_token = set_workflow_collaboration_context(outer)
    try:
        inner_token = set_workflow_collaboration_context(inner)
        try:
            assert get_workflow_collaboration_context() is inner
        finally:
            reset_workflow_collaboration_context(inner_token)
        assert get_workflow_collaboration_context() is outer
    finally:
        reset_workflow_collaboration_context(outer_token)

    assert get_workflow_collaboration_context() is None


def test_workflow_collaboration_context_is_isolated_between_async_tasks() -> None:
    """ContextVar 值应在并发 asyncio task 之间隔离。"""

    async def worker(run_id: str) -> tuple[str, str | None]:
        ctx = _context(run_id)
        token = set_workflow_collaboration_context(ctx)
        try:
            await asyncio.sleep(0)
            current = get_workflow_collaboration_context()
            return run_id, current.run_id if current else None
        finally:
            reset_workflow_collaboration_context(token)

    async def main() -> list[tuple[str, str | None]]:
        return list(await asyncio.gather(worker("run-a"), worker("run-b")))

    assert sorted(asyncio.run(main())) == [("run-a", "run-a"), ("run-b", "run-b")]
    assert get_workflow_collaboration_context() is None
