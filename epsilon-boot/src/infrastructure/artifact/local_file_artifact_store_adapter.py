"""本地 JSONL 文件任务产物存储适配器。

实现 ArtifactStorePort，将 ArtifactTrace 以 append-only JSONL 持久化到对应
tier 的 Artifacts_Dir。每个 session 一个 ``{session_id}.jsonl`` 文件。故障隔离、
大字段截断语义与 ``LocalFileTraceStoreAdapter`` 保持同构。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from domain.agent.trace_value_objects import ArtifactTrace
from domain.storage.storage_tier import StorageTier
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver

logger = logging.getLogger(__name__)


class LocalFileArtifactStoreAdapter:
    """本地 JSONL 文件产物存储。

    经 LocalFileTierResolver 按 StorageTier 定位 artifacts 目录，每个 session 的
    产物记录保存为 ``<artifacts_dir>/{session_id}.jsonl``，每行一个 JSON 编码的
    ArtifactTrace（含判别字段 ``kind``）。目录随 tier 由解析器映射，默认
    PROJECT tier 落 ``<workspace>/.epsilon/artifacts``。
    """

    def __init__(self, tier_resolver: LocalFileTierResolver) -> None:
        """构造产物存储适配器。

        Args:
            tier_resolver: 本地文件存储等级解析器，负责把 StorageTier 映射到
                具体 artifacts 目录。
        """
        self._resolver = tier_resolver

    async def append_artifact(
        self,
        session_id: str,
        artifact: ArtifactTrace,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> None:
        """追加一条产物记录到指定 tier 的 Artifacts_Dir。

        Args:
            session_id: 会话唯一标识符。
            artifact: 产物追踪值对象。
            tier: 存储等级定位维度，默认 PROJECT。

        IO 失败时记录 warning 并隔离故障，不中断主流程（Property 7）。
        """
        try:
            store_dir = self._resolver.resolve(tier).artifacts_dir(create=True)
            line = json.dumps(asdict(artifact), ensure_ascii=False)
            self._append_line(store_dir, session_id, line)
        except Exception:
            logger.warning(
                "artifact append 失败，session_id=%s", session_id, exc_info=True
            )

    async def list_artifacts(
        self,
        session_id: str,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> list[ArtifactTrace]:
        """列出指定会话已记录的产物。

        Args:
            session_id: 会话唯一标识符。
            tier: 存储等级定位维度，默认 PROJECT。

        Returns:
            ArtifactTrace 列表；文件不存在或读取失败时返回空列表；坏行跳过。
        """
        try:
            store_dir = self._resolver.resolve(tier).artifacts_dir(create=False)
            path = store_dir / f"{session_id}.jsonl"
            if not path.exists():
                return []
            return self._read_artifacts(path)
        except Exception:
            logger.warning(
                "artifact list 失败，session_id=%s", session_id, exc_info=True
            )
            return []

    def _append_line(self, store_dir: Path, session_id: str, line: str) -> None:
        """把单行 JSONL 追加写入 ``<store_dir>/{session_id}.jsonl``。"""
        path = store_dir / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_artifacts(self, path: Path) -> list[ArtifactTrace]:
        """逐行解析产物 JSONL；去除判别字段 ``kind`` 后重建 ArtifactTrace，坏行跳过。"""
        items: list[ArtifactTrace] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
                d.pop("kind", None)
                items.append(ArtifactTrace(**d))
            except Exception:
                logger.warning("artifact 行解析失败，跳过: %s", raw[:100])
        return items
