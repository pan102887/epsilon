"""本地文件 Run Store workflow 字段单元测试。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from domain.run.ports import ApprovalResumeStoreResult
from domain.run.value_objects import RunCreateRequest, RunKind, RunPayload
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter

pytestmark = pytest.mark.asyncio


def _adapter(tmp_path) -> LocalFileRunStoreAdapter:
    """使用真实本地文件 helper 构造测试适配器。"""

    return LocalFileRunStoreAdapter(
        root=tmp_path,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _request() -> RunCreateRequest:
    """构造带 workflow 字段的创建请求。"""

    return RunCreateRequest(
        payload=RunPayload(
            kind=RunKind.TASK,
            session_id="session-1",
            task={"goal": "fix code"},
            model="model-a",
        ),
        client_request_id=None,
        workflow_name="code_change",
        workflow_run_state={"current_phase": "plan"},
        collaboration_summary={"latest_steps": []},
    )


async def test_create_run_persists_workflow_fields(tmp_path) -> None:
    """create_run 应把 RunCreateRequest workflow 字段写入 snapshot。"""
    store = _adapter(tmp_path)

    snapshot = await store.create_run(_request())
    loaded = await store.get_run(snapshot.run_id)

    assert loaded is not None
    assert loaded.workflow_name == "code_change"
    assert loaded.workflow_run_state == {"current_phase": "plan"}
    assert loaded.collaboration_summary == {"latest_steps": []}


async def test_legacy_snapshot_without_workflow_fields_loads_as_none(tmp_path) -> None:
    """旧 JSON 缺失 workflow 字段时反序列化应得到 None。"""
    store = _adapter(tmp_path)
    snapshot = await store.create_run(_request())
    path = store._snapshot_path(snapshot.run_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("workflow_name", None)
    data.pop("workflow_run_state", None)
    data.pop("collaboration_summary", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = await store.get_run(snapshot.run_id)

    assert loaded is not None
    assert loaded.workflow_name is None
    assert loaded.workflow_run_state is None
    assert loaded.collaboration_summary is None


async def test_worker_mark_methods_override_or_preserve_workflow_fields(tmp_path) -> None:
    """worker mark 方法传入字段时覆盖，None 时保留原值。"""
    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.run_id == created.run_id

    paused = await store.mark_paused(
        run_id=claimed.run_id,
        owner_id="owner-a",
        result={"step": "paused"},
        workflow_run_state={"current_phase": "execute"},
        collaboration_summary={"latest_steps": [{"link_id": "step-1"}]},
    )
    assert paused.workflow_run_state == {"current_phase": "execute"}
    assert paused.collaboration_summary == {"latest_steps": [{"link_id": "step-1"}]}

    await store.enqueue_continue(run_id=paused.run_id)
    claimed_again = await store.claim_next(owner_id="owner-b", lease_seconds=60)
    assert claimed_again is not None
    succeeded = await store.mark_succeeded(
        run_id=claimed_again.run_id,
        owner_id="owner-b",
        result={"ok": True},
    )

    assert succeeded.workflow_run_state == {"current_phase": "execute"}
    assert succeeded.collaboration_summary == {"latest_steps": [{"link_id": "step-1"}]}


async def test_approval_resume_preserves_or_overrides_workflow_fields(tmp_path) -> None:
    """approval resume 应保留当前 phase，传入字段时可覆盖。"""
    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None
    awaiting = await store.mark_awaiting_approval(
        run_id=created.run_id,
        owner_id="owner-a",
        approval_id="approval-1",
        result={"status": "approval_required"},
        workflow_run_state={"current_phase": "execute"},
    )

    await store.acquire_approval_resume_lease(
        run_id=awaiting.run_id,
        owner_id="approval-resume-a",
        lease_seconds=60,
    )
    queued = await store.resolve_approval_resume(
        run_id=awaiting.run_id,
        owner_id="approval-resume-a",
        result=ApprovalResumeStoreResult(status="queued", result={"approved": True}),
    )

    assert queued.workflow_run_state == {"current_phase": "execute"}
    assert queued.collaboration_summary == {"latest_steps": []}

    claimed_again = await store.claim_next(owner_id="owner-b", lease_seconds=60)
    assert claimed_again is not None
    awaiting_again = await store.mark_awaiting_approval(
        run_id=claimed_again.run_id,
        owner_id="owner-b",
        approval_id="approval-2",
        result={"status": "approval_required"},
    )
    await store.acquire_approval_resume_lease(
        run_id=awaiting_again.run_id,
        owner_id="approval-resume-b",
        lease_seconds=60,
    )
    succeeded = await store.resolve_approval_resume(
        run_id=awaiting_again.run_id,
        owner_id="approval-resume-b",
        result=ApprovalResumeStoreResult(status="succeeded", result={"ok": True}),
        workflow_run_state={"current_phase": "finalize"},
        collaboration_summary={"latest_steps": [{"link_id": "step-2"}]},
    )

    assert succeeded.workflow_run_state == {"current_phase": "finalize"}
    assert succeeded.collaboration_summary == {"latest_steps": [{"link_id": "step-2"}]}


async def test_enqueue_recovery_preserves_or_overrides_workflow_fields(tmp_path) -> None:
    """recovery 入队应保留当前 phase，传入字段时可覆盖。"""
    store = _adapter(tmp_path)
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=0)
    assert claimed is not None
    assert claimed.run_id == created.run_id

    recovered = await store.enqueue_recovery(
        run_id=claimed.run_id,
        latest_checkpoint_id="chk-1",
        recovery_attempt_count=1,
        workflow_run_state={"current_phase": "evaluate"},
    )

    assert recovered.workflow_run_state == {"current_phase": "evaluate"}
    assert recovered.collaboration_summary == {"latest_steps": []}
    assert recovered.recoverable is True

    claimed_again = await store.claim_next(owner_id="owner-b", lease_seconds=0)
    assert claimed_again is not None
    recovered_again = await store.enqueue_recovery(
        run_id=claimed_again.run_id,
        latest_checkpoint_id="chk-2",
        recovery_attempt_count=2,
    )

    assert recovered_again.workflow_run_state == {"current_phase": "evaluate"}
    assert recovered_again.collaboration_summary == {"latest_steps": []}
    assert recovered_again.lease is None
    assert claimed_again.lease is not None
    assert recovered_again.updated_at >= claimed_again.lease.lease_until - timedelta(0)
