"""本地文件 Run 观察存储契约测试。"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domain.run.exceptions import RunLeaseConflictError
from domain.run.ports import ApprovalResumeStoreResult
from domain.run.value_objects import RunCreateRequest, RunEventType, RunKind, RunPayload, RunStatus
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import (
    FileLock,
    LockFactory,
    LockHandle,
    LockMode,
)
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter

pytestmark = pytest.mark.asyncio


def _adapter(tmp_path: Path) -> LocalFileRunStoreAdapter:
    """使用真实本地文件 helper 构造测试适配器。"""

    return LocalFileRunStoreAdapter(
        root=tmp_path,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _request() -> RunCreateRequest:
    """构造带历史协作摘要字段的 Run 创建请求。"""

    return RunCreateRequest(
        payload=RunPayload(
            kind=RunKind.CHAT,
            session_id="session-1",
            chat={"message": "hello"},
            model="model-a",
        ),
        client_request_id=None,
        collaboration_summary={
            "recent_steps": [{"link_id": "legacy-step", "action": "handoff"}],
            "handoff_count": 1,
        },
    )


class _SignalingLock:
    """为指定锁获取动作发出信号的测试包装器。"""

    def __init__(self, delegate: FileLock, *, wait_started: threading.Event) -> None:
        """初始化带信号的锁包装器。"""

        self._delegate = delegate
        self._wait_started = wait_started

    def acquire(self, mode: LockMode) -> LockHandle:
        """在获取独占锁前通知测试线程。"""

        if mode is LockMode.EXCLUSIVE:
            self._wait_started.set()
        return self._delegate.acquire(mode)


async def test_record_runtime_observation_updates_event_cursor_and_summary_atomically(
    tmp_path: Path,
) -> None:
    """观察写入应在同一锁区内追加事件并同步更新 snapshot 摘要。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None

    stale_summary = {
        "action": "require_approval",
        "last_event_cursor": 999,
        "metadata": {"tool_name": "shell_exec"},
    }
    snapshot, event = await store.record_runtime_observation(
        run_id=created.run_id,
        owner_id="owner-a",
        event_type=RunEventType.GUARDRAIL_BLOCKED,
        payload={"action": "require_approval", "reason": "tool_risk_gate_required"},
        guardrail_summary=stale_summary,
        workflow_run_state={"current_phase": "execute"},
        collaboration_summary={
            "recent_steps": [{"link_id": "legacy-write", "action": "handoff"}],
            "handoff_count": 2,
        },
    )
    loaded = await store.get_run(created.run_id)
    events = await store.list_events(created.run_id, after_cursor=None, limit=10)

    assert stale_summary == {
        "action": "require_approval",
        "last_event_cursor": 999,
        "metadata": {"tool_name": "shell_exec"},
    }
    assert event.cursor == 1
    assert snapshot.latest_event_cursor == 1
    assert loaded == snapshot
    assert loaded is not None
    assert loaded.guardrail_summary == {
        "action": "require_approval",
        "last_event_cursor": 1,
        "metadata": {"tool_name": "shell_exec"},
    }
    assert loaded.guardrail_summary is not None
    assert (
        event.cursor
        == snapshot.latest_event_cursor
        == loaded.guardrail_summary["last_event_cursor"]
    )
    assert loaded.workflow_run_state == {"current_phase": "execute"}
    assert loaded.collaboration_summary == {
        "latest_steps": [{"link_id": "legacy-write", "action": "handoff"}],
        "handoff_count": 2,
    }
    assert events == [event]


