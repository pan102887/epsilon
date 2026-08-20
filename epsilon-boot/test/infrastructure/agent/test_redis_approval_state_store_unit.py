"""Redis 审批状态存储单元测试模块。"""

import time
from collections.abc import AsyncIterator

from domain.agent.value_objects import ApprovalInterrupt, PendingActionRequest
from infrastructure.agent.approval_state_store import RedisApprovalStateStore


class FakeRedis:
    """用于测试 RedisApprovalStateStore 的内存 fake。"""

    def __init__(self) -> None:
        """初始化 fake 存储。"""
        self.data: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int) -> None:
        """保存 key。"""
        self.data[key] = value
        self.expires[key] = ex

    async def get(self, key: str) -> str | None:
        """读取 key。"""
        return self.data.get(key)

    async def getdel(self, key: str) -> str | None:
        """原子读取并删除 key。"""
        return self.data.pop(key, None)

    async def delete(self, *keys: str) -> int:
        """删除 key。"""
        count = 0
        for key in keys:
            if key in self.data:
                count += 1
                del self.data[key]
                self.expires.pop(key, None)
        return count

    async def scan_iter(self, match: str) -> AsyncIterator[str]:
        """按前缀扫描 key。"""
        prefix = match.removesuffix("*")
        for key in list(self.data):
            if key.startswith(prefix):
                yield key


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
        context_snapshot={"messages": []},
        round_num=1,
        model="gpt-test",
        created_at_epoch=time.time(),
        expires_at_epoch=expires_at_epoch,
    )


async def test_redis_save_uses_key_and_ttl() -> None:
    """验证 Redis save 使用 key 和 TTL。"""
    redis = FakeRedis()
    store = RedisApprovalStateStore(redis, ttl_seconds=42)  # type: ignore[arg-type]

    await store.save(_interrupt())

    key = "agent:approval:session-1:approval-1"
    assert key in redis.data
    assert redis.expires[key] == 42


async def test_redis_load_and_consume_success() -> None:
    """验证 Redis load/consume 成功路径。"""
    redis = FakeRedis()
    store = RedisApprovalStateStore(redis)  # type: ignore[arg-type]
    await store.save(_interrupt())

    loaded = await store.load("session-1", "approval-1")
    assert loaded is not None
    assert loaded.actions[0].tool_call_id == "call-1"

    consumed = await store.consume("session-1", "approval-1")
    assert consumed is not None
    assert await store.consume("session-1", "approval-1") is None


async def test_redis_delete_and_delete_session() -> None:
    """验证 Redis delete 和 delete_session。"""
    redis = FakeRedis()
    store = RedisApprovalStateStore(redis)  # type: ignore[arg-type]
    await store.save(_interrupt(approval_id="a1"))
    await store.save(_interrupt(approval_id="a2"))

    await store.delete("session-1", "a1")
    assert await store.load("session-1", "a1") is None
    await store.delete_session("session-1")
    assert await store.load("session-1", "a2") is None


async def test_redis_list_pending_by_session_returns_unexpired_summaries() -> None:
    """验证 Redis 按会话列出未过期审批摘要且不消费状态。"""
    redis = FakeRedis()
    store = RedisApprovalStateStore(redis)  # type: ignore[arg-type]
    await store.save(_interrupt(approval_id="a1", expires_at_epoch=time.time() + 60))
    await store.save(
        _interrupt(
            session_id="session-2",
            approval_id="other",
            expires_at_epoch=time.time() + 60,
        )
    )

    summaries = await store.list_pending_by_session("session-1")

    assert len(summaries) == 1
    assert summaries[0].session_id == "session-1"
    assert summaries[0].approval_id == "a1"
    assert summaries[0].action_count == 1
    assert summaries[0].tool_names == ("write_file",)
    assert summaries[0].expired is False
    assert await store.load("session-1", "a1") is not None


async def test_redis_list_pending_by_session_filters_expired() -> None:
    """验证 Redis 过期审批不会出现在摘要列表中。"""
    redis = FakeRedis()
    store = RedisApprovalStateStore(redis)  # type: ignore[arg-type]
    await store.save(_interrupt(approval_id="expired", expires_at_epoch=time.time() - 1))

    assert await store.list_pending_by_session("session-1") == []
    assert await store.load("session-1", "expired") is None
