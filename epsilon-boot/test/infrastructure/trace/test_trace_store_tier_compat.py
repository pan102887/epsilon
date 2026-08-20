"""LocalFileTraceStoreAdapter tier 兼容回归测试。

验证 trace adapter 从构造期 store_dir 迁移到注入 LocalFileTierResolver 后，
既有不传 tier 的调用点（router / ReActAgentAdapter 用法）行为与迁移前等价：

- 不传 tier 的 append→get→list 同 session round-trip（Property 6）。
- PROJECT tier 默认写入位置与 <project_base 规范化>/.epsilon/traces 等价（Property 2）。

对 project_base 与期望路径均做 .resolve() 规范化比较，避免 symlink / 相对路径误判。
"""

from __future__ import annotations

from pathlib import Path

from domain.agent.trace_value_objects import ModelCallTrace, ToolCallTrace
from domain.storage.storage_tier import StorageTier
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver
from infrastructure.trace.local_file_trace_store_adapter import LocalFileTraceStoreAdapter


def _make_store(project_base: Path) -> LocalFileTraceStoreAdapter:
    """以 project_base 为 PROJECT 基点构造注入 resolver 的 trace adapter。"""
    resolver = LocalFileTierResolver(project_base=project_base)
    return LocalFileTraceStoreAdapter(tier_resolver=resolver)


def _expected_trace_file(project_base: Path, session_id: str) -> Path:
    """返回 PROJECT tier 下 <project_base 规范化>/.epsilon/traces/{session_id}.jsonl。

    与 resolver 一致对 project_base 做 .resolve() 规范化，供 async 测试比对
    （避免在 async 函数体内直接调用 pathlib，触发 ASYNC240）。
    """
    return project_base.resolve() / ".epsilon" / "traces" / f"{session_id}.jsonl"


def _model_trace() -> ModelCallTrace:
    return ModelCallTrace(
        round_num=1,
        model="gpt-4",
        prompt_id="chat@v1",
        input_tokens=100,
        output_tokens=50,
        latency_ms=200.0,
        timestamp_epoch=1000.0,
    )


def _tool_trace() -> ToolCallTrace:
    return ToolCallTrace(
        round_num=1,
        tool_name="shell_exec",
        tool_call_id="tc_1",
        arguments_summary='{"cmd":"ls"}',
        result_summary="file.txt",
        success=True,
        latency_ms=50.0,
        timestamp_epoch=1001.0,
    )


async def test_default_tier_round_trip_matches_legacy_behavior(tmp_path: Path) -> None:
    """不传 tier 的 append→get→list 同 session round-trip 与迁移前等价（Property 6）。"""
    store = _make_store(tmp_path)

    # 模拟既有 router / ReActAgentAdapter：全部调用点均不传 tier。
    await store.append_step("sess_compat", _model_trace())
    await store.append_step("sess_compat", _tool_trace())

    trace = await store.get_session_trace("sess_compat")
    assert trace is not None
    assert trace.session_id == "sess_compat"
    assert len(trace.steps) == 2
    assert trace.steps[0].kind == "model_call"
    assert trace.steps[1].kind == "tool_call"
    assert trace.started_at_epoch == 1000.0

    summaries = await store.list_traces()
    assert len(summaries) == 1
    assert summaries[0].session_id == "sess_compat"
    assert summaries[0].metadata["step_count"] == 2


async def test_default_tier_writes_to_project_epsilon_traces(tmp_path: Path) -> None:
    """PROJECT tier 默认写入位置与 <project_base 规范化>/.epsilon/traces 等价（Property 2）。"""
    # 期望路径预先在 async 体外规范化，避免在 async 函数内调用 pathlib。
    expected_file = _expected_trace_file(tmp_path, "sess_loc")
    store = _make_store(tmp_path)
    await store.append_step("sess_loc", _model_trace())

    assert expected_file.exists()


async def test_explicit_default_tier_equivalent_to_omitting_tier(tmp_path: Path) -> None:
    """显式传 tier=PROJECT 与不传 tier 写入同一位置，验证默认值语义一致。"""
    store = _make_store(tmp_path)

    await store.append_step("sess_explicit", _model_trace(), tier=StorageTier.PROJECT)
    implicit = await store.get_session_trace("sess_explicit")
    explicit = await store.get_session_trace("sess_explicit", tier=StorageTier.PROJECT)

    assert implicit is not None
    assert explicit is not None
    assert len(implicit.steps) == len(explicit.steps) == 1


async def test_get_and_list_on_missing_traces_dir_are_safe(tmp_path: Path) -> None:
    """未写入任何 trace 时（traces 目录尚不存在）get 返回 None、list 返回空列表。"""
    store = _make_store(tmp_path)

    assert await store.get_session_trace("never_written") is None
    assert await store.list_traces() == []
