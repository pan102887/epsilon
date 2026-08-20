"""本地文件审批状态存储单元测试模块。"""

import time
from pathlib import Path

from domain.agent.value_objects import ApprovalInterrupt, PendingActionRequest
from infrastructure.agent.approval_state_store import LocalFileApprovalStateStore
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy


def _make_store(root: Path, ttl_seconds: int = 3600) -> LocalFileApprovalStateStore:
    """构造本地文件审批状态存储。"""
    return LocalFileApprovalStateStore(
        root=root,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
        ttl_seconds=ttl_seconds,
    )


def _interrupt(
    session_id: str = "session-1",
    approval_id: str = "approval-1",
    expires_at_epoch: float = 0.0,
) -> ApprovalInterrupt:
    """构造审批中断测试对象。"""
    return ApprovalInterrupt(
        session_id=session_id,
        approval_id=approval_id,
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="write_file",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        context_snapshot={"messages": [{"role": "assistant", "content": ""}]},
        round_num=1,
        model="gpt-test",
        usage_so_far={"total_tokens": 3},
        created_at_epoch=time.time(),
        expires_at_epoch=expires_at_epoch,
    )


async def test_save_load_consume_delete_roundtrip(tmp_path: Path) -> None:
    """验证 save/load/consume/delete 基本语义。"""
    store = _make_store(tmp_path)
    interrupt = _interrupt()

    await store.save(interrupt)
    loaded = await store.load("session-1", "approval-1")
    assert loaded is not None
    assert loaded.session_id == "session-1"
    assert loaded.actions[0].tool_call_id == "call-1"

    consumed = await store.consume("session-1", "approval-1")
    assert consumed is not None
    assert consumed.approval_id == "approval-1"
    assert await store.consume("session-1", "approval-1") is None

    await store.delete("session-1", "approval-1")


async def test_load_expired_returns_none(tmp_path: Path) -> None:
    """验证过期状态 load 返回 None。"""
    store = _make_store(tmp_path)
    await store.save(_interrupt(expires_at_epoch=time.time() - 1))

    assert await store.load("session-1", "approval-1") is None


async def test_consume_expired_deletes_and_returns_none(tmp_path: Path) -> None:
    """验证过期状态 consume 返回 None 且后续不可再读。"""
    store = _make_store(tmp_path)
    await store.save(_interrupt(expires_at_epoch=time.time() - 1))

    assert await store.consume("session-1", "approval-1") is None
    assert await store.load("session-1", "approval-1") is None


async def test_delete_session_removes_all_session_approvals(tmp_path: Path) -> None:
    """验证 delete_session 删除同一 session 下全部审批。"""
    store = _make_store(tmp_path)
    await store.save(_interrupt(approval_id="approval-1"))
    await store.save(_interrupt(approval_id="approval-2"))

    await store.delete_session("session-1")

    assert await store.load("session-1", "approval-1") is None
    assert await store.load("session-1", "approval-2") is None


async def test_list_pending_by_session_returns_unexpired_summaries(
    tmp_path: Path,
) -> None:
    """验证按会话列出未过期审批摘要且不消费状态。"""
    store = _make_store(tmp_path)
    await store.save(
        _interrupt(
            approval_id="approval-1",
            expires_at_epoch=time.time() + 60,
        )
    )
    await store.save(
        _interrupt(
            session_id="session-2",
            approval_id="approval-other",
            expires_at_epoch=time.time() + 60,
        )
    )

    summaries = await store.list_pending_by_session("session-1")

    assert len(summaries) == 1
    assert summaries[0].session_id == "session-1"
    assert summaries[0].approval_id == "approval-1"
    assert summaries[0].action_count == 1
    assert summaries[0].tool_names == ("write_file",)
    assert summaries[0].expired is False
    assert await store.load("session-1", "approval-1") is not None


async def test_list_pending_by_session_filters_expired(
    tmp_path: Path,
) -> None:
    """验证过期审批不会出现在摘要列表中。"""
    store = _make_store(tmp_path)
    await store.save(
        _interrupt(
            approval_id="expired",
            expires_at_epoch=time.time() - 1,
        )
    )

    assert await store.list_pending_by_session("session-1") == []
    assert await store.load("session-1", "expired") is None
