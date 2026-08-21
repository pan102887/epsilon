"""Redis Run Store workflow 字段单元测试。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest

fakeredis = pytest.importorskip("fakeredis.aioredis")

from domain.run.exceptions import RunLeaseConflictError  # noqa: E402
from domain.run.ports import ApprovalResumeStoreResult  # noqa: E402
from domain.run.value_objects import RunCreateRequest, RunKind, RunPayload  # noqa: E402
from infrastructure.run.redis_run_store_adapter import RedisRunStoreAdapter  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store() -> AsyncGenerator[RedisRunStoreAdapter, None]:
    """构造 fakeredis Run store。"""

    client = fakeredis.FakeRedis()
    try:
        yield RedisRunStoreAdapter(redis_client=client, conflict_retry_max=8)
    finally:
        await client.aclose()


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


async def test_create_run_persists_workflow_fields(
    store: RedisRunStoreAdapter,
) -> None:
    """create_run 应把 RunCreateRequest workflow 字段写入 Redis snapshot。"""
    snapshot = await store.create_run(_request())
    loaded = await store.get_run(snapshot.run_id)

    assert loaded is not None
    assert loaded.workflow_name == "code_change"
    assert loaded.workflow_run_state == {"current_phase": "plan"}
    assert loaded.collaboration_summary == {"latest_steps": []}


async def test_legacy_snapshot_without_workflow_fields_loads_as_none(
    store: RedisRunStoreAdapter,
) -> None:
    """旧 Redis JSON 缺失 workflow 字段时反序列化应得到 None。"""
    snapshot = await store.create_run(_request())
    key = store.snapshot_key(snapshot.run_id)
    raw = await store.redis_client.get(key)
    assert raw is not None
    data = json.loads(raw)
    data.pop("workflow_name", None)
    data.pop("workflow_run_state", None)
    data.pop("collaboration_summary", None)
    await store.redis_client.set(key, json.dumps(data))

    loaded = await store.get_run(snapshot.run_id)

    assert loaded is not None
    assert loaded.workflow_name is None
    assert loaded.workflow_run_state is None
    assert loaded.collaboration_summary is None


async def test_worker_mark_methods_override_or_preserve_workflow_fields(
    store: RedisRunStoreAdapter,
) -> None:
    """worker mark 方法传入字段时覆盖，None 时保留原值。"""
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


async def test_approval_resume_and_recovery_preserve_workflow_fields(
    store: RedisRunStoreAdapter,
) -> None:
    """approval resume 与 recovery 入队应保留或覆盖 workflow 字段。"""
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

    claimed_again = await store.claim_next(owner_id="owner-b", lease_seconds=0)
    assert claimed_again is not None
    recovered = await store.enqueue_recovery(
        run_id=claimed_again.run_id,
        latest_checkpoint_id="chk-1",
        recovery_attempt_count=1,
        workflow_run_state={"current_phase": "evaluate"},
        collaboration_summary={"latest_steps": [{"link_id": "step-2"}]},
    )

    assert recovered.workflow_run_state == {"current_phase": "evaluate"}
    assert recovered.collaboration_summary == {"latest_steps": [{"link_id": "step-2"}]}
    assert recovered.recoverable is True


async def test_owner_validation_still_rejects_wrong_worker(
    store: RedisRunStoreAdapter,
) -> None:
    """新增 workflow 字段不得放宽 worker owner 校验。"""
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.run_id == created.run_id

    with pytest.raises(RunLeaseConflictError):
        await store.mark_paused(
            run_id=claimed.run_id,
            owner_id="owner-b",
            result={},
            workflow_run_state={"current_phase": "execute"},
        )
