"""``LocalFileSessionContextAdapter`` property-based 测试。

覆盖需求 10.2 与以下正确性属性：

- **Property 1**：``save(id, ctx); load(id).to_dict() == ctx.to_dict()``
  对任意合法 ``session_id`` 与 ``ConversationContext`` 成立（需求 1.2 / 1.3）。
- **Property 2**：``delete`` 幂等；``load`` 对不存在的 ``session_id`` 返回空
  ``ConversationContext``（需求 1.3 / 1.5）。
- **Property 8（无 TTL 反向锁死）**：``save`` 后把 ``mtime`` 回拨到 1 天前 /
  30 天前，``load`` 仍返回原 ``ConversationContext``；锁死需求 2.补.2
  "load 路径不得读取 mtime 做过期判断" 与需求 2.补.1 "本期会话无 TTL"。

Hypothesis 策略均做了 bounded 约束（``max_size`` 不超过 ~200 字符），以免
CI 时间被 Hypothesis 爆搜放大。
"""

import asyncio
import os
import time
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from domain.chat.context import ConversationContext
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.session.local_file_session_context_adapter import (
    LocalFileSessionContextAdapter,
)

# ── Hypothesis 策略（均 bounded） ──


# 允许 Unicode / NUL / Windows 保留名 / 非法字符：哈希后天然规避冲突。
# 需求 4.2：``session_id`` 经 sha256 后使用，故允许任意字符输入。
session_id_st = st.text(min_size=1, max_size=64)

# 单条消息内容；限制 200 字符以内控制 ctx 体量。
_content_st = st.text(min_size=0, max_size=200)

# ``ConversationContext`` 策略：生成 0-5 条混合角色的消息。
_message_shape_st = st.tuples(
    st.sampled_from(["system", "user", "assistant", "tool"]),
    _content_st,
)


def _build_context(messages: list[tuple[str, str]]) -> ConversationContext:
    """根据 ``(role, content)`` 元组列表构造 ``ConversationContext``。

    纯辅助函数；避免把 Hypothesis 策略拖进 ``ConversationContext`` 内部。
    """
    ctx = ConversationContext()
    for role, content in messages:
        if role == "system":
            ctx.add_system_message(content)
        elif role == "user":
            ctx.add_user_message(content)
        elif role == "assistant":
            ctx.add_assistant_message(content)
        elif role == "tool":
            ctx.add_tool_result(tool_name="t", result=content)
    return ctx


context_st = st.lists(_message_shape_st, min_size=0, max_size=5).map(_build_context)


def _make_adapter(root: Path) -> LocalFileSessionContextAdapter:
    """构造一个落盘到 ``root`` 的适配器；``fsync_on_write=False`` 以加速。"""
    return LocalFileSessionContextAdapter(
        root=root,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


# ── Property 1: save → load 往返等价 ──


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(session_id=session_id_st, ctx=context_st)
def test_save_load_roundtrip_is_idempotent(
    tmp_path: Path, session_id: str, ctx: ConversationContext
):
    """``save(id, ctx); load(id).to_dict() == ctx.to_dict()`` 恒成立。"""
    adapter = _make_adapter(tmp_path)

    async def _run():
        await adapter.save(session_id, ctx)
        loaded = await adapter.load(session_id)
        return loaded

    loaded = asyncio.run(_run())
    assert loaded.to_dict() == ctx.to_dict()


# ── Property 2: delete 幂等 + load 空返回 ──


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(session_id=session_id_st)
def test_delete_is_idempotent_and_load_returns_empty(tmp_path: Path, session_id: str):
    """``delete`` 两次不抛异常，之后 ``load`` 返回空 ``ConversationContext``。"""
    adapter = _make_adapter(tmp_path)

    async def _run():
        await adapter.delete(session_id)
        await adapter.delete(session_id)
        return await adapter.load(session_id)

    loaded = asyncio.run(_run())
    assert loaded.to_dict() == {"messages": []}


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(session_id=session_id_st, ctx=context_st)
def test_delete_then_load_returns_empty_after_save(
    tmp_path: Path, session_id: str, ctx: ConversationContext
):
    """``save → delete → load`` 返回空（锁死幂等与删除语义组合）。"""
    adapter = _make_adapter(tmp_path)

    async def _run():
        await adapter.save(session_id, ctx)
        await adapter.delete(session_id)
        # 第二次 delete 也不抛
        await adapter.delete(session_id)
        return await adapter.load(session_id)

    loaded = asyncio.run(_run())
    assert loaded.to_dict() == {"messages": []}


# ── Property 8：无 TTL 反向回归（锁死需求 2.补.2） ──


@pytest.mark.parametrize("age_seconds", [86400, 30 * 86400])
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(session_id=session_id_st, ctx=context_st)
def test_load_ignores_mtime_regardless_of_age(
    tmp_path: Path,
    session_id: str,
    ctx: ConversationContext,
    age_seconds: int,
):
    """``save`` 后任意老化 ``mtime``（1 天 / 30 天），``load`` 仍返回原 ctx。

    实现层面锁死：``load`` 路径**不得**读取 ``stat().st_mtime`` 做过期判断
    （需求 2.补.2）。本属性测试通过 ``os.utime`` 把 mtime 回拨到任意远古
    时间并断言 ``load`` 仍然返回 ``to_dict()`` 等价结果。
    """
    adapter = _make_adapter(tmp_path)
    policy = CrossPlatformPathPolicy()

    async def _save():
        await adapter.save(session_id, ctx)

    asyncio.run(_save())

    bucket, stem = policy.hash_session_id(session_id)
    path = tmp_path / "sessions" / bucket / f"{stem}.json"
    assert path.exists(), "save 未产生预期文件"

    past = time.time() - age_seconds
    os.utime(path, (past, past))

    async def _load():
        return await adapter.load(session_id)

    loaded = asyncio.run(_load())
    assert loaded.to_dict() == ctx.to_dict()


# ── 反向断言：适配器类层面不得存在任何 TTL / Reaper 相关属性 ──


def test_adapter_class_has_no_ttl_or_reaper_attributes():
    """``LocalFileSessionContextAdapter`` **不得**具备 TTL / Reaper 相关名。

    这是需求 2.补.1 / 2.补.6 的反向锁死，配合 conftest / property 测试
    确保 "无 TTL" 不变量不因后续重构而回退。
    """
    for banned in (
        "is_expired",
        "_reaper",
        "reaper",
        "_ttl_seconds",
        "ttl_seconds",
        "_ttl_reaper",
        "start_reaper",
        "stop_reaper",
    ):
        assert not hasattr(LocalFileSessionContextAdapter, banned), (
            f"LocalFileSessionContextAdapter 不得拥有属性 {banned!r}"
            "（需求 2.补.1 / 2.补.6：本期会话无 TTL / 无 Reaper）"
        )