async def test_record_runtime_observation_preserves_existing_optional_summaries_when_none(
    tmp_path: Path,
) -> None:
    """观察写入传入 None 时应保留已存在摘要值。"""

    store = _adapter(tmp_path)
    created = await store.create_run(
        RunCreateRequest(
            payload=RunPayload(
                kind=RunKind.CHAT,
                session_id="session-1",
                chat={"message": "hello"},
                model="model-a",
            ),
            client_request_id=None,
            guardrail_summary={"action": "observe"},
            workflow_run_state={"current_phase": "plan"},
            collaboration_summary={"latest_steps": [{"link_id": "step-1"}]},
        )
    )
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None

    snapshot, event = await store.record_runtime_observation(
        run_id=created.run_id,
        owner_id="owner-a",
        event_type=RunEventType.GUARDRAIL_EVALUATED,
        payload={"action": "observe"},
        guardrail_summary={"action": "observe", "last_event_cursor": 999},
    )

    assert event.cursor == 1
    assert snapshot.guardrail_summary == {"action": "observe", "last_event_cursor": 1}
    assert snapshot.workflow_run_state == {"current_phase": "plan"}
    assert snapshot.collaboration_summary == {"latest_steps": [{"link_id": "step-1"}]}


async def test_record_runtime_observation_keeps_cursor_monotonic_across_multiple_writes(
    tmp_path: Path,
) -> None:
    """同一 Run 的多次观察写入应保持 cursor 单调递增且摘要游标同步。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None

    first_snapshot, first_event = await store.record_runtime_observation(
        run_id=created.run_id,
        owner_id="owner-a",
        event_type=RunEventType.GUARDRAIL_EVALUATED,
        payload={"action": "observe", "round": 1},
        guardrail_summary={"action": "observe", "last_event_cursor": 999},
    )
    second_snapshot, second_event = await store.record_runtime_observation(
        run_id=created.run_id,
        owner_id="owner-a",
        event_type=RunEventType.GUARDRAIL_BLOCKED,
        payload={"action": "require_approval", "round": 2},
        guardrail_summary={"action": "require_approval", "last_event_cursor": 999},
    )
    events = await store.list_events(created.run_id, after_cursor=None, limit=10)

    assert first_event.cursor == 1
    assert first_snapshot.latest_event_cursor == 1
    assert first_snapshot.guardrail_summary == {"action": "observe", "last_event_cursor": 1}
    assert second_event.cursor == 2
    assert second_snapshot.latest_event_cursor == 2
    assert second_snapshot.guardrail_summary == {
        "action": "require_approval",
        "last_event_cursor": 2,
    }
    assert events == [first_event, second_event]


async def test_record_runtime_observation_rejects_owner_mismatch(tmp_path: Path) -> None:
    """观察写入必须校验当前 Run 租约 owner。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    await store.claim_next(owner_id="owner-a", lease_seconds=60)

    with pytest.raises(RunLeaseConflictError):
        await store.record_runtime_observation(
            run_id=created.run_id,
            owner_id="owner-b",
            event_type=RunEventType.GUARDRAIL_EVALUATED,
            payload={"action": "observe"},
        )


