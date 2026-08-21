"""多进程并发集成测试（``Local_File_Session_Context_Adapter``）。

覆盖需求 10.3 与正确性属性 Property 6：

- N 个独立进程（``multiprocessing.Process``）对**同一 ``session_id``**
  并发调用 ``save``，写入各自区分字段标记的 ``ConversationContext``；
- 所有子进程 ``join`` 后，主进程 ``load`` 必须返回**某一个完整**子进程写入
  的 ``ConversationContext``——不得是字节片段交叉、截断、零字节或抛出；
- 收敛后的 ``ConversationContext`` 的 "区分字段"（本测试使用 "pid-N"
  作为 system 消息内容）必须**完全等于** N 个子进程中某一个的输入。

设计要点：

- 显式使用 ``multiprocessing.get_context("spawn")`` 以保证 Linux / macOS /
  Windows 三个平台下的一致启动语义（Linux 默认 "fork" 会复制父进程状态）。
- 每个子进程独立构造自己的 ``LockFactory`` / ``TempFileAtomicWriter``
  / ``CrossPlatformPathPolicy`` / ``LocalFileSessionContextAdapter``
  实例；锁协调通过 ``portalocker`` 在底层实现，不依赖任何进程间共享对象。
- 子进程退出码应为 0；若子进程内部抛异常，``Process.exitcode`` 会非零
  → 测试失败（参见 ``_assert_all_children_succeeded`` 辅助）。
- 测试标为 ``integration``；不依赖任何外部中间件（纯本地文件 I/O）。

需求：10.3；正确性属性：Property 6。
"""

from __future__ import annotations

import asyncio
import multiprocessing
import time
from multiprocessing.process import BaseProcess
from pathlib import Path

# 对齐需求 10.3：显式使用 "spawn"，以在 Linux 上与 Windows 行为对齐。
_SPAWN_CTX = multiprocessing.get_context("spawn")


def _run_save_in_subprocess(
    root_str: str,
    session_id: str,
    worker_id: int,
    iterations: int,
    ready_barrier_path: str,
) -> None:
    """子进程入口：对同一 ``session_id`` 执行 ``iterations`` 次 ``save``。

    函数必须为模块级可 pickle；不能闭包捕获测试 fixture。

    Args:
        root_str: 本地持久化根目录（字符串，子进程重新构造 ``Path``）。
        session_id: 要写入的会话 ID（所有子进程共用同一 ID）。
        worker_id: 本子进程区分编号（写入为 system 消息 "pid-{worker_id}"）。
        iterations: 每个子进程重复调用 ``save`` 的次数。
        ready_barrier_path: 用来让子进程"等待其它子进程到齐"的哨兵文件；
            子进程自身 touch 一个 ``<worker_id>.ready`` 文件，然后轮询直到
            目录内出现预期数量的 ``*.ready`` 文件再正式开写。
    """
    # 延迟 import 以避免 fork 子进程也加载 pytest 等测试依赖
    from domain.chat.context import ConversationContext
    from infrastructure.persistence.local_file.atomic_writer import (
        TempFileAtomicWriter,
    )
    from infrastructure.persistence.local_file.file_lock import LockFactory
    from infrastructure.persistence.local_file.path_policy import (
        CrossPlatformPathPolicy,
    )
    from infrastructure.session.local_file_session_context_adapter import (
        LocalFileSessionContextAdapter,
    )

    root = Path(root_str)
    adapter = LocalFileSessionContextAdapter(
        root=root,
        # 锁超时 5s：并发 8 个进程 × 低频 save，完全足够。
        lock_factory=LockFactory(acquire_timeout_ms=5000),
        path_policy=CrossPlatformPathPolicy(),
        # fsync_on_write=False 加速测试；不影响多进程互斥语义（互斥靠锁 +
        # os.replace 原子性而非 fsync）。
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )

    # 自身 ready
    ready_dir = Path(ready_barrier_path)
    ready_dir.mkdir(parents=True, exist_ok=True)
    (ready_dir / f"{worker_id}.ready").touch()

    async def _save_payload(payload_id: int) -> None:
        ctx = ConversationContext()
        # 区分字段：内容携带 pid-{worker_id} 与 iter-{payload_id}
        ctx.add_system_message(f"pid-{worker_id}")
        ctx.add_user_message(f"user-{worker_id}-{payload_id}")
        ctx.add_assistant_message(f"assistant-{worker_id}-{payload_id}")
        await adapter.save(session_id, ctx)

    async def _run_loop() -> None:
        for i in range(iterations):
            await _save_payload(i)

    asyncio.run(_run_loop())


def _assert_all_children_succeeded(
    procs: list[BaseProcess],
) -> None:
    """断言所有子进程退出码为 0（无未捕获异常）。"""
    for proc in procs:
        assert proc.exitcode == 0, (
            f"子进程 {proc.name}(pid={proc.pid}) 非 0 退出：exitcode={proc.exitcode}"
        )


