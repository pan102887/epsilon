"""ArtifactTrace 值对象与截断常量单元测试。"""

import dataclasses
import time
from collections.abc import Callable
from typing import cast

import pytest

from domain.agent.trace_value_objects import (
    ARTIFACT_LOGICAL_PATH_MAX_LEN,
    ARTIFACT_SUMMARY_MAX_LEN,
    ArtifactTrace,
)


class TestArtifactTraceKind:
    def test_kind_field(self):
        t = ArtifactTrace(
            session_id="s-1",
            logical_path="out/report.md",
            artifact_type="file",
            timestamp_epoch=time.time(),
        )
        assert t.kind == "artifact"

    def test_kind_is_init_false(self):
        """kind 使用 init=False，构造器不接受 kind 参数。"""
        constructor = cast(Callable[..., ArtifactTrace], ArtifactTrace)
        with pytest.raises(TypeError):
            constructor(
                session_id="s-1",
                logical_path="out/report.md",
                artifact_type="file",
                timestamp_epoch=1000.0,
                kind="artifact",
            )


class TestArtifactTraceFrozen:
    def test_frozen(self):
        t = ArtifactTrace(
            session_id="s-1",
            logical_path="out/report.md",
            artifact_type="file",
            timestamp_epoch=1000.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.session_id = "s-2"  # type: ignore[misc]


class TestArtifactTraceRoundTrip:
    def test_asdict_round_trip(self):
        """asdict 去掉 kind 后可 round-trip 回等价实例。"""
        original = ArtifactTrace(
            session_id="s-123",
            logical_path="out/report.md",
            artifact_type="file",
            timestamp_epoch=1751731200.5,
            size_bytes=2048,
            content_summary="生成的报告摘要",
            source_tool="write_file",
        )
        d = dataclasses.asdict(original)
        assert d["kind"] == "artifact"
        d.pop("kind")
        restored = ArtifactTrace(**d)
        assert restored == original

    def test_optional_fields_default_none(self):
        t = ArtifactTrace(
            session_id="s-1",
            logical_path="out/x",
            artifact_type="command_output",
            timestamp_epoch=1000.0,
        )
        assert t.size_bytes is None
        assert t.content_summary is None
        assert t.source_tool is None


class TestArtifactTruncationConstants:
    def test_summary_max_len(self):
        assert ARTIFACT_SUMMARY_MAX_LEN == 256

    def test_logical_path_max_len(self):
        assert ARTIFACT_LOGICAL_PATH_MAX_LEN == 512
