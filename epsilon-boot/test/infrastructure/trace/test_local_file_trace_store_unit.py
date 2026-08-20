"""本地文件 trace store adapter 单元测试。"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from domain.agent.trace_value_objects import (
    ModelCallTrace,
    ToolCallTrace,
)
from domain.storage.storage_tier import StorageTier
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver
from infrastructure.trace.local_file_trace_store_adapter import LocalFileTraceStoreAdapter


def _traces_dir(tmp_path: Path) -> Path:
    """返回 project_base=tmp_path 时 PROJECT tier 解析出的 traces 目录。"""
    return LocalFileTierResolver(project_base=tmp_path).resolve(
        StorageTier.PROJECT
    ).traces_dir(create=False)


@pytest.fixture
def store(tmp_path: Path) -> LocalFileTraceStoreAdapter:
    resolver = LocalFileTierResolver(project_base=tmp_path)
    return LocalFileTraceStoreAdapter(tier_resolver=resolver)


@pytest.fixture
def sample_model_trace() -> ModelCallTrace:
    return ModelCallTrace(
        round_num=1,
        model="gpt-4",
        prompt_id="chat@v1",
        input_tokens=100,
        output_tokens=50,
        latency_ms=200.0,
        timestamp_epoch=1000.0,
    )


@pytest.fixture
def sample_tool_trace() -> ToolCallTrace:
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


async def test_append_step_creates_file_and_writes_jsonl(
    store: LocalFileTraceStoreAdapter, sample_model_trace: ModelCallTrace, tmp_path: Path
):
    await store.append_step("sess_1", sample_model_trace)
    path = _traces_dir(tmp_path) / "sess_1.jsonl"
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["kind"] == "model_call"
    assert data["model"] == "gpt-4"


async def test_get_session_trace_returns_none_for_missing(store: LocalFileTraceStoreAdapter):
    result = await store.get_session_trace("nonexistent")
    assert result is None


async def test_get_session_trace_reads_all_steps(
    store: LocalFileTraceStoreAdapter,
    sample_model_trace: ModelCallTrace,
    sample_tool_trace: ToolCallTrace,
):
    await store.append_step("sess_1", sample_model_trace)
    await store.append_step("sess_1", sample_tool_trace)
    trace = await store.get_session_trace("sess_1")
    assert trace is not None
    assert trace.session_id == "sess_1"
    assert len(trace.steps) == 2
    assert trace.steps[0].kind == "model_call"
    assert trace.steps[1].kind == "tool_call"
    assert trace.started_at_epoch == 1000.0


async def test_list_traces_returns_sorted_by_mtime(
    store: LocalFileTraceStoreAdapter, tmp_path: Path
):
    # 写入两个 session trace 文件
    trace1 = ModelCallTrace(
        round_num=1,
        model="m1",
        prompt_id="p@v1",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100.0,
        timestamp_epoch=900.0,
    )
    trace2 = ModelCallTrace(
        round_num=1,
        model="m2",
        prompt_id="p@v1",
        input_tokens=20,
        output_tokens=10,
        latency_ms=200.0,
        timestamp_epoch=1100.0,
    )
    await store.append_step("old_sess", trace1)
    # 确保第二个文件有更晚的 mtime
    await asyncio.sleep(0.05)
    await store.append_step("new_sess", trace2)

    traces = await store.list_traces(limit=10)
    assert len(traces) == 2
    # 最新的排在前面
    assert traces[0].session_id == "new_sess"
    assert traces[1].session_id == "old_sess"


async def test_append_step_auto_creates_directory(tmp_path: Path):
    nested = tmp_path / "deep" / "nested"
    resolver = LocalFileTierResolver(project_base=nested)
    store = LocalFileTraceStoreAdapter(tier_resolver=resolver)
    trace = ModelCallTrace(
        round_num=1,
        model="m",
        prompt_id="p@v1",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1.0,
        timestamp_epoch=time.time(),
    )
    await store.append_step("s1", trace)
    assert (_traces_dir(nested) / "s1.jsonl").exists()


async def test_malformed_line_skipped(
    store: LocalFileTraceStoreAdapter, tmp_path: Path, sample_model_trace: ModelCallTrace
):
    # 先正常写入一行
    await store.append_step("bad_sess", sample_model_trace)
    # 手动追加一个无效 JSON 行
    path = _traces_dir(tmp_path) / "bad_sess.jsonl"
    with path.open("a") as f:
        f.write("this is not json\n")
    # 再写入一个有效行
    await store.append_step("bad_sess", sample_model_trace)

    trace = await store.get_session_trace("bad_sess")
    assert trace is not None
    # 无效行被跳过，只剩 2 条有效记录
    assert len(trace.steps) == 2


# ═══════════════════════════════════════════════════════════════
# T5.4：ToolCallTrace.metadata JSONL 前向兼容（需求 7.8，INV-4）
# ═══════════════════════════════════════════════════════════════


async def test_tool_call_trace_metadata_roundtrips_through_jsonl(
    store: LocalFileTraceStoreAdapter, tmp_path: Path
):
    """含 metadata 的 ToolCallTrace 经写入/读回后 metadata 完整保留。"""
    trace = ToolCallTrace(
        round_num=2,
        tool_name="shell_exec",
        tool_call_id="tc_meta",
        arguments_summary='{"command":"ls"}',
        result_summary="ok",
        success=True,
        latency_ms=12.0,
        timestamp_epoch=2000.0,
        metadata={"exit_code": 0, "working_dir": "/", "truncated": False},
    )
    await store.append_step("meta_sess", trace)

    # 序列化后的 JSONL 行确实含 metadata 字段
    path = _traces_dir(tmp_path) / "meta_sess.jsonl"
    data = json.loads(path.read_text().splitlines()[0])
    assert data["metadata"] == {"exit_code": 0, "working_dir": "/", "truncated": False}

    restored = await store.get_session_trace("meta_sess")
    assert restored is not None
    assert len(restored.steps) == 1
    step = restored.steps[0]
    assert isinstance(step, ToolCallTrace)
    assert step.metadata == {"exit_code": 0, "working_dir": "/", "truncated": False}


async def test_legacy_tool_call_line_without_metadata_falls_back_to_empty_dict(
    store: LocalFileTraceStoreAdapter, tmp_path: Path
):
    """旧 JSONL 行缺少 metadata 字段时，读回静默回退为空 dict（INV-4）。"""
    traces_dir = _traces_dir(tmp_path)
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / "legacy_sess.jsonl"
    # 手工构造一条不含 metadata 字段的旧格式 tool_call 行
    legacy_line = {
        "round_num": 1,
        "tool_name": "read_file",
        "tool_call_id": "tc_old",
        "arguments_summary": '{"file_path":"a.txt"}',
        "result_summary": "content",
        "success": True,
        "latency_ms": 5.0,
        "timestamp_epoch": 1500.0,
        "error_class": None,
        "error_message": None,
        "kind": "tool_call",
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(legacy_line, ensure_ascii=False) + "\n")

    restored = await store.get_session_trace("legacy_sess")
    assert restored is not None
    assert len(restored.steps) == 1
    step = restored.steps[0]
    assert isinstance(step, ToolCallTrace)
    assert step.tool_name == "read_file"
    # 缺失字段兜底为空 dict，其余字段正常反序列化
    assert step.metadata == {}
    assert step.success is True


async def test_mixed_legacy_and_new_tool_call_lines_both_read(
    store: LocalFileTraceStoreAdapter, tmp_path: Path
):
    """同一文件混合新旧 tool_call 行时均可正确读回。"""
    traces_dir = _traces_dir(tmp_path)
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / "mixed_sess.jsonl"
    legacy_line = {
        "round_num": 1,
        "tool_name": "read_file",
        "tool_call_id": "tc_old",
        "arguments_summary": "{}",
        "result_summary": "r1",
        "success": True,
        "latency_ms": 1.0,
        "timestamp_epoch": 1000.0,
        "error_class": None,
        "error_message": None,
        "kind": "tool_call",
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(legacy_line, ensure_ascii=False) + "\n")
    # 新格式行经 append_step 写入
    new_trace = ToolCallTrace(
        round_num=2,
        tool_name="shell_exec",
        tool_call_id="tc_new",
        arguments_summary="{}",
        result_summary="r2",
        success=False,
        latency_ms=2.0,
        timestamp_epoch=1001.0,
        error_class="ToolExecutionError",
        error_message="boom",
        metadata={"exit_code": 1},
    )
    await store.append_step("mixed_sess", new_trace)

    restored = await store.get_session_trace("mixed_sess")
    assert restored is not None
    assert len(restored.steps) == 2
    old_step, new_step = restored.steps
    assert isinstance(old_step, ToolCallTrace)
    assert isinstance(new_step, ToolCallTrace)
    assert old_step.metadata == {}
    assert new_step.metadata == {"exit_code": 1}
    assert new_step.error_class == "ToolExecutionError"
