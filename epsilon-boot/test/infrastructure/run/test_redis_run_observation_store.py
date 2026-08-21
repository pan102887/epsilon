"""Redis Run 观察存储契约测试。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any

import pytest
import redis.asyncio as aioredis

fakeredis = pytest.importorskip("fakeredis.aioredis")

from domain.run.exceptions import RunLeaseConflictError  # noqa: E402
from domain.run.ports import ApprovalResumeStoreResult  # noqa: E402
from domain.run.value_objects import (  # noqa: E402
    RunCreateRequest,
    RunEventType,
    RunKind,
    RunPayload,
    RunStatus,
)
from infrastructure.run.redis_run_store_adapter import RedisRunStoreAdapter  # noqa: E402

pytestmark = pytest.mark.asyncio


class _ConflictOncePipelineContext:
    """在首次 execute 时注入 WatchError 的 pipeline 包装器。"""

    def __init__(self, delegate: Any, *, conflict_state: dict[str, bool]) -> None:
        """初始化冲突注入包装器。"""

        self._delegate = delegate
        self._conflict_state = conflict_state

    async def __aenter__(self) -> Any:
        """进入底层 pipeline 上下文并包装 execute。"""

        pipe = await self._delegate.__aenter__()
        original_execute = pipe.execute

        async def execute(*args: Any, **kwargs: Any) -> Any:
            if not self._conflict_state["raised"]:
                self._conflict_state["raised"] = True
                raise aioredis.WatchError("conflict")
            return await original_execute(*args, **kwargs)

        pipe.execute = execute
        return pipe

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        """退出底层 pipeline 上下文。"""

        return await self._delegate.__aexit__(exc_type, exc, tb)


@pytest.fixture
async def store() -> AsyncIterator[RedisRunStoreAdapter]:
    """构造 fakeredis Run store。"""

    client = fakeredis.FakeRedis()
    try:
        yield RedisRunStoreAdapter(redis_client=client, conflict_retry_max=8)
    finally:
        await client.aclose()


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


async def test_record_runtime_observation_updates_event_cursor_and_summary_atomically(
    store: RedisRunStoreAdapter,
) -> None:
    """观察写入应在同一事务内追加事件并同步更新 snapshot 摘要。"""

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
    store: RedisRunStoreAdapter,
) -> None:
    """观察写入传入 None 时应保留已存在摘要值。"""

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
    store: RedisRunStoreAdapter,
) -> None:
    """同一 Run 的多次观察写入应保持 cursor 单调递增且摘要游标同步。"""

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


async def test_record_runtime_observation_rejects_owner_mismatch(
    store: RedisRunStoreAdapter,
) -> None:
    """观察写入必须校验当前 Run 租约 owner。"""

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
    store: RedisRunStoreAdapter,
) -> None:
    """审批恢复短租约应允许 awaiting_approval Run 继续写入 guardrail 观察。"""

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
    store: RedisRunStoreAdapter,
) -> None:
    """审批恢复异常释放只清理当前 awaiting_approval owner 的短租约。"""

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


async def test_approval_resume_lease_rejects_concurrent_resume_owner(
    store: RedisRunStoreAdapter,
) -> None:
    """审批恢复短租约未过期时应拒绝另一个审批恢复 owner 并发写入。"""

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
    store: RedisRunStoreAdapter,
) -> None:
    """旧审批恢复 owner 不得在新短租约 owner 建立后完成状态迁移。"""

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
    store: RedisRunStoreAdapter,
    monkeypatch: pytest.MonkeyPatch,
    lease_seconds: int,
    record_now: datetime,
) -> None:
    """观察写入必须拒绝零租约或过期租约，且不得追加事件。"""

    claim_now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(
        RedisRunStoreAdapter,
        "_now",
        staticmethod(lambda: claim_now),
    )
    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=lease_seconds)
    assert claimed is not None

    monkeypatch.setattr(
        RedisRunStoreAdapter,
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


async def test_record_runtime_observation_retries_after_watch_conflict(
    store: RedisRunStoreAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WATCH 冲突时应按既有适配器惯例重试，并只写入一次事件。"""

    created = await store.create_run(_request())
    claimed = await store.claim_next(owner_id="owner-a", lease_seconds=60)
    assert claimed is not None

    original_pipeline = store.redis_client.pipeline
    conflict_state = {"raised": False}

    def pipeline_with_conflict(*args: Any, **kwargs: Any) -> _ConflictOncePipelineContext:
        return _ConflictOncePipelineContext(
            original_pipeline(*args, **kwargs),
            conflict_state=conflict_state,
        )

    monkeypatch.setattr(store.redis_client, "pipeline", pipeline_with_conflict)

    snapshot, event = await store.record_runtime_observation(
        run_id=created.run_id,
        owner_id="owner-a",
        event_type=RunEventType.GUARDRAIL_EVALUATED,
        payload={"action": "observe"},
        guardrail_summary={"action": "observe", "last_event_cursor": 999},
    )

    monkeypatch.setattr(store.redis_client, "pipeline", original_pipeline)
    loaded = await store.get_run(created.run_id)
    events = await store.list_events(created.run_id, after_cursor=None, limit=10)

    assert conflict_state["raised"] is True
    assert event.cursor == 1
    assert snapshot.latest_event_cursor == 1
    assert loaded is not None
    assert loaded.latest_event_cursor == 1
    assert loaded.guardrail_summary == {"action": "observe", "last_event_cursor": 1}
    assert events == [event]


async def test_snapshot_deserialization_maps_legacy_recent_steps_without_rewriting_value(
    store: RedisRunStoreAdapter,
) -> None:
    """历史 snapshot 仅含 recent_steps 时读取结果应归一为 latest_steps。"""

    created = await store.create_run(_request())
    key = store.snapshot_key(created.run_id)
    raw = await store.redis_client.get(key)
    assert raw is not None
    data = json.loads(raw)
    data["collaboration_summary"] = {
        "recent_steps": [{"link_id": "legacy-only", "action": "delegation"}],
        "delegation_count": 1,
    }
    await store.redis_client.set(key, json.dumps(data))

    loaded = await store.get_run(created.run_id)
    persisted_raw = await store.redis_client.get(key)
    assert persisted_raw is not None
    persisted = json.loads(persisted_raw)

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