async def test_approval_resume_lease_allows_guardrail_observation_on_awaiting_run(
    tmp_path: Path,
) -> None:
    """审批恢复短租约应允许 awaiting_approval Run 继续写入 guardrail 观察。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    await store.claim_next(owner_id="worker-a", lease_seconds=60)
    await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="worker-a",
        approval_id="approval-1",
        result={"status": "approval_required"},
    )

    leased = await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        lease_seconds=60,
    )
    snapshot, event = await store.record_runtime_observation(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        event_type=RunEventType.GUARDRAIL_BLOCKED,
        payload={"action": "require_approval", "approval_id": "approval-2"},
        guardrail_summary={
            "action": "require_approval",
            "blocked_count": 2,
            "approval_request_count": 2,
            "last_event_cursor": 999,
        },
    )

    assert leased.lease is not None
    assert leased.lease.owner_id == "approval-resume-a"
    assert snapshot.guardrail_summary is not None
    assert event.cursor == snapshot.guardrail_summary["last_event_cursor"]
    assert snapshot.guardrail_summary["blocked_count"] == 2


async def test_approval_resume_lease_release_only_clears_matching_awaiting_owner(
    tmp_path: Path,
) -> None:
    """审批恢复异常释放只清理当前 awaiting_approval owner 的短租约。"""

    store = _adapter(tmp_path)
    created = await store.create_run(
        RunCreateRequest(
            payload=RunPayload(
                kind=RunKind.CHAT,
                session_id="session-1",
                chat={"message": "hello"},
                model="model-a",
            ),
            client_request_id=None,
            guardrail_summary={"action": "require_approval", "evaluation_count": 1},
        )
    )
    await store.claim_next(owner_id="worker-a", lease_seconds=60)
    await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="worker-a",
        approval_id="approval-1",
        result={"status": "approval_required"},
    )
    await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        lease_seconds=60,
    )

    released = await store.release_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
    )

    assert released.status is RunStatus.AWAITING_APPROVAL
    assert released.lease is None
    assert released.guardrail_summary == {"action": "require_approval", "evaluation_count": 1}

    leased_again = await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-b",
        lease_seconds=60,
    )
    assert leased_again.lease is not None
    assert leased_again.lease.owner_id == "approval-resume-b"


async def test_approval_resume_lease_rejects_concurrent_resume_owner(tmp_path: Path) -> None:
    """审批恢复短租约未过期时应拒绝另一个审批恢复 owner 并发写入。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    await store.claim_next(owner_id="worker-a", lease_seconds=60)
    await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="worker-a",
        approval_id="approval-1",
        result={"status": "approval_required"},
    )
    await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        lease_seconds=60,
    )

    with pytest.raises(RunLeaseConflictError):
        await store.acquire_approval_resume_lease(
            run_id=created.run_id,
            owner_id="approval-resume-b",
            lease_seconds=60,
        )


