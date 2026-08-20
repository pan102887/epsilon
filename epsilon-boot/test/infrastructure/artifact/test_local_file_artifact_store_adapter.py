"""LocalFileArtifactStoreAdapter 单元测试。

覆盖任务产物本地文件后端的核心契约：

- append→list round-trip：写入后可原样读回，且 ``kind == "artifact"``（Property 6）。
- 故障隔离：写入 / 目录创建抛错时 append_artifact 不抛、记录 warning、返回 None（Property 7）。
- 缺失文件与坏行：list_artifacts 对不存在文件返回 []、对坏行跳过（Property 7）。

resolver 以 tmp_path 作 PROJECT 基点构造，避免污染真实工作区。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from domain.agent.trace_value_objects import ArtifactTrace
from domain.storage.storage_tier import StorageTier
from infrastructure.artifact.local_file_artifact_store_adapter import (
    LocalFileArtifactStoreAdapter,
)
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver


def _make_store(project_base: Path) -> LocalFileArtifactStoreAdapter:
    """以 project_base 为 PROJECT 基点构造注入 resolver 的 artifact adapter。"""
    resolver = LocalFileTierResolver(project_base=project_base)
    return LocalFileArtifactStoreAdapter(tier_resolver=resolver)


def _artifact(session_id: str = "sess_1") -> ArtifactTrace:
    """构造一条带全部字段的产物记录，用于 round-trip 断言。"""
    return ArtifactTrace(
        session_id=session_id,
        logical_path="out/report.md",
        artifact_type="file",
        timestamp_epoch=1751731200.5,
        size_bytes=2048,
        content_summary="生成的报告摘要",
        source_tool="write_file",
    )


def _expected_artifact_file(project_base: Path, session_id: str) -> Path:
    """返回 PROJECT tier 下 <project_base 规范化>/.epsilon/artifacts/{session_id}.jsonl。

    路径在 async 体外预先规范化，避免在 async 函数内直接调用 pathlib（ASYNC240）。
    """
    return project_base.resolve() / ".epsilon" / "artifacts" / f"{session_id}.jsonl"


async def test_append_then_list_round_trip(tmp_path: Path) -> None:
    """append→list round-trip：读回内容与写入一致，且 kind == "artifact"（Property 6）。"""
    store = _make_store(tmp_path)
    original = _artifact("sess_rt")

    await store.append_artifact("sess_rt", original)
    items = await store.list_artifacts("sess_rt")

    assert len(items) == 1
    read_back = items[0]
    assert read_back == original
    assert read_back.kind == "artifact"
    assert read_back.logical_path == "out/report.md"
    assert read_back.size_bytes == 2048
    assert read_back.content_summary == "生成的报告摘要"


async def test_append_multiple_preserves_order(tmp_path: Path) -> None:
    """多条 append 以 append-only 顺序读回，验证 JSONL 逐行累积语义。"""
    store = _make_store(tmp_path)

    await store.append_artifact("sess_multi", _artifact("sess_multi"))
    await store.append_artifact(
        "sess_multi",
        ArtifactTrace(
            session_id="sess_multi",
            logical_path="out/log.txt",
            artifact_type="command_output",
            timestamp_epoch=1751731300.0,
        ),
    )

    items = await store.list_artifacts("sess_multi")
    assert len(items) == 2
    assert items[0].artifact_type == "file"
    assert items[1].artifact_type == "command_output"
    assert items[1].size_bytes is None


async def test_explicit_default_tier_equivalent_to_omitting_tier(tmp_path: Path) -> None:
    """显式 tier=PROJECT 与不传 tier 写入 / 读取同一位置，验证默认值语义一致。"""
    expected_file = _expected_artifact_file(tmp_path, "sess_tier")
    store = _make_store(tmp_path)

    await store.append_artifact("sess_tier", _artifact("sess_tier"), tier=StorageTier.PROJECT)
    implicit = await store.list_artifacts("sess_tier")
    explicit = await store.list_artifacts("sess_tier", tier=StorageTier.PROJECT)

    assert expected_file.exists()
    assert len(implicit) == len(explicit) == 1


async def test_list_missing_file_returns_empty(tmp_path: Path) -> None:
    """从未写入时（artifacts 目录 / 文件尚不存在）list_artifacts 返回空列表（Property 7）。"""
    store = _make_store(tmp_path)
    assert await store.list_artifacts("never_written") == []


async def test_list_skips_corrupt_lines(tmp_path: Path) -> None:
    """坏行（非法 JSON / 缺字段）被跳过，合法行仍被读回（Property 7）。"""
    store = _make_store(tmp_path)
    await store.append_artifact("sess_bad", _artifact("sess_bad"))

    # 追加一条非法 JSON 行与一条缺必填字段的行，制造坏数据。
    path = _expected_artifact_file(tmp_path, "sess_bad")
    with path.open("a", encoding="utf-8") as f:
        f.write("not-a-json-line\n")
        f.write('{"kind":"artifact","logical_path":"x"}\n')

    items = await store.list_artifacts("sess_bad")
    assert len(items) == 1
    assert items[0].session_id == "sess_bad"


class _FailingResolver(LocalFileTierResolver):
    """在解析目录时抛错的 resolver，用于验证 append 故障隔离。"""

    def resolve(self, tier: StorageTier) -> object:  # type: ignore[override]
        """恒抛 OSError，模拟目录创建 / 解析失败。"""
        raise OSError("模拟 mkdir/解析失败")


async def test_append_isolates_failure_and_returns_none(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """resolve/mkdir 抛错时 append_artifact 不抛、记录 warning、返回 None（Property 7）。"""
    store = LocalFileArtifactStoreAdapter(tier_resolver=_FailingResolver(project_base=tmp_path))

    with caplog.at_level(logging.WARNING):
        result = await store.append_artifact("sess_fail", _artifact("sess_fail"))

    assert result is None
    assert any("artifact append 失败" in rec.message for rec in caplog.records)


async def test_append_write_error_isolated(tmp_path: Path) -> None:
    """底层写入 (_append_line) 抛错时 append_artifact 不抛（Property 7）。"""

    class _WriteFailingStore(LocalFileArtifactStoreAdapter):
        """覆写 _append_line 使写入抛错，验证 to_thread 内异常被上层隔离。"""

        def _append_line(self, store_dir: Path, session_id: str, line: str) -> None:
            raise OSError("模拟写入失败")

    store = _WriteFailingStore(tier_resolver=LocalFileTierResolver(project_base=tmp_path))
    result = await store.append_artifact("sess_wfail", _artifact("sess_wfail"))
    assert result is None
    # 写入失败后不应产生可读回内容。
    assert await store.list_artifacts("sess_wfail") == []
