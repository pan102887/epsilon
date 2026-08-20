"""Run worker 运行时协作者协议定义模块。

本模块只描述基础设施层 worker runtime 需要调用的结构化协作者能力，
用于避免 worker 和 manager 直接依赖应用层具体实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from domain.run.outcome import RunExecutionOutcome
from domain.run.ports import RunProgressSink
from domain.run.value_objects import RunSnapshot


class RunSegmentExecutor(Protocol):
    """执行单个 Run segment 的结构化协议。"""

    async def execute(
        self,
        snapshot: RunSnapshot,
        progress: RunProgressSink,
    ) -> RunExecutionOutcome:
        """执行 Run 快照并返回执行结果。"""
        ...


class RunRecoverySweep(Protocol):
    """执行过期 lease checkpoint recovery sweep 的结构化协议。"""

    async def sweep_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        """扫描过期 lease 并返回恢复或标记后的 Run 快照。"""
        ...


class RunRuntimeMetricsSink(Protocol):
    """写入 Run worker runtime 指标的结构化协议。"""

    def increment_claim_success(self) -> None:
        """记录成功领取 Run 的次数。"""
        ...

    def increment_lost(self, count: int = 1) -> None:
        """按数量记录 lost Run。"""
        ...

    def observe_execution_duration(self, duration_seconds: float) -> None:
        """记录单次 Run segment 执行耗时秒数。"""
        ...

    def increment_execution_failed(self) -> None:
        """记录 Run segment 执行失败次数。"""
        ...