async def test_resolve_approval_resume_rejects_stale_resume_owner_after_new_owner_acquires(
    tmp_path: Path,
) -> None:
    """旧审批恢复 owner 不得在新短租约 owner 建立后完成状态迁移。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    await store.claim_next(owner_id="worker-a", lease_seconds=60)
    await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="worker-a",
        approval_id="approval-1",
        result={"status": "approval_required"},
        guardrail_summary={"action": "require_approval", "evaluation_count": 1},
    )
    await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
        lease_seconds=60,
    )
    await store.release_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-a",
    )
    await store.acquire_approval_resume_lease(
        run_id=created.run_id,
        owner_id="approval-resume-b",
        lease_seconds=60,
    )

    with pytest.raises(RunLeaseConflictError):
        await store.resolve_approval_resume(
            run_id=created.run_id,
            owner_id="approval-resume-a",
            result=ApprovalResumeStoreResult(status="queued", result={"accepted": True}),
        )

    loaded = await store.get_run(created.run_id)
    assert loaded is not None
    assert loaded.status is RunStatus.AWAITING_APPROVAL
    assert loaded.lease is not None
    assert loaded.lease.owner_id == "approval-resume-b"
    assert loaded.guardrail_summary == {"action": "require_approval", "evaluation_count": 1}

    resolved = await store.resolve_approval_resume(
        run_id=created.run_id,
        owner_id="approval-resume-b",
        result=ApprovalResumeStoreResult(status="queued", result={"accepted": True}),
    )
    assert resolved.status is RunStatus.QUEUED


@pytest.mark.parametrize(
    ("lease_seconds", "record_now"),
    [
        pytest.param(0, datetime(2026, 1, 1, tzinfo=UTC), id="zero-lease"),
        pytest.param(
            60,
            datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=61),
            id="expired-lease",
        ),
    ],
)
async def test_record_runtime_observation_rejects_stale_lease_without_appending_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease_seconds: int,
    record_now: datetime,
) -> None:
    """观察写入必须拒绝零租约或过期租约，且不得追加事件。"""

    claim_now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(
        LocalFileRunStoreAdapter,
        "_now",
        staticmethod(lambda: claim_now),
    )
    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=lease_seconds)
    assert claimed is not None

    monkeypatch.setattr(
        LocalFileRunStoreAdapter,
        "_now",
        staticmethod(lambda: record_now),
    )

    with pytest.raises(RunLeaseConflictError):
        await store.record_runtime_observation(
            run_id=created.run_id,
            owner_id="owner-a",
            event_type=RunEventType.GUARDRAIL_EVALUATED,
            payload={"action": "observe"},
            guardrail_summary={"action": "observe", "last_event_cursor": 1},
        )

    loaded = await store.get_run(created.run_id)
    events = await store.list_events(created.run_id, after_cursor=None, limit=10)

    assert loaded is not None
    assert loaded.latest_event_cursor is None
    assert loaded.lease == claimed.lease
    assert events == []


async def test_record_runtime_observation_rejects_lease_that_expires_while_waiting_for_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """方法入口租约未过期、等待锁后过期时必须拒绝写入且不追加事件。"""

    claim_now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(
        LocalFileRunStoreAdapter,
        "_now",
        staticmethod(lambda: claim_now),
    )

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None

    before_wait = claim_now + timedelta(seconds=59)
    after_wait = claim_now + timedelta(seconds=61)
    wait_started = threading.Event()
    monkeypatch.setattr(
        LocalFileRunStoreAdapter,
        "_now",
        staticmethod(lambda: after_wait if wait_started.is_set() else before_wait),
    )

    run_lock_path = store.run_lock_path(created.run_id)
    lock_factory = LockFactory(acquire_timeout_ms=1000)
    blocking_lock = lock_factory(run_lock_path)

    def signaling_lock_factory(path: Path) -> FileLock:
        lock = lock_factory(path)
        if path == run_lock_path:
            return _SignalingLock(lock, wait_started=wait_started)
        return lock

    blocked_store = LocalFileRunStoreAdapter(
        root=tmp_path,
        lock_factory=signaling_lock_factory,
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )

    async def record_with_wait() -> None:
        await blocked_store.record_runtime_observation(
            run_id=created.run_id,
            owner_id="owner-a",
            event_type=RunEventType.GUARDRAIL_EVALUATED,
            payload={"action": "observe"},
            guardrail_summary={"action": "observe", "last_event_cursor": 1},
        )

    record_error: list[BaseException] = []

    def run_record_with_wait() -> None:
        try:
            asyncio.run(record_with_wait())
        except BaseException as exc:
            record_error.append(exc)

    with blocking_lock.acquire(LockMode.EXCLUSIVE):
        record_thread = threading.Thread(target=run_record_with_wait)
        record_thread.start()
        assert wait_started.wait(1.0)

    record_thread.join(timeout=5)
    assert not record_thread.is_alive()
    assert len(record_error) == 1
    assert isinstance(record_error[0], RunLeaseConflictError)

    loaded = await store.get_run(created.run_id)
    events = await store.list_events(created.run_id, after_cursor=None, limit=10)

    assert loaded is not None
    assert loaded.latest_event_cursor is None
    assert loaded.lease == claimed.lease
    assert events == []


async def test_snapshot_deserialization_maps_legacy_recent_steps_without_rewriting_file(
    tmp_path: Path,
) -> None:
    """历史 snapshot 仅含 recent_steps 时读取结果应归一为 latest_steps。"""

    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    path = store.snapshot_path(created.run_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["collaboration_summary"] = {
        "recent_steps": [{"link_id": "legacy-only", "action": "delegation"}],
        "delegation_count": 1,
    }
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = await store.get_run(created.run_id)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert loaded is not None
    assert loaded.status is RunStatus.QUEUED
    assert loaded.collaboration_summary == {
        "latest_steps": [{"link_id": "legacy-only", "action": "delegation"}],
        "delegation_count": 1,
    }
    assert persisted["collaboration_summary"] == {
        "recent_steps": [{"link_id": "legacy-only", "action": "delegation"}],
        "delegation_count": 1,
    }