def _wait_for_barrier(ready_dir: Path, expected: int, timeout_s: float) -> None:
    """轮询等待 ready 目录内出现 ``expected`` 个 ``*.ready`` 哨兵文件。

    只用于测试日志判断子进程是否如期启动；不影响测试正确性。
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ready_dir.exists() and len(list(ready_dir.glob("*.ready"))) >= expected:
            return
        time.sleep(0.05)


# ── 用例 A：N 个进程对同一 session_id 并发 save（收敛到单一胜者） ──


def test_concurrent_saves_converge_to_a_single_winner(tmp_path: Path):
    """N=8 个进程并发 save 不同 payload，最终 load 必须返回其中某一个完整 ctx。

    验证需求 2.3 "load 读取到的永远是 ctx_A 或 ctx_B 之一的**完整**
    to_dict() 输出，不得读取到字节片段交叉、截断或零字节"；正确性属性
    Property 6。
    """
    from domain.chat.context import ConversationContext
    from infrastructure.persistence.local_file.atomic_writer import (
        TempFileAtomicWriter,
    )
    from infrastructure.persistence.local_file.file_lock import LockFactory
    from infrastructure.persistence.local_file.path_policy import (
        CrossPlatformPathPolicy,
    )
    from infrastructure.session.local_file_session_context_adapter import (
        LocalFileSessionContextAdapter,
    )

    session_id = "multiprocess-concurrent-session"
    num_workers = 8
    iterations = 10
    ready_dir = tmp_path / "ready"

    procs: list[BaseProcess] = []
    for worker_id in range(num_workers):
        p = _SPAWN_CTX.Process(
            target=_run_save_in_subprocess,
            args=(
                str(tmp_path),
                session_id,
                worker_id,
                iterations,
                str(ready_dir),
            ),
            name=f"worker-{worker_id}",
        )
        procs.append(p)
        p.start()

    _wait_for_barrier(ready_dir, num_workers, timeout_s=30.0)

    for proc in procs:
        proc.join(timeout=60.0)
        assert not proc.is_alive(), f"子进程 {proc.name} 超时未结束"

    _assert_all_children_succeeded(procs)

    # 构造一个本进程内的 adapter 做最终 load
    adapter = LocalFileSessionContextAdapter(
        root=tmp_path,
        lock_factory=LockFactory(acquire_timeout_ms=5000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )

    async def _load() -> ConversationContext:
        return await adapter.load(session_id)

    loaded = asyncio.run(_load())
    data = loaded.to_dict()
    messages = data["messages"]

    # 收敛后 ctx 必须有完整的 3 条消息（system + user + assistant）且来自
    # 同一子进程的同一次 iteration（即 "pid-X" 与 "user-X-Y" / "assistant-X-Y"
    # 的 X 必须一致）。
    assert len(messages) == 3, f"收敛后消息数应为 3，实际={len(messages)}；消息={messages}"

    system_msg = messages[0]
    user_msg = messages[1]
    assistant_msg = messages[2]

    assert system_msg["role"] == "system"
    assert user_msg["role"] == "user"
    assert assistant_msg["role"] == "assistant"

    # 解析 pid-{worker_id}
    sys_content = system_msg["content"]
    assert sys_content.startswith("pid-"), f"system 消息应以 'pid-' 开头，实际={sys_content!r}"
    winning_worker = sys_content[len("pid-") :]
    assert winning_worker.isdigit(), f"winning_worker 应为整数，实际={winning_worker!r}"
    winning_worker_id = int(winning_worker)
    assert 0 <= winning_worker_id < num_workers, (
        f"winning_worker_id={winning_worker_id} 不在子进程编号范围内"
    )

    # user 与 assistant 消息的 worker 段必须与 system 一致（完整 ctx）
    assert user_msg["content"].startswith(f"user-{winning_worker_id}-"), (
        f"user 消息 worker_id 与 system 不一致：{user_msg['content']!r}"
    )
    assert assistant_msg["content"].startswith(f"assistant-{winning_worker_id}-"), (
        f"assistant 消息 worker_id 与 system 不一致：{assistant_msg['content']!r}"
    )


# ── 用例 B：多进程写入不得产生半写文件（Property 8 + 崩溃一致性） ──


def test_concurrent_saves_leave_no_partial_written_files(tmp_path: Path):
    """并发写入完成后，sessions 目录下**不得**残留任何 ``.tmp-*`` 半写文件。

    ``TempFileAtomicWriter`` 的 ``os.replace`` 原子替换协议要求：成功路径
    下 tmp 文件在 replace 时即被重命名为目标；失败路径下 tmp 文件在
    ``finally`` 中被 ``unlink``。多进程并发不应破坏该不变量。
    """
    session_id = "multiprocess-no-partial-session"
    num_workers = 6
    iterations = 5
    ready_dir = tmp_path / "ready"

    procs: list[BaseProcess] = []
    for worker_id in range(num_workers):
        p = _SPAWN_CTX.Process(
            target=_run_save_in_subprocess,
            args=(
                str(tmp_path),
                session_id,
                worker_id,
                iterations,
                str(ready_dir),
            ),
            name=f"worker-{worker_id}",
        )
        procs.append(p)
        p.start()

    _wait_for_barrier(ready_dir, num_workers, timeout_s=30.0)

    for proc in procs:
        proc.join(timeout=60.0)
        assert not proc.is_alive()

    _assert_all_children_succeeded(procs)

    # 扫描 sessions/ 下所有文件：只应见到 .json 与 .json.lock，不得见 .tmp-
    sessions_root = tmp_path / "sessions"
    assert sessions_root.exists(), "sessions/ 目录应已创建"
    leftover_tmp = [p for p in sessions_root.rglob("*") if ".tmp-" in p.name]
    assert leftover_tmp == [], f"并发写入完成后不得残留 .tmp-* 文件；实际残留={leftover_tmp}"


# ── 用例 C：多个不同 session_id 并发写入互不干扰 ──


def _run_distinct_sessions_subprocess(
    root_str: str,
    session_id: str,
    worker_id: int,
    ready_barrier_path: str,
) -> None:
    """子进程入口：对**各自独立**的 ``session_id`` 执行一次 ``save``。"""
    from domain.chat.context import ConversationContext
    from infrastructure.persistence.local_file.atomic_writer import (
        TempFileAtomicWriter,
    )
    from infrastructure.persistence.local_file.file_lock import LockFactory
    from infrastructure.persistence.local_file.path_policy import (
        CrossPlatformPathPolicy,
    )
    from infrastructure.session.local_file_session_context_adapter import (
        LocalFileSessionContextAdapter,
    )

    root = Path(root_str)
    adapter = LocalFileSessionContextAdapter(
        root=root,
        lock_factory=LockFactory(acquire_timeout_ms=5000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )

    ready_dir = Path(ready_barrier_path)
    ready_dir.mkdir(parents=True, exist_ok=True)
    (ready_dir / f"{worker_id}.ready").touch()

    async def _save() -> None:
        ctx = ConversationContext()
        ctx.add_user_message(f"user-{worker_id}")
        ctx.add_assistant_message(f"assistant-{worker_id}")
        await adapter.save(session_id, ctx)

    asyncio.run(_save())


def test_concurrent_saves_on_distinct_sessions_do_not_lose_data(tmp_path: Path):
    """N 个进程对**不同** session_id 并发 save；每个 session 的 load 必须
    返回该进程原始写入——跨会话的并发写入不得互相丢失或交叉。

    验证锁粒度为"单会话一把锁"（需求 2.2 分布式一致性的单主机实现：
    不同 session_id 之间零竞争）。
    """
    from domain.chat.context import ConversationContext
    from infrastructure.persistence.local_file.atomic_writer import (
        TempFileAtomicWriter,
    )
    from infrastructure.persistence.local_file.file_lock import LockFactory
    from infrastructure.persistence.local_file.path_policy import (
        CrossPlatformPathPolicy,
    )
    from infrastructure.session.local_file_session_context_adapter import (
        LocalFileSessionContextAdapter,
    )

    num_workers = 6
    sessions = [f"session-distinct-{i}" for i in range(num_workers)]
    ready_dir = tmp_path / "ready"

    procs: list[BaseProcess] = []
    for worker_id in range(num_workers):
        p = _SPAWN_CTX.Process(
            target=_run_distinct_sessions_subprocess,
            args=(
                str(tmp_path),
                sessions[worker_id],
                worker_id,
                str(ready_dir),
            ),
            name=f"worker-{worker_id}",
        )
        procs.append(p)
        p.start()

    for proc in procs:
        proc.join(timeout=60.0)
        assert not proc.is_alive()

    _assert_all_children_succeeded(procs)

    # 主进程逐一 load 并断言每个 session 保存了本进程的内容
    adapter = LocalFileSessionContextAdapter(
        root=tmp_path,
        lock_factory=LockFactory(acquire_timeout_ms=5000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )

    async def _load_all() -> list[ConversationContext]:
        results: list[ConversationContext] = []
        for session_id in sessions:
            loaded = await adapter.load(session_id)
            results.append(loaded)
        return results

    loaded_all = asyncio.run(_load_all())
    assert len(loaded_all) == num_workers

    for worker_id, loaded in enumerate(loaded_all):
        data = loaded.to_dict()
        messages = data["messages"]
        assert len(messages) == 2, (
            f"session-distinct-{worker_id} 应有 2 条消息，实际={len(messages)}"
        )
        assert messages[0]["content"] == f"user-{worker_id}"
        assert messages[1]["content"] == f"assistant-{worker_id}"
