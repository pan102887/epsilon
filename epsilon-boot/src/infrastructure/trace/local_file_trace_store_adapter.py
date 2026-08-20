"""本地 JSONL 文件 trace 存储适配器。

实现 TraceStorePort，将 Agent 步骤追踪以 append-only JSONL 格式
持久化到本地文件系统。每个 session 一个 `{session_id}.jsonl` 文件。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from domain.agent.trace_value_objects import (
    AgentStepTrace,
    ApprovalTrace,
    ErrorTrace,
    ModelCallTrace,
    SessionTrace,
    ToolCallTrace,
)
from domain.storage.storage_tier import StorageTier
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver

logger = logging.getLogger(__name__)

_KIND_MAP: dict[str, type] = {
    "model_call": ModelCallTrace,
    "tool_call": ToolCallTrace,
    "approval": ApprovalTrace,
    "error": ErrorTrace,
}


class LocalFileTraceStoreAdapter:
    """本地 JSONL 文件 trace 存储。

    经 LocalFileTierResolver 按 StorageTier 定位 traces 目录，每个 session 的
    trace 保存为 `<traces_dir>/{session_id}.jsonl`，每行一个 JSON 编码的
    AgentStepTrace。目录随 tier 由解析器映射（PROJECT tier 结果与既有
    .epsilon/traces 等价），既有序列化逻辑保持不变。
    """

    def __init__(self, tier_resolver: LocalFileTierResolver) -> None:
        """构造 trace 存储适配器。

        Args:
            tier_resolver: 本地文件存储等级解析器，负责把 StorageTier 映射到
                具体 traces 目录（替代旧的构造期 store_dir）。
        """
        self._resolver = tier_resolver

    async def append_step(
        self,
        session_id: str,
        step: AgentStepTrace,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> None:
        """追加一步到指定 session trace 文件。

        Args:
            session_id: 会话唯一标识符。
            step: Agent 步骤追踪对象。
            tier: 存储等级定位维度，默认 PROJECT（兼容既有调用点）。

        IO 失败时记录 warning 并隔离故障，不中断主流程。
        """
        try:
            store_dir = self._resolver.resolve(tier).traces_dir(create=True)
            line = json.dumps(self._step_to_dict(step), ensure_ascii=False)
            self._append_line(store_dir, session_id, line)
        except Exception:
            logger.warning("trace append_step 失败，session_id=%s", session_id, exc_info=True)

    async def get_session_trace(
        self,
        session_id: str,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> SessionTrace | None:
        """读取完整 session trace。路径不存在返回 None。

        Args:
            session_id: 会话唯一标识符。
            tier: 存储等级定位维度，默认 PROJECT（兼容既有调用点）。

        Returns:
            SessionTrace 对象；文件不存在或读取失败时返回 None。
        """
        try:
            store_dir = self._resolver.resolve(tier).traces_dir(create=False)
            path = store_dir / f"{session_id}.jsonl"
            if not path.exists():
                return None
            steps = self._read_steps(path)
            started = steps[0].timestamp_epoch if steps else 0.0
            return SessionTrace(session_id=session_id, started_at_epoch=started, steps=steps)
        except Exception:
            logger.warning("trace get_session_trace 失败，session_id=%s", session_id, exc_info=True)
            return None

    async def list_traces(
        self,
        limit: int = 20,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> list[SessionTrace]:
        """按文件 mtime 倒序列出最近的 session trace 摘要。

        Args:
            limit: 最大返回条数。
            tier: 存储等级定位维度，默认 PROJECT（兼容既有调用点）。

        Returns:
            SessionTrace 摘要列表；目录不存在或读取失败时返回空列表。
        """
        try:
            store_dir = self._resolver.resolve(tier).traces_dir(create=False)
            files = sorted(
                store_dir.glob("*.jsonl"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )[:limit]
            return [self._file_to_summary(f) for f in files]
        except Exception:
            logger.warning("trace list_traces 失败", exc_info=True)
            return []

    def _append_line(self, store_dir: Path, session_id: str, line: str) -> None:
        """把单行 JSONL 追加写入 <store_dir>/{session_id}.jsonl。"""
        path = store_dir / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_steps(self, path: Path) -> list[AgentStepTrace]:
        steps: list[AgentStepTrace] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                steps.append(self._dict_to_step(json.loads(raw)))
            except Exception:
                logger.warning("trace 行解析失败，跳过: %s", raw[:100])
        return steps

    @staticmethod
    def _file_to_summary(path: Path) -> SessionTrace:
        """只读首行获取 started_at + 统计行数。"""
        line_count = 0
        started = 0.0
        try:
            with path.open("r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    line_count += 1
                    if i == 0 and line.strip():
                        data = json.loads(line)
                        started = data.get("timestamp_epoch", 0.0)
        except Exception:
            pass
        return SessionTrace(
            session_id=path.stem,
            started_at_epoch=started,
            steps=[],
            metadata={"step_count": line_count},
        )

    @staticmethod
    def _step_to_dict(step: AgentStepTrace) -> dict[str, Any]:
        return asdict(step)  # type: ignore[arg-type]

    @staticmethod
    def _dict_to_step(d: dict[str, Any]) -> AgentStepTrace:
        kind = d.pop("kind", None)
        cls = _KIND_MAP.get(kind)  # type: ignore[arg-type]
        if cls is None:
            raise ValueError(f"未知 trace kind: {kind}")
        if kind == "tool_call":
            # 兼容旧 JSONL 数据：新增的 metadata 字段在旧行中缺失，
            # 显式 pop 并以空 dict 兜底，保证含/不含 metadata 的新旧行均可读回。
            metadata = d.pop("metadata", {})
            return ToolCallTrace(**d, metadata=metadata)
        return cls(**d)
