"""LocalFileSessionContextAdapter CAS 单元测试。"""

from pathlib import Path

import pytest

from domain.chat.context import ConversationContext
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import (
    CrossPlatformFileLock,
)
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.session.local_file_session_context_adapter import (
    LocalFileSessionContextAdapter,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """临时持久化根目录。"""
    return tmp_path


@pytest.fixture
def adapter(tmp_root: Path) -> LocalFileSessionContextAdapter:
    """创建真实本地文件适配器。"""
    policy = CrossPlatformPathPolicy()
    writer = TempFileAtomicWriter(fsync_on_write=False)

    def lock_factory(path: Path) -> CrossPlatformFileLock:
        return CrossPlatformFileLock(path, acquire_timeout_ms=5000)

    return LocalFileSessionContextAdapter(
        root=tmp_root,
        lock_factory=lock_factory,
        path_policy=policy,
        atomic_writer=writer,
    )


@pytest.mark.asyncio
async def test_cas_single_writer_success(adapter: LocalFileSessionContextAdapter) -> None:
    """单写者 CAS 成功。"""

    async def mutator(ctx: ConversationContext) -> str:
        ctx.add_user_message("cas-hello")
        return "done"

    result = await adapter.compare_and_swap("sess-file-1", mutator)
    assert result == "done"

    loaded = await adapter.load("sess-file-1")
    assert loaded.get_messages()[0].content == "cas-hello"


@pytest.mark.asyncio
async def test_cas_two_writers_no_lost_update(
    adapter: LocalFileSessionContextAdapter,
) -> None:
    """两次顺序 CAS 不丢更新（文件锁在同一事件循环中串行化）。"""

    async def writer_a(ctx: ConversationContext) -> str:
        ctx.add_user_message("from_a")
        return "a"

    async def writer_b(ctx: ConversationContext) -> str:
        ctx.add_user_message("from_b")
        return "b"

    result_a = await adapter.compare_and_swap("sess-file-2", writer_a)
    result_b = await adapter.compare_and_swap("sess-file-2", writer_b)
    assert result_a == "a"
    assert result_b == "b"

    loaded = await adapter.load("sess-file-2")
    messages = loaded.get_messages()
    contents = {m.content for m in messages}
    assert "from_a" in contents
    assert "from_b" in contents


@pytest.mark.asyncio
async def test_cas_does_not_raise_session_conflict_error(
    adapter: LocalFileSessionContextAdapter,
) -> None:
    """文件锁路径不抛 SessionConflictError。"""

    async def mutator(ctx: ConversationContext) -> None:
        ctx.add_user_message("no-conflict")
        return None

    result = await adapter.compare_and_swap("sess-file-3", mutator)
    assert result is None


@pytest.mark.asyncio
async def test_cas_os_error_logged_and_propagated(tmp_root: Path) -> None:
    """底层 OSError 记录日志后透传。"""
    policy = CrossPlatformPathPolicy()

    class FailingWriter(TempFileAtomicWriter):
        def write_bytes_atomic(self, target: Path, payload: bytes) -> None:
            raise PermissionError("no write")

    def lock_factory(path: Path) -> CrossPlatformFileLock:
        return CrossPlatformFileLock(path, acquire_timeout_ms=5000)

    adapter = LocalFileSessionContextAdapter(
        root=tmp_root,
        lock_factory=lock_factory,
        path_policy=policy,
        atomic_writer=FailingWriter(fsync_on_write=False),
    )

    async def mutator(ctx: ConversationContext) -> None:
        ctx.add_user_message("will-fail")
        return None

    with pytest.raises(PermissionError):
        await adapter.compare_and_swap("sess-fail", mutator)
